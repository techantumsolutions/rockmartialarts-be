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
    LeadFollowUpCreate,
    LeadOtpVerifyBody,
    LeadResponse,
    LeadStatus,
    LeadStatusUpdate,
)
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.indian_phone import canonical_indian_phone, otp_phone_variants
from utils.lead_service import (
    create_lead_follow_up,
    create_lead_from_payload,
    get_lead,
    lead_pipeline_summary,
    lead_to_response_dict,
    list_lead_follow_ups,
    list_lead_source_options,
    list_leads_service,
    update_lead_status_with_history,
)
from utils.student_status_service import get_managed_branch_ids_for_user
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


def _lead_response_from_ser(ser: Dict[str, Any], fallback_now: Optional[datetime] = None) -> LeadResponse:
    data = lead_to_response_dict(ser, fallback_now)
    return LeadResponse(**data)


class LeadController:
    @staticmethod
    async def create_lead(data: LeadCreate) -> LeadResponse:
        try:
            payload = await create_lead_from_payload(data)
            return LeadResponse(**payload)
        except HTTPException:
            raise
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
        source: Optional[str] = None,
        source_type: Optional[str] = None,
        branch_id: Optional[str] = None,
        follow_up_due: Optional[str] = None,
        sort: Optional[str] = None,
        coach_assignment_status: Optional[str] = None,
        unassigned_coach: bool = False,
        current_user: Optional[dict] = None,
    ) -> Dict[str, Any]:
        try:
            data = await list_leads_service(
                skip=skip,
                limit=limit,
                search=search,
                status=status,
                source=source,
                source_type=source_type,
                branch_id=branch_id,
                follow_up_due=follow_up_due,
                sort=sort,
                coach_assignment_status=coach_assignment_status,
                unassigned_coach=unassigned_coach,
                current_user=current_user,
            )
            # Keep response shape: leads as LeadResponse-compatible dicts
            return data
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to list leads: {str(e)}",
            )

    @staticmethod
    async def list_sources(current_user: Optional[dict] = None) -> Dict[str, Any]:
        return await list_lead_source_options(current_user=current_user)

    @staticmethod
    async def pipeline_summary(current_user: Optional[dict] = None) -> Dict[str, Any]:
        return await lead_pipeline_summary(current_user=current_user)

    @staticmethod
    async def get_lead_detail(
        lead_id: str, current_user: Optional[dict] = None
    ) -> Dict[str, Any]:
        return await get_lead(lead_id, current_user=current_user)

    @staticmethod
    async def list_follow_ups(
        lead_id: str,
        skip: int = 0,
        limit: int = 50,
        current_user: Optional[dict] = None,
    ) -> Dict[str, Any]:
        return await list_lead_follow_ups(
            lead_id, current_user=current_user, skip=skip, limit=limit
        )

    @staticmethod
    async def create_follow_up(
        lead_id: str,
        body: LeadFollowUpCreate,
        current_user: Optional[dict] = None,
    ) -> Dict[str, Any]:
        return await create_lead_follow_up(
            lead_id,
            note=body.note,
            action=body.action,
            status=body.status,
            next_follow_up_at=body.next_follow_up_at,
            clear_next_follow_up=body.clear_next_follow_up,
            current_user=current_user,
        )

    @staticmethod
    async def update_status(lead_id: str, body: LeadStatusUpdate, current_user: Optional[dict] = None) -> LeadResponse:
        try:
            data = await update_lead_status_with_history(
                lead_id,
                status=body.status,
                note=body.note,
                current_user=current_user,
            )
            return LeadResponse(**data)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update lead: {str(e)}",
            )

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
