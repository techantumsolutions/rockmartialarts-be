"""Partner organization (white-label tenant) service."""

from __future__ import annotations

import logging
import os
import re
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import HTTPException

from models.partner_org_models import (
    PartnerLogin,
    PartnerOrgCreate,
    PartnerOrgStatus,
    PartnerOrgUpdateStatus,
    PartnerPaymentMethod,
    PartnerProfileUpdate,
    PartnerSetPassword,
    PartnerOrgVerifyPayment,
    new_partner_org_id,
    partner_org_public,
)
from models.user_models import UserRole
from utils.auth import create_access_token, hash_password, verify_password
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.indian_phone import canonical_indian_phone
from utils.razorpay_client import get_razorpay_client, verify_razorpay_signature
from utils.resend_email import send_partner_invite_email

logger = logging.getLogger(__name__)

COL = "partner_organizations"
COL_USERS = "users"


def _phone_lookup_variants(phone: str) -> list:
    raw = (phone or "").strip()
    digits = "".join(c for c in raw if c.isdigit())
    if len(digits) < 10:
        return [raw] if raw else []
    norm = digits[-10:]
    variants = {
        norm,
        f"91{norm}",
        f"+91{norm}",
        f"+91-{norm}",
        f"0{norm}",
        f"91-{norm}",
        f"+91 {norm}",
        raw,
    }
    canonical = canonical_indian_phone(raw)
    if canonical:
        variants.add(canonical)
        variants.add(canonical[3:] if canonical.startswith("+91") else canonical)
    return [v for v in variants if v]


async def _assert_partner_email_phone_available(
    db,
    *,
    email: str,
    phone: str,
    allow_pending_partner_email: bool = False,
) -> None:
    """Fail fast before payment / partner creation when email or phone is already used."""
    email_clean = str(email or "").lower().strip()
    phone_clean = str(phone or "").strip()
    if not email_clean:
        raise HTTPException(status_code=400, detail="Email is required")
    if not phone_clean:
        raise HTTPException(status_code=400, detail="Mobile number is required")

    org_email_q: Dict[str, Any] = {"email": email_clean}
    if allow_pending_partner_email:
        org_email_q["status"] = {"$ne": PartnerOrgStatus.PENDING_PAYMENT.value}
    if await db[COL].find_one(org_email_q, {"id": 1}):
        raise HTTPException(status_code=400, detail="A partner with this email already exists")

    existing_user = await db[COL_USERS].find_one(
        {"email": email_clean},
        {"id": 1, "role": 1},
    )
    if existing_user and existing_user.get("role") != UserRole.PARTNER_SUPER_ADMIN.value:
        raise HTTPException(status_code=400, detail="This email is already registered")

    variants = _phone_lookup_variants(phone_clean)
    if not variants:
        raise HTTPException(status_code=400, detail="Enter a valid mobile number")

    phone_org_q: Dict[str, Any] = {"phone": {"$in": variants}}
    if allow_pending_partner_email:
        # Same email retrying unpaid checkout may keep the pending row's phone
        phone_org_q = {
            "$and": [
                {"phone": {"$in": variants}},
                {
                    "$or": [
                        {"email": {"$ne": email_clean}},
                        {"status": {"$ne": PartnerOrgStatus.PENDING_PAYMENT.value}},
                    ]
                },
            ]
        }
    if await db[COL].find_one(phone_org_q, {"id": 1}):
        raise HTTPException(
            status_code=400,
            detail="A partner with this mobile number already exists",
        )

    if await db[COL_USERS].find_one({"phone": {"$in": variants}}, {"id": 1}):
        raise HTTPException(
            status_code=400,
            detail="This mobile number is already registered",
        )

    # Digits-suffix match for legacy phone formatting in users
    digits = "".join(c for c in phone_clean if c.isdigit())
    norm = digits[-10:] if len(digits) >= 10 else ""
    if norm:
        async for doc in db[COL_USERS].find(
            {"phone": {"$exists": True, "$nin": [None, ""]}},
            {"phone": 1},
        ).limit(5000):
            stored = "".join(c for c in str(doc.get("phone") or "") if c.isdigit())
            if len(stored) >= 10 and stored[-10:] == norm:
                raise HTTPException(
                    status_code=400,
                    detail="This mobile number is already registered",
                )


def _frontend_url() -> str:
    return (os.getenv("FRONTEND_URL") or os.getenv("NEXT_PUBLIC_APP_URL") or "http://localhost:3022").rstrip(
        "/"
    )


async def resolve_partner_role_login_urls(db, partner_org_id: Optional[str]) -> dict:
    """White-label role login URLs for a partner org, or Rock defaults when unscoped."""
    fe = _frontend_url()
    defaults = {
        "slug": None,
        "admin": f"{fe}/partners/login",
        "branch_manager": f"{fe}/branch-manager/login",
        "coach": f"{fe}/coach/login",
        "student": f"{fe}/login",
    }
    org_id = (partner_org_id or "").strip()
    if not org_id:
        return defaults
    org = await db[COL].find_one({"id": org_id})
    slug = ((org or {}).get("slug") or "").strip()
    if not slug:
        return defaults
    base = f"{fe}/partners/{slug}"
    return {
        "slug": slug,
        "admin": f"{base}/login",
        "branch_manager": f"{base}/branch-manager/login",
        "coach": f"{base}/coach/login",
        "student": f"{base}/student/login",
    }


async def _onboarding_fee_paise() -> int:
    """Partner online onboarding fee in paise — prefers system_settings, else env."""
    inr: Optional[float] = None
    try:
        from controllers.settings_controller import SettingsController

        inr = float(await SettingsController.get_default_partner_registration_fee())
    except Exception:
        inr = None
    if inr is None:
        try:
            inr = float(os.getenv("PARTNER_ONBOARDING_FEE_INR", "999") or "999")
        except ValueError:
            inr = 999.0
    paise = int(round(inr * 100))
    if paise < 100:
        raise HTTPException(
            status_code=500,
            detail="Partner registration fee must be at least ₹1",
        )
    return paise


def slugify_partner_name(name: str) -> str:
    raw = (name or "").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    return slug or "partner"


async def ensure_unique_partner_slug(db, base: str, *, exclude_id: Optional[str] = None) -> str:
    slug = slugify_partner_name(base)
    n = 0
    while True:
        candidate = slug if n == 0 else f"{slug}-{n}"
        q: Dict[str, Any] = {"slug": candidate}
        if exclude_id:
            q["id"] = {"$ne": exclude_id}
        exists = await db[COL].find_one(q, {"id": 1})
        if not exists:
            return candidate
        n += 1


async def ensure_partner_org_indexes(db) -> None:
    try:
        await db[COL].create_index("id", unique=True)
        await db[COL].create_index("slug", unique=True)
        await db[COL].create_index("email", unique=True)
        await db[COL].create_index("invite_token")
        await db[COL].create_index([("is_active", 1), ("status", 1)])
    except Exception:
        logger.exception("partner_organizations index ensure failed")


def partner_org_id_of(user: Optional[dict]) -> Optional[str]:
    if not user:
        return None
    if str(user.get("role") or "").lower() != UserRole.PARTNER_SUPER_ADMIN.value:
        return None
    return (user.get("partner_org_id") or "").strip() or None


async def assert_partner_org_active(db, partner_org_id: str) -> dict:
    org = await db[COL].find_one({"id": partner_org_id})
    if not org:
        raise HTTPException(status_code=404, detail="Partner organization not found")
    if org.get("is_active") is False or org.get("status") == PartnerOrgStatus.DISABLED.value:
        raise HTTPException(status_code=403, detail="This partner account is disabled")
    return org


async def _send_invite_email(org: dict, invite_token: str) -> bool:
    slug = org.get("slug") or "partner"
    fe = _frontend_url()
    set_link = f"{fe}/partners/{slug}/set-password?token={invite_token}"
    login_link = f"{fe}/partners/{slug}/login"
    role_login_links = {
        "admin": login_link,
        "branch_manager": f"{fe}/partners/{slug}/branch-manager/login",
        "coach": f"{fe}/partners/{slug}/coach/login",
        "student": f"{fe}/partners/{slug}/student/login",
    }
    name = org.get("name") or "Partner"
    email = org.get("email")
    if not email:
        logger.error("Partner invite skipped: org %s has no email", org.get("id"))
        return False
    sent = await send_partner_invite_email(
        to_email=str(email),
        partner_name=str(name),
        phone=str(org.get("phone") or ""),
        address=str(org.get("address") or ""),
        payment_method=str(org.get("payment_method") or ""),
        payment_status=str(org.get("payment_status") or ""),
        set_password_link=set_link,
        login_link=login_link,
        role_login_links=role_login_links,
    )
    if not sent:
        logger.error(
            "Partner invite email was not delivered to %s (check Resend logs / FROM domain). Link: %s",
            email,
            set_link,
        )
    return bool(sent)


async def _finalize_paid_org(
    db,
    org: dict,
    *,
    payment_method: str,
    payment_status: str,
    razorpay_order_id: Optional[str] = None,
    razorpay_payment_id: Optional[str] = None,
    razorpay_signature: Optional[str] = None,
    current_user: Optional[dict] = None,
) -> dict:
    now = datetime.utcnow()
    invite_token = secrets.token_urlsafe(32)
    invite_expires = now + timedelta(days=7)

    # Create partner super admin user without password (set via invite)
    existing = await db[COL_USERS].find_one({"email": org["email"].lower()})
    if existing and existing.get("role") != UserRole.PARTNER_SUPER_ADMIN.value:
        raise HTTPException(
            status_code=400,
            detail="This email is already registered to another account type",
        )

    admin_id = (existing or {}).get("id") or str(new_partner_org_id())
    user_doc = {
        "id": admin_id,
        "email": org["email"].lower(),
        "phone": org.get("phone") or "",
        "first_name": (org.get("name") or "Partner").split(" ")[0],
        "last_name": "Admin",
        "full_name": f"{org.get('name') or 'Partner'} Admin",
        "role": UserRole.PARTNER_SUPER_ADMIN.value,
        "partner_org_id": org["id"],
        "is_active": True,
        "password_hash": (existing or {}).get("password_hash"),  # may be None until set
        "must_set_password": True,
        "updated_at": now,
    }
    if not existing:
        user_doc["created_at"] = now
        if current_user:
            user_doc["created_by"] = {"user_id": current_user.get("id"), "role": current_user.get("role")}
        await db[COL_USERS].insert_one(user_doc)
    else:
        await db[COL_USERS].update_one(
            {"id": admin_id},
            {
                "$set": {
                    **{k: v for k, v in user_doc.items() if k != "id"},
                }
            },
        )

    patch = {
        "payment_method": payment_method,
        "payment_status": payment_status,
        "status": PartnerOrgStatus.INVITED.value,
        "is_active": True,
        "admin_user_id": admin_id,
        "invite_token": invite_token,
        "invite_expires_at": invite_expires,
        "updated_at": now,
    }
    if razorpay_order_id:
        patch["razorpay_order_id"] = razorpay_order_id
    if razorpay_payment_id:
        patch["razorpay_payment_id"] = razorpay_payment_id
    if razorpay_signature:
        patch["razorpay_signature"] = razorpay_signature

    await db[COL].update_one({"id": org["id"]}, {"$set": patch})
    updated = await db[COL].find_one({"id": org["id"]})
    email_sent = await _send_invite_email(updated, invite_token)
    await db[COL].update_one(
        {"id": org["id"]},
        {
            "$set": {
                "invite_email_sent": email_sent,
                "invite_email_sent_at": datetime.utcnow() if email_sent else None,
                "updated_at": datetime.utcnow(),
            }
        },
    )
    updated = await db[COL].find_one({"id": org["id"]})
    updated["_invite_email_sent"] = email_sent
    return updated


async def create_partner_offline(body: PartnerOrgCreate, current_user: dict) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_partner_org_indexes(db)

    email = str(body.email).lower().strip()
    phone = body.phone.strip()
    await _assert_partner_email_phone_available(
        db,
        email=email,
        phone=phone,
        allow_pending_partner_email=False,
    )

    now = datetime.utcnow()
    org_id = new_partner_org_id()
    slug = await ensure_unique_partner_slug(db, body.name)
    org = {
        "id": org_id,
        "name": body.name.strip(),
        "slug": slug,
        "email": email,
        "phone": phone,
        "address": body.address.strip(),
        "payment_method": PartnerPaymentMethod.OFFLINE.value,
        "payment_status": "offline_recorded",
        "status": PartnerOrgStatus.PENDING_PAYMENT.value,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
        "created_by": current_user.get("id"),
    }
    await db[COL].insert_one(org)
    updated = await _finalize_paid_org(
        db,
        org,
        payment_method=PartnerPaymentMethod.OFFLINE.value,
        payment_status="offline_recorded",
        current_user=current_user,
    )
    email_sent = bool(updated.get("_invite_email_sent"))
    return {
        "message": (
            "Partner created. Invitation email sent to set password."
            if email_sent
            else "Partner created, but the invitation email could not be delivered. Use Resend invite from Partner Profiles."
        ),
        "invite_email_sent": email_sent,
        "partner": partner_org_public(updated),
    }


async def create_partner_online_order(body: PartnerOrgCreate, current_user: dict) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_partner_org_indexes(db)

    email = str(body.email).lower().strip()
    phone = body.phone.strip()
    # Validate email + phone before opening Razorpay (avoid pay-then-fail)
    await _assert_partner_email_phone_available(
        db,
        email=email,
        phone=phone,
        allow_pending_partner_email=True,
    )

    amount_paise = await _onboarding_fee_paise()
    now = datetime.utcnow()
    existing_pending = await db[COL].find_one(
        {"email": email, "status": PartnerOrgStatus.PENDING_PAYMENT.value}
    )
    if existing_pending:
        org_id = existing_pending["id"]
        slug = existing_pending.get("slug") or await ensure_unique_partner_slug(db, body.name)
        await db[COL].update_one(
            {"id": org_id},
            {
                "$set": {
                    "name": body.name.strip(),
                    "phone": phone,
                    "address": body.address.strip(),
                    "payment_method": PartnerPaymentMethod.ONLINE.value,
                    "updated_at": now,
                }
            },
        )
    else:
        org_id = new_partner_org_id()
        slug = await ensure_unique_partner_slug(db, body.name)
        await db[COL].insert_one(
            {
                "id": org_id,
                "name": body.name.strip(),
                "slug": slug,
                "email": email,
                "phone": phone,
                "address": body.address.strip(),
                "payment_method": PartnerPaymentMethod.ONLINE.value,
                "payment_status": "pending",
                "status": PartnerOrgStatus.PENDING_PAYMENT.value,
                "is_active": True,
                "created_at": now,
                "updated_at": now,
                "created_by": current_user.get("id"),
            }
        )

    client = get_razorpay_client()
    try:
        order = client.order.create(
            {
                "amount": amount_paise,
                "currency": "INR",
                "payment_capture": 1,
                "notes": {
                    "type": "partner_onboarding",
                    "partner_org_id": org_id,
                    "email": email,
                },
            }
        )
    except Exception:
        logger.exception("Razorpay partner onboarding order.create failed")
        raise HTTPException(
            status_code=502,
            detail="We could not reach the payment service. Please try again.",
        )

    await db[COL].update_one(
        {"id": org_id},
        {"$set": {"razorpay_order_id": order["id"], "updated_at": datetime.utcnow()}},
    )
    org = await db[COL].find_one({"id": org_id})
    return {
        "message": "Complete Razorpay payment to activate the partner",
        "partner": partner_org_public(org),
        "order": {"id": order["id"], "amount": amount_paise, "currency": "INR"},
        "key": os.getenv("RAZORPAY_KEY_ID", "").strip(),
    }


async def verify_partner_online_payment(
    body: PartnerOrgVerifyPayment, current_user: dict
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    org = await db[COL].find_one({"id": body.partner_org_id})
    if not org:
        raise HTTPException(status_code=404, detail="Partner not found")
    if org.get("status") != PartnerOrgStatus.PENDING_PAYMENT.value:
        return {
            "message": "Partner already activated",
            "partner": partner_org_public(org),
        }
    if org.get("razorpay_order_id") and org["razorpay_order_id"] != body.razorpay_order_id:
        raise HTTPException(status_code=400, detail="Order mismatch")

    if not verify_razorpay_signature(
        body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature
    ):
        raise HTTPException(status_code=400, detail="Invalid payment signature")

    updated = await _finalize_paid_org(
        db,
        org,
        payment_method=PartnerPaymentMethod.ONLINE.value,
        payment_status="paid",
        razorpay_order_id=body.razorpay_order_id,
        razorpay_payment_id=body.razorpay_payment_id,
        razorpay_signature=body.razorpay_signature,
        current_user=current_user,
    )
    email_sent = bool(updated.get("_invite_email_sent"))
    return {
        "message": (
            "Payment verified. Invitation email sent to set password."
            if email_sent
            else "Payment verified and partner created, but the invitation email could not be delivered. Use Resend invite from Partner Profiles."
        ),
        "invite_email_sent": email_sent,
        "partner": partner_org_public(updated),
    }


async def resend_partner_invite(partner_org_id: str, current_user: dict) -> Dict[str, Any]:
    """Rotate invite token and re-send set-password email (Rock SA only)."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    org = await db[COL].find_one({"id": partner_org_id})
    if not org:
        raise HTTPException(status_code=404, detail="Partner not found")

    status = org.get("status")
    if status == PartnerOrgStatus.PENDING_PAYMENT.value:
        raise HTTPException(
            status_code=400,
            detail="Partner payment is still pending. Complete payment before sending invite.",
        )
    if status == PartnerOrgStatus.DISABLED.value or org.get("is_active") is False:
        raise HTTPException(status_code=400, detail="Enable the partner before resending the invite.")

    admin = None
    if org.get("admin_user_id"):
        admin = await db[COL_USERS].find_one({"id": org["admin_user_id"]})
    if not admin and org.get("email"):
        admin = await db[COL_USERS].find_one(
            {"email": str(org["email"]).lower(), "role": UserRole.PARTNER_SUPER_ADMIN.value}
        )

    if admin and admin.get("password_hash") and not admin.get("must_set_password"):
        raise HTTPException(
            status_code=400,
            detail="Partner admin already set a password. Ask them to use Forgot password instead.",
        )

    now = datetime.utcnow()
    invite_token = secrets.token_urlsafe(32)
    invite_expires = now + timedelta(days=7)
    await db[COL].update_one(
        {"id": partner_org_id},
        {
            "$set": {
                "invite_token": invite_token,
                "invite_expires_at": invite_expires,
                "status": PartnerOrgStatus.INVITED.value,
                "updated_at": now,
                "updated_by": current_user.get("id"),
            }
        },
    )
    if admin:
        await db[COL_USERS].update_one(
            {"id": admin["id"]},
            {"$set": {"must_set_password": True, "updated_at": now}},
        )

    updated = await db[COL].find_one({"id": partner_org_id})
    email_sent = await _send_invite_email(updated, invite_token)
    await db[COL].update_one(
        {"id": partner_org_id},
        {
            "$set": {
                "invite_email_sent": email_sent,
                "invite_email_sent_at": now if email_sent else None,
                "updated_at": datetime.utcnow(),
            }
        },
    )
    updated = await db[COL].find_one({"id": partner_org_id})
    if not email_sent:
        raise HTTPException(
            status_code=502,
            detail="Could not deliver the invitation email. Check Resend configuration and try again.",
        )
    return {
        "message": "Invitation email resent successfully.",
        "invite_email_sent": True,
        "partner": partner_org_public(updated),
    }


async def set_partner_password(body: PartnerSetPassword) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    org = await db[COL].find_one({"invite_token": body.token})
    if not org:
        raise HTTPException(status_code=400, detail="Invalid or expired invitation link")
    expires = org.get("invite_expires_at")
    if isinstance(expires, datetime) and expires < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Invitation link has expired")

    admin_id = org.get("admin_user_id")
    user = await db[COL_USERS].find_one({"id": admin_id}) if admin_id else None
    if not user:
        raise HTTPException(status_code=404, detail="Partner admin user not found")

    now = datetime.utcnow()
    await db[COL_USERS].update_one(
        {"id": admin_id},
        {
            "$set": {
                "password_hash": hash_password(body.new_password),
                "must_set_password": False,
                "updated_at": now,
            }
        },
    )
    await db[COL].update_one(
        {"id": org["id"]},
        {
            "$set": {
                "status": PartnerOrgStatus.ACTIVE.value,
                "activated_at": now,
                "updated_at": now,
            },
            "$unset": {"invite_token": "", "invite_expires_at": ""},
        },
    )
    updated = await db[COL].find_one({"id": org["id"]})
    return {
        "message": "Password created successfully. You can now log in.",
        "partner": partner_org_public(updated),
        "login_path": f"/partners/{updated.get('slug')}/login",
    }


async def login_partner(slug: str, body: PartnerLogin) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    org = await db[COL].find_one({"slug": (slug or "").strip().lower()})
    if not org:
        raise HTTPException(status_code=404, detail="Partner not found")
    if org.get("is_active") is False or org.get("status") == PartnerOrgStatus.DISABLED.value:
        raise HTTPException(
            status_code=401,
            detail="This partner account is disabled. Please contact administrator.",
        )

    email = str(body.email).lower().strip()
    if email != str(org.get("email") or "").lower():
        # Allow login with admin user email (same in normal flow)
        pass

    user = await db[COL_USERS].find_one(
        {
            "email": email,
            "role": UserRole.PARTNER_SUPER_ADMIN.value,
            "partner_org_id": org["id"],
        }
    )
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if user.get("is_active") is False:
        raise HTTPException(status_code=401, detail="Account is inactive")
    if user.get("must_set_password") or not user.get("password_hash"):
        raise HTTPException(
            status_code=403,
            detail="Please create your password using the invitation email link first.",
        )
    if not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(
        data={
            "sub": user["id"],
            "email": user["email"],
            "role": UserRole.PARTNER_SUPER_ADMIN.value,
            "partner_org_id": org["id"],
            "partner_slug": org.get("slug"),
        }
    )
    user_out = serialize_doc(user)
    user_out.pop("password_hash", None)
    user_out["partner_org_id"] = org["id"]
    user_out["partner_slug"] = org.get("slug")
    user_out["partner_name"] = org.get("name")
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": 60 * 60 * 24,
        "user": user_out,
        "partner": partner_org_public(org),
        "message": "Login successful",
    }


async def get_partner_public_by_slug(slug: str) -> Dict[str, Any]:
    db = get_db()
    org = await db[COL].find_one({"slug": (slug or "").strip().lower()})
    if not org:
        raise HTTPException(status_code=404, detail="Partner not found")
    return {
        "name": org.get("name"),
        "slug": org.get("slug"),
        "is_active": org.get("is_active", True),
        "status": org.get("status"),
    }


async def get_partner_me(current_user: dict) -> Dict[str, Any]:
    """Return the logged-in partner admin profile (+ org name)."""
    db = get_db()
    org_id = partner_org_id_of(current_user)
    if not org_id:
        raise HTTPException(status_code=403, detail="Partner organization is missing on this account")
    org = await db[COL].find_one({"id": org_id})
    user = await db[COL_USERS].find_one({"id": current_user.get("id")})
    if not user:
        raise HTTPException(status_code=404, detail="Partner admin user not found")
    return {
        "id": user.get("id"),
        "full_name": user.get("full_name")
        or f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
        or (org or {}).get("name")
        or "Partner Admin",
        "email": user.get("email") or "",
        "phone": user.get("phone") or "",
        "is_active": user.get("is_active", True),
        "partner_org_id": org_id,
        "partner_name": (org or {}).get("name"),
        "partner_slug": (org or {}).get("slug"),
        "role": UserRole.PARTNER_SUPER_ADMIN.value,
        "role_label": "Partner Admin",
        "created_at": user.get("created_at"),
        "updated_at": user.get("updated_at"),
    }


async def update_partner_me(body: PartnerProfileUpdate, current_user: dict) -> Dict[str, Any]:
    """Update the logged-in partner admin profile fields."""
    db = get_db()
    user_id = current_user.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    patch: Dict[str, Any] = {"updated_at": datetime.utcnow()}
    if body.full_name is not None:
        name = body.full_name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Full name is required")
        patch["full_name"] = name
        parts = name.split(None, 1)
        patch["first_name"] = parts[0]
        patch["last_name"] = parts[1] if len(parts) > 1 else ""
    if body.email is not None:
        email_clean = str(body.email).strip().lower()
        conflict = await db[COL_USERS].find_one(
            {
                "email": email_clean,
                "id": {"$ne": user_id},
            },
            {"id": 1},
        )
        if conflict:
            raise HTTPException(status_code=400, detail="This email address is already registered")
        patch["email"] = email_clean
    if body.phone is not None:
        phone_clean = body.phone.strip()
        if phone_clean:
            conflict = await db[COL_USERS].find_one(
                {
                    "phone": phone_clean,
                    "id": {"$ne": user_id},
                },
                {"id": 1},
            )
            if conflict:
                raise HTTPException(status_code=400, detail="This mobile number is already registered")
            patch["phone"] = phone_clean

    result = await db[COL_USERS].update_one({"id": user_id}, {"$set": patch})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Partner admin user not found")

    # Keep org contact email in sync when partner admin email changes
    org_id = partner_org_id_of(current_user)
    if org_id and "email" in patch:
        await db[COL].update_one(
            {"id": org_id},
            {"$set": {"email": patch["email"], "updated_at": datetime.utcnow()}},
        )

    updated = await get_partner_me(current_user)
    return {"message": "Profile updated successfully", "data": updated}


async def get_partner_admin_profile(current_user: dict) -> Dict[str, Any]:
    """Return the logged-in partner super admin profile for the dashboard Profile page."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    user_id = str((current_user or {}).get("id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await db[COL_USERS].find_one({"id": user_id})
    if not user or str(user.get("role") or "") != UserRole.PARTNER_SUPER_ADMIN.value:
        raise HTTPException(status_code=404, detail="Partner profile not found")
    org_id = str(user.get("partner_org_id") or current_user.get("partner_org_id") or "").strip()
    org = await db[COL].find_one({"id": org_id}) if org_id else None
    data = serialize_doc(user)
    data.pop("password_hash", None)
    data["partner_org_id"] = org_id or None
    data["partner_name"] = (org or {}).get("name") or current_user.get("partner_name")
    data["partner_slug"] = (org or {}).get("slug") or current_user.get("partner_slug")
    return {"status": "success", "data": data}


async def update_partner_admin_profile(current_user: dict, body: Dict[str, Any]) -> Dict[str, Any]:
    """Update name / email / phone for the logged-in partner super admin."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    user_id = str((current_user or {}).get("id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await db[COL_USERS].find_one({"id": user_id})
    if not user or str(user.get("role") or "") != UserRole.PARTNER_SUPER_ADMIN.value:
        raise HTTPException(status_code=404, detail="Partner profile not found")

    patch: Dict[str, Any] = {"updated_at": datetime.utcnow()}
    full_name = str((body or {}).get("full_name") or "").strip()
    email = str((body or {}).get("email") or "").strip().lower()
    phone = str((body or {}).get("phone") or "").strip()

    if full_name:
        patch["full_name"] = full_name
        parts = full_name.split(None, 1)
        patch["first_name"] = parts[0]
        patch["last_name"] = parts[1] if len(parts) > 1 else ""
    if email:
        other = await db[COL_USERS].find_one(
            {"email": email, "id": {"$ne": user_id}},
            {"id": 1},
        )
        if other:
            raise HTTPException(status_code=400, detail="Email is already in use")
        patch["email"] = email
    if "phone" in (body or {}):
        patch["phone"] = phone or None

    await db[COL_USERS].update_one({"id": user_id}, {"$set": patch})
    return await get_partner_admin_profile(current_user)


async def list_partner_orgs(
    *,
    skip: int = 0,
    limit: int = 100,
    include_inactive: bool = True,
) -> Dict[str, Any]:
    db = get_db()
    await ensure_partner_org_indexes(db)
    q: Dict[str, Any] = {}
    if not include_inactive:
        q["is_active"] = True
    total = await db[COL].count_documents(q)
    rows = (
        await db[COL]
        .find(q)
        .sort("created_at", -1)
        .skip(max(0, skip))
        .limit(max(1, min(limit, 200)))
        .to_list(limit)
    )
    return {
        "partners": [partner_org_public(r) for r in rows],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


async def set_partner_org_active(
    partner_org_id: str, body: PartnerOrgUpdateStatus, current_user: dict
) -> Dict[str, Any]:
    db = get_db()
    org = await db[COL].find_one({"id": partner_org_id})
    if not org:
        raise HTTPException(status_code=404, detail="Partner not found")
    now = datetime.utcnow()
    patch: Dict[str, Any] = {
        "is_active": bool(body.is_active),
        "updated_at": now,
        "updated_by": current_user.get("id"),
    }
    if body.is_active:
        if org.get("status") == PartnerOrgStatus.DISABLED.value:
            patch["status"] = (
                PartnerOrgStatus.ACTIVE.value
                if org.get("activated_at")
                else PartnerOrgStatus.INVITED.value
            )
    else:
        patch["status"] = PartnerOrgStatus.DISABLED.value

    await db[COL].update_one({"id": partner_org_id}, {"$set": patch})
    # Mirror onto admin user
    if org.get("admin_user_id"):
        await db[COL_USERS].update_one(
            {"id": org["admin_user_id"]},
            {"$set": {"is_active": bool(body.is_active), "updated_at": now}},
        )
    updated = await db[COL].find_one({"id": partner_org_id})
    return {
        "message": "Partner enabled" if body.is_active else "Partner disabled",
        "partner": partner_org_public(updated),
    }
