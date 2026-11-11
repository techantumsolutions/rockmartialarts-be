"""
Admin (Super Admin / Branch Manager) student plan renewal.

Reuses M07 renewal_service + PaymentController pricing. Does not change
student self-serve renewal routes.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from utils.billing_state import calculate_arrear_amount, compute_enrollment_billing_state
from utils.database import get_db
from utils.enrollment_dates import resolve_enrollment_end_date
from utils.renewal_service import (
    compute_renewal_validity_start,
    finalize_renewal_after_payment,
    package_quote_response,
)
from utils.student_status_service import get_managed_branch_ids_for_user

logger = logging.getLogger(__name__)

_SUPER = {"super_admin", "superadmin"}
_BRANCH_ADMIN = {"branch_manager", "branch_admin", "branch-admin"}
_COACH_ADMIN = {"coach_admin"}
_ALLOWED = _SUPER | _BRANCH_ADMIN | _COACH_ADMIN


def _role(user: dict) -> str:
    return str((user or {}).get("role") or "").strip().lower().replace("-", "_")


def _admin_actor(current_user: dict) -> Dict[str, Any]:
    return {
        "user_id": current_user.get("id"),
        "name": current_user.get("full_name")
        or current_user.get("name")
        or current_user.get("email")
        or "Admin",
        "role": current_user.get("role"),
    }


async def assert_admin_can_renew_enrollment(
    db,
    current_user: dict,
    enrollment: dict,
) -> None:
    role = _role(current_user)
    if role not in _ALLOWED:
        raise HTTPException(
            status_code=403,
            detail="Only Super Admin or Branch Admin can renew plans",
        )

    branch_id = str(enrollment.get("branch_id") or "")
    if not branch_id:
        raise HTTPException(status_code=400, detail="Enrollment is missing branch")

    if role in _SUPER:
        return

    if role in _COACH_ADMIN:
        user_branch = str(current_user.get("branch_id") or "")
        if user_branch and user_branch != branch_id:
            raise HTTPException(status_code=403, detail="Enrollment is outside your branch")
        return

    managed = await get_managed_branch_ids_for_user(db, current_user)
    allowed = {str(x) for x in (managed or []) if x}
    if branch_id not in allowed:
        raise HTTPException(
            status_code=403,
            detail="You can only renew students in your managed branches",
        )


async def admin_quote_renewal(
    *,
    enrollment_id: str,
    duration: Optional[str],
    batch_ref: Optional[str],
    current_user: dict,
) -> Dict[str, Any]:
    from controllers.payment_controller import (
        PaymentController,
        should_charge_admission_fee_for_checkout,
    )

    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    enrollment = await db.enrollments.find_one({"id": enrollment_id})
    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    await assert_admin_can_renew_enrollment(db, current_user, enrollment)

    student_id = str(enrollment.get("student_id") or "")
    course_id = enrollment.get("course_id")
    branch_id = enrollment.get("branch_id")
    if not course_id or not branch_id or not student_id:
        raise HTTPException(status_code=400, detail="Enrollment is missing course, branch, or student")

    dur = (duration or enrollment.get("duration_id") or "").strip()
    if not dur:
        raise HTTPException(status_code=400, detail="Duration is required for renewal quote")

    br = (batch_ref or enrollment.get("batch_ref") or "").strip() or None
    beneficiary_payload = enrollment.get("beneficiary") or {"beneficiary_type": "self"}
    if not isinstance(beneficiary_payload, dict):
        beneficiary_payload = {"beneficiary_type": "self"}

    should_charge_admission = await should_charge_admission_fee_for_checkout(
        db, student_id=student_id, beneficiary=beneficiary_payload
    )
    info = await PaymentController.get_course_payment_info(
        course_id,
        branch_id,
        dur,
        batch_ref=br,
        optional_student_id=student_id,
        admission_fee_beneficiary=beneficiary_payload,
    )
    course_fee = float(info.pricing.course_fee)
    admission_fee = float(info.pricing.admission_fee) if should_charge_admission else 0.0

    billing = compute_enrollment_billing_state(enrollment.get("end_date"))
    duration_months = 1
    dur_row = await db.durations.find_one({"id": dur}) or await db.durations.find_one({"code": dur})
    if dur_row and dur_row.get("duration_months") is not None:
        try:
            duration_months = max(1, int(dur_row["duration_months"]))
        except (TypeError, ValueError):
            duration_months = 1

    arrear = calculate_arrear_amount(
        overdue_days=int(billing.get("overdue_days") or 0),
        course_fee=course_fee,
        duration_months=duration_months,
        billing_state=str(billing.get("billing_state") or "active"),
    )

    cycle = None
    try:
        from utils.billing_cycle_service import get_active_cycle_for_enrollment

        cycle = await get_active_cycle_for_enrollment(enrollment_id)
    except Exception:
        cycle = None

    quote = package_quote_response(
        enrollment_id=enrollment_id,
        course_id=course_id,
        branch_id=branch_id,
        duration=dur,
        batch_ref=br,
        course_name=info.course_name,
        branch_name=info.branch_name,
        course_fee=course_fee,
        admission_fee=admission_fee,
        arrear=arrear,
        billing=billing,
        duration_months=duration_months,
        cycle=cycle,
        enrollment=enrollment,
    )

    validity = compute_renewal_validity_start(enrollment.get("end_date"))
    start_date = validity["start_date"]
    end_date = await resolve_enrollment_end_date(
        db, dur, start_date, months_hint=duration_months
    )

    current_dur_id = enrollment.get("duration_id")
    current_dur_row = None
    if current_dur_id:
        current_dur_row = await db.durations.find_one({"id": current_dur_id}) or await db.durations.find_one(
            {"code": current_dur_id}
        )

    student = await db.users.find_one(
        {"id": student_id}, {"full_name": 1, "name": 1, "email": 1, "phone": 1}
    )
    quote["student_id"] = student_id
    quote["student_name"] = (
        (student or {}).get("full_name") or (student or {}).get("name") or "Student"
    )
    quote["current_duration_id"] = current_dur_id
    quote["current_plan_label"] = (
        (current_dur_row or {}).get("name")
        or (current_dur_row or {}).get("label")
        or current_dur_id
        or "Current plan"
    )
    quote["prior_end_date"] = enrollment.get("end_date")
    quote["start_date"] = enrollment.get("start_date") or enrollment.get("enrollment_date")
    quote["batch_ref"] = br or enrollment.get("batch_ref")
    quote["validity_preview"] = {
        "mode": validity.get("mode"),
        "start_date": start_date,
        "end_date": end_date,
        "billing_state": validity.get("billing_state"),
        "next_renewal_due": end_date,
    }
    quote["duration_label"] = (
        (dur_row or {}).get("name")
        or (dur_row or {}).get("label")
        or f"{duration_months} Month{'s' if duration_months != 1 else ''}"
    )
    quote["plan_status"] = _plan_status_label(billing)
    return quote


def _plan_status_label(billing: Dict[str, Any]) -> str:
    state = str(billing.get("billing_state") or "").lower()
    if state == "active":
        days = billing.get("days_until_expiry")
        try:
            if days is not None and int(days) <= 7:
                return "Expiring Soon"
        except (TypeError, ValueError):
            pass
        return "Active"
    if state == "grace":
        return "Expiring Soon"
    if state in ("overdue", "expired"):
        return "Expired"
    if billing.get("is_expired"):
        return "Expired"
    return "Active" if not billing.get("is_expired") else "Expired"


async def admin_confirm_renewal(
    *,
    enrollment_id: str,
    duration: Optional[str],
    batch_ref: Optional[str],
    payment_method: str,
    notes: Optional[str],
    current_user: dict,
) -> Dict[str, Any]:
    """
    Admin renewal checkout: create a pending renewal enrollment and a Razorpay order.
    Plan activates only after gateway payment is verified (admin_confirm_razorpay_renewal).
    """
    import os

    from controllers.payment_controller import (
        PaymentController,
        _batches_for_course_on_branch,
        _duration_price_keys,
        resolve_checkout_batch_ref,
        should_charge_admission_fee_for_checkout,
    )
    from models.enrollment_models import Enrollment as EnrollmentModel, PaymentStatus as EnrollmentPaymentStatus
    from models.payment_models import PaymentMethod, PaymentStatus, PaymentType
    from utils.razorpay_client import get_razorpay_client

    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    source = await db.enrollments.find_one({"id": enrollment_id})
    if not source:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    await assert_admin_can_renew_enrollment(db, current_user, source)

    student_id = str(source.get("student_id") or "")
    course_id = source.get("course_id")
    branch_id = source.get("branch_id")
    if not course_id or not branch_id or not student_id:
        raise HTTPException(status_code=400, detail="Enrollment is missing course, branch, or student")

    dur = (duration or source.get("duration_id") or "").strip()
    if not dur:
        raise HTTPException(status_code=400, detail="Duration is required for renewal")

    # Clear stale admin/student renewal pendings for this course+student
    stale_pending = await db.enrollments.find(
        {
            "student_id": student_id,
            "course_id": course_id,
            "payment_status": EnrollmentPaymentStatus.PENDING.value,
            "is_renewal": True,
        },
        {"id": 1},
    ).to_list(length=None)
    stale_ids = [str(e.get("id")) for e in stale_pending if e.get("id")]
    if stale_ids:
        await db.payments.update_many(
            {
                "student_id": student_id,
                "enrollment_id": {"$in": stale_ids},
                "payment_status": {"$in": ["pending", "processing", "failed"]},
            },
            {
                "$set": {
                    "payment_status": "cancelled",
                    "status": "cancelled",
                    "updated_at": datetime.utcnow(),
                    "notes": "Auto-cancelled stale renewal before admin renew",
                }
            },
        )
        await db.enrollments.delete_many({"id": {"$in": stale_ids}})

    requested_batch_ref = (batch_ref or source.get("batch_ref") or "").strip() or None
    branch = await db.branches.find_one({"id": branch_id})
    batches = _batches_for_course_on_branch(branch or {}, course_id)
    duration_row = await db.durations.find_one({"id": dur}) or await db.durations.find_one({"code": dur})
    resolved_batch = await resolve_checkout_batch_ref(
        db,
        course_id=course_id,
        branch_id=branch_id,
        batches=batches,
        requested_batch_ref=requested_batch_ref,
        student_id=student_id,
        duration_keys=_duration_price_keys(dur, duration_row),
    )

    beneficiary_payload = source.get("beneficiary") or {"beneficiary_type": "self"}
    if not isinstance(beneficiary_payload, dict):
        beneficiary_payload = {"beneficiary_type": "self"}

    should_charge_admission = await should_charge_admission_fee_for_checkout(
        db, student_id=student_id, beneficiary=beneficiary_payload
    )
    info = await PaymentController.get_course_payment_info(
        course_id,
        branch_id,
        dur,
        batch_ref=resolved_batch,
        optional_student_id=student_id,
        admission_fee_beneficiary=beneficiary_payload,
    )

    validity = compute_renewal_validity_start(source.get("end_date"))
    start_date = validity["start_date"]
    billing = validity["billing"]
    months_hint = None
    if duration_row and duration_row.get("duration_months") is not None:
        try:
            months_hint = int(duration_row["duration_months"])
        except (TypeError, ValueError):
            months_hint = None

    end_date = await resolve_enrollment_end_date(
        db, dur, start_date, months_hint=months_hint
    )

    course_fee = float(info.pricing.course_fee)
    admission_fee = float(info.pricing.admission_fee) if should_charge_admission else 0.0
    arrear = calculate_arrear_amount(
        overdue_days=int(billing.get("overdue_days") or 0),
        course_fee=course_fee,
        duration_months=max(1, months_hint or 1),
        billing_state=str(billing.get("billing_state") or "active"),
    )
    arrear_amount = float(arrear.get("arrear_amount") or 0)
    total_amount = round(course_fee + admission_fee + arrear_amount, 2)

    if course_fee <= 0:
        raise HTTPException(
            status_code=400,
            detail="Renewal fee is unavailable for this tenure. Check plan pricing for this course and branch.",
        )

    method = (payment_method or "razorpay").strip().lower()
    cash_methods = {"physical_cash", "cash", "offline", "hand_cash", "counter_cash"}
    is_cash = method in cash_methods

    amount_paise = int(round(total_amount * 100))
    if not is_cash and amount_paise < 100:
        raise HTTPException(status_code=400, detail="Amount too small for online payment.")

    actor = _admin_actor(current_user)
    enrollment = EnrollmentModel(
        student_id=student_id,
        course_id=course_id,
        branch_id=branch_id,
        start_date=start_date,
        end_date=end_date,
        fee_amount=course_fee,
        admission_fee=admission_fee,
        payment_status=EnrollmentPaymentStatus.PENDING,
        is_active=False,
    )
    enrollment_doc = enrollment.dict()
    enrollment_doc["duration_id"] = dur
    enrollment_doc["duration_months"] = months_hint
    enrollment_doc["enrollment_date"] = datetime.utcnow()
    enrollment_doc["is_renewal"] = True
    enrollment_doc["renewed_from_enrollment_id"] = source.get("id")
    enrollment_doc["renewal_prior_end_date"] = source.get("end_date")
    enrollment_doc["renewal_prior_duration_id"] = source.get("duration_id")
    enrollment_doc["renewal_billing_state"] = billing.get("billing_state")
    enrollment_doc["renewal_overdue_days"] = billing.get("overdue_days")
    enrollment_doc["renewal_start_mode"] = validity.get("mode")
    enrollment_doc["arrear_amount"] = arrear_amount
    enrollment_doc["arrear_rule"] = arrear.get("arrear_rule")
    enrollment_doc["renewal_checkout_pending"] = True
    enrollment_doc["renewed_by"] = actor
    enrollment_doc["renewed_by_admin_id"] = actor.get("user_id")
    enrollment_doc["renewed_by_admin_name"] = actor.get("name")
    enrollment_doc["renewed_by_admin_role"] = actor.get("role")
    if notes:
        enrollment_doc["admin_renewal_notes"] = notes.strip()
    if resolved_batch:
        enrollment_doc["batch_ref"] = resolved_batch
    await db.enrollments.insert_one(enrollment_doc)

    now = datetime.utcnow()

    # Authorized counter cash: admin attests cash collected; activate immediately.
    if is_cash:
        payment_id = str(uuid.uuid4())
        cash_txn = f"CASH-RENEW-{payment_id[:8].upper()}"
        payment_doc = {
            "id": payment_id,
            "student_id": student_id,
            "enrollment_id": enrollment.id,
            "course_id": course_id,
            "branch_id": branch_id,
            "amount": total_amount,
            "course_fee": course_fee,
            "admission_fee": admission_fee,
            "arrear_amount": arrear_amount,
            "payment_status": PaymentStatus.PAID.value,
            "status": "paid",
            "payment_method": PaymentMethod.PHYSICAL_CASH.value
            if hasattr(PaymentMethod, "PHYSICAL_CASH")
            else "physical_cash",
            "payment_type": PaymentType.COURSE_FEE.value,
            "is_renewal": True,
            "renewed_from_enrollment_id": source.get("id"),
            "transaction_id": cash_txn,
            "payment_date": now,
            "due_date": now,
            "created_at": now,
            "updated_at": now,
            "created_by": actor,
            "notes": (notes or "").strip()
            or f"Admin counter-cash renewal attested by {actor.get('name') or actor.get('user_id')}",
            "currency": "INR",
            "gateway_payment_label": "Counter cash",
        }
        try:
            from utils.partner_tenant import (
                apply_partner_org_stamp,
                resolve_partner_org_id_for_branch,
            )

            apply_partner_org_stamp(
                payment_doc,
                await resolve_partner_org_id_for_branch(db, branch_id),
            )
        except Exception:
            logger.exception("partner stamp failed for cash renewal payment")
        await db.payments.insert_one(payment_doc)
        renewal_meta = await finalize_renewal_after_payment(
            db, enrollment=enrollment_doc, payment_doc=payment_doc, now=now
        )
        history_id = renewal_meta.get("history_id")
        enrich = {
            "renewed_by": actor,
            "payment_status": "paid",
            "payment_method": "physical_cash",
            "previous_duration_id": source.get("duration_id"),
            "renewed_duration_id": dur,
            "renewed_duration_months": months_hint,
            "new_start_date": start_date,
            "course_id": course_id,
            "branch_id": branch_id,
        }
        if history_id:
            try:
                await db.renewal_history.update_one({"id": history_id}, {"$set": enrich})
            except Exception:
                logger.exception("Failed to enrich cash renewal history")
        try:
            from utils.invoice_service import safe_generate_invoice_for_payment

            inv_doc = await safe_generate_invoice_for_payment(
                payment_doc=payment_doc, source_hint="renewal"
            )
            if isinstance(inv_doc, dict) and inv_doc.get("id"):
                await db.payments.update_one(
                    {"id": payment_id},
                    {
                        "$set": {
                            "invoice_id": inv_doc.get("id"),
                            "invoice_number": inv_doc.get("invoice_number"),
                        }
                    },
                )
        except Exception:
            logger.exception("invoice after admin cash renewal failed")
        return {
            "message": "Renewal activated with counter cash payment.",
            "requires_payment": False,
            "enrollment_id": enrollment.id,
            "source_enrollment_id": source.get("id"),
            "payment_id": payment_id,
            "amount": total_amount,
            "payment_status": "paid",
            "payment_method": "physical_cash",
            "course_name": info.course_name,
            "branch_name": info.branch_name,
            "duration": dur,
            "start_date": start_date,
            "end_date": renewal_meta.get("end_date") or end_date,
            "new_end_date": renewal_meta.get("end_date") or end_date,
            "is_renewal": True,
            "renewed_by": actor,
        }

    client = get_razorpay_client()
    try:
        order = client.order.create(
            {
                "amount": amount_paise,
                "currency": "INR",
                "payment_capture": 1,
                "notes": {
                    "enrollment_id": (enrollment.id or "")[:40],
                    "student_id": (student_id or "")[:40],
                    "source_enrollment_id": str(source.get("id") or "")[:40],
                    "admin_renewal": "1",
                    "admin_id": str(actor.get("user_id") or "")[:40],
                },
            }
        )
    except Exception:
        logger.exception("Razorpay order.create failed for admin renewal enrollment_id=%s", enrollment.id)
        await db.enrollments.delete_one({"id": enrollment.id, "payment_status": "pending"})
        raise HTTPException(
            status_code=502,
            detail="We could not reach the payment service. Please try again in a moment.",
        )

    await db.enrollments.update_one(
        {"id": enrollment.id},
        {"$set": {"razorpay_last_order_id": order["id"], "updated_at": now}},
    )

    from utils.partner_tenant import (
        apply_partner_org_stamp,
        resolve_partner_org_id_for_branch,
    )

    pending_doc = {
        "id": str(uuid.uuid4()),
        "user_id": student_id,
        "student_id": student_id,
        "enrollment_id": enrollment.id,
        "course_id": course_id,
        "branch_id": branch_id,
        "amount": total_amount,
        "course_fee": course_fee,
        "admission_fee": admission_fee,
        "arrear_amount": arrear_amount,
        "payment_type": PaymentType.COURSE_FEE.value,
        "payment_method": PaymentMethod.DIGITAL_WALLET.value,
        "payment_status": PaymentStatus.PENDING.value,
        "status": "initiated",
        "is_renewal": True,
        "renewed_from_enrollment_id": source.get("id"),
        "transaction_id": None,
        "razorpay_payment_id": None,
        "razorpay_order_id": order["id"],
        "payment_date": None,
        "due_date": now,
        "notes": (notes or "").strip() or f"Admin renewal Razorpay order: {order['id']}",
        "created_by": actor,
        "course_details": {
            "course_id": course_id,
            "course_name": info.course_name,
            "duration": dur,
            "duration_id": dur,
            "duration_months": months_hint,
        },
        "branch_details": {
            "branch_id": branch_id,
            "branch_name": info.branch_name,
        },
        "created_at": now,
        "updated_at": now,
        "currency": "INR",
    }
    apply_partner_org_stamp(
        pending_doc,
        await resolve_partner_org_id_for_branch(db, branch_id),
    )
    await db.payments.insert_one(pending_doc)

    student = await db.users.find_one(
        {"id": student_id}, {"full_name": 1, "name": 1, "email": 1, "phone": 1}
    ) or {}

    key_id = os.getenv("RAZORPAY_KEY_ID", "").strip()
    return {
        "message": "Renewal checkout ready. Complete payment on Razorpay to activate the plan.",
        "requires_payment": True,
        "enrollment_id": enrollment.id,
        "source_enrollment_id": source.get("id"),
        "amount": total_amount,
        "payment_status": "pending",
        "course_name": info.course_name,
        "branch_name": info.branch_name,
        "duration": dur,
        "duration_months": months_hint,
        "prior_end_date": source.get("end_date"),
        "start_date": start_date,
        "end_date": end_date,
        "provisional_end_date": end_date,
        "student_id": student_id,
        "student_name": student.get("full_name") or student.get("name") or "Student",
        "student_email": student.get("email") or "",
        "student_phone": student.get("phone") or "",
        "renewed_by": actor,
        "pricing": {
            "course_fee": course_fee,
            "admission_fee": admission_fee,
            "arrear_amount": arrear_amount,
            "total_amount": total_amount,
            "currency": "INR",
        },
        "order": {
            "id": order["id"],
            "amount": int(order["amount"]),
            "currency": str(order.get("currency") or "INR"),
        },
        "key": key_id,
    }


async def admin_confirm_razorpay_renewal(
    *,
    enrollment_id: str,
    razorpay_payment_id: str,
    razorpay_order_id: Optional[str],
    razorpay_signature: Optional[str],
    current_user: dict,
) -> Dict[str, Any]:
    """Verify Razorpay payment for an admin-initiated renewal and activate the plan."""
    from controllers.payment_controller import (
        _enrollment_checkout_total_inr,
        _fetch_razorpay_payment_entity,
        _razorpay_method_to_payment_fields,
        _validate_razorpay_captured_amount,
    )
    from models.payment_models import PaymentMethod, PaymentStatus
    from utils.razorpay_client import verify_razorpay_signature

    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    enrollment = await db.enrollments.find_one({"id": enrollment_id})
    if not enrollment:
        raise HTTPException(status_code=404, detail="Renewal enrollment not found")
    if not enrollment.get("is_renewal"):
        raise HTTPException(status_code=400, detail="Enrollment is not an admin renewal checkout")

    await assert_admin_can_renew_enrollment(db, current_user, enrollment)

    if razorpay_order_id:
        if not razorpay_signature:
            raise HTTPException(status_code=400, detail="Missing Razorpay signature")
        if not verify_razorpay_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature):
            raise HTTPException(status_code=400, detail="Invalid payment signature")

    expected_total_inr = _enrollment_checkout_total_inr(enrollment)
    if expected_total_inr <= 0:
        raise HTTPException(status_code=400, detail="Invalid enrollment amount.")

    student_id = str(enrollment.get("student_id") or "")
    now = datetime.utcnow()
    actor = _admin_actor(current_user)

    pm_value = PaymentMethod.DIGITAL_WALLET.value
    gw_raw = "razorpay"
    gw_label = "Razorpay"
    rz = _fetch_razorpay_payment_entity(razorpay_payment_id)
    if rz:
        pm_value, gw_raw, gw_label = _razorpay_method_to_payment_fields(rz)
        if razorpay_order_id:
            expected_paise = int(round(expected_total_inr * 100))
            _validate_razorpay_captured_amount(
                enrollment_expected_paise=expected_paise,
                order_id=razorpay_order_id,
                payment_entity=rz,
                enrollment_id=enrollment_id,
            )

    existing_paid = await db.payments.find_one(
        {"student_id": student_id, "transaction_id": razorpay_payment_id}
    )
    if existing_paid:
        renewal_meta = await finalize_renewal_after_payment(
            db, enrollment=enrollment, payment_doc=existing_paid, now=now
        )
        return {
            "message": "Payment already recorded; renewal activated",
            "payment_id": existing_paid.get("id"),
            "enrollment_id": enrollment_id,
            "is_renewal": True,
            "new_end_date": renewal_meta.get("end_date") or enrollment.get("end_date"),
            "prior_end_date": renewal_meta.get("prior_end_date"),
        }

    payment_id = str(uuid.uuid4())
    payment_doc = {
        "id": payment_id,
        "student_id": student_id,
        "enrollment_id": enrollment_id,
        "course_id": enrollment.get("course_id"),
        "branch_id": enrollment.get("branch_id"),
        "amount": expected_total_inr,
        "course_fee": float(enrollment.get("fee_amount") or 0),
        "admission_fee": float(enrollment.get("admission_fee") or 0),
        "arrear_amount": float(enrollment.get("arrear_amount") or 0),
        "payment_status": PaymentStatus.PAID.value,
        "status": "paid",
        "payment_method": pm_value,
        "payment_type": "course_fee",
        "is_renewal": True,
        "renewed_from_enrollment_id": enrollment.get("renewed_from_enrollment_id"),
        "transaction_id": razorpay_payment_id,
        "razorpay_payment_id": razorpay_payment_id,
        "razorpay_order_id": razorpay_order_id,
        "gateway_method": gw_raw,
        "gateway_payment_label": gw_label,
        "payment_date": now,
        "due_date": now,
        "created_at": now,
        "updated_at": now,
        "created_by": actor,
        "notes": "Admin renewal paid via Razorpay",
        "currency": "INR",
    }
    await db.payments.insert_one(payment_doc)

    # Cancel leftover pending payment attempts for this enrollment
    await db.payments.update_many(
        {
            "student_id": student_id,
            "enrollment_id": enrollment_id,
            "payment_status": {"$in": [PaymentStatus.PENDING.value, "processing", "failed"]},
            "id": {"$ne": payment_id},
        },
        {
            "$set": {
                "payment_status": "cancelled",
                "status": "cancelled",
                "updated_at": now,
                "notes": "Auto-cancelled after admin renewal Razorpay verification",
            }
        },
    )

    renewal_meta = await finalize_renewal_after_payment(
        db, enrollment=enrollment, payment_doc=payment_doc, now=now
    )

    history_id = renewal_meta.get("history_id")
    enrich = {
        "renewed_by": actor,
        "payment_status": "paid",
        "payment_method": "razorpay",
        "previous_duration_id": enrollment.get("renewal_prior_duration_id"),
        "renewed_duration_id": enrollment.get("duration_id"),
        "renewed_duration_months": enrollment.get("duration_months"),
        "new_start_date": enrollment.get("start_date"),
        "course_id": enrollment.get("course_id"),
        "branch_id": enrollment.get("branch_id"),
    }
    if history_id:
        try:
            await db.renewal_history.update_one({"id": history_id}, {"$set": enrich})
            source_id = enrollment.get("renewed_from_enrollment_id")
            if source_id:
                await db.enrollments.update_one(
                    {"id": source_id, "renewal_history.id": history_id},
                    {"$set": {f"renewal_history.$.{k}": v for k, v in enrich.items()}},
                )
        except Exception:
            logger.exception("Failed to enrich admin Razorpay renewal history")

    try:
        from utils.invoice_service import safe_generate_invoice_for_payment

        inv_doc = await safe_generate_invoice_for_payment(
            payment_doc=payment_doc, source_hint="renewal"
        )
        if isinstance(inv_doc, dict) and inv_doc.get("id"):
            await db.payments.update_one(
                {"id": payment_id},
                {
                    "$set": {
                        "invoice_id": inv_doc.get("id"),
                        "invoice_number": inv_doc.get("invoice_number"),
                    }
                },
            )
    except Exception:
        logger.exception("invoice after admin razorpay renewal failed")

    try:
        from utils.billing_cycle_service import safe_upsert_billing_cycles_for_payment

        await safe_upsert_billing_cycles_for_payment(payment_doc=payment_doc)
    except Exception:
        logger.exception("billing cycle after admin razorpay renewal failed")

    try:
        from utils.ops_email_hooks import fire_payment_success

        student = await db.users.find_one({"id": student_id}) or {}
        await fire_payment_success(
            db,
            student_email=student.get("email"),
            student_name=student.get("full_name") or student.get("name") or "Student",
            amount=expected_total_inr,
            course_or_plan="Course renewal",
            txn_id=razorpay_payment_id,
            payment_date=now,
            branch_id=enrollment.get("branch_id"),
            start_date=enrollment.get("start_date"),
            student_phone=student.get("phone"),
        )
    except Exception:
        logger.exception("ops notify after admin razorpay renewal failed")

    return {
        "message": "Renewal payment verified; plan activated",
        "payment_id": payment_id,
        "enrollment_id": enrollment_id,
        "source_enrollment_id": enrollment.get("renewed_from_enrollment_id"),
        "amount": expected_total_inr,
        "is_renewal": True,
        "new_end_date": renewal_meta.get("end_date") or enrollment.get("end_date"),
        "prior_end_date": renewal_meta.get("prior_end_date"),
        "renewed_by": actor,
    }


async def admin_cancel_pending_renewal(
    *,
    enrollment_id: str,
    current_user: dict,
) -> Dict[str, Any]:
    """Cancel an unpaid admin-initiated renewal checkout (Razorpay dismiss/fail)."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    enrollment = await db.enrollments.find_one({"id": enrollment_id})
    if not enrollment:
        return {"cancelled": False, "message": "No pending renewal found"}

    await assert_admin_can_renew_enrollment(db, current_user, enrollment)

    pay_status = str(enrollment.get("payment_status") or "").lower()
    if pay_status in {"paid", "completed", "success"}:
        return {"cancelled": False, "message": "Enrollment is already paid; not cancelled."}
    if not enrollment.get("is_renewal"):
        return {"cancelled": False, "message": "Not an admin renewal checkout."}

    now = datetime.utcnow()
    student_id = enrollment.get("student_id")
    await db.payments.update_many(
        {
            "student_id": student_id,
            "enrollment_id": enrollment_id,
            "payment_status": {"$in": ["pending", "processing", "failed"]},
        },
        {
            "$set": {
                "payment_status": "cancelled",
                "status": "cancelled",
                "updated_at": now,
                "notes": "Auto-cancelled: admin renewal Razorpay dismissed/failed",
            }
        },
    )
    delete_result = await db.enrollments.delete_one(
        {
            "id": enrollment_id,
            "is_renewal": True,
            "payment_status": {"$in": ["pending", "processing", "failed"]},
        }
    )
    return {
        "cancelled": delete_result.deleted_count > 0,
        "enrollment_id": enrollment_id,
        "message": (
            "Pending renewal checkout cancelled."
            if delete_result.deleted_count > 0
            else "Pending renewal was already cleared."
        ),
    }


async def admin_list_renewal_history(
    enrollment_id: str,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    enrollment = await db.enrollments.find_one({"id": enrollment_id})
    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")
    await assert_admin_can_renew_enrollment(db, current_user, enrollment)

    items: List[Dict[str, Any]] = []
    try:
        cursor = db.renewal_history.find(
            {
                "$or": [
                    {"enrollment_id": enrollment_id},
                    {"source_enrollment_id": enrollment_id},
                    {"renewal_enrollment_id": enrollment_id},
                ]
            }
        ).sort("renewal_date", -1)
        async for row in cursor:
            row.pop("_id", None)
            items.append(row)
    except Exception:
        logger.exception("renewal_history query failed")

    # Also gather history from chain (source + any renewals of this enrollment)
    seen = {str(i.get("id")) for i in items if isinstance(i, dict) and i.get("id")}
    related_ids = {enrollment_id}
    if enrollment.get("renewed_from_enrollment_id"):
        related_ids.add(str(enrollment["renewed_from_enrollment_id"]))
    try:
        async for enr in db.enrollments.find(
            {
                "$or": [
                    {"id": {"$in": list(related_ids)}},
                    {"renewed_from_enrollment_id": enrollment_id},
                    {"superseded_by_enrollment_id": enrollment_id},
                ]
            },
            {"renewal_history": 1},
        ):
            for h in enr.get("renewal_history") or []:
                if isinstance(h, dict) and str(h.get("id")) not in seen:
                    seen.add(str(h.get("id")))
                    items.append(h)
    except Exception:
        logger.exception("embedded renewal_history gather failed")

    items.sort(
        key=lambda x: str(x.get("created_at") or x.get("renewal_date") or ""),
        reverse=True,
    )
    return {"enrollment_id": enrollment_id, "history": items, "count": len(items)}
