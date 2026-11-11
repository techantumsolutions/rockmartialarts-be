"""
Multi-channel ops notifications for W1–W45 catalog.

Sends the same approved template bodies over:
  - Email (Resend — existing notify_ops_email)
  - WhatsApp (when phone provided + provider configured)
  - SMS (only for high-priority customer codes — short critical alerts)

Never raises into live flows.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Sequence, Set

from utils.ops_email_catalog import get_ops_email_template
from utils.ops_email_notify import (
    DEFAULT_ACADEMY,
    _default_context,
    _fill,
    notify_ops_email,
    notify_staff_ops_email,
)

logger = logging.getLogger(__name__)

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

# Customer-facing codes that should also go out as SMS (short, critical).
SMS_PRIORITY_CODES: Set[str] = {
    "rock_payment_success",
    "rock_payment_failed",
    "rock_payment_reminder",
    "rock_renewal_reminder",
    "rock_enrollment_expired",
    "rock_student_password_setup",
    "rock_coach_password_setup",
    "rock_student_welcome_admin",
    "rock_bm_welcome",
    "rock_partner_invite",
}

# Staff / ops alert codes — email primary; WhatsApp when phone known; no SMS.
STAFF_CODES: Set[str] = {
    "rock_lead_staff_alert",
    "rock_demo_staff_alert",
    "rock_training_request_staff",
    "rock_callback_staff_alert",
    "rock_camp_staff_alert",
    "rock_collab_partner_lead_staff",
    "rock_sa_new_registration",
    "rock_stock_low_alert",
    "rock_bm_lead_alert",
    "rock_bm_new_registration",
}


def render_ops_body(code: str, context: Optional[Dict[str, Any]] = None) -> Optional[str]:
    tmpl = get_ops_email_template(code)
    if not tmpl:
        return None
    ctx = _default_context(context)
    return _fill(tmpl.get("body") or "", ctx, escape=False).strip()


def _append_cta(plain: str, code: str, context: Optional[Dict[str, Any]]) -> str:
    tmpl = get_ops_email_template(code) or {}
    label = (tmpl.get("button_label") or "").strip()
    url = _fill(tmpl.get("button_url") or "", _default_context(context), escape=False).strip()
    if not label or not url:
        return plain
    if url in plain:
        return plain
    return f"{plain}\n\n{label}: {url}"


async def notify_ops_whatsapp(
    code: str,
    phone: Optional[str],
    context: Optional[Dict[str, Any]] = None,
) -> bool:
    try:
        if not phone or not str(phone).strip():
            return False
        tmpl = get_ops_email_template(code)
        if not tmpl:
            return False
        plain = render_ops_body(code, context)
        if not plain:
            return False
        message = _append_cta(plain, code, context)
        from utils.whatsapp_sender import send_whatsapp_message, whatsapp_is_configured
        from utils.helpers import send_whatsapp

        if whatsapp_is_configured():
            ok, err = await send_whatsapp_message(
                str(phone),
                message,
                template_name=code,
                template_body=tmpl.get("body") or "",
                context=_default_context(context),
            )
            if ok:
                logger.info("ops whatsapp sent code=%s phone=****%s", code, str(phone)[-4:])
                return True
            logger.warning("ops whatsapp failed code=%s err=%s", code, err)
            return False

        # Fall through to helpers (stub / refuse policy)
        return bool(await send_whatsapp(str(phone), message))
    except Exception:
        logger.exception("ops whatsapp failure code=%s", code)
        return False


async def notify_ops_sms(
    code: str,
    phone: Optional[str],
    context: Optional[Dict[str, Any]] = None,
) -> bool:
    """SMS only for SMS_PRIORITY_CODES — keep messages short."""
    try:
        if code not in SMS_PRIORITY_CODES:
            return False
        if not phone or not str(phone).strip():
            return False
        plain = render_ops_body(code, context)
        if not plain:
            return False
        # SMS length soft-cap
        msg = plain
        if len(msg) > 600:
            msg = msg[:597] + "..."
        from utils.helpers import send_sms

        ok = await send_sms(str(phone), msg)
        if ok:
            logger.info("ops sms sent code=%s phone=****%s", code, str(phone)[-4:])
        return bool(ok)
    except Exception:
        logger.exception("ops sms failure code=%s", code)
        return False


async def notify_ops(
    code: str,
    *,
    email: Optional[str | Sequence[str]] = None,
    phone: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    channels: Optional[Sequence[str]] = None,
    force_email: bool = False,
) -> Dict[str, bool]:
    """
    Send catalog notification on relevant channels.
    Default channels: email (if email), whatsapp (if phone), sms (if priority + phone).
    """
    result = {"email": False, "whatsapp": False, "sms": False}
    try:
        wanted = {c.lower() for c in (channels or ["email", "whatsapp", "sms"])}
        ctx = _default_context(context)

        if "email" in wanted and email:
            result["email"] = await notify_ops_email(
                code, email, ctx, force=force_email
            )

        if "whatsapp" in wanted and phone:
            result["whatsapp"] = await notify_ops_whatsapp(code, phone, ctx)

        if "sms" in wanted and phone:
            result["sms"] = await notify_ops_sms(code, phone, ctx)

        return result
    except Exception:
        logger.exception("notify_ops top-level failure code=%s", code)
        return result


async def notify_staff_ops(
    code: str,
    context: Optional[Dict[str, Any]] = None,
    *,
    db=None,
    extra_emails: Optional[Sequence[str]] = None,
    staff_phones: Optional[Sequence[str]] = None,
) -> Dict[str, bool]:
    """Staff alert: email to SA/env list; optional WhatsApp to staff phones."""
    result = {"email": False, "whatsapp": False, "sms": False}
    try:
        result["email"] = await notify_staff_ops_email(
            code, context, db=db, extra_emails=extra_emails
        )
        for phone in staff_phones or []:
            if phone:
                ok = await notify_ops_whatsapp(code, phone, context)
                result["whatsapp"] = result["whatsapp"] or ok
        return result
    except Exception:
        logger.exception("notify_staff_ops failed code=%s", code)
        return result


async def notify_ops_mobile(
    code: str,
    phone: Optional[str],
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, bool]:
    """
    WhatsApp (+ SMS for priority codes) companion for legacy Resend emails.
    Does not send email (dedicated HTML mailers own those templates).
    """
    return await notify_ops(
        code,
        phone=phone,
        context=context,
        channels=["whatsapp", "sms"],
    )
