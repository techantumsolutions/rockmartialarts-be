"""
WhatsApp delivery for ops templates (W1–W45).

Providers (WHATSAPP_PROVIDER):
  - meta | cloud | graph  → Meta WhatsApp Cloud API
  - webhook | http        → POST JSON to WHATSAPP_API_URL
  - mock | stub | log | (empty) → stub / refuse per helpers policy

Env (Meta):
  WHATSAPP_TOKEN / WHATSAPP_ACCESS_TOKEN
  WHATSAPP_PHONE_NUMBER_ID
  WHATSAPP_API_VERSION (default v19.0)
  WHATSAPP_SEND_MODE = text | template  (default text — rendered body)

Env (webhook):
  WHATSAPP_API_URL
  WHATSAPP_API_KEY (optional Bearer)
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)


def _digits(phone: str) -> str:
    return "".join(c for c in (phone or "") if c.isdigit())


def format_whatsapp_to(phone: str) -> str:
    """E.164 without +: 91XXXXXXXXXX for India when possible."""
    d = _digits(phone)
    if not d:
        return ""
    if len(d) == 10:
        return f"91{d}"
    if len(d) == 12 and d.startswith("91"):
        return d
    if phone.strip().startswith("+"):
        return d
    return d


def whatsapp_provider_name() -> str:
    return (os.getenv("WHATSAPP_PROVIDER") or "").strip().lower()


def whatsapp_is_configured() -> bool:
    prov = whatsapp_provider_name()
    if prov in ("meta", "cloud", "graph", "facebook"):
        token = (os.getenv("WHATSAPP_TOKEN") or os.getenv("WHATSAPP_ACCESS_TOKEN") or "").strip()
        phone_id = (os.getenv("WHATSAPP_PHONE_NUMBER_ID") or "").strip()
        return bool(token and phone_id)
    if prov in ("webhook", "http", "json"):
        return bool((os.getenv("WHATSAPP_API_URL") or "").strip())
    return False


async def _send_meta_text(to: str, body: str) -> Tuple[bool, Optional[str]]:
    token = (os.getenv("WHATSAPP_TOKEN") or os.getenv("WHATSAPP_ACCESS_TOKEN") or "").strip()
    phone_id = (os.getenv("WHATSAPP_PHONE_NUMBER_ID") or "").strip()
    version = (os.getenv("WHATSAPP_API_VERSION") or "v19.0").strip()
    if not token or not phone_id:
        return False, "Meta WhatsApp credentials missing"
    url = f"https://graph.facebook.com/{version}/{phone_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": body[:4096]},
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if 200 <= resp.status_code < 300:
                return True, None
            return False, f"HTTP {resp.status_code}: {resp.text[:240]}"
    except Exception as exc:
        logger.exception("Meta WhatsApp text send failed")
        return False, str(exc)[:200]


async def _send_meta_template(
    to: str,
    template_name: str,
    body_params: List[str],
    *,
    language: str = "en",
) -> Tuple[bool, Optional[str]]:
    token = (os.getenv("WHATSAPP_TOKEN") or os.getenv("WHATSAPP_ACCESS_TOKEN") or "").strip()
    phone_id = (os.getenv("WHATSAPP_PHONE_NUMBER_ID") or "").strip()
    version = (os.getenv("WHATSAPP_API_VERSION") or "v19.0").strip()
    if not token or not phone_id:
        return False, "Meta WhatsApp credentials missing"
    url = f"https://graph.facebook.com/{version}/{phone_id}/messages"
    components: List[Dict[str, Any]] = []
    if body_params:
        components.append(
            {
                "type": "body",
                "parameters": [{"type": "text", "text": str(p)[:1024]} for p in body_params],
            }
        )
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language or "en"},
            "components": components,
        },
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if 200 <= resp.status_code < 300:
                return True, None
            return False, f"HTTP {resp.status_code}: {resp.text[:240]}"
    except Exception as exc:
        logger.exception("Meta WhatsApp template send failed")
        return False, str(exc)[:200]


async def _send_webhook(to: str, body: str, *, template_name: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    api_url = (os.getenv("WHATSAPP_API_URL") or "").strip()
    if not api_url:
        return False, "WHATSAPP_API_URL missing"
    api_key = (os.getenv("WHATSAPP_API_KEY") or "").strip()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "to": to,
        "phone": to,
        "message": body,
        "text": body,
        "template_name": template_name,
    }
    try:
        async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
            resp = await client.post(api_url, headers=headers, json=payload)
            if 200 <= resp.status_code < 300:
                return True, None
            return False, f"HTTP {resp.status_code}: {resp.text[:240]}"
    except Exception as exc:
        logger.exception("WhatsApp webhook send failed")
        return False, str(exc)[:200]


_VAR_ORDER_CACHE: Dict[str, List[str]] = {}


def extract_template_var_order(body: str) -> List[str]:
    """Preserve first-seen order of {{var}} placeholders for Meta template params."""
    seen = []
    for m in re.finditer(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", body or ""):
        key = m.group(1)
        if key not in seen:
            seen.append(key)
    return seen


async def send_whatsapp_message(
    phone: str,
    message: str,
    *,
    template_name: Optional[str] = None,
    template_body: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Send WhatsApp. Returns (ok, error).
    Prefer Meta template mode when WHATSAPP_SEND_MODE=template and template_name set;
    otherwise send rendered text body.
    """
    to = format_whatsapp_to(phone)
    if not to:
        return False, "Invalid phone"
    body = (message or "").strip()
    if not body:
        return False, "Empty message"

    prov = whatsapp_provider_name()
    mode = (os.getenv("WHATSAPP_SEND_MODE") or "text").strip().lower()

    if prov in ("meta", "cloud", "graph", "facebook"):
        if mode == "template" and template_name:
            order = extract_template_var_order(template_body or "")
            ctx = context or {}
            params = [str(ctx.get(k) or "") for k in order]
            lang = (os.getenv("WHATSAPP_TEMPLATE_LANG") or "en").strip() or "en"
            ok, err = await _send_meta_template(to, template_name, params, language=lang)
            if ok:
                return True, None
            # Fall back to text if template send fails (e.g. not approved yet)
            logger.warning(
                "WhatsApp template send failed name=%s err=%s — falling back to text",
                template_name,
                err,
            )
        return await _send_meta_text(to, body)

    if prov in ("webhook", "http", "json"):
        return await _send_webhook(to, body, template_name=template_name)

    return False, f"WhatsApp provider not configured ({prov or 'unset'})"
