"""
M07-S02 billing cycle service.

Creates/updates an enrollment-linked billing cycle from a successful payment
using paid-date + calendar months (month-end safe). Soft-syncs enrollment
next_due_date / billing_cycle_id without changing checkout APIs.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from models.billing_cycle_models import BillingCycleStatus
from utils.billing_cycle_dates import (
    compute_billing_period,
    derive_cycle_status,
    parse_to_naive_datetime,
)
from utils.database import get_db
from utils.helpers import serialize_doc

logger = logging.getLogger(__name__)


async def ensure_billing_cycle_indexes(db=None) -> None:
    db = db or get_db()
    if db is None:
        return
    try:
        await db.billing_cycles.create_index("id", unique=True)
        await db.billing_cycles.create_index([("enrollment_id", 1), ("created_at", -1)])
        await db.billing_cycles.create_index([("student_id", 1), ("created_at", -1)])
        await db.billing_cycles.create_index([("branch_id", 1), ("next_due_date", 1)])
        await db.billing_cycles.create_index("payment_id", sparse=True)
        await db.billing_cycles.create_index(
            [("enrollment_id", 1), ("status", 1), ("created_at", -1)]
        )
    except Exception:
        logger.exception("Failed ensuring billing_cycle indexes")


async def _duration_months_for_enrollment(db, enrollment: dict) -> int:
    dur_ref = enrollment.get("duration_id")
    if dur_ref:
        dr = await db.durations.find_one({"id": str(dur_ref)})
        if not dr:
            dr = await db.durations.find_one({"code": str(dur_ref)})
        if dr:
            dm = dr.get("duration_months")
            if dm is not None:
                try:
                    months = int(dm)
                    if months > 0:
                        return months
                except (TypeError, ValueError):
                    pass
            dd = dr.get("duration_days")
            if dd is not None:
                try:
                    days = int(dd)
                    if days > 0:
                        return max(1, round(days / 30))
                except (TypeError, ValueError):
                    pass
    # Infer from existing start/end when possible
    start = parse_to_naive_datetime(enrollment.get("start_date") or enrollment.get("enrollment_date"))
    end = parse_to_naive_datetime(enrollment.get("end_date"))
    if start and end and end > start:
        days = (end.date() - start.date()).days
        if days > 0:
            return max(1, round(days / 30))
    return 1


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


async def upsert_billing_cycle_for_enrollment(
    *,
    enrollment_id: str,
    payment_doc: Optional[dict] = None,
    paid_at: Optional[datetime] = None,
    cart_checkout_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Create a new active cycle for an enrollment after successful payment."""
    db = get_db()
    if db is None or not enrollment_id:
        return None

    await ensure_billing_cycle_indexes(db)

    enrollment = await db.enrollments.find_one({"id": enrollment_id})
    if not enrollment:
        logger.warning("Billing cycle skipped: enrollment %s not found", enrollment_id)
        return None

    payment = payment_doc
    if payment is None:
        payment = await db.payments.find_one(
            {"enrollment_id": enrollment_id, "payment_status": {"$in": ["paid", "completed"]}},
            sort=[("payment_date", -1), ("created_at", -1)],
        )

    paid = paid_at or parse_to_naive_datetime(
        (payment or {}).get("payment_date")
        or (payment or {}).get("updated_at")
        or enrollment.get("updated_at")
        or datetime.utcnow()
    )
    if paid is None:
        paid = datetime.utcnow()

    # Prefer paid-date cycle; if enrollment already has a later end_date from validity
    # engine, still compute next_due from paid date for billing (story requirement).
    months = await _duration_months_for_enrollment(db, enrollment)
    period_start, period_end, next_due, anchor = compute_billing_period(paid, months)

    # If existing enrollment.end_date is later and within reason, keep next_due aligned
    # to validity end when it is after computed period_end (renewal-in-advance cases).
    existing_end = parse_to_naive_datetime(enrollment.get("end_date"))
    if existing_end and existing_end > period_end:
        next_due = existing_end
        period_end = existing_end

    now = datetime.utcnow()
    status = derive_cycle_status(period_end, now=now)

    # Supersede prior active/due cycles for this enrollment
    await db.billing_cycles.update_many(
        {
            "enrollment_id": enrollment_id,
            "status": {
                "$in": [
                    BillingCycleStatus.ACTIVE.value,
                    BillingCycleStatus.DUE_SOON.value,
                    BillingCycleStatus.OVERDUE.value,
                ]
            },
        },
        {
            "$set": {
                "status": BillingCycleStatus.SUPERSEDED.value,
                "updated_at": now,
            }
        },
    )

    doc: Dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "enrollment_id": enrollment_id,
        "student_id": enrollment.get("student_id") or (payment or {}).get("student_id"),
        "account_user_id": (payment or {}).get("user_id") or enrollment.get("student_id"),
        "branch_id": enrollment.get("branch_id")
        or ((payment or {}).get("branch_details") or {}).get("branch_id"),
        "course_id": enrollment.get("course_id") or (payment or {}).get("course_id"),
        "payment_id": (payment or {}).get("id"),
        "cart_checkout_id": cart_checkout_id or (payment or {}).get("cart_checkout_id"),
        "period_start": period_start,
        "period_end": period_end,
        "next_due_date": next_due,
        "anchor_day": anchor,
        "duration_months": months,
        "status": status,
        "currency": (payment or {}).get("currency") or "INR",
        "amount_hint": _f((payment or {}).get("amount") or enrollment.get("fee_amount")),
        "created_at": now,
        "updated_at": now,
    }
    await db.billing_cycles.insert_one(doc)

    # Soft-sync enrollment (additive fields only)
    try:
        await db.enrollments.update_one(
            {"id": enrollment_id},
            {
                "$set": {
                    "next_due_date": next_due,
                    "billing_cycle_id": doc["id"],
                    "billing_period_start": period_start,
                    "billing_period_end": period_end,
                    "updated_at": now,
                }
            },
        )
    except Exception:
        logger.exception("Failed syncing billing fields on enrollment %s", enrollment_id)

    if payment and payment.get("id"):
        try:
            await db.payments.update_one(
                {"id": payment["id"]},
                {"$set": {"billing_cycle_id": doc["id"], "updated_at": now}},
            )
        except Exception:
            logger.exception("Failed linking billing_cycle_id on payment %s", payment.get("id"))

    doc.pop("_id", None)
    return serialize_doc(doc)


async def upsert_billing_cycles_for_payment(
    *,
    payment_id: Optional[str] = None,
    payment_doc: Optional[dict] = None,
    cart_checkout_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Create cycles for one payment (single enrollment) or all cart enrollments."""
    db = get_db()
    if db is None:
        return []

    payment = payment_doc
    if payment is None and payment_id:
        payment = await db.payments.find_one({"id": payment_id})

    checkout_id = cart_checkout_id or (payment or {}).get("cart_checkout_id")
    results: List[Dict[str, Any]] = []

    if checkout_id:
        checkout = await db.cart_checkouts.find_one({"id": checkout_id})
        links = (checkout or {}).get("enrollment_links") or []
        if not payment:
            payment = await db.payments.find_one(
                {"cart_checkout_id": checkout_id, "payment_status": {"$in": ["paid", "completed"]}}
            ) or await db.payments.find_one({"cart_checkout_id": checkout_id})
        for link in links:
            eid = link.get("enrollment_id")
            if not eid:
                continue
            cycle = await upsert_billing_cycle_for_enrollment(
                enrollment_id=eid,
                payment_doc=payment,
                cart_checkout_id=checkout_id,
            )
            if cycle:
                results.append(cycle)
        return results

    if payment and payment.get("enrollment_id"):
        cycle = await upsert_billing_cycle_for_enrollment(
            enrollment_id=payment["enrollment_id"],
            payment_doc=payment,
        )
        if cycle:
            results.append(cycle)
    return results


async def safe_upsert_billing_cycles_for_payment(**kwargs) -> List[Dict[str, Any]]:
    try:
        return await upsert_billing_cycles_for_payment(**kwargs)
    except Exception:
        logger.exception(
            "Billing cycle upsert failed payment_id=%s cart=%s",
            kwargs.get("payment_id"),
            kwargs.get("cart_checkout_id"),
        )
        return []


async def refresh_cycle_status(cycle_id: str) -> Optional[dict]:
    db = get_db()
    if db is None:
        return None
    doc = await db.billing_cycles.find_one({"id": cycle_id})
    if not doc:
        return None
    if doc.get("status") in (
        BillingCycleStatus.CANCELLED.value,
        BillingCycleStatus.SUPERSEDED.value,
    ):
        return serialize_doc(doc)
    status = derive_cycle_status(doc.get("period_end") or doc.get("next_due_date"))
    if status != doc.get("status"):
        await db.billing_cycles.update_one(
            {"id": cycle_id},
            {"$set": {"status": status, "updated_at": datetime.utcnow()}},
        )
        doc["status"] = status
    doc.pop("_id", None)
    return serialize_doc(doc)


async def get_active_cycle_for_enrollment(enrollment_id: str) -> Optional[dict]:
    db = get_db()
    if db is None:
        return None
    doc = await db.billing_cycles.find_one(
        {
            "enrollment_id": enrollment_id,
            "status": {
                "$in": [
                    BillingCycleStatus.ACTIVE.value,
                    BillingCycleStatus.DUE_SOON.value,
                    BillingCycleStatus.OVERDUE.value,
                ]
            },
        },
        sort=[("created_at", -1)],
    )
    if not doc:
        doc = await db.billing_cycles.find_one(
            {"enrollment_id": enrollment_id},
            sort=[("created_at", -1)],
        )
    if not doc:
        return None
    refreshed = await refresh_cycle_status(doc["id"])
    return refreshed or serialize_doc(doc)
