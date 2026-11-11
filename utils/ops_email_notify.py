"""
Immediate ops-email dispatcher for W1–W45 catalog.

- Email only (does not touch SMS / WhatsApp).
- Never raises — live flows must not break if mail fails.
- Skips codes marked legacy_email in the catalog (dedicated senders already cover those).
"""

from __future__ import annotations

import html
import logging
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

from utils.ops_email_catalog import get_ops_email_template

logger = logging.getLogger(__name__)

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

DEFAULT_ACADEMY = "Rock Martial Arts Academy"


def _fill(template: str, context: Dict[str, Any], *, escape: bool = False) -> str:
    def repl(m: re.Match) -> str:
        key = m.group(1)
        val = context.get(key, "")
        if val is None:
            val = ""
        text = str(val)
        return html.escape(text) if escape else text

    return _VAR_RE.sub(repl, template or "")


def _plain_to_html(
    plain: str,
    *,
    button_label: str = "",
    button_url: str = "",
    eyebrow: str = "",
    accent: str = "",
) -> str:
    from utils.email_layout import (
        ACCENT_GOLD,
        ACCENT_RED,
        plain_body_to_content_html,
        wrap_rock_email_html,
    )

    body = plain_body_to_content_html(plain)
    return wrap_rock_email_html(
        content_html=f'<div style="margin:0;">{body}</div>',
        eyebrow=eyebrow or "Notification",
        cta_label=button_label or "",
        cta_url=button_url or "",
        accent=accent or ACCENT_RED,
        preheader=(plain or "").split("\n")[0][:120],
    )


def _normalize_emails(emails: Optional[Sequence[str] | str]) -> List[str]:
    if emails is None:
        return []
    if isinstance(emails, str):
        parts = re.split(r"[,;\s]+", emails)
    else:
        parts = []
        for item in emails:
            if not item:
                continue
            parts.extend(re.split(r"[,;\s]+", str(item)))
    out: List[str] = []
    seen = set()
    for p in parts:
        e = (p or "").strip().lower()
        if not e or "@" not in e or e in seen:
            continue
        seen.add(e)
        out.append(e)
    return out


def get_ops_staff_alert_emails() -> List[str]:
    """Staff alert recipients from env (comma-separated). Never guesses production secrets."""
    raw = (
        (os.getenv("OPS_ALERT_EMAIL") or "").strip()
        or (os.getenv("NOTIFICATION_ALERT_EMAIL") or "").strip()
        or (os.getenv("SUPPORT_EMAIL") or "").strip()
        or (os.getenv("ADMIN_ALERT_EMAIL") or "").strip()
    )
    return _normalize_emails(raw)


async def resolve_superadmin_emails(db) -> List[str]:
    """Best-effort SA emails from DB + env. Failures return []."""
    emails: List[str] = []
    emails.extend(get_ops_staff_alert_emails())
    try:
        if db is not None:
            async for doc in db.superadmins.find({}, {"email": 1}).limit(20):
                e = (doc or {}).get("email")
                if e:
                    emails.append(str(e))
            async for doc in db.users.find(
                {"role": {"$in": ["super_admin", "superadmin", "Super Admin"]}},
                {"email": 1},
            ).limit(20):
                e = (doc or {}).get("email")
                if e:
                    emails.append(str(e))
    except Exception:
        logger.exception("resolve_superadmin_emails failed")
    return _normalize_emails(emails)


async def resolve_branch_manager_email(db, branch_id: Optional[str]) -> Optional[str]:
    if not db or not branch_id:
        return None
    try:
        from bson import ObjectId

        bid = str(branch_id).strip()
        branch = None
        if ObjectId.is_valid(bid):
            branch = await db.branches.find_one({"_id": ObjectId(bid)})
        if not branch:
            branch = await db.branches.find_one({"id": bid}) or await db.branches.find_one({"_id": bid})
        if not branch:
            return None
        manager_id = branch.get("manager_id") or branch.get("branch_manager_id")
        mgr = None
        if manager_id:
            mid = str(manager_id)
            if ObjectId.is_valid(mid):
                mgr = await db.branch_managers.find_one({"_id": ObjectId(mid)})
            if not mgr:
                mgr = await db.branch_managers.find_one({"id": mid}) or await db.branch_managers.find_one({"_id": mid})
        if not mgr:
            # Some schemas store manager email on the branch
            direct = (branch.get("manager_email") or branch.get("email") or "").strip()
            return direct or None
        contact = mgr.get("contact_info") if isinstance(mgr.get("contact_info"), dict) else {}
        email = (mgr.get("email") or contact.get("email") or "").strip()
        return email or None
    except Exception:
        logger.exception("resolve_branch_manager_email failed for branch=%s", branch_id)
        return None


async def resolve_partner_admin_email(db, partner_org_id: Optional[str] = None, partner_doc: Optional[dict] = None) -> Optional[str]:
    try:
        org = partner_doc
        if org is None and db is not None and partner_org_id:
            from bson import ObjectId

            pid = str(partner_org_id).strip()
            if ObjectId.is_valid(pid):
                org = await db.partner_organizations.find_one({"_id": ObjectId(pid)})
            if not org:
                org = await db.partner_organizations.find_one({"id": pid})
        if not org:
            return None
        email = (org.get("email") or "").strip()
        if email:
            return email
        admin_user_id = org.get("admin_user_id")
        if admin_user_id and db is not None:
            from bson import ObjectId

            uid = str(admin_user_id)
            user = None
            if ObjectId.is_valid(uid):
                user = await db.users.find_one({"_id": ObjectId(uid)})
            if not user:
                user = await db.users.find_one({"id": uid})
            if user and user.get("email"):
                return str(user["email"]).strip()
        return None
    except Exception:
        logger.exception("resolve_partner_admin_email failed")
        return None


def _default_context(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    ctx = dict(context or {})
    ctx.setdefault("academy_name", DEFAULT_ACADEMY)
    # Keep missing vars as empty string rather than leaving {{var}}
    return ctx


async def notify_ops_email(
    code: str,
    to_email: Optional[str | Sequence[str]],
    context: Optional[Dict[str, Any]] = None,
    *,
    subject_override: Optional[str] = None,
    force: bool = False,
) -> bool:
    """
    Send one ops catalog email immediately.
    Returns True if at least one recipient was accepted by the mailer.
    Never raises.
    """
    try:
        tmpl = get_ops_email_template(code)
        if not tmpl:
            logger.warning("ops email unknown code=%s", code)
            return False
        if tmpl.get("legacy_email") and not force:
            logger.info("ops email skip legacy code=%s (dedicated sender owns this)", code)
            return False

        recipients = _normalize_emails(to_email)
        if not recipients:
            logger.info("ops email skip code=%s: no recipients", code)
            return False

        ctx = _default_context(context)
        plain = _fill(tmpl.get("body") or "", ctx, escape=False)
        button_url = _fill(tmpl.get("button_url") or "", ctx, escape=False).strip()
        button_label = (tmpl.get("button_label") or "").strip()
        subject = (subject_override or tmpl.get("title") or "Rock Martial Arts").strip()
        html_message = _plain_to_html(
            plain,
            button_label=button_label,
            button_url=button_url,
            eyebrow=(tmpl.get("title") or "Notification")[:60],
        )

        from utils.resend_email import send_html_email_via_resend
        from utils.email_service import send_custom_email_webhook

        any_ok = False
        for recipient in recipients:
            try:
                ok = await send_html_email_via_resend(
                    recipient,
                    subject,
                    html_message,
                    plain_message=plain,
                )
                if not ok:
                    # Webhook fallback (same pattern as partner invite / credentials)
                    ok = await send_custom_email_webhook(
                        recipient, subject, html_message, plain
                    )
                if ok:
                    any_ok = True
                    logger.info("ops email sent code=%s to=%s", code, recipient)
                else:
                    logger.error("ops email failed code=%s to=%s", code, recipient)
            except Exception:
                logger.exception("ops email exception code=%s to=%s", code, recipient)
        return any_ok
    except Exception:
        logger.exception("ops email top-level failure code=%s", code)
        return False


async def notify_ops_email_many(
    code: str,
    recipients: Iterable[Optional[str]],
    context: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> bool:
    return await notify_ops_email(code, list(recipients), context, **kwargs)


async def notify_staff_ops_email(
    code: str,
    context: Optional[Dict[str, Any]] = None,
    *,
    db=None,
    extra_emails: Optional[Sequence[str]] = None,
) -> bool:
    """Staff-facing alert: env list + optional SA emails + extras."""
    try:
        emails = list(get_ops_staff_alert_emails())
        if db is not None:
            emails.extend(await resolve_superadmin_emails(db))
        if extra_emails:
            emails.extend([e for e in extra_emails if e])
        return await notify_ops_email(code, emails, context)
    except Exception:
        logger.exception("notify_staff_ops_email failed code=%s", code)
        return False
