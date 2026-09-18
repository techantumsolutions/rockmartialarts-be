"""
M07-S01 invoice generation service.

Creates one persisted invoice from a successful payment (single enrollment,
registration, or multi-student cart). Idempotent by payment_id / cart_checkout_id.
Failures are logged and never raise into the payment flow when called via
`safe_generate_invoice_for_payment`.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from models.invoice_models import InvoiceLineItem, InvoiceStatus
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.invoice_document import build_invoice_html
from utils.invoice_number import ensure_invoice_counter_indexes, next_invoice_number

logger = logging.getLogger(__name__)


async def ensure_invoice_indexes(db=None) -> None:
    db = db or get_db()
    if db is None:
        return
    try:
        await db.invoices.create_index("id", unique=True)
        await db.invoices.create_index("invoice_number", unique=True)
        await db.invoices.create_index("payment_id", unique=True, sparse=True)
        await db.invoices.create_index("cart_checkout_id", unique=True, sparse=True)
        await db.invoices.create_index([("student_id", 1), ("created_at", -1)])
        await db.invoices.create_index([("account_user_id", 1), ("created_at", -1)])
        await db.invoices.create_index([("branch_id", 1), ("created_at", -1)])
        await ensure_invoice_counter_indexes(db)
    except Exception:
        logger.exception("Failed ensuring invoice indexes")


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return round(float(value), 2)
    except (TypeError, ValueError):
        return default


def _name_from_user(user: Optional[dict]) -> str:
    if not user:
        return "Customer"
    full = (user.get("full_name") or "").strip()
    if full:
        return full
    parts = [user.get("first_name") or "", user.get("last_name") or ""]
    joined = " ".join(p for p in parts if p).strip()
    return joined or (user.get("email") or "Customer")


async def _load_company(db) -> Dict[str, Any]:
    settings = await db.system_settings.find_one({}) or {}
    return {
        "name": settings.get("system_name") or "Rock Martial Arts Academy",
        "email": settings.get("smtp_username") or None,
        "phone": None,
        "address": None,
    }


async def _find_existing(db, *, payment_id: Optional[str], cart_checkout_id: Optional[str]) -> Optional[dict]:
    if payment_id:
        found = await db.invoices.find_one({"payment_id": payment_id})
        if found:
            return found
    if cart_checkout_id:
        found = await db.invoices.find_one({"cart_checkout_id": cart_checkout_id})
        if found:
            return found
    return None


async def _resolve_payment(db, payment_id: Optional[str] = None, payment_doc: Optional[dict] = None) -> Optional[dict]:
    if payment_doc:
        return payment_doc
    if not payment_id:
        return None
    return await db.payments.find_one({"id": payment_id})


async def _build_lines_from_cart(db, checkout: dict, payment: dict) -> List[InvoiceLineItem]:
    links = checkout.get("enrollment_links") or []
    items_snap = checkout.get("items_snapshot") or []
    students_snap = {s.get("id"): s for s in (checkout.get("students_snapshot") or []) if isinstance(s, dict)}
    item_by_id = {i.get("id"): i for i in items_snap if isinstance(i, dict) and i.get("id")}

    discount_total = _f(
        (checkout.get("totals") or {}).get("promo_discount_total")
        or (checkout.get("discount_snapshot") or {}).get("discount_total")
        or 0
    )
    lines: List[InvoiceLineItem] = []
    raw_lines = []

    for link in links:
        item = item_by_id.get(link.get("cart_item_id")) or {}
        pricing = item.get("pricing") or {}
        course_fee = _f(pricing.get("course_fee") or item.get("course_fee") or pricing.get("amount"))
        admission_fee = _f(pricing.get("admission_fee") or item.get("admission_fee"))
        student_line_id = link.get("student_line_id")
        student_snap = students_snap.get(student_line_id) or {}
        student_label = (
            link.get("student_label")
            or student_snap.get("display_name")
            or student_snap.get("full_name")
            or student_snap.get("name")
            or ""
        )
        course_name = link.get("course_name") or item.get("course_name") or "Course"
        branch_name = link.get("branch_name") or item.get("branch_name") or ""
        line_sub = round(course_fee + admission_fee, 2)
        raw_lines.append(
            {
                "course_fee": course_fee,
                "admission_fee": admission_fee,
                "line_sub": line_sub,
                "link": link,
                "student_label": student_label,
                "course_name": course_name,
                "branch_name": branch_name,
            }
        )

    # Allocate cart-level discount proportionally across lines
    alloc_remaining = discount_total
    for i, raw in enumerate(raw_lines):
        if i == len(raw_lines) - 1:
            disc = round(alloc_remaining, 2)
        else:
            share = (raw["line_sub"] / sum(r["line_sub"] for r in raw_lines if r["line_sub"] > 0)) if sum(
                r["line_sub"] for r in raw_lines
            ) > 0 else 0
            disc = round(discount_total * share, 2)
            alloc_remaining = round(alloc_remaining - disc, 2)
        line_total = round(raw["line_sub"] - disc, 2)
        link = raw["link"]
        lines.append(
            InvoiceLineItem(
                description=f"{raw['course_name']} enrollment",
                student_id=link.get("student_id"),
                student_label=raw["student_label"],
                enrollment_id=link.get("enrollment_id"),
                course_id=link.get("course_id"),
                course_name=raw["course_name"],
                branch_id=link.get("branch_id"),
                branch_name=raw["branch_name"],
                course_fee=raw["course_fee"],
                admission_fee=raw["admission_fee"],
                discount_amount=disc,
                line_total=line_total,
                metadata={"cart_item_id": link.get("cart_item_id")},
            )
        )

    if not lines:
        # Fallback single line from payment amount
        amount = _f(payment.get("amount") or checkout.get("amount_inr"))
        lines.append(
            InvoiceLineItem(
                description="Cart enrollment payment",
                course_fee=amount,
                line_total=amount,
            )
        )
    return lines


async def _build_lines_from_enrollment(db, payment: dict, enrollment: Optional[dict]) -> List[InvoiceLineItem]:
    enrollment = enrollment or {}
    course_id = enrollment.get("course_id") or payment.get("course_id")
    branch_id = enrollment.get("branch_id") or (payment.get("branch_details") or {}).get("branch_id")
    course = await db.courses.find_one({"id": course_id}) if course_id else None
    branch = await db.branches.find_one({"id": branch_id}) if branch_id else None

    course_name = (
        (payment.get("course_details") or {}).get("course_name")
        or (course or {}).get("title")
        or (course or {}).get("name")
        or "Course"
    )
    branch_name = (
        (payment.get("branch_details") or {}).get("branch_name")
        or ((branch or {}).get("branch") or {}).get("name")
        or (branch or {}).get("name")
        or ""
    )

    course_fee = _f(enrollment.get("fee_amount"))
    admission_fee = _f(enrollment.get("admission_fee"))
    if course_fee <= 0 and admission_fee <= 0:
        # Fall back to payment amount as course fee
        course_fee = _f(payment.get("amount"))
        admission_fee = 0.0

    student = None
    student_id = payment.get("student_id") or enrollment.get("student_id")
    if student_id:
        student = await db.users.find_one({"id": student_id})
    beneficiary = enrollment.get("beneficiary") or {}
    student_label = ""
    if isinstance(beneficiary, dict) and (beneficiary.get("beneficiary_type") or "self") != "self":
        student_label = (beneficiary.get("beneficiary_name") or "").strip()
    if not student_label:
        student_label = _name_from_user(student)

    line_total = round(course_fee + admission_fee, 2)
    # If payment amount differs (e.g. discounts applied later), prefer payment amount as total
    pay_amount = _f(payment.get("amount"))
    discount = 0.0
    if pay_amount > 0 and abs(pay_amount - line_total) > 0.05 and line_total > pay_amount:
        discount = round(line_total - pay_amount, 2)
        line_total = pay_amount

    return [
        InvoiceLineItem(
            description=f"{course_name} enrollment",
            student_id=student_id,
            student_label=student_label,
            enrollment_id=enrollment.get("id") or payment.get("enrollment_id"),
            course_id=course_id,
            course_name=course_name,
            branch_id=branch_id,
            branch_name=branch_name,
            course_fee=course_fee,
            admission_fee=admission_fee,
            discount_amount=discount,
            line_total=line_total,
        )
    ]


async def generate_invoice_for_payment(
    *,
    payment_id: Optional[str] = None,
    payment_doc: Optional[dict] = None,
    cart_checkout_id: Optional[str] = None,
    source_hint: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Create (or return existing) invoice for a paid payment / cart checkout.
    """
    db = get_db()
    if db is None:
        return None

    await ensure_invoice_indexes(db)

    payment = await _resolve_payment(db, payment_id=payment_id, payment_doc=payment_doc)
    checkout_id = cart_checkout_id or (payment or {}).get("cart_checkout_id")

    existing = await _find_existing(
        db,
        payment_id=(payment or {}).get("id") or payment_id,
        cart_checkout_id=checkout_id,
    )
    if existing:
        existing.pop("_id", None)
        return serialize_doc(existing)

    if not payment and checkout_id:
        payment = await db.payments.find_one(
            {"cart_checkout_id": checkout_id, "payment_status": {"$in": ["paid", "completed"]}}
        ) or await db.payments.find_one({"cart_checkout_id": checkout_id})

    if not payment:
        logger.warning("Invoice skipped: payment not found payment_id=%s cart=%s", payment_id, checkout_id)
        return None

    status = str(payment.get("payment_status") or payment.get("status") or "").lower()
    if status not in {"paid", "completed", "success"}:
        # Still allow if cart already fulfilled
        if not checkout_id:
            logger.info("Invoice skipped: payment not paid id=%s status=%s", payment.get("id"), status)
            return None

    checkout = None
    if checkout_id:
        checkout = await db.cart_checkouts.find_one({"id": checkout_id})

    if checkout:
        lines = await _build_lines_from_cart(db, checkout, payment)
        source = "cart"
        account_user_id = checkout.get("owner_user_id") or payment.get("student_id")
        branch_id = None
        for link in checkout.get("enrollment_links") or []:
            if link.get("branch_id"):
                branch_id = link.get("branch_id")
                break
    else:
        enrollment = None
        eid = payment.get("enrollment_id")
        if eid:
            enrollment = await db.enrollments.find_one({"id": eid})
        lines = await _build_lines_from_enrollment(db, payment, enrollment)
        source = source_hint or (
            "registration"
            if str(payment.get("payment_type") or "").lower() in {"registration_fee", "admission_fee"}
            else "payment"
        )
        account_user_id = payment.get("student_id") or payment.get("user_id")
        branch_id = (enrollment or {}).get("branch_id") or (payment.get("branch_details") or {}).get("branch_id")

    student_id = payment.get("student_id") or account_user_id
    user = await db.users.find_one({"id": student_id}) if student_id else None
    company = await _load_company(db)
    customer = {
        "name": _name_from_user(user),
        "email": (user or {}).get("email"),
        "phone": (user or {}).get("phone") or (user or {}).get("mobile"),
        "address": None,
    }
    # Registration may carry student on payment.registration_data
    reg = payment.get("registration_data") or {}
    if isinstance(reg, dict) and (reg.get("full_name") or reg.get("email")):
        customer = {
            "name": (reg.get("full_name") or customer["name"]),
            "email": reg.get("email") or customer["email"],
            "phone": reg.get("phone") or customer["phone"],
            "address": None,
        }

    subtotal = round(sum(_f(i.course_fee) for i in lines), 2)
    admission_total = round(sum(_f(i.admission_fee) for i in lines), 2)
    discount_total = round(sum(_f(i.discount_amount) for i in lines), 2)
    computed_total = round(sum(_f(i.line_total) for i in lines), 2)
    pay_total = _f(payment.get("amount") or (checkout or {}).get("amount_inr"))
    total_amount = pay_total if pay_total > 0 else computed_total

    paid_at = payment.get("payment_date") or payment.get("updated_at") or datetime.utcnow()
    invoice_number = await next_invoice_number(paid_at=paid_at if isinstance(paid_at, datetime) else None)

    now = datetime.utcnow()
    doc: Dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "invoice_number": invoice_number,
        "status": InvoiceStatus.ISSUED.value,
        "payment_id": payment.get("id"),
        "cart_checkout_id": checkout_id,
        "student_id": student_id,
        "account_user_id": account_user_id or student_id,
        "branch_id": branch_id,
        "currency": payment.get("currency") or (checkout or {}).get("currency") or "INR",
        "company": company,
        "customer": customer,
        "line_items": [i.dict() if hasattr(i, "dict") else i.model_dump() for i in lines],
        "subtotal_amount": subtotal,
        "admission_total": admission_total,
        "discount_total": discount_total,
        "tax_total": 0.0,
        "total_amount": total_amount,
        "payment_reference": payment.get("razorpay_payment_id")
        or payment.get("transaction_id")
        or payment.get("id"),
        "payment_method": payment.get("payment_method"),
        "payment_gateway_label": payment.get("gateway_payment_label"),
        "paid_at": paid_at,
        "notes": payment.get("notes"),
        "source": source,
        "created_at": now,
        "updated_at": now,
    }
    doc["document_html"] = build_invoice_html(doc)

    try:
        await db.invoices.insert_one(doc)
    except Exception as exc:
        # Race: another worker inserted first — return existing
        logger.warning("Invoice insert race/error payment=%s: %s", payment.get("id"), exc)
        existing = await _find_existing(db, payment_id=payment.get("id"), cart_checkout_id=checkout_id)
        if existing:
            existing.pop("_id", None)
            return serialize_doc(existing)
        raise

    # Soft-link payment → invoice (additive field; never breaks readers)
    try:
        await db.payments.update_one(
            {"id": payment.get("id")},
            {"$set": {"invoice_id": doc["id"], "invoice_number": invoice_number, "updated_at": now}},
        )
    except Exception:
        logger.exception("Failed linking invoice_id on payment %s", payment.get("id"))

    if checkout_id:
        try:
            await db.cart_checkouts.update_one(
                {"id": checkout_id},
                {"$set": {"invoice_id": doc["id"], "invoice_number": invoice_number, "updated_at": now}},
            )
        except Exception:
            logger.exception("Failed linking invoice_id on cart checkout %s", checkout_id)

    # M07-S05: auto WhatsApp after NEW invoice only (idempotent early-return above skips this)
    try:
        from utils.invoice_whatsapp_service import safe_send_invoice_whatsapp_for_invoice

        await safe_send_invoice_whatsapp_for_invoice(doc, trigger="auto")
    except Exception:
        logger.exception("Invoice WhatsApp hook failed invoice_id=%s", doc.get("id"))

    doc.pop("_id", None)
    return serialize_doc(doc)


async def safe_generate_invoice_for_payment(**kwargs) -> Optional[Dict[str, Any]]:
    """Never raise into payment/checkout flows."""
    try:
        return await generate_invoice_for_payment(**kwargs)
    except Exception:
        logger.exception(
            "Invoice generation failed payment_id=%s cart=%s",
            kwargs.get("payment_id"),
            kwargs.get("cart_checkout_id"),
        )
        return None


def public_invoice_payload(doc: dict, *, include_html: bool = False) -> dict:
    if not doc:
        return {}
    out = serialize_doc(doc)
    out.pop("_id", None)
    if not include_html:
        out.pop("document_html", None)
    # Surface denormalized WhatsApp fields when present (additive)
    if out.get("whatsapp_delivery_status") or out.get("whatsapp_delivery_id"):
        out["whatsapp_delivery"] = {
            "id": out.get("whatsapp_delivery_id"),
            "status": out.get("whatsapp_delivery_status"),
            "phone_masked": out.get("whatsapp_phone_masked"),
            "sent_at": out.get("whatsapp_last_sent_at"),
        }
    return out
