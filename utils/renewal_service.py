"""
M07-S04 student renewal helpers.

Additive utilities for renewal validity start, quote packaging, history records,
and post-payment finalization. Does not change route-guard / access control.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from utils.billing_state import (
    calculate_arrear_amount,
    compute_enrollment_billing_state,
    grace_days_configured,
)
from utils.subscription_dates import subscription_end_of_day_utc

logger = logging.getLogger(__name__)


def _naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def compute_renewal_validity_start(
    prior_end_date: Any,
    *,
    now: Optional[datetime] = None,
    grace_days: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Decide when the renewed period starts.

    - active (still valid): stack after prior end (in-advance renew)
    - grace: stack after prior end (continuous coverage, no gap)
    - overdue / unknown: start from payment/now
    """
    current = now or datetime.utcnow()
    if current.tzinfo is not None:
        current_naive = _naive_utc(current)
        current_aware = current.astimezone(timezone.utc)
    else:
        current_naive = current
        current_aware = current.replace(tzinfo=timezone.utc)

    billing = compute_enrollment_billing_state(
        prior_end_date, now=current_aware, grace_days=grace_days
    )
    state = str(billing.get("billing_state") or "unknown")
    end_eod = subscription_end_of_day_utc(prior_end_date)

    if state in ("active", "grace") and end_eod is not None:
        start = _naive_utc(end_eod + timedelta(microseconds=1))
        mode = "stack_from_prior_end"
    else:
        start = current_naive
        mode = "from_now"

    return {
        "start_date": start,
        "mode": mode,
        "billing_state": state,
        "billing": billing,
        "prior_end_eod": end_eod,
    }


def build_renewal_history_entry(
    *,
    source_enrollment_id: str,
    renewal_enrollment_id: str,
    payment_id: Optional[str],
    prior_end_date: Any,
    renewal_date: datetime,
    resulting_end_date: Any,
    course_fee: float,
    admission_fee: float,
    arrear_amount: float,
    total_amount: float,
    overdue_days: int,
    billing_state: str,
    duration_id: Optional[str] = None,
    duration_months: Optional[int] = None,
) -> Dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "source_enrollment_id": source_enrollment_id,
        "renewal_enrollment_id": renewal_enrollment_id,
        "payment_id": payment_id,
        "prior_end_date": prior_end_date,
        "renewal_date": renewal_date,
        "resulting_end_date": resulting_end_date,
        "course_fee": float(course_fee or 0),
        "admission_fee": float(admission_fee or 0),
        "arrear_amount": float(arrear_amount or 0),
        "total_amount": float(total_amount or 0),
        "overdue_days": int(overdue_days or 0),
        "billing_state_at_renewal": billing_state,
        "duration_id": duration_id,
        "duration_months": duration_months,
        "created_at": renewal_date,
    }


async def append_renewal_history(
    db,
    *,
    source_enrollment_id: str,
    entry: Dict[str, Any],
) -> None:
    """Persist history on source enrollment and a dedicated collection (additive)."""
    now = datetime.utcnow()
    try:
        await db.enrollments.update_one(
            {"id": source_enrollment_id},
            {
                "$push": {"renewal_history": entry},
                "$set": {"updated_at": now, "last_renewed_at": entry.get("renewal_date") or now},
            },
        )
    except Exception:
        logger.exception("Failed to push renewal_history on enrollment %s", source_enrollment_id)

    try:
        doc = dict(entry)
        doc["enrollment_id"] = source_enrollment_id
        await db.renewal_history.insert_one(doc)
    except Exception:
        logger.exception("Failed to insert renewal_history collection row")


async def finalize_renewal_after_payment(
    db,
    *,
    enrollment: Dict[str, Any],
    payment_doc: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    After verified payment on a renewal pending enrollment:
    - recompute end_date from stored start + duration
    - append renewal history on source enrollment (once per payment)
    - soft-deactivate the prior paid enrollment when it is a different id
    """
    from utils.enrollment_dates import enrollment_subscription_end_after_payment

    current = now or datetime.utcnow()
    is_renewal = bool(enrollment.get("is_renewal") or enrollment.get("renewed_from_enrollment_id"))
    if not is_renewal:
        recomputed = await enrollment_subscription_end_after_payment(db, enrollment)
        patch: Dict[str, Any] = {
            "payment_status": "paid",
            "is_active": True,
            "updated_at": current,
        }
        if recomputed:
            patch["end_date"] = recomputed
        await db.enrollments.update_one(
            {"id": enrollment["id"]},
            {"$set": patch, "$unset": {"status": ""}},
        )
        return {"renewal": False, "end_date": recomputed or enrollment.get("end_date")}

    source_id = enrollment.get("renewed_from_enrollment_id") or enrollment.get("id")
    prior_end = enrollment.get("renewal_prior_end_date") or enrollment.get("prior_end_date")
    if prior_end is None and source_id:
        source = await db.enrollments.find_one({"id": source_id})
        if source:
            prior_end = source.get("end_date")

    recomputed = await enrollment_subscription_end_after_payment(db, enrollment)
    patch = {
        "payment_status": "paid",
        "is_active": True,
        "updated_at": current,
        "is_renewal": True,
    }
    if recomputed:
        patch["end_date"] = recomputed

    await db.enrollments.update_one(
        {"id": enrollment["id"]},
        {"$set": patch, "$unset": {"status": "", "renewal_checkout_pending": ""}},
    )

    # Soft-deactivate superseded prior enrollment when renewal created a new row.
    if source_id and source_id != enrollment.get("id"):
        try:
            await db.enrollments.update_one(
                {
                    "id": source_id,
                    "payment_status": "paid",
                },
                {
                    "$set": {
                        "is_active": False,
                        "superseded_by_enrollment_id": enrollment["id"],
                        "superseded_at": current,
                        "updated_at": current,
                    }
                },
            )
        except Exception:
            logger.exception("Failed to supersede source enrollment %s", source_id)

    payment_id = None
    if payment_doc:
        payment_id = payment_doc.get("id") or str(payment_doc.get("_id") or "") or None

    # Idempotent history: skip if this payment already recorded a renewal.
    if payment_id:
        try:
            existing = await db.renewal_history.find_one({"payment_id": payment_id})
            if existing:
                return {
                    "renewal": True,
                    "end_date": recomputed or enrollment.get("end_date"),
                    "prior_end_date": prior_end,
                    "history_id": existing.get("id"),
                    "source_enrollment_id": source_id,
                    "duplicate": True,
                }
        except Exception:
            pass
        # Also check embedded history on source
        try:
            src = await db.enrollments.find_one(
                {"id": source_id, "renewal_history.payment_id": payment_id},
                {"renewal_history": 1},
            )
            if src:
                for h in src.get("renewal_history") or []:
                    if isinstance(h, dict) and h.get("payment_id") == payment_id:
                        return {
                            "renewal": True,
                            "end_date": recomputed or enrollment.get("end_date"),
                            "prior_end_date": prior_end,
                            "history_id": h.get("id"),
                            "source_enrollment_id": source_id,
                            "duplicate": True,
                        }
        except Exception:
            pass

    entry = build_renewal_history_entry(
        source_enrollment_id=str(source_id),
        renewal_enrollment_id=str(enrollment.get("id")),
        payment_id=payment_id,
        prior_end_date=prior_end,
        renewal_date=current,
        resulting_end_date=recomputed or enrollment.get("end_date"),
        course_fee=float(enrollment.get("fee_amount") or 0),
        admission_fee=float(enrollment.get("admission_fee") or 0),
        arrear_amount=float(enrollment.get("arrear_amount") or 0),
        total_amount=float(
            (enrollment.get("fee_amount") or 0)
            + (enrollment.get("admission_fee") or 0)
            + (enrollment.get("arrear_amount") or 0)
        ),
        overdue_days=int(enrollment.get("renewal_overdue_days") or 0),
        billing_state=str(enrollment.get("renewal_billing_state") or "unknown"),
        duration_id=enrollment.get("duration_id"),
        duration_months=enrollment.get("duration_months"),
    )
    await append_renewal_history(db, source_enrollment_id=str(source_id), entry=entry)
    # Also attach history on the active renewed enrollment for easy UI reads.
    if enrollment.get("id") and enrollment.get("id") != source_id:
        try:
            await db.enrollments.update_one(
                {"id": enrollment["id"]},
                {"$push": {"renewal_history": entry}},
            )
        except Exception:
            pass

    return {
        "renewal": True,
        "end_date": recomputed or enrollment.get("end_date"),
        "prior_end_date": prior_end,
        "history_id": entry.get("id"),
        "source_enrollment_id": source_id,
    }


def package_quote_response(
    *,
    enrollment_id: str,
    course_id: str,
    branch_id: str,
    duration: str,
    batch_ref: Optional[str],
    course_name: str,
    branch_name: str,
    course_fee: float,
    admission_fee: float,
    arrear: Dict[str, Any],
    billing: Dict[str, Any],
    duration_months: int,
    cycle: Optional[Dict[str, Any]] = None,
    enrollment: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    arrear_amount = float(arrear.get("arrear_amount") or 0)
    renewal_fee = round(float(course_fee) + float(admission_fee), 2)
    total_amount = round(renewal_fee + arrear_amount, 2)
    enr = enrollment or {}
    return {
        "enrollment_id": enrollment_id,
        "course_id": course_id,
        "branch_id": branch_id,
        "duration": duration,
        "batch_ref": batch_ref,
        "course_name": course_name,
        "branch_name": branch_name,
        "billing": {
            "billing_state": billing.get("billing_state"),
            "is_expired": billing.get("is_expired"),
            "is_within_grace": billing.get("is_within_grace"),
            "overdue_days": billing.get("overdue_days"),
            "grace_days_total": billing.get("grace_days_total") or grace_days_configured(),
            "grace_days_remaining": billing.get("grace_days_remaining"),
            "days_until_expiry": billing.get("days_until_expiry"),
            "expiry_at": billing.get("expiry_at"),
            "grace_ends_at": billing.get("grace_ends_at"),
            "next_due_date": (cycle or {}).get("next_due_date")
            or enr.get("next_due_date")
            or enr.get("end_date"),
            "period_start": (cycle or {}).get("period_start") or enr.get("billing_period_start"),
            "period_end": (cycle or {}).get("period_end")
            or enr.get("billing_period_end")
            or enr.get("end_date"),
        },
        "pricing": {
            "course_fee": float(course_fee),
            "admission_fee": float(admission_fee),
            "renewal_fee": renewal_fee,
            "arrear_amount": arrear_amount,
            "total_amount": total_amount,
            "currency": "INR",
        },
        "arrear": arrear,
        "admission_fee_applied": float(admission_fee) > 0,
        "renewal_eligible": True,
        "duration_months": duration_months,
        "message": (
            f"Within {grace_days_configured()}-day grace window — renew to continue without interruption."
            if billing.get("billing_state") == "grace"
            else (
                "Subscription expired past grace — renew now. Arrear rules pending client confirmation."
                if billing.get("billing_state") == "overdue"
                else "On-time renewal quote."
            )
        ),
    }
