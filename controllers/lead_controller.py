from fastapi import HTTPException, status
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import logging
import os
import random
import string
import uuid

import jwt
from passlib.context import CryptContext

from models.lead_models import (
    LEAD_STATUSES,
    LeadCreate,
    LeadOtpVerifyBody,
    LeadResponse,
    LeadStatus,
    LeadStatusUpdate,
)
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.indian_phone import canonical_indian_phone, otp_phone_variants
from utils.reg_checkout_sms import (
    public_sms_failure_hint,
    send_registration_sms,
    sms_provider_expects_delivery,
)
from controllers.reg_checkout_controller import _otp_dlt_template_id, _otp_sms_message

logger = logging.getLogger(__name__)

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
COL_LEAD_OTP = "lead_popup_otp"
SECRET_KEY = os.environ.get("SECRET_KEY", "student_management_secret_key_2025_secure")
JWT_ALG = "HS256"
LEAD_OTP_SCOPE = "lead_popup"


def _lead_otp_ttl_seconds() -> int:
    raw = os.getenv("LEAD_OTP_EXPIRY_SECONDS", "300")
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = 300
    return n if n > 0 else 300


def _lead_resend_cooldown_sec() -> int:
    raw = os.getenv("LEAD_OTP_RESEND_COOLDOWN_SEC", "30")
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = 30
    return max(0, n)


def _issue_lead_popup_token(phone: str) -> str:
    now = datetime.now(timezone.utc)
    ttl = max(_lead_otp_ttl_seconds(), 300)
    payload = {
        "sub": phone,
        "scope": LEAD_OTP_SCOPE,
        "iat": now,
        "exp": now + timedelta(seconds=ttl),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=JWT_ALG)

# Must match Next.js default LEAD_CAPTURE_PLACEHOLDER_EMAIL (website popup, no email field)
_LEAD_EMAIL_PLACEHOLDER = (os.getenv("LEAD_EMAIL_PLACEHOLDER") or "website-popup@example.com").strip().lower()


def _normalize_lead_status(raw: Any) -> LeadStatus:
    s = (str(raw).strip().lower() if raw is not None else "")
    if s in LEAD_STATUSES:
        return s  # type: ignore[return-value]
    return "new"


def _lead_response_from_ser(ser: Dict[str, Any], fallback_now: Optional[datetime] = None) -> LeadResponse:
    now = fallback_now or datetime.utcnow()
    return LeadResponse(
        id=ser["id"],
        name=ser.get("name", "") or "",
        email=ser.get("email") or "",
        phone=ser.get("phone", "") or "",
        course=ser.get("course", "") or "",
        source=ser.get("source"),
        branch_id=ser.get("branch_id"),
        branch_name=ser.get("branch_name"),
        status=_normalize_lead_status(ser.get("status")),
        created_at=ser.get("created_at", now),
    )


class LeadController:
    @staticmethod
    async def create_lead(data: LeadCreate) -> LeadResponse:
        try:
            db = get_db()
            now = datetime.utcnow()
            email_raw = (data.email or "").strip().lower() if data.email else ""
            if email_raw == _LEAD_EMAIL_PLACEHOLDER:
                email_raw = ""
            doc: Dict[str, Any] = {
                "id": str(uuid.uuid4()),
                "name": data.name.strip(),
                "email": email_raw,
                "phone": data.phone.strip(),
                "course": (data.course or "").strip(),
                "source": (data.source or "").strip() or None,
                "branch_id": (data.branch_id or "").strip() or None,
                "branch_name": (data.branch_name or "").strip() or None,
                "status": "new",
                "created_at": now,
                "updated_at": now,
            }
            await db.leads.insert_one(doc)
            ser = serialize_doc(doc)
            return _lead_response_from_ser(ser, now)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to save lead: {str(e)}",
            )

    @staticmethod
    async def list_leads(
        skip: int = 0,
        limit: int = 50,
        search: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            db = get_db()
            clauses: List[Dict[str, Any]] = []
            if search and search.strip():
                s = search.strip()
                clauses.append(
                    {
                        "$or": [
                            {"name": {"$regex": s, "$options": "i"}},
                            {"email": {"$regex": s, "$options": "i"}},
                            {"phone": {"$regex": s, "$options": "i"}},
                            {"course": {"$regex": s, "$options": "i"}},
                            {"branch_name": {"$regex": s, "$options": "i"}},
                            {"branch_id": {"$regex": s, "$options": "i"}},
                        ]
                    }
                )
            st = (status or "").strip().lower()
            if st in LEAD_STATUSES:
                if st == "new":
                    clauses.append(
                        {
                            "$or": [
                                {"status": "new"},
                                {"status": {"$exists": False}},
                                {"status": None},
                                {"status": ""},
                            ]
                        }
                    )
                else:
                    clauses.append({"status": st})
            if not clauses:
                q: Dict[str, Any] = {}
            elif len(clauses) == 1:
                q = clauses[0]
            else:
                q = {"$and": clauses}
            limit = min(max(limit, 1), 200)
            skip = max(skip, 0)
            cursor = db.leads.find(q).sort("created_at", -1).skip(skip).limit(limit)
            items: List[LeadResponse] = []
            for raw in await cursor.to_list(length=limit):
                ser = serialize_doc(raw)
                items.append(_lead_response_from_ser(ser))
            total = await db.leads.count_documents(q)
            return {"leads": items, "total": total, "skip": skip, "limit": limit}
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to list leads: {str(e)}",
            )

    @staticmethod
    async def update_status(lead_id: str, body: LeadStatusUpdate) -> LeadResponse:
        db = get_db()
        if db is None:
            raise HTTPException(status_code=503, detail="Database not initialized")
        lid = (lead_id or "").strip()
        if not lid:
            raise HTTPException(status_code=404, detail="Lead not found")
        now = datetime.utcnow()
        result = await db.leads.find_one_and_update(
            {"id": lid},
            {"$set": {"status": body.status, "updated_at": now}},
            return_document=True,
        )
        if not result:
            raise HTTPException(status_code=404, detail="Lead not found")
        return _lead_response_from_ser(serialize_doc(result), now)

    @staticmethod
    async def send_otp(phone: str) -> Dict[str, Any]:
        db = get_db()
        if db is None:
            raise HTTPException(status_code=503, detail="Database not initialized")

        canonical = canonical_indian_phone(phone)
        if not canonical:
            raise HTTPException(status_code=400, detail="Invalid phone number.")

        variants = otp_phone_variants(canonical)
        existing = await db[COL_LEAD_OTP].find_one({"phone": {"$in": variants}})
        now = datetime.utcnow()
        cooldown = _lead_resend_cooldown_sec()
        if existing and existing.get("last_sent_at") and cooldown > 0:
            delta = (now - existing["last_sent_at"]).total_seconds()
            if delta < cooldown:
                raise HTTPException(
                    status_code=429,
                    detail=f"Please wait {int(cooldown - delta)}s before resending OTP.",
                )

        otp = "".join(random.choices(string.digits, k=6))
        ttl = _lead_otp_ttl_seconds()
        otp_doc = {
            "phone": canonical,
            "code_hash": _pwd.hash(otp),
            "expires_at": now + timedelta(seconds=ttl),
            "last_sent_at": now,
        }
        if existing:
            await db[COL_LEAD_OTP].update_one({"_id": existing["_id"]}, {"$set": otp_doc})
        else:
            await db[COL_LEAD_OTP].insert_one(otp_doc)

        msg = _otp_sms_message(otp)
        tid = _otp_dlt_template_id()
        ok, sms_err = send_registration_sms(canonical, msg, template_id=tid)
        allow_stub = os.getenv("REG_CHECKOUT_ALLOW_SMS_STUB", "").lower() in ("1", "true", "yes")
        expects_sms = sms_provider_expects_delivery()
        if expects_sms and not allow_stub and not ok:
            await db[COL_LEAD_OTP].delete_many({"phone": {"$in": variants}})
            hint = public_sms_failure_hint(sms_err)
            base = "Could not send OTP SMS. Please try again in a moment."
            if hint:
                base = f"{base} {hint}"
            raise HTTPException(status_code=503, detail=base)
        if expects_sms and ok:
            logger.info("Lead popup OTP sent for phone ending ...%s", canonical[-4:])
        elif not expects_sms:
            logger.warning("Lead popup OTP stored but SMS gateway is not configured.")

        return {"ok": True, "message": "OTP sent", "expires_in_seconds": ttl}

    @staticmethod
    async def verify_otp(body: LeadOtpVerifyBody) -> Dict[str, Any]:
        db = get_db()
        if db is None:
            raise HTTPException(status_code=503, detail="Database not initialized")

        canonical = canonical_indian_phone(body.phone)
        if not canonical:
            raise HTTPException(status_code=400, detail="Invalid phone number.")
        variants = otp_phone_variants(canonical)
        doc = await db[COL_LEAD_OTP].find_one({"phone": {"$in": variants}})
        if not doc:
            raise HTTPException(status_code=400, detail="No OTP request for this number.")
        if doc.get("expires_at") and datetime.utcnow() > doc["expires_at"]:
            raise HTTPException(status_code=400, detail="OTP expired. Please resend.")
        if not _pwd.verify(body.otp, doc["code_hash"]):
            raise HTTPException(status_code=400, detail="Invalid OTP.")

        await db[COL_LEAD_OTP].delete_one({"_id": doc["_id"]})
        token = _issue_lead_popup_token(canonical)
        return {
            "verified": True,
            "verification_token": token,
            "expires_in": _lead_otp_ttl_seconds(),
        }
