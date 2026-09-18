"""
M18-S02 provider abstraction — SMS + WhatsApp adapters.

Wraps existing reg_checkout_sms + helpers.send_whatsapp without replacing them.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

from utils.reg_checkout_sms import (
    send_registration_sms,
    sms_provider_expects_delivery,
)

logger = logging.getLogger(__name__)

# status, error, provider_message_id
ProviderResult = Tuple[str, Optional[str], Optional[str]]


def _allow_sms_stub() -> bool:
    return os.getenv("NOTIFICATION_ALLOW_SMS_STUB", "").lower() in (
        "1",
        "true",
        "yes",
    ) or os.getenv("REG_CHECKOUT_ALLOW_SMS_STUB", "").lower() in (
        "1",
        "true",
        "yes",
    )


def _whatsapp_provider_name() -> str:
    return (os.getenv("WHATSAPP_PROVIDER") or "mock").strip().lower()


async def deliver_sms(
    phone: str,
    message: str,
    *,
    dlt_template_id: Optional[str] = None,
    dry_run: bool = False,
) -> ProviderResult:
    if dry_run:
        return "skipped", None, None
    ok, err = send_registration_sms(phone, message, template_id=dlt_template_id)
    if ok:
        return "sent", None, None
    if _allow_sms_stub() or not sms_provider_expects_delivery():
        return "stubbed", err or "SMS stub / provider not configured", None
    return "failed", err or "SMS send failed", None


async def deliver_whatsapp(
    phone: str,
    message: str,
    *,
    provider_template_name: Optional[str] = None,
    dry_run: bool = False,
) -> ProviderResult:
    if dry_run:
        return "skipped", None, None
    try:
        from utils.helpers import send_whatsapp

        ok = await send_whatsapp(phone, message)
    except Exception as exc:
        logger.exception("WhatsApp provider error")
        ok = False
        err = str(exc)[:200]
    else:
        err = None if ok else "WhatsApp send returned false"

    provider = _whatsapp_provider_name()
    if ok:
        # Mock provider marks as delivered immediately (existing invoice behavior)
        if provider in ("", "mock", "stub", "log"):
            return "delivered", None, f"mock-{provider_template_name or 'wa'}"
        return "sent", None, None

    if provider in ("", "mock", "stub", "log"):
        # helpers.send_whatsapp always returns True today; keep stub path for future
        return "stubbed", err or "WhatsApp stub", None
    return "failed", err or "WhatsApp send failed", None


async def deliver_email(
    recipient: str,
    subject: str,
    body: str,
    *,
    dry_run: bool = False,
) -> ProviderResult:
    """Email channel stub — logs only until mail provider is wired (S02+)."""
    if dry_run:
        return "skipped", None, None
    logger.info(
        "[notification email stub] to=%s subject=%s body=%s",
        recipient,
        subject[:80],
        body[:120],
    )
    return "stubbed", "Email provider not configured", None


async def deliver_channel(
    channel: str,
    recipient: str,
    message: str,
    *,
    subject: Optional[str] = None,
    dlt_template_id: Optional[str] = None,
    provider_template_name: Optional[str] = None,
    dry_run: bool = False,
) -> ProviderResult:
    ch = (channel or "sms").lower()
    if ch == "sms":
        return await deliver_sms(
            recipient,
            message,
            dlt_template_id=dlt_template_id,
            dry_run=dry_run,
        )
    if ch == "whatsapp":
        return await deliver_whatsapp(
            recipient,
            message,
            provider_template_name=provider_template_name,
            dry_run=dry_run,
        )
    if ch == "email":
        return await deliver_email(
            recipient, subject or "Notification", message, dry_run=dry_run
        )
    return "failed", f"Unsupported channel: {ch}", None


def provider_info() -> Dict[str, Any]:
    return {
        "sms_provider": (os.getenv("SMS_PROVIDER") or "json").strip().lower(),
        "sms_expects_delivery": sms_provider_expects_delivery(),
        "whatsapp_provider": _whatsapp_provider_name(),
        "sms_stub_allowed": _allow_sms_stub(),
    }
