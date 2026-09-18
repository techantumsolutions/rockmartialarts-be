"""
M07-S05 Invoice WhatsApp delivery.

Auto-send after new invoice insert, authorized resend, and delivery tracking.
Uses existing send_whatsapp (mock until Zaptra/Meta credentials are configured).
Never raises into payment / invoice generation flows when wrapped with safe_*.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from utils.database import get_db
from utils.helpers import send_whatsapp, serialize_doc

logger = logging.getLogger(__name__)

Trigger = Literal["auto", "resend", "manual"]

DEFAULT_TEMPLATE_NAME = "invoice_payment_receipt"


def invoice_whatsapp_auto_send_enabled() -> bool:
    raw = (os.getenv("INVOICE_WHATSAPP_AUTO_SEND") or "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def invoice_whatsapp_template_name() -> str:
    return (
        (os.getenv("WHATSAPP_INVOICE_TEMPLATE_NAME") or "").strip()
        or DEFAULT_TEMPLATE_NAME
    )


def _frontend_base() -> str:
    return (
        (os.getenv("FRONTEND_URL") or os.getenv("NEXT_PUBLIC_APP_URL") or "").strip().rstrip("/")
        or "http://localhost:3000"
    )


def mask_phone(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    if len(digits) < 4:
        return "****"
    return f"****{digits[-4:]}"


def _fmt_amount(amount: Any, currency: str = "INR") -> str:
    try:
        val = float(amount or 0)
    except (TypeError, ValueError):
        val = 0.0
    cur = (currency or "INR").upper()
    if cur == "INR":
        return f"₹{val:,.0f}" if val == int(val) else f"₹{val:,.2f}"
    return f"{cur} {val:,.2f}"


def _fmt_date(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y")
    s = str(value)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y")
    except Exception:
        return s[:10]


def build_invoice_whatsapp_context(invoice_doc: Dict[str, Any]) -> Dict[str, str]:
    customer = invoice_doc.get("customer") or {}
    lines = invoice_doc.get("line_items") or []
    first = lines[0] if lines and isinstance(lines[0], dict) else {}
    course_summary = first.get("course_name") or first.get("description") or "Course"
    if len(lines) > 1:
        course_summary = f"{course_summary} (+{len(lines) - 1} more)"
    branch_name = ""
    for li in lines:
        if isinstance(li, dict) and li.get("branch_name"):
            branch_name = str(li.get("branch_name"))
            break
    invoice_id = str(invoice_doc.get("id") or "")
    return {
        "customer_name": str(customer.get("name") or "Student"),
        "invoice_number": str(invoice_doc.get("invoice_number") or ""),
        "amount": _fmt_amount(invoice_doc.get("total_amount"), invoice_doc.get("currency") or "INR"),
        "paid_date": _fmt_date(invoice_doc.get("paid_at") or invoice_doc.get("created_at")),
        "payment_ref": str(invoice_doc.get("payment_reference") or "")[:24],
        "course_summary": str(course_summary),
        "branch_name": branch_name or "Rock Martial Arts",
        "invoice_link": f"{_frontend_base()}/student-dashboard/invoices/{invoice_id}",
    }


def render_invoice_whatsapp_message(ctx: Dict[str, str], template_body: Optional[str] = None) -> str:
    body = template_body or (
        "Hi {{customer_name}}, your payment is confirmed.\n"
        "Invoice {{invoice_number}} for {{amount}} was issued on {{paid_date}}.\n"
        "Course: {{course_summary}} · {{branch_name}}\n"
        "Ref: {{payment_ref}}\n"
        "View invoice: {{invoice_link}}\n"
        "— Rock Martial Arts"
    )
    out = body
    for key, val in ctx.items():
        out = out.replace("{{" + key + "}}", str(val or ""))
    return out


def resolve_invoice_phone(invoice_doc: Dict[str, Any], phone_override: Optional[str] = None) -> Optional[str]:
    if phone_override and str(phone_override).strip():
        return str(phone_override).strip()
    customer = invoice_doc.get("customer") or {}
    phone = customer.get("phone") or customer.get("mobile")
    if phone and str(phone).strip():
        return str(phone).strip()
    return None


async def _load_template_body(db, template_name: str) -> Optional[str]:
    try:
        row = await db.notification_templates.find_one(
            {"name": template_name, "is_active": {"$ne": False}}
        )
        if not row:
            row = await db.notification_templates.find_one({"name": template_name})
        if row and row.get("body"):
            return str(row["body"])
    except Exception:
        logger.exception("Failed loading notification template %s", template_name)
    return None


async def ensure_invoice_whatsapp_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database.invoice_whatsapp_deliveries.create_index("id", unique=True)
        await database.invoice_whatsapp_deliveries.create_index([("invoice_id", 1), ("created_at", -1)])
        await database.invoice_whatsapp_deliveries.create_index("payment_id")
        await database.invoice_whatsapp_deliveries.create_index("provider_message_id")
    except Exception:
        logger.exception("Failed ensuring invoice WhatsApp delivery indexes")

    # Seed default template once (additive; does not overwrite custom body)
    try:
        name = invoice_whatsapp_template_name()
        existing = await database.notification_templates.find_one({"name": name})
        if not existing:
            await database.notification_templates.insert_one(
                {
                    "id": str(uuid.uuid4()),
                    "name": name,
                    "type": "whatsapp",
                    "channel": "whatsapp",
                    "provider_template_name": os.getenv("WHATSAPP_INVOICE_PROVIDER_TEMPLATE")
                    or "rock_invoice_paid_v1",
                    "body": (
                        "Hi {{customer_name}}, your payment is confirmed.\n"
                        "Invoice {{invoice_number}} for {{amount}} was issued on {{paid_date}}.\n"
                        "Course: {{course_summary}} · {{branch_name}}\n"
                        "Ref: {{payment_ref}}\n"
                        "View invoice: {{invoice_link}}\n"
                        "— Rock Martial Arts"
                    ),
                    "is_active": True,
                    "created_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                }
            )
    except Exception:
        logger.exception("Failed seeding invoice WhatsApp template")


async def _next_attempt(db, invoice_id: str) -> int:
    try:
        n = await db.invoice_whatsapp_deliveries.count_documents({"invoice_id": invoice_id})
        return int(n) + 1
    except Exception:
        return 1


async def _latest_delivery(db, invoice_id: str) -> Optional[Dict[str, Any]]:
    try:
        row = await db.invoice_whatsapp_deliveries.find_one(
            {"invoice_id": invoice_id},
            sort=[("created_at", -1)],
        )
        if row:
            row.pop("_id", None)
            return serialize_doc(row)
    except Exception:
        logger.exception("Failed reading latest WhatsApp delivery for %s", invoice_id)
    return None


async def list_invoice_whatsapp_deliveries(invoice_id: str, *, limit: int = 20) -> List[Dict[str, Any]]:
    db = get_db()
    if db is None:
        return []
    await ensure_invoice_whatsapp_indexes(db)
    cursor = (
        db.invoice_whatsapp_deliveries.find({"invoice_id": invoice_id})
        .sort("created_at", -1)
        .limit(max(1, min(limit, 100)))
    )
    rows = await cursor.to_list(length=limit)
    out = []
    for r in rows:
        r.pop("_id", None)
        out.append(serialize_doc(r))
    return out


async def send_invoice_whatsapp(
    invoice_doc: Dict[str, Any],
    *,
    trigger: Trigger = "auto",
    phone_override: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Send invoice WhatsApp and persist delivery status.
    Returns a delivery record dict (never raises for provider mock failures — records failed).
    """
    db = get_db()
    if db is None:
        raise RuntimeError("Database connection not available")

    await ensure_invoice_whatsapp_indexes(db)

    invoice_id = str(invoice_doc.get("id") or "")
    if not invoice_id:
        raise ValueError("Invoice id required")

    if trigger == "auto" and not force and not invoice_whatsapp_auto_send_enabled():
        return {
            "id": None,
            "invoice_id": invoice_id,
            "status": "skipped",
            "skip_reason": "auto_send_disabled",
            "trigger": trigger,
        }

    # Idempotent auto: do not re-send if already sent/delivered
    if trigger == "auto" and not force:
        latest = await _latest_delivery(db, invoice_id)
        if latest and str(latest.get("status") or "") in {"sent", "delivered"}:
            return {**latest, "duplicate": True}

    phone = resolve_invoice_phone(invoice_doc, phone_override)
    now = datetime.utcnow()
    attempt = await _next_attempt(db, invoice_id)
    template_name = invoice_whatsapp_template_name()
    ctx = build_invoice_whatsapp_context(invoice_doc)
    template_body = await _load_template_body(db, template_name)
    message = render_invoice_whatsapp_message(ctx, template_body)

    delivery: Dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "invoice_id": invoice_id,
        "invoice_number": invoice_doc.get("invoice_number"),
        "payment_id": invoice_doc.get("payment_id"),
        "student_id": invoice_doc.get("student_id") or invoice_doc.get("account_user_id"),
        "channel": "whatsapp",
        "template_name": template_name,
        "provider_template_name": os.getenv("WHATSAPP_INVOICE_PROVIDER_TEMPLATE")
        or "rock_invoice_paid_v1",
        "trigger": trigger,
        "attempt": attempt,
        "phone": phone,
        "phone_masked": mask_phone(phone),
        "message_preview": (message[:240] + "…") if len(message) > 240 else message,
        "variables": ctx,
        "status": "queued",
        "provider_message_id": None,
        "error": None,
        "actor_user_id": actor_user_id,
        "sent_at": None,
        "delivered_at": None,
        "created_at": now,
        "updated_at": now,
    }

    if not phone:
        delivery["status"] = "skipped"
        delivery["skip_reason"] = "no_phone"
        delivery["error"] = "No registered mobile number on invoice"
        await db.invoice_whatsapp_deliveries.insert_one(dict(delivery))
        await _denormalize_invoice(db, invoice_id, delivery)
        delivery.pop("_id", None)
        return serialize_doc(delivery)

    try:
        ok = await send_whatsapp(phone, message)
        if ok:
            delivery["status"] = "sent"
            delivery["sent_at"] = datetime.utcnow()
            # Mock / sync providers: treat sent as delivered unless webhook updates later
            provider = (os.getenv("WHATSAPP_PROVIDER") or "mock").strip().lower()
            if provider in {"", "mock"}:
                delivery["status"] = "delivered"
                delivery["delivered_at"] = delivery["sent_at"]
            delivery["provider_message_id"] = f"local-{delivery['id'][:8]}"
        else:
            delivery["status"] = "failed"
            delivery["error"] = "Provider returned failure"
    except Exception as exc:
        logger.exception("WhatsApp send failed invoice_id=%s", invoice_id)
        delivery["status"] = "failed"
        delivery["error"] = str(exc)[:500]

    delivery["updated_at"] = datetime.utcnow()
    await db.invoice_whatsapp_deliveries.insert_one(dict(delivery))
    await _denormalize_invoice(db, invoice_id, delivery)

    # Also write a notification_logs row for ops (additive)
    try:
        await db.notification_logs.insert_one(
            {
                "id": str(uuid.uuid4()),
                "type": "whatsapp",
                "channel": "whatsapp",
                "template_name": template_name,
                "recipient": phone,
                "status": "sent" if delivery["status"] in {"sent", "delivered"} else delivery["status"],
                "invoice_id": invoice_id,
                "payment_id": invoice_doc.get("payment_id"),
                "delivery_id": delivery["id"],
                "provider_message_id": delivery.get("provider_message_id"),
                "error": delivery.get("error"),
                "message": message,
                "created_at": datetime.utcnow(),
            }
        )
    except Exception:
        logger.exception("Failed writing notification_logs for invoice WhatsApp")

    delivery.pop("_id", None)
    return serialize_doc(delivery)


async def _denormalize_invoice(db, invoice_id: str, delivery: Dict[str, Any]) -> None:
    try:
        await db.invoices.update_one(
            {"id": invoice_id},
            {
                "$set": {
                    "whatsapp_delivery_status": delivery.get("status"),
                    "whatsapp_delivery_id": delivery.get("id"),
                    "whatsapp_last_sent_at": delivery.get("sent_at") or delivery.get("created_at"),
                    "whatsapp_phone_masked": delivery.get("phone_masked"),
                    "updated_at": datetime.utcnow(),
                }
            },
        )
    except Exception:
        logger.exception("Failed denormalizing WhatsApp status on invoice %s", invoice_id)


async def safe_send_invoice_whatsapp_for_invoice(
    invoice_doc: Dict[str, Any],
    *,
    trigger: Trigger = "auto",
) -> Optional[Dict[str, Any]]:
    """Never raise into invoice/payment flows."""
    try:
        return await send_invoice_whatsapp(invoice_doc, trigger=trigger)
    except Exception:
        logger.exception(
            "Invoice WhatsApp auto-send failed invoice_id=%s",
            (invoice_doc or {}).get("id"),
        )
        return None


def public_whatsapp_delivery_summary(delivery: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not delivery:
        return None
    return {
        "id": delivery.get("id"),
        "status": delivery.get("status"),
        "trigger": delivery.get("trigger"),
        "attempt": delivery.get("attempt"),
        "phone_masked": delivery.get("phone_masked"),
        "template_name": delivery.get("template_name"),
        "error": delivery.get("error"),
        "skip_reason": delivery.get("skip_reason"),
        "sent_at": delivery.get("sent_at"),
        "delivered_at": delivery.get("delivered_at"),
        "created_at": delivery.get("created_at"),
        "provider_message_id": delivery.get("provider_message_id"),
    }
