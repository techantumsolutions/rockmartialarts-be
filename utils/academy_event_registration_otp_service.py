"""
M17-S04 Academy Event Registration OTP + My Registrations.

Collection: academy_event_registration_otp
JWT scope: academy_event_registration
Reuses SMS helpers from reg-checkout; does not alter lead/reg-checkout OTP.
"""
from __future__ import annotations

import logging
import os
import random
import string
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import jwt
from fastapi import HTTPException
from passlib.context import CryptContext

from utils.database import get_db
from utils.helpers import serialize_doc
from utils.indian_phone import (
    canonical_indian_phone,
    otp_phone_variants,
    subscriber_phone_matches,
)
from utils.reg_checkout_sms import (
    public_sms_failure_hint,
    send_registration_sms,
    sms_provider_expects_delivery,
)

logger = logging.getLogger(__name__)

COL_OTP = "academy_event_registration_otp"
COL_REGS = "academy_event_registrations"
COL_STUDENTS = "students"

SECRET_KEY = os.environ.get("SECRET_KEY", "student_management_secret_key_2025_secure")
JWT_ALG = "HS256"
SCOPE = "academy_event_registration"

OTP_TTL_MIN = max(5, min(15, int(os.getenv("ACADEMY_EVENT_OTP_TTL_MIN", "10"))))
RESEND_COOLDOWN_SEC = int(os.getenv("ACADEMY_EVENT_OTP_RESEND_COOLDOWN_SEC", "30"))
VERIFICATION_JWT_HOURS = int(os.getenv("ACADEMY_EVENT_VERIFY_JWT_HOURS", "24"))
JWT_DECODE_LEEWAY_SEC = int(os.getenv("ACADEMY_EVENT_JWT_LEEWAY_SEC", "120"))

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


async def ensure_academy_event_registration_otp_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL_OTP].create_index("phone")
        await database[COL_OTP].create_index("expires_at")
    except Exception:
        logger.exception("Failed ensuring academy event registration OTP indexes")


def _otp_dlt_template_id() -> Optional[str]:
    v = os.getenv("DLT_OTP_TEMPLATE_ID", "").strip() or os.getenv("SMS_OTP_TEMPLATE_ID", "").strip()
    if v:
        return v
    legacy = os.getenv("DLT_TEMPLATE_OTP", "").strip()
    if not legacy or "%" in legacy or "{" in legacy or len(legacy) > 32:
        return None
    compact = legacy.replace("-", "").replace("_", "")
    if compact.isalnum() and not legacy.lower().startswith("your "):
        return legacy
    return None


def _otp_sms_message(otp: str) -> str:
    body = os.getenv("DLT_OTP_MESSAGE", "").strip()
    if not body:
        legacy = os.getenv("DLT_TEMPLATE_OTP", "").strip()
        if legacy and ("%s" in legacy or "{otp}" in legacy.lower()):
            body = legacy
    if body:
        if "%s" in body:
            try:
                return body % otp
            except Exception:
                logger.exception("DLT_OTP_MESSAGE formatting failed")
        return body.replace("{otp}", otp)
    return (
        "ROCK MARTIAL ARTS ACADEMY: Your OTP is "
        f"{otp}. Use this to view your event registrations. Do not share this code with anyone."
    )


def issue_event_registration_token(phone: str) -> str:
    norm = canonical_indian_phone(phone)
    if not norm:
        raise HTTPException(status_code=400, detail="Invalid phone number.")
    now = datetime.now(timezone.utc)
    payload = {
        "sub": norm,
        "scope": SCOPE,
        "iat": now,
        "exp": now + timedelta(hours=VERIFICATION_JWT_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=JWT_ALG)


def phone_from_event_registration_token(token: Optional[str]) -> str:
    if not token or not str(token).strip():
        raise HTTPException(status_code=401, detail="Phone verification required.")
    try:
        payload = jwt.decode(
            token.strip(),
            SECRET_KEY,
            algorithms=[JWT_ALG],
            leeway=JWT_DECODE_LEEWAY_SEC,
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail="Phone verification expired. Please verify your number again.",
        )
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired phone verification.")
    if payload.get("scope") != SCOPE:
        raise HTTPException(status_code=403, detail="Invalid phone verification.")
    sub = str(payload.get("sub") or "").strip()
    norm = canonical_indian_phone(sub)
    if not norm:
        raise HTTPException(status_code=403, detail="Invalid phone verification.")
    return norm


def _allow_sms_stub() -> bool:
    return os.getenv("ACADEMY_EVENT_ALLOW_SMS_STUB", "").lower() in (
        "1",
        "true",
        "yes",
    ) or os.getenv("REG_CHECKOUT_ALLOW_SMS_STUB", "").lower() in ("1", "true", "yes")


async def send_event_registration_otp(phone: str) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database not initialized")
    await ensure_academy_event_registration_otp_indexes(db)

    canonical = canonical_indian_phone(phone)
    if not canonical:
        raise HTTPException(status_code=400, detail="Invalid phone number.")

    variants = otp_phone_variants(canonical)
    filt: Dict[str, Any] = {"phone": {"$in": variants}}
    existing = await db[COL_OTP].find_one(filt)

    now = datetime.utcnow()
    if existing and existing.get("last_sent_at"):
        delta = (now - existing["last_sent_at"]).total_seconds()
        if delta < RESEND_COOLDOWN_SEC:
            raise HTTPException(
                status_code=429,
                detail=f"Please wait {int(RESEND_COOLDOWN_SEC - delta)}s before resending OTP.",
            )

    otp = "".join(random.choices(string.digits, k=6))
    code_hash = _pwd.hash(otp)
    expires = now + timedelta(minutes=OTP_TTL_MIN)
    otp_doc = {
        "phone": canonical,
        "code_hash": code_hash,
        "expires_at": expires,
        "last_sent_at": now,
    }
    if existing:
        await db[COL_OTP].update_one({"_id": existing["_id"]}, {"$set": otp_doc})
    else:
        await db[COL_OTP].insert_one(otp_doc)

    msg = _otp_sms_message(otp)
    tid = _otp_dlt_template_id()
    ok, sms_err = send_registration_sms(canonical, msg, template_id=tid)
    allow_stub = _allow_sms_stub()
    expects_sms = sms_provider_expects_delivery()
    if expects_sms and not allow_stub and not ok:
        await db[COL_OTP].delete_many({"phone": {"$in": variants}})
        hint = public_sms_failure_hint(sms_err)
        base = (
            "Could not send OTP SMS. Configure SMS credentials and DLT_OTP_TEMPLATE_ID, "
            "or set ACADEMY_EVENT_ALLOW_SMS_STUB=true for local/staging."
        )
        if hint:
            base = f"{base} Hint: {hint}"
        raise HTTPException(status_code=503, detail=base)
    if expects_sms and allow_stub and not ok:
        logger.warning(
            "Event registration OTP SMS failed but stub allowed — code stored for phone ...%s",
            canonical[-4:],
        )
    elif expects_sms and ok:
        logger.info("Event registration OTP sent for phone ending ...%s", canonical[-4:])

    return {"message": "OTP sent", "expires_in_seconds": OTP_TTL_MIN * 60}


async def verify_event_registration_otp(phone: str, otp: str) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database not initialized")

    canonical = canonical_indian_phone(phone)
    if not canonical:
        raise HTTPException(status_code=400, detail="Invalid phone number.")
    code = (otp or "").strip()
    if len(code) < 4:
        raise HTTPException(status_code=400, detail="Invalid OTP.")

    variants = otp_phone_variants(canonical)
    doc = await db[COL_OTP].find_one({"phone": {"$in": variants}})
    if not doc:
        raise HTTPException(status_code=400, detail="No OTP request for this number.")
    expires = doc.get("expires_at")
    if expires and expires < datetime.utcnow():
        raise HTTPException(status_code=400, detail="OTP expired. Please resend.")
    if not _pwd.verify(code, doc["code_hash"]):
        raise HTTPException(status_code=400, detail="Invalid OTP.")

    await db[COL_OTP].delete_one({"_id": doc["_id"]})
    token = issue_event_registration_token(canonical)
    return {
        "verified": True,
        "verification_token": token,
        "expires_in": VERIFICATION_JWT_HOURS * 3600,
        "phone": canonical,
    }


async def _phone_from_account(db, current_user: Optional[dict]) -> Optional[str]:
    if not current_user:
        return None
    for key in ("phone", "mobile", "contact_number", "contact"):
        p = canonical_indian_phone(current_user.get(key))
        if p:
            return p
    uid = current_user.get("id") or current_user.get("user_id")
    if not uid:
        return None
    student = await db[COL_STUDENTS].find_one({"id": uid})
    if not student:
        student = await db[COL_STUDENTS].find_one({"user_id": uid})
    if student:
        for key in ("phone", "mobile", "contact_number", "guardian_phone"):
            p = canonical_indian_phone(student.get(key))
            if p:
                return p
    return None


def _registration_phone_filter(canonical: str) -> Dict[str, Any]:
    variants = otp_phone_variants(canonical)
    # Also match legacy whitespace-stripped numbers without +91
    extra: List[str] = []
    for v in list(variants):
        digits = "".join(c for c in v if c.isdigit())
        if digits and digits not in variants:
            extra.append(digits)
        if len(digits) == 10 and f"+91{digits}" not in variants:
            extra.append(f"+91{digits}")
    all_v = list(dict.fromkeys([*variants, *extra]))
    return {"participant_phone": {"$in": all_v}}


def _public_reg(doc: Optional[dict]) -> Dict[str, Any]:
    out = serialize_doc(doc) or {}
    out.pop("razorpay_signature", None)
    return out


async def list_my_academy_event_registrations(
    *,
    verification_token: Optional[str] = None,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    phone: Optional[str] = None
    auth_via = None

    # Prefer OTP token when provided
    if verification_token:
        try:
            phone = phone_from_event_registration_token(verification_token)
            auth_via = "otp"
        except HTTPException:
            # Bearer may be a user JWT — fall through to account
            phone = None

    if not phone:
        phone = await _phone_from_account(db, current_user)
        if phone:
            auth_via = "account"

    if not phone and verification_token:
        # Re-raise OTP errors if account path failed
        phone = phone_from_event_registration_token(verification_token)
        auth_via = "otp"

    if not phone:
        raise HTTPException(
            status_code=401,
            detail="Verify your phone with OTP, or sign in with an account that has a mobile number.",
        )

    q = _registration_phone_filter(phone)
    rows = (
        await db[COL_REGS]
        .find(q)
        .sort([("created_at", -1)])
        .to_list(length=200)
    )
    filtered = [
        r
        for r in rows
        if subscriber_phone_matches(r.get("participant_phone"), phone)
    ]
    return {
        "registrations": [_public_reg(r) for r in filtered],
        "total": len(filtered),
        "phone": phone,
        "auth_via": auth_via,
    }


async def get_academy_event_registration_owned(
    registration_id: str,
    *,
    verification_token: Optional[str] = None,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    """Fetch registration only if caller owns it (OTP phone or account phone)."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    doc = await db[COL_REGS].find_one({"id": registration_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Registration not found")

    phone: Optional[str] = None
    if verification_token:
        phone = phone_from_event_registration_token(verification_token)
    else:
        phone = await _phone_from_account(db, current_user)

    if not phone:
        raise HTTPException(
            status_code=401,
            detail="Phone verification required to view this registration.",
        )

    if not subscriber_phone_matches(doc.get("participant_phone"), phone):
        raise HTTPException(status_code=403, detail="You do not own this registration.")

    return {"registration": _public_reg(doc)}
