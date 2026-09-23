from fastapi import HTTPException, Depends, status
from datetime import datetime, timedelta
import uuid
import secrets
import csv
import io
import os
import logging
import requests
from typing import Optional, Tuple, Dict, List, Any

logger = logging.getLogger(__name__)

from models.payment_models import (
    PaymentStatus,
    PaymentType,
    PaymentMethod,
    Payment,
    RegistrationPaymentCreate,
    RegistrationPaymentResponse,
    AdminPaymentRecoveryBody,
    FamilyStudentEnrollment,
)
from models.enrollment_models import Enrollment as EnrollmentModel, PaymentStatus as EnrollmentPaymentStatus
from models.student_models import (
    StudentPaymentCreate,
    CoursePaymentInfo,
    ConfirmRazorpayPayment,
    PrepareStudentCheckoutBody,
    CreateStudentRazorpayOrderBody,
    RenewalQuoteBody,
    PrepareRenewalCheckoutBody,
)
from models.user_models import UserRole, UserCreate
from models.notification_models import PaymentNotification, PaymentNotificationCreate
from utils.auth import require_role
from utils.database import get_db
from utils.helpers import send_whatsapp
from utils.enrollment_dates import resolve_enrollment_end_date, enrollment_subscription_end_after_payment
from utils.subscription_dates import is_subscription_period_over, subscription_end_of_day_utc
from controllers.settings_controller import SettingsController
from utils.razorpay_client import get_razorpay_client, verify_razorpay_signature
from utils.razorpay_reconciliation import (
    ensure_collections_indexes,
    fetch_order,
    get_last_reconciled_at,
    reconcile_payments_batch,
    reconcile_one_payment_row,
    normalize_gateway_fields,
    map_gateway_to_internal_status,
)
from utils.admission_fee_rules import should_charge_admission_fee_for_checkout
from utils.student_branch_sync import get_student_assigned_branch_id
from utils.branch_courses import assert_course_available_at_branch
from controllers.course_controller import _expand_branch_prices_to_branch_pricing


async def _enforce_student_assigned_branch(
    db,
    student_id: str,
    requested_branch_id: str,
    current_user: dict,
) -> None:
    """Existing students must checkout at their assigned branch (renewal or new course)."""
    assigned = await get_student_assigned_branch_id(db, student_id)
    if not assigned or str(requested_branch_id or "") == assigned:
        return
    user_role = str(current_user.get("role") or "").lower()
    if user_role in ("super_admin", "superadmin"):
        return
    raise HTTPException(
        status_code=400,
        detail=(
            "You cannot change the branch when renewing or enrolling in a course. "
            "Please use your assigned branch or contact admin for a branch transfer."
        ),
    )


def _course_fee_per_duration_map(course: dict) -> dict:
    """Course-level tenure fees (top-level or nested under pricing)."""
    fpd = course.get("fee_per_duration") or {}
    if isinstance(fpd, dict) and fpd:
        return fpd
    pricing = course.get("pricing") or {}
    if isinstance(pricing, dict):
        nested = pricing.get("fee_per_duration") or {}
        if isinstance(nested, dict):
            return nested
    return {}


def _course_branch_pricing_map(course: dict) -> dict:
    """Merged branch_id -> duration fees from branch_pricing and pricing.branch_prices."""
    out: dict = {}
    raw = course.get("branch_pricing") or {}
    if isinstance(raw, dict):
        out.update(raw)
    pricing = course.get("pricing") or {}
    if isinstance(pricing, dict):
        branch_prices = pricing.get("branch_prices") or []
        expanded = _expand_branch_prices_to_branch_pricing(branch_prices)
        for bid, val in expanded.items():
            if bid not in out:
                out[bid] = val
            elif isinstance(out.get(bid), dict) and isinstance(val, dict):
                merged = dict(out[bid])
                merged.update(val)
                out[bid] = merged
    return out


def _enrollment_checkout_total_inr(enrollment: dict) -> float:
    fee = float(enrollment.get("fee_amount") or 0)
    adm = float(enrollment.get("admission_fee") or 0)
    arrear = float(enrollment.get("arrear_amount") or 0)
    return fee + adm + arrear


def _duration_price_keys(duration: str, duration_info: Optional[dict]) -> list:
    """Keys to match against fee_per_duration / branch_pricing maps (id, code, or raw query)."""
    keys: list = []
    if duration:
        keys.append(duration)
    if duration_info:
        for k in ("id", "code"):
            v = duration_info.get(k)
            if v is not None and str(v) not in keys:
                keys.append(str(v))
    return keys


def _batches_for_course_on_branch(branch: dict, course_id: str) -> list:
    sched = (branch.get("assignments") or {}).get("course_schedule") or []
    for entry in sched:
        cid = str(entry.get("course_id") or entry.get("courseId") or "")
        if cid == str(course_id):
            return list(entry.get("batches") or [])
    return []


def _batch_identity(batch: dict, index: int) -> str:
    bid = str((batch.get("batch_id") or batch.get("id") or "")).strip()
    return bid or f"__index:{index}__"


def _resolve_branch_batch(batches: list, batch_ref: Optional[str]):
    """Match persisted batch_id/id or synthetic __index:n__ from public API."""
    if not batch_ref or not str(batch_ref).strip():
        return None
    s = str(batch_ref).strip()
    if s.startswith("__index:") and s.endswith("__"):
        inner = s[8:-2]
        try:
            idx = int(inner)
            if 0 <= idx < len(batches):
                return batches[idx]
        except ValueError:
            return None
        return None
    for b in batches:
        bid = str((b.get("batch_id") or b.get("id") or "")).strip()
        if bid == s:
            return b
    return None


def _fee_from_batch_for_keys(batch: Optional[dict], dur_keys: list) -> Optional[float]:
    """Current admin batch fee for a tenure: fee_per_duration first, else legacy batch_fee."""
    if not batch:
        return None
    fpd = batch.get("fee_per_duration") or {}
    if isinstance(fpd, dict):
        for dk in dur_keys or []:
            if dk in fpd and fpd[dk] is not None:
                try:
                    return float(fpd[dk])
                except (TypeError, ValueError):
                    pass
    return _batch_fee_from_doc(batch)


async def _latest_student_batch_ref(
    db,
    student_id: Optional[str],
    course_id: str,
    branch_id: str,
) -> Optional[str]:
    """Most recent enrollment batch for this student/course/branch (paid first)."""
    if not student_id:
        return None
    rows = await db.enrollments.find(
        {
            "student_id": student_id,
            "course_id": course_id,
            "branch_id": branch_id,
            "batch_ref": {"$exists": True, "$nin": [None, ""]},
        },
        {"batch_ref": 1, "end_date": 1, "created_at": 1, "payment_status": 1},
    ).sort([("end_date", -1), ("created_at", -1)]).to_list(length=40)
    paid = []
    other = []
    for row in rows:
        ref = str(row.get("batch_ref") or "").strip()
        if not ref:
            continue
        status = str(row.get("payment_status") or "").lower()
        if status in (EnrollmentPaymentStatus.PAID.value, "completed", "paid"):
            paid.append(ref)
        else:
            other.append(ref)
    return (paid or other or [None])[0]


async def _latest_active_paid_enrollment(db, student_id: str, course_id: str):
    """Latest still-active paid enrollment for this course (by end_date, not arbitrary find_one)."""
    return await db.enrollments.find_one(
        {
            "student_id": student_id,
            "course_id": course_id,
            "is_active": True,
            "payment_status": {"$in": [EnrollmentPaymentStatus.PAID.value, "completed", "paid"]},
        },
        sort=[("end_date", -1), ("created_at", -1)],
    )


async def resolve_checkout_batch_ref(
    db,
    *,
    course_id: str,
    branch_id: str,
    batches: list,
    requested_batch_ref: Optional[str] = None,
    student_id: Optional[str] = None,
    duration_keys: Optional[list] = None,
) -> Optional[str]:
    """
    Source of truth for renewal/checkout is the branch course_schedule batch fee
    set in super admin — never leftover course-level catalog amounts (e.g. ₹1500).
    """
    requested = (requested_batch_ref or "").strip()
    if requested:
        if not batches or _resolve_branch_batch(batches, requested) is not None:
            return requested
    if student_id:
        prior = await _latest_student_batch_ref(db, student_id, course_id, branch_id)
        if prior and (not batches or _resolve_branch_batch(batches, prior) is not None):
            return prior
    if len(batches) == 1:
        return _batch_identity(batches[0], 0)
    keys = duration_keys or []
    priced_refs: list = []
    priced_fees: list = []
    for i, batch in enumerate(batches):
        fee = _fee_from_batch_for_keys(batch, keys)
        if fee is None:
            continue
        priced_refs.append(_batch_identity(batch, i))
        priced_fees.append(fee)
    if len(priced_fees) == 1:
        return priced_refs[0]
    if len(priced_fees) > 1 and len(set(priced_fees)) == 1:
        return priced_refs[0]
    return None


def _batch_fee_from_doc(batch_doc: Optional[dict]) -> Optional[float]:
    if not batch_doc:
        return None
    raw = batch_doc.get("batch_fee")
    if raw is None:
        raw = batch_doc.get("batchFee")
    if raw is None:
        raw = batch_doc.get("fee")
    if raw is None:
        raw = batch_doc.get("price")
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v < 0:
        return None
    return v


def _validate_razorpay_captured_amount(
    *,
    enrollment_expected_paise: int,
    order_id: Optional[str],
    payment_entity: dict,
    enrollment_id: str,
) -> None:
    """
    Ensure a captured Razorpay payment matches the enrollment checkout order.

    When convenience/gateway fees are passed to the customer, payment.amount exceeds
    order.amount; compare against the Razorpay order amount instead of payment.amount.
    """
    pay_amount = payment_entity.get("amount")
    if pay_amount is None:
        return

    pay_paise = int(pay_amount)
    order_paise = enrollment_expected_paise
    if order_id:
        pay_order_id = str(payment_entity.get("order_id") or "").strip()
        if pay_order_id and pay_order_id != order_id:
            logger.error(
                "Razorpay payment order mismatch enrollment=%s expected_order=%s payment_order=%s",
                enrollment_id,
                order_id,
                pay_order_id,
            )
            raise HTTPException(
                status_code=400,
                detail="Payment does not match this checkout order. Please contact support.",
            )
        rz_order = fetch_order(order_id)
        if rz_order and rz_order.get("amount") is not None:
            order_paise = int(rz_order["amount"])

    if order_paise != enrollment_expected_paise:
        logger.error(
            "Razorpay order amount mismatch enrollment=%s expected_paise=%s order_amount=%s order_id=%s",
            enrollment_id,
            enrollment_expected_paise,
            order_paise,
            order_id,
        )
        raise HTTPException(
            status_code=400,
            detail="Payment amount does not match enrollment. Please contact support.",
        )

    if pay_paise < order_paise:
        logger.error(
            "Razorpay underpayment enrollment=%s order_paise=%s payment_amount=%s",
            enrollment_id,
            order_paise,
            pay_paise,
        )
        raise HTTPException(
            status_code=400,
            detail="Payment amount is less than the order total. Please contact support.",
        )


def _fetch_razorpay_payment_entity(payment_id: str) -> Optional[dict]:
    """Fetch payment entity from Razorpay REST API (method, card network, etc.)."""
    key = (os.getenv("RAZORPAY_KEY_ID") or "").strip()
    secret = (os.getenv("RAZORPAY_KEY_SECRET") or "").strip()
    if not key or not secret or not payment_id:
        return None
    url = f"https://api.razorpay.com/v1/payments/{payment_id}"
    sess = requests.Session()
    sess.trust_env = False
    try:
        r = sess.get(url, auth=(key, secret), timeout=25)
        if r.status_code == 200:
            return r.json()
        logger.warning("Razorpay payment fetch HTTP %s for %s", r.status_code, payment_id)
    except Exception:
        logger.exception("Razorpay payment fetch failed for %s", payment_id)
    return None


def _razorpay_method_to_payment_fields(entity: dict) -> Tuple[str, str, str]:
    """
    Map Razorpay payment entity to (payment_method enum, gateway_method raw, human label).
    """
    method = (entity.get("method") or "").lower()
    if method == "upi":
        vpa = entity.get("vpa") or entity.get("email")
        label = "UPI"
        if vpa:
            label = f"UPI ({vpa})"
        return PaymentMethod.UPI.value, "upi", label
    if method == "card":
        card = entity.get("card") or {}
        network = (card.get("network") or "").upper()
        last4 = card.get("last4") or ""
        ctype = (card.get("type") or "").lower()
        if ctype in ("debit", "credit"):
            pm = PaymentMethod.DEBIT_CARD.value if ctype == "debit" else PaymentMethod.CREDIT_CARD.value
        else:
            pm = PaymentMethod.DEBIT_CARD.value
        parts = [p for p in [network or None, f"••••{last4}" if last4 else None] if p]
        label = f"Card ({' '.join(parts)})" if parts else "Card"
        return pm, "card", label
    if method == "netbanking":
        bank = (entity.get("bank") or "") or ""
        label = f"Net Banking ({bank})" if bank else "Net Banking"
        return PaymentMethod.NET_BANKING.value, "netbanking", label
    if method == "wallet":
        wname = (entity.get("wallet") or "") or ""
        label = f"Wallet ({wname})" if wname else "Wallet"
        return PaymentMethod.DIGITAL_WALLET.value, "wallet", label
    if method == "emi":
        return PaymentMethod.CREDIT_CARD.value, "emi", "EMI"
    label = (method or "Razorpay").replace("_", " ").title()
    return PaymentMethod.DIGITAL_WALLET.value, method or "unknown", label


class PaymentController:
    @staticmethod
    def _safe_webhook_event_id(payload: dict) -> str:
        """
        Create a stable idempotency key for webhook events.
        Razorpay does not guarantee a global event id in all payloads, so derive one.
        """
        try:
            event = str(payload.get("event") or "").strip()
            created_at = str(payload.get("created_at") or payload.get("createdAt") or "").strip()
            ent = (((payload.get("payload") or {}).get("payment") or {}).get("entity") or {})
            pay_id = str(ent.get("id") or "").strip()
            order_id = str(ent.get("order_id") or "").strip()
            if pay_id:
                return f"rzp:{event}:{pay_id}:{created_at}"
            if order_id:
                return f"rzp:{event}:{order_id}:{created_at}"
            return f"rzp:{event}:{created_at}:{uuid.uuid4()}"
        except Exception:
            return f"rzp:unknown:{uuid.uuid4()}"

    @staticmethod
    async def process_razorpay_webhook(payload: dict, headers: Optional[dict] = None):
        """
        Idempotent webhook processor. Does NOT replace the existing frontend confirm flow;
        it only makes the system resilient when callbacks fail.
        """
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        await ensure_collections_indexes(db)

        event_id = PaymentController._safe_webhook_event_id(payload or {})
        now = datetime.utcnow()

        # Store webhook payload log (duplicate-safe).
        try:
            ins = await db.razorpay_webhook_events.update_one(
                {"event_id": event_id},
                {
                    "$setOnInsert": {
                        "event_id": event_id,
                        "created_at": now,
                        "event": payload.get("event"),
                        "payload": payload,
                        "headers": headers or {},
                    }
                },
                upsert=True,
            )
            if not ins.upserted_id and ins.matched_count:
                # Already processed/logged; still attempt reconcile as retry-safe (idempotent updates).
                pass
        except Exception:
            logger.exception("Failed to persist webhook event log event_id=%s", event_id)

        # Extract Razorpay entity (payment/order/refund events).
        pld = payload.get("payload") if isinstance(payload, dict) else {}
        payment_entity = None
        if isinstance(pld, dict):
            payment_entity = ((pld.get("payment") or {}).get("entity")) if isinstance(pld.get("payment"), dict) else None
        if not isinstance(payment_entity, dict):
            payment_entity = None

        refund_entity = None
        if isinstance(pld, dict):
            refund_entity = ((pld.get("refund") or {}).get("entity")) if isinstance(pld.get("refund"), dict) else None
        if not isinstance(refund_entity, dict):
            refund_entity = None

        # Use payment entity as main reconciliation input when present.
        razorpay_payment_id = None
        razorpay_order_id = None
        if payment_entity:
            razorpay_payment_id = str(payment_entity.get("id") or "").strip() or None
            razorpay_order_id = str(payment_entity.get("order_id") or "").strip() or None
        elif refund_entity:
            razorpay_payment_id = str(refund_entity.get("payment_id") or "").strip() or None

        # Find matching local payment rows.
        candidates = []
        try:
            q = {"$or": []}
            if razorpay_payment_id:
                q["$or"].append({"razorpay_payment_id": razorpay_payment_id})
                q["$or"].append({"transaction_id": razorpay_payment_id})
            if razorpay_order_id:
                q["$or"].append({"razorpay_order_id": razorpay_order_id})
            if not q["$or"]:
                q = None
            if q:
                candidates = await db.payments.find(q).sort("created_at", -1).to_list(length=10)
        except Exception:
            logger.exception("Webhook candidate lookup failed event_id=%s", event_id)
            candidates = []

        updated = 0
        for row in candidates:
            try:
                # If webhook includes entity, reconcile using it directly (avoid extra Razorpay fetch).
                if payment_entity:
                    norm = normalize_gateway_fields(payment_entity)
                    mapped = map_gateway_to_internal_status(
                        str(norm.get("razorpay_status") or ""),
                        norm.get("razorpay_captured"),
                        norm.get("razorpay_refunded_amount_paise"),
                        norm.get("razorpay_amount_paise"),
                    )
                    patch = {
                        **norm,
                        "payment_status": mapped["payment_status"],
                        "refund_status": mapped["refund_status"],
                        "status": "success" if mapped["payment_status"] == "paid" else (mapped["payment_status"] or row.get("status")),
                        "reconciled_at": now,
                        "reconciled_by": "razorpay_webhook",
                        "reconciliation_reason": f"webhook:{payload.get('event') or 'unknown'}",
                        "updated_at": now,
                    }
                    res = await db.payments.update_one({"_id": row["_id"]}, {"$set": patch})
                    if res.modified_count:
                        updated += 1
                    # Ensure enrollment is active/paid on success (subscription auto repair).
                    if mapped["payment_status"] == "paid" and (row.get("enrollment_id") or patch.get("enrollment_id")):
                        eid = patch.get("enrollment_id") or row.get("enrollment_id")
                        try:
                            from utils.renewal_service import finalize_renewal_after_payment

                            enr = await db.enrollments.find_one({"id": eid})
                            if enr:
                                merged_pay = {**row, **patch}
                                await finalize_renewal_after_payment(
                                    db,
                                    enrollment=enr,
                                    payment_doc=merged_pay,
                                    now=now,
                                )
                            else:
                                await db.enrollments.update_one(
                                    {"id": eid},
                                    {
                                        "$set": {"payment_status": "paid", "is_active": True, "updated_at": now},
                                        "$unset": {"status": ""},
                                    },
                                )
                        except Exception:
                            logger.exception("Webhook renewal finalize failed enrollment_id=%s", eid)
                            await db.enrollments.update_one(
                                {"id": eid},
                                {
                                    "$set": {"payment_status": "paid", "is_active": True, "updated_at": now},
                                    "$unset": {"status": ""},
                                },
                            )
                        await db.payments.update_many(
                            {
                                "enrollment_id": eid,
                                "payment_status": {"$in": ["pending", "processing", "failed"]},
                                "_id": {"$ne": row["_id"]},
                            },
                            {"$set": {"payment_status": "cancelled", "status": "cancelled", "updated_at": now}},
                        )
                    # M05-S05: cart checkout fulfillment (idempotent; safe on webhook retries)
                    if mapped["payment_status"] == "paid" and (
                        row.get("cart_checkout_id") or patch.get("cart_checkout_id")
                    ):
                        try:
                            from utils.cart_fulfillment import fulfill_from_payment_row

                            merged = {**row, **patch}
                            await fulfill_from_payment_row(merged, actor="razorpay_webhook")
                        except Exception:
                            logger.exception(
                                "Cart checkout fulfillment failed payment_id=%s",
                                row.get("id"),
                            )
                    # M07-S01: invoice for non-cart (and cart via fulfill hook) payments
                    if mapped["payment_status"] == "paid":
                        try:
                            from utils.invoice_service import safe_generate_invoice_for_payment

                            merged = {**row, **patch}
                            await safe_generate_invoice_for_payment(payment_doc=merged)
                        except Exception:
                            logger.exception(
                                "Invoice generation failed on webhook payment_id=%s",
                                row.get("id"),
                            )
                        try:
                            from utils.billing_cycle_service import safe_upsert_billing_cycles_for_payment

                            merged = {**row, **patch}
                            await safe_upsert_billing_cycles_for_payment(payment_doc=merged)
                        except Exception:
                            logger.exception(
                                "Billing cycle upsert failed on webhook payment_id=%s",
                                row.get("id"),
                            )
                else:
                    out = await reconcile_one_payment_row(
                        db,
                        row,
                        actor="razorpay_webhook",
                        reason=f"webhook:{payload.get('event') or 'unknown'}",
                    )
                    if out.get("updated"):
                        updated += 1
            except Exception:
                logger.exception("Webhook reconcile failed event_id=%s payment_row=%s", event_id, row.get("id"))

        # Update last sync marker even if no candidates matched (so ops can see webhook activity).
        try:
            from utils.razorpay_reconciliation import set_last_reconciled_at

            await set_last_reconciled_at(db, now)
        except Exception:
            pass

        return {"ok": True, "event_id": event_id, "matched_rows": len(candidates), "updated_rows": updated}

    @staticmethod
    async def sync_razorpay_payments(current_user: dict):
        """Admin-triggered reconciliation run (safe, idempotent)."""
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        await ensure_collections_indexes(db)

        actor = str(current_user.get("id") or current_user.get("user_id") or "super_admin")
        out = await reconcile_payments_batch(
            db,
            actor=actor,
            reason="admin_manual_sync",
            lookback_days=30,
            pending_stuck_minutes=5,
            limit=300,
        )
        # Return strictly JSON-serializable output (avoid leaking any BSON types).
        last = await get_last_reconciled_at(db)
        return {
            "ok": True,
            "checked": int(out.get("checked") or 0),
            "updated": int(out.get("updated") or 0),
            "last_razorpay_sync_at": last.isoformat() if last else None,
        }

    @staticmethod
    async def sync_one_razorpay_payment(current_user: dict, *, payment_id: Optional[str] = None, order_id: Optional[str] = None):
        """
        Reconcile a single Razorpay payment/order against local DB.
        Useful for immediate manual repair (e.g., a specific student case).
        """
        if not payment_id and not order_id:
            raise HTTPException(status_code=400, detail="Provide payment_id or order_id")

        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        await ensure_collections_indexes(db)

        q = {"$or": []}
        if payment_id:
            q["$or"].append({"razorpay_payment_id": payment_id})
            q["$or"].append({"transaction_id": payment_id})
        if order_id:
            q["$or"].append({"razorpay_order_id": order_id})
        if not q["$or"]:
            raise HTTPException(status_code=400, detail="Invalid query")

        rows = await db.payments.find(q).sort("created_at", -1).limit(5).to_list(length=5)
        actor = str(current_user.get("id") or current_user.get("user_id") or "super_admin")

        checked = 0
        updated = 0
        for row in rows:
            checked += 1
            out = await reconcile_one_payment_row(
                db,
                row,
                actor=actor,
                reason="admin_single_sync",
            )
            if out.get("updated"):
                updated += 1

        last = await get_last_reconciled_at(db)
        return {
            "ok": True,
            "matched_rows": len(rows),
            "checked": checked,
            "updated": updated,
            "last_razorpay_sync_at": last.isoformat() if last else None,
        }

    @staticmethod
    def _payment_status_rank(status: Optional[str]) -> int:
        s = str(status or "").strip().lower()
        order = {
            "paid": 5,
            "completed": 5,
            "processing": 4,
            "pending": 3,
            "overdue": 2,
            "failed": 1,
            "cancelled": 0,
            "canceled": 0,
        }
        return order.get(s, -1)

    @staticmethod
    def _payment_dedup_key(payment: dict) -> str:
        """One logical checkout/payment record key.

        Repeated retries often create multiple pending rows with different order ids.
        Prefer enrollment-level grouping when transaction id is not available yet.
        """
        tx_id = str(payment.get("transaction_id") or "").strip()
        if tx_id:
            return f"txn:{tx_id}"

        enrollment_id = str(payment.get("enrollment_id") or "").strip()
        if enrollment_id:
            return f"enr:{enrollment_id}"

        order_id = str(payment.get("razorpay_order_id") or "").strip()
        if order_id:
            return f"order:{order_id}"

        payment_id = str(payment.get("id") or "").strip()
        return f"id:{payment_id}"

    @staticmethod
    def _pick_better_payment(existing: dict, candidate: dict) -> dict:
        """Pick the most meaningful row for a dedupe key."""
        existing_rank = PaymentController._payment_status_rank(existing.get("payment_status"))
        candidate_rank = PaymentController._payment_status_rank(candidate.get("payment_status"))
        if candidate_rank != existing_rank:
            return candidate if candidate_rank > existing_rank else existing

        # Prefer row with real transaction id
        existing_has_tx = bool(str(existing.get("transaction_id") or "").strip())
        candidate_has_tx = bool(str(candidate.get("transaction_id") or "").strip())
        if candidate_has_tx != existing_has_tx:
            return candidate if candidate_has_tx else existing

        # Then keep latest updated/created timestamp
        existing_ts = existing.get("updated_at") or existing.get("created_at")
        candidate_ts = candidate.get("updated_at") or candidate.get("created_at")
        if existing_ts is None:
            return candidate
        if candidate_ts is None:
            return existing
        return candidate if candidate_ts > existing_ts else existing

    @staticmethod
    async def admin_sync_enrollment_payment_status(current_user: dict):
        """
        One-time/maintenance repair:
        1) backfill payments.enrollment_id from razorpay_order_id when missing
        2) mark enrollments paid where latest linked payment succeeded
        """
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        actor_id = current_user.get("id") if isinstance(current_user, dict) else None
        now = datetime.utcnow()
        repaired_payment_links = 0
        updated_enrollments = 0

        # 1) Backfill missing enrollment link by order_id -> enrollment.razorpay_last_order_id
        missing_filter = {
            "$or": [{"enrollment_id": {"$exists": False}}, {"enrollment_id": None}, {"enrollment_id": ""}],
            "razorpay_order_id": {"$exists": True, "$ne": None},
        }
        async for p in db.payments.find(missing_filter):
            student_id = p.get("student_id") or p.get("user_id")
            order_id = p.get("razorpay_order_id")
            if not student_id or not order_id:
                continue
            enrollment = await db.enrollments.find_one(
                {"student_id": student_id, "razorpay_last_order_id": order_id},
                {"id": 1, "course_id": 1},
            )
            if not enrollment:
                continue
            upd = await db.payments.update_one(
                {"_id": p["_id"]},
                {
                    "$set": {
                        "enrollment_id": enrollment["id"],
                        "course_id": enrollment.get("course_id"),
                        "updated_at": now,
                    }
                },
            )
            if upd.modified_count:
                repaired_payment_links += 1

        # 2) Latest successful payment per enrollment -> enrollment.payment_status='paid'
        pipeline = [
            {
                "$match": {
                    "enrollment_id": {"$exists": True, "$ne": None, "$ne": ""},
                    "$or": [{"payment_status": PaymentStatus.PAID.value}, {"status": "success"}],
                }
            },
            {"$sort": {"created_at": -1}},
            {"$group": {"_id": "$enrollment_id"}},
        ]
        rows = await db.payments.aggregate(pipeline).to_list(length=None)
        for row in rows:
            enrollment_id = row.get("_id")
            if not enrollment_id:
                continue
            upd = await db.enrollments.update_one(
                {"id": enrollment_id},
                {"$set": {"payment_status": EnrollmentPaymentStatus.PAID.value, "updated_at": now}},
            )
            if upd.modified_count:
                updated_enrollments += 1

        return {
            "message": "Enrollment/payment sync completed",
            "repaired_payment_links": repaired_payment_links,
            "updated_enrollments": updated_enrollments,
            "performed_by": actor_id,
            "performed_at": now.isoformat(),
        }

    
    @staticmethod
    async def quote_student_course_checkout(
        body: PrepareStudentCheckoutBody,
        current_user: dict,
    ):
        """Return student checkout amount with admission-fee rules, without creating enrollment."""
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        if current_user.get("role") != "student":
            raise HTTPException(status_code=403, detail="Only students can use this endpoint")

        student_id = current_user["id"]
        await _enforce_student_assigned_branch(db, student_id, body.branch_id, current_user)
        branch = await db.branches.find_one({"id": body.branch_id})
        if not branch:
            raise HTTPException(status_code=404, detail="Branch not found")
        duration_info = await db.durations.find_one({"id": body.duration})
        if not duration_info:
            duration_info = await db.durations.find_one({"code": body.duration})
        batches = _batches_for_course_on_branch(branch, body.course_id)
        br = await resolve_checkout_batch_ref(
            db,
            course_id=body.course_id,
            branch_id=body.branch_id,
            batches=batches,
            requested_batch_ref=body.batch_ref,
            student_id=student_id,
            duration_keys=_duration_price_keys(body.duration, duration_info),
        )
        beneficiary_payload = body.beneficiary.dict() if body.beneficiary else {"beneficiary_type": "self"}
        should_charge_admission = await should_charge_admission_fee_for_checkout(
            db,
            student_id=student_id,
            beneficiary=beneficiary_payload,
        )
        info = await PaymentController.get_course_payment_info(
            body.course_id,
            body.branch_id,
            body.duration,
            batch_ref=br,
            optional_student_id=student_id,
            admission_fee_beneficiary=beneficiary_payload,
        )
        course_fee = float(info.pricing.course_fee)
        effective_admission_fee = float(info.pricing.admission_fee)
        total_amount = float(info.pricing.total_amount)

        return {
            "course_id": body.course_id,
            "branch_id": body.branch_id,
            "duration": body.duration,
            "batch_ref": br,
            "course_name": info.course_name,
            "branch_name": info.branch_name,
            "pricing": {
                "course_fee": course_fee,
                "admission_fee": effective_admission_fee,
                "total_amount": total_amount,
                "currency": "INR",
            },
            "admission_fee_applied": should_charge_admission,
        }

    @staticmethod
    async def quote_student_renewal(
        body: RenewalQuoteBody,
        current_user: dict,
    ):
        """
        M07-S03 renewal quote:
        - normal renewal course fee (admission usually 0 after first paid enrollment)
        - overdue days + grace window
        - arrear component (0 until client formula confirmed)
        - final payable amount
        """
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        if current_user.get("role") != "student":
            raise HTTPException(status_code=403, detail="Only students can use this endpoint")

        from utils.billing_state import calculate_arrear_amount, compute_enrollment_billing_state
        from utils.renewal_service import package_quote_response

        student_id = current_user["id"]
        enrollment = await db.enrollments.find_one(
            {"id": body.enrollment_id, "student_id": student_id}
        )
        if not enrollment:
            raise HTTPException(status_code=404, detail="Enrollment not found or does not belong to you.")

        course_id = enrollment.get("course_id")
        branch_id = enrollment.get("branch_id")
        if not course_id or not branch_id:
            raise HTTPException(status_code=400, detail="Enrollment is missing course or branch.")

        await _enforce_student_assigned_branch(db, student_id, branch_id, current_user)

        duration = (body.duration or enrollment.get("duration_id") or "").strip()
        if not duration:
            raise HTTPException(
                status_code=400,
                detail="Duration is required for renewal quote.",
            )

        br = (body.batch_ref or enrollment.get("batch_ref") or "").strip() or None
        beneficiary_payload = (
            body.beneficiary.dict()
            if body.beneficiary
            else (enrollment.get("beneficiary") or {"beneficiary_type": "self"})
        )
        if not isinstance(beneficiary_payload, dict):
            beneficiary_payload = {"beneficiary_type": "self"}

        should_charge_admission = await should_charge_admission_fee_for_checkout(
            db,
            student_id=student_id,
            beneficiary=beneficiary_payload,
        )
        info = await PaymentController.get_course_payment_info(
            course_id,
            branch_id,
            duration,
            batch_ref=br,
            optional_student_id=student_id,
            admission_fee_beneficiary=beneficiary_payload,
        )
        course_fee = float(info.pricing.course_fee)
        admission_fee = float(info.pricing.admission_fee) if should_charge_admission else 0.0

        billing = compute_enrollment_billing_state(enrollment.get("end_date"))
        duration_months = 1
        try:
            dur_row = await db.durations.find_one({"id": duration})
            if not dur_row:
                dur_row = await db.durations.find_one({"code": duration})
            if dur_row and dur_row.get("duration_months") is not None:
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

            cycle = await get_active_cycle_for_enrollment(body.enrollment_id)
        except Exception:
            cycle = None

        return package_quote_response(
            enrollment_id=body.enrollment_id,
            course_id=course_id,
            branch_id=branch_id,
            duration=duration,
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

    @staticmethod
    async def prepare_student_renewal_checkout(
        body: PrepareRenewalCheckoutBody,
        current_user: dict,
    ):
        """
        M07-S04: create a pending renewal enrollment from an existing enrollment + quote.
        Reuses shared M06 Razorpay order/confirm afterward.
        """
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        if current_user.get("role") != "student":
            raise HTTPException(status_code=403, detail="Only students can use this endpoint")

        from utils.billing_state import calculate_arrear_amount
        from utils.renewal_service import compute_renewal_validity_start

        student_id = current_user["id"]
        source = await db.enrollments.find_one({"id": body.enrollment_id, "student_id": student_id})
        if not source:
            raise HTTPException(status_code=404, detail="Enrollment not found or does not belong to you.")

        course_id = source.get("course_id")
        branch_id = source.get("branch_id")
        if not course_id or not branch_id:
            raise HTTPException(status_code=400, detail="Enrollment is missing course or branch.")

        await _enforce_student_assigned_branch(db, student_id, branch_id, current_user)

        duration = (body.duration or source.get("duration_id") or "").strip()
        if not duration:
            raise HTTPException(status_code=400, detail="Duration is required for renewal.")

        # Cancel stale renewal pending checkouts for this course (do not touch paid source).
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
                    "payment_status": {"$in": [PaymentStatus.PENDING.value, "processing", "failed"]},
                },
                {
                    "$set": {
                        "payment_status": "cancelled",
                        "status": "cancelled",
                        "updated_at": datetime.utcnow(),
                        "notes": "Auto-cancelled stale renewal checkout attempt",
                    }
                },
            )
            await db.enrollments.delete_many({"id": {"$in": stale_ids}})

        batch_ref = (body.batch_ref or source.get("batch_ref") or "").strip() or None
        beneficiary_payload = (
            body.beneficiary.dict()
            if body.beneficiary
            else (source.get("beneficiary") or {"beneficiary_type": "self"})
        )
        if not isinstance(beneficiary_payload, dict):
            beneficiary_payload = {"beneficiary_type": "self"}

        should_charge_admission = await should_charge_admission_fee_for_checkout(
            db,
            student_id=student_id,
            beneficiary=beneficiary_payload,
        )
        info = await PaymentController.get_course_payment_info(
            course_id,
            branch_id,
            duration,
            batch_ref=batch_ref,
            optional_student_id=student_id,
            admission_fee_beneficiary=beneficiary_payload,
        )

        validity = compute_renewal_validity_start(source.get("end_date"))
        start_date = validity["start_date"]
        billing = validity["billing"]

        duration_row = await db.durations.find_one({"id": duration})
        if not duration_row:
            duration_row = await db.durations.find_one({"code": duration})
        months_hint = None
        if duration_row and duration_row.get("duration_months") is not None:
            try:
                months_hint = int(duration_row["duration_months"])
            except (TypeError, ValueError):
                months_hint = None

        # Provisional end_date for display; authoritative end is recomputed after verified payment.
        end_date = await resolve_enrollment_end_date(
            db, duration, start_date, months_hint=months_hint
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

        enrollment = EnrollmentModel(
            student_id=student_id,
            course_id=course_id,
            branch_id=branch_id,
            start_date=start_date,
            end_date=end_date,
            fee_amount=course_fee,
            admission_fee=admission_fee,
            payment_status=EnrollmentPaymentStatus.PENDING,
            is_active=True,
        )
        enrollment_doc = enrollment.dict()
        enrollment_doc["duration_id"] = duration
        enrollment_doc["duration_months"] = months_hint
        enrollment_doc["enrollment_date"] = start_date
        enrollment_doc["is_renewal"] = True
        enrollment_doc["renewed_from_enrollment_id"] = source.get("id")
        enrollment_doc["renewal_prior_end_date"] = source.get("end_date")
        enrollment_doc["renewal_billing_state"] = billing.get("billing_state")
        enrollment_doc["renewal_overdue_days"] = billing.get("overdue_days")
        enrollment_doc["renewal_start_mode"] = validity.get("mode")
        enrollment_doc["arrear_amount"] = arrear_amount
        enrollment_doc["arrear_rule"] = arrear.get("arrear_rule")
        enrollment_doc["renewal_checkout_pending"] = True
        if batch_ref:
            enrollment_doc["batch_ref"] = batch_ref
        if beneficiary_payload and beneficiary_payload.get("beneficiary_type") != "self":
            enrollment_doc["beneficiary"] = beneficiary_payload
        await db.enrollments.insert_one(enrollment_doc)

        return {
            "enrollment_id": enrollment.id,
            "source_enrollment_id": source.get("id"),
            "amount": total_amount,
            "course_name": info.course_name,
            "branch_name": info.branch_name,
            "duration_months": months_hint,
            "is_renewal": True,
            "billing_state": billing.get("billing_state"),
            "overdue_days": billing.get("overdue_days"),
            "grace_days_remaining": billing.get("grace_days_remaining"),
            "prior_end_date": source.get("end_date"),
            "provisional_end_date": end_date,
            "pricing": {
                "course_fee": course_fee,
                "admission_fee": admission_fee,
                "arrear_amount": arrear_amount,
                "total_amount": total_amount,
                "currency": "INR",
            },
            "arrear": arrear,
            "message": (
                "Renewal checkout ready. Validity updates only after verified payment."
            ),
        }

    @staticmethod
    async def get_student_renewal_history(
        current_user: dict,
        enrollment_id: Optional[str] = None,
    ):
        """M07-S04: list renewal history for the student (optionally filtered by enrollment)."""
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        if current_user.get("role") != "student":
            raise HTTPException(status_code=403, detail="Only students can use this endpoint")

        student_id = current_user["id"]
        query: Dict = {"student_id": student_id}
        if enrollment_id:
            # Match either source or renewal enrollment ownership
            owned = await db.enrollments.find_one(
                {"id": enrollment_id, "student_id": student_id},
                {"id": 1},
            )
            if not owned:
                raise HTTPException(status_code=404, detail="Enrollment not found")
            query = {
                "$or": [
                    {"source_enrollment_id": enrollment_id},
                    {"renewal_enrollment_id": enrollment_id},
                    {"enrollment_id": enrollment_id},
                ]
            }

        rows = []
        try:
            cursor = db.renewal_history.find(query).sort("renewal_date", -1)
            rows = await cursor.to_list(length=100)
        except Exception:
            # Fallback: read embedded history from enrollments
            enr_q: Dict = {"student_id": student_id}
            if enrollment_id:
                enr_q["id"] = enrollment_id
            enrollments = await db.enrollments.find(
                enr_q, {"id": 1, "renewal_history": 1, "course_id": 1}
            ).to_list(length=50)
            for enr in enrollments:
                for item in enr.get("renewal_history") or []:
                    if isinstance(item, dict):
                        rows.append({**item, "enrollment_id": enr.get("id")})

        cleaned = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            item = {k: v for k, v in r.items() if k != "_id"}
            cleaned.append(item)
        cleaned.sort(key=lambda x: str(x.get("renewal_date") or ""), reverse=True)
        return {"renewals": cleaned, "total": len(cleaned)}

    @staticmethod
    async def student_process_payment(
        payment_data: StudentPaymentCreate,
        current_user: dict = Depends(require_role([UserRole.STUDENT]))
    ):
        """Allow a student to process a payment for their enrollment."""
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        student_id = current_user["id"]

        # Validate enrollment and payment
        enrollment = await db.enrollments.find_one({"id": payment_data.enrollment_id, "student_id": student_id})
        if not enrollment:
            raise HTTPException(status_code=404, detail="Enrollment not found or does not belong to you.")

        # Find the pending payment for this enrollment
        # This assumes there's a specific pending payment the student is trying to clear
        # In a real system, you might have a more complex payment reconciliation logic
        pending_payment = await db.payments.find_one({
            "enrollment_id": payment_data.enrollment_id,
            "student_id": student_id,
            "payment_status": PaymentStatus.PENDING.value,
            "amount": payment_data.amount  # Ensure the amount matches
        })

        if not pending_payment:
            raise HTTPException(status_code=400, detail="No matching pending payment found for this enrollment and amount.")

        # Simulate payment gateway interaction (update payment status)
        update_data = {
            "payment_status": PaymentStatus.PAID,
            "payment_method": payment_data.payment_method,
            "transaction_id": payment_data.transaction_id,
            "payment_date": datetime.utcnow(),
            "notes": payment_data.notes,
            "updated_at": datetime.utcnow()
        }

        result = await db.payments.update_one(
            {"id": pending_payment["id"]},
            {"$set": update_data}
        )

        if result.matched_count == 0:
            raise HTTPException(status_code=500, detail="Failed to update payment status.")

        # Update enrollment payment status if needed (e.g., if all payments are cleared)
        # This logic might need to be more sophisticated in a real app
        await db.enrollments.update_one(
            {"id": enrollment["id"]},
            {"$set": {"payment_status": PaymentStatus.PAID}}  # Simplified: mark enrollment paid if this payment clears it
        )

        # Send payment confirmation
        await send_whatsapp(current_user["phone"], f"Payment of ₹{payment_data.amount} received for enrollment {payment_data.enrollment_id}. Thank you!")

        return {"message": "Payment processed successfully", "payment_id": pending_payment["id"]}

    @staticmethod
    async def confirm_razorpay_payment(
        data: ConfirmRazorpayPayment,
        current_user: dict = Depends(require_role([UserRole.STUDENT]))
    ):
        """Verify/record Razorpay payment and activate linked enrollment."""
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        student_id = current_user["id"]

        if data.razorpay_order_id:
            if not data.razorpay_signature:
                raise HTTPException(status_code=400, detail="Missing Razorpay signature")
            if not verify_razorpay_signature(
                data.razorpay_order_id, data.razorpay_payment_id, data.razorpay_signature
            ):
                raise HTTPException(status_code=400, detail="Invalid payment signature")

        # Prefer order linkage when present; fallback to requested enrollment_id.
        enrollment = None
        if data.razorpay_order_id:
            enrollment = await db.enrollments.find_one(
                {"student_id": student_id, "razorpay_last_order_id": data.razorpay_order_id}
            )
        if enrollment is None:
            enrollment = await db.enrollments.find_one(
                {"id": data.enrollment_id, "student_id": student_id}
            )
        if not enrollment:
            raise HTTPException(
                status_code=404,
                detail="Enrollment not found for this payment. Please refresh and try again.",
            )

        expected_total_inr = _enrollment_checkout_total_inr(enrollment)
        if expected_total_inr <= 0:
            raise HTTPException(status_code=400, detail="Invalid enrollment amount.")

        now = datetime.utcnow()
        due_date = now + timedelta(days=30)

        course_name = data.course_name
        if not course_name and enrollment.get("course_id"):
            crs = await db.courses.find_one({"id": enrollment["course_id"]})
            if crs:
                course_name = crs.get("title") or crs.get("name")

        pm_value = PaymentMethod.DIGITAL_WALLET.value
        gw_raw = "razorpay"
        gw_label = "Razorpay"
        rz = _fetch_razorpay_payment_entity(data.razorpay_payment_id)
        if rz:
            pm_value, gw_raw, gw_label = _razorpay_method_to_payment_fields(rz)
            if data.razorpay_order_id:
                expected_paise = int(round(expected_total_inr * 100))
                _validate_razorpay_captured_amount(
                    enrollment_expected_paise=expected_paise,
                    order_id=data.razorpay_order_id,
                    payment_entity=rz,
                    enrollment_id=data.enrollment_id,
                )

        # Idempotency: if this Razorpay payment is already recorded, just ensure enrollment is active/paid.
        existing_paid = await db.payments.find_one(
            {"student_id": student_id, "transaction_id": data.razorpay_payment_id}
        )
        if existing_paid:
            # Ensure stale pending attempts for this enrollment do not keep showing as pending.
            await db.payments.update_many(
                {
                    "student_id": student_id,
                    "enrollment_id": enrollment["id"],
                    "payment_status": {"$in": [PaymentStatus.PENDING.value, "processing", "failed"]},
                },
                {
                    "$set": {
                        "payment_status": "cancelled",
                        "status": "cancelled",
                        "updated_at": now,
                        "notes": "Auto-cancelled after payment verification",
                    }
                },
            )
            renewal_meta = {}
            try:
                from utils.renewal_service import finalize_renewal_after_payment

                renewal_meta = await finalize_renewal_after_payment(
                    db,
                    enrollment=enrollment,
                    payment_doc=existing_paid,
                    now=now,
                )
            except Exception:
                logger.exception("Renewal finalize failed on idempotent confirm enrollment_id=%s", enrollment.get("id"))
                paid_patch = {
                    "payment_status": PaymentStatus.PAID.value,
                    "is_active": True,
                    "updated_at": now,
                }
                recomputed = await enrollment_subscription_end_after_payment(db, enrollment)
                if recomputed:
                    paid_patch["end_date"] = recomputed
                await db.enrollments.update_one(
                    {"id": enrollment["id"]},
                    {"$set": paid_patch, "$unset": {"status": ""}},
                )
            invoice = None
            try:
                from utils.invoice_service import safe_generate_invoice_for_payment

                invoice = await safe_generate_invoice_for_payment(payment_doc=existing_paid)
            except Exception:
                pass
            try:
                from utils.billing_cycle_service import safe_upsert_billing_cycles_for_payment

                await safe_upsert_billing_cycles_for_payment(payment_doc=existing_paid)
            except Exception:
                pass
            out = {
                "message": "Payment already verified",
                "payment_id": existing_paid.get("id") or str(existing_paid.get("_id")),
            }
            if invoice and invoice.get("id"):
                out["invoice_id"] = invoice.get("id")
                out["invoice_number"] = invoice.get("invoice_number")
            if renewal_meta.get("renewal"):
                out["is_renewal"] = True
                out["new_end_date"] = renewal_meta.get("end_date")
                out["prior_end_date"] = renewal_meta.get("prior_end_date")
            return out

        is_renewal_enr = bool(enrollment.get("is_renewal") or enrollment.get("renewed_from_enrollment_id"))
        payment_doc = {
            "id": str(uuid.uuid4()),
            "user_id": student_id,  # alias for reporting compatibility
            "student_id": student_id,
            "enrollment_id": enrollment["id"],
            "course_id": enrollment.get("course_id"),
            "amount": expected_total_inr,
            "payment_type": PaymentType.COURSE_FEE.value,
            "payment_method": pm_value,
            "gateway_method": gw_raw,
            "gateway_payment_label": gw_label,
            "status": "success",
            "payment_status": PaymentStatus.PAID.value,
            "transaction_id": data.razorpay_payment_id,
            "razorpay_payment_id": data.razorpay_payment_id,
            "razorpay_order_id": data.razorpay_order_id,
            "payment_date": now,
            "due_date": due_date,
            "notes": f"Razorpay order: {data.razorpay_order_id or 'N/A'}",
            "course_details": {"course_name": course_name} if course_name else None,
            "branch_details": {"branch_name": data.branch_name} if data.branch_name else None,
            "created_at": now,
            "updated_at": now,
        }
        if is_renewal_enr:
            payment_doc["is_renewal"] = True
            payment_doc["renewed_from_enrollment_id"] = enrollment.get("renewed_from_enrollment_id")
            payment_doc["arrear_amount"] = float(enrollment.get("arrear_amount") or 0)
            payment_doc["notes"] = (
                f"Renewal via Razorpay order: {data.razorpay_order_id or 'N/A'}"
            )

        # Update pending row created at order time when available; otherwise insert paid row.
        pending_filter = {
            "student_id": student_id,
            "enrollment_id": enrollment["id"],
            "payment_status": PaymentStatus.PENDING.value,
            "razorpay_order_id": data.razorpay_order_id,
        }
        if data.razorpay_order_id:
            upd = await db.payments.update_one(
                pending_filter,
                {"$set": payment_doc},
            )
            if upd.matched_count == 0:
                await db.payments.insert_one(payment_doc)
        else:
            await db.payments.insert_one(payment_doc)

        renewal_meta = {}
        try:
            from utils.renewal_service import finalize_renewal_after_payment

            renewal_meta = await finalize_renewal_after_payment(
                db,
                enrollment=enrollment,
                payment_doc=payment_doc,
                now=now,
            )
        except Exception:
            logger.exception("Renewal finalize failed on confirm enrollment_id=%s", enrollment.get("id"))
            update_enrollment = {
                "payment_status": PaymentStatus.PAID.value,
                "is_active": True,
                "updated_at": now,
            }
            recomputed_end = await enrollment_subscription_end_after_payment(db, enrollment)
            if recomputed_end:
                update_enrollment["end_date"] = recomputed_end

            enr_upd = await db.enrollments.update_one(
                {"id": enrollment["id"]},
                {"$set": update_enrollment, "$unset": {"status": ""}},
            )
            if enr_upd.matched_count == 0:
                raise HTTPException(status_code=500, detail="Failed to activate enrollment after payment.")

        # Mark any sibling pending attempts for the same enrollment as cancelled.
        await db.payments.update_many(
            {
                "student_id": student_id,
                "enrollment_id": enrollment["id"],
                "id": {"$ne": payment_doc["id"]},
                "payment_status": {"$in": [PaymentStatus.PENDING.value, "processing", "failed"]},
            },
            {
                "$set": {
                    "payment_status": "cancelled",
                    "status": "cancelled",
                    "updated_at": now,
                    "notes": "Auto-cancelled duplicate attempt after successful payment",
                }
            },
        )

        invoice = None
        try:
            from utils.invoice_service import safe_generate_invoice_for_payment

            invoice = await safe_generate_invoice_for_payment(payment_doc=payment_doc)
        except Exception:
            pass
        try:
            from utils.billing_cycle_service import safe_upsert_billing_cycles_for_payment

            await safe_upsert_billing_cycles_for_payment(payment_doc=payment_doc)
        except Exception:
            pass

        out = {"message": "Payment recorded successfully", "payment_id": payment_doc["id"]}
        if invoice and invoice.get("id"):
            out["invoice_id"] = invoice.get("id")
            out["invoice_number"] = invoice.get("invoice_number")
        if renewal_meta.get("renewal"):
            out["is_renewal"] = True
            out["new_end_date"] = renewal_meta.get("end_date")
            out["prior_end_date"] = renewal_meta.get("prior_end_date")
            out["message"] = "Renewal payment recorded successfully"
        return out

    @staticmethod
    async def create_student_razorpay_order(
        body: CreateStudentRazorpayOrderBody,
        current_user: dict,
    ):
        """Create a real Razorpay order for a pending enrollment; amount is derived server-side (INR → paise)."""
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        if current_user.get("role") != "student":
            raise HTTPException(status_code=403, detail="Only students can use this endpoint")

        student_id = current_user["id"]
        enrollment = await db.enrollments.find_one(
            {"id": body.enrollment_id, "student_id": student_id}
        )
        if not enrollment:
            raise HTTPException(status_code=404, detail="Enrollment not found or does not belong to you.")

        raw_ps = enrollment.get("payment_status")
        if hasattr(raw_ps, "value"):
            ps = str(raw_ps.value).lower()
        else:
            ps = str(raw_ps or "").lower()
        if ps != EnrollmentPaymentStatus.PENDING.value:
            raise HTTPException(
                status_code=400,
                detail="Checkout is only available for pending enrollments. Refresh and try again.",
            )

        total_inr = _enrollment_checkout_total_inr(enrollment)
        if total_inr <= 0:
            raise HTTPException(status_code=400, detail="Invalid checkout amount.")

        amount_paise = int(round(total_inr * 100))
        if amount_paise < 100:
            raise HTTPException(status_code=400, detail="Amount too small for online payment.")

        logger.info(
            "Razorpay order.create (student enrollment) enrollment_id=%s amount_paise=%s amount_inr=%s",
            body.enrollment_id,
            amount_paise,
            total_inr,
        )

        client = get_razorpay_client()
        try:
            order = client.order.create(
                {
                    "amount": amount_paise,
                    "currency": "INR",
                    "payment_capture": 1,
                    "notes": {
                        "enrollment_id": (body.enrollment_id or "")[:40],
                        "student_id": (student_id or "")[:40],
                    },
                }
            )
        except Exception:
            logger.exception("Razorpay order.create failed enrollment_id=%s", body.enrollment_id)
            raise HTTPException(
                status_code=502,
                detail="We could not reach the payment service. Please try again in a moment.",
            )

        now = datetime.utcnow()
        await db.enrollments.update_one(
            {"id": body.enrollment_id},
            {"$set": {"razorpay_last_order_id": order["id"], "updated_at": now}},
        )

        # Cancel any previous pending payment rows for this enrollment (from earlier abandoned attempts).
        await db.payments.update_many(
            {
                "student_id": student_id,
                "enrollment_id": body.enrollment_id,
                "payment_status": PaymentStatus.PENDING.value,
                "razorpay_order_id": {"$ne": order["id"]},
            },
            {
                "$set": {
                    "payment_status": "cancelled",
                    "status": "cancelled",
                    "updated_at": now,
                    "notes": "Auto-cancelled: superseded by new payment order",
                }
            },
        )

        # Create pending payment row linked to enrollment/order (verify step will mark paid/success).
        existing_order_payment = await db.payments.find_one(
            {"student_id": student_id, "enrollment_id": body.enrollment_id, "razorpay_order_id": order["id"]}
        )
        if not existing_order_payment:
            pending_doc = {
                "id": str(uuid.uuid4()),
                "user_id": student_id,
                "student_id": student_id,
                "enrollment_id": body.enrollment_id,
                "course_id": enrollment.get("course_id"),
                "amount": total_inr,
                "payment_type": PaymentType.COURSE_FEE.value,
                "payment_method": PaymentMethod.DIGITAL_WALLET.value,
                "payment_status": PaymentStatus.PENDING.value,
                "status": "initiated",
                "transaction_id": None,
                "razorpay_payment_id": None,
                "razorpay_order_id": order["id"],
                "payment_date": None,
                "due_date": now + timedelta(days=30),
                "notes": f"Razorpay order created: {order['id']}",
                "created_at": now,
                "updated_at": now,
            }
            await db.payments.insert_one(pending_doc)

        key_id = os.getenv("RAZORPAY_KEY_ID", "").strip()
        return {
            "order": {
                "id": order["id"],
                "amount": int(order["amount"]),
                "currency": str(order.get("currency") or "INR"),
            },
            "key": key_id,
        }

    @staticmethod
    async def prepare_student_course_checkout(
        body: PrepareStudentCheckoutBody,
        current_user: dict,
    ):
        """Create a pending enrollment using the same pricing as payment-info; client then pays via Razorpay."""
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        if current_user.get("role") != "student":
            raise HTTPException(status_code=403, detail="Only students can use this endpoint")

        student_id = current_user["id"]
        await _enforce_student_assigned_branch(db, student_id, body.branch_id, current_user)

        active_paid = await _latest_active_paid_enrollment(db, student_id, body.course_id)

        # Drop abandoned pending checkouts so the student can retry after canceling Razorpay.
        # Also cancel pending payment attempts linked to those stale pending enrollments.
        stale_pending_enrollments = await db.enrollments.find(
            {
                "student_id": student_id,
                "course_id": body.course_id,
                "payment_status": EnrollmentPaymentStatus.PENDING.value,
            },
            {"id": 1},
        ).to_list(length=None)
        stale_pending_enrollment_ids = [str(e.get("id")) for e in stale_pending_enrollments if e.get("id")]

        if stale_pending_enrollment_ids:
            await db.payments.update_many(
                {
                    "student_id": student_id,
                    "enrollment_id": {"$in": stale_pending_enrollment_ids},
                    "payment_status": {"$in": [PaymentStatus.PENDING.value, "processing", "failed"]},
                },
                {
                    "$set": {
                        "payment_status": "cancelled",
                        "status": "cancelled",
                        "updated_at": datetime.utcnow(),
                        "notes": "Auto-cancelled stale checkout attempt before new checkout",
                    }
                },
            )

        await db.enrollments.delete_many(
            {
                "student_id": student_id,
                "course_id": body.course_id,
                "payment_status": EnrollmentPaymentStatus.PENDING.value,
            }
        )

        branch = await db.branches.find_one({"id": body.branch_id})
        if not branch:
            raise HTTPException(status_code=404, detail="Branch not found")

        duration_row = await db.durations.find_one({"id": body.duration})
        if not duration_row:
            duration_row = await db.durations.find_one({"code": body.duration})
        batches = _batches_for_course_on_branch(branch, body.course_id)
        batch_ref = await resolve_checkout_batch_ref(
            db,
            course_id=body.course_id,
            branch_id=body.branch_id,
            batches=batches,
            requested_batch_ref=body.batch_ref,
            student_id=student_id,
            duration_keys=_duration_price_keys(body.duration, duration_row),
        )

        beneficiary_payload = body.beneficiary.dict() if body.beneficiary else {"beneficiary_type": "self"}
        info = await PaymentController.get_course_payment_info(
            body.course_id,
            body.branch_id,
            body.duration,
            batch_ref=batch_ref,
            optional_student_id=student_id,
            admission_fee_beneficiary=beneficiary_payload,
        )

        start_date = datetime.utcnow()
        if active_paid and not is_subscription_period_over(active_paid.get("end_date")):
            # Allow in-advance renewal while preserving current plan validity window.
            active_end_eod = subscription_end_of_day_utc(active_paid.get("end_date"))
            if active_end_eod is not None:
                start_date = (active_end_eod + timedelta(microseconds=1)).replace(tzinfo=None)
        months_hint = None
        if duration_row and duration_row.get("duration_months") is not None:
            try:
                months_hint = int(duration_row["duration_months"])
            except (TypeError, ValueError):
                months_hint = None

        end_date = await resolve_enrollment_end_date(
            db, body.duration, start_date, months_hint=months_hint
        )

        effective_admission_fee = float(info.pricing.admission_fee)
        effective_total = float(info.pricing.total_amount)

        enrollment = EnrollmentModel(
            student_id=student_id,
            course_id=body.course_id,
            branch_id=body.branch_id,
            start_date=start_date,
            end_date=end_date,
            fee_amount=float(info.pricing.course_fee),
            admission_fee=effective_admission_fee,
            payment_status=EnrollmentPaymentStatus.PENDING,
            is_active=True,
            duration_id=body.duration,
            batch_ref=batch_ref,
        )
        enrollment_doc = enrollment.dict()
        enrollment_doc["duration_id"] = body.duration
        enrollment_doc["enrollment_date"] = start_date
        if batch_ref:
            enrollment_doc["batch_ref"] = batch_ref
        # Store beneficiary info if provided
        if body.beneficiary and body.beneficiary.beneficiary_type != "self":
            enrollment_doc["beneficiary"] = beneficiary_payload
        await db.enrollments.insert_one(enrollment_doc)

        return {
            "enrollment_id": enrollment.id,
            "amount": effective_total,
            "course_name": info.course_name,
            "branch_name": info.branch_name,
            "duration_months": months_hint,
            "batch_ref": batch_ref,
        }

    @staticmethod
    async def _duration_ref_for_months(db, months: int) -> Optional[str]:
        """Resolve duration document id/code for a canonical tenure length (e.g. 1 month)."""
        if months <= 0:
            return None
        row = await db.durations.find_one({"duration_months": months, "is_active": True})
        if not row:
            row = await db.durations.find_one({"duration_months": months})
        if not row:
            return None
        rid = row.get("id") or row.get("code")
        return str(rid) if rid is not None else None

    @staticmethod
    async def get_course_payment_info(
        course_id: str,
        branch_id: str,
        duration: str,
        batch_ref: Optional[str] = None,
        optional_student_id: Optional[str] = None,
        admission_fee_beneficiary: Optional[dict] = None,
    ):
        """Get payment information for a course.

        When ``optional_student_id`` is set (logged-in student), branch admission is included only if
        this is their first completed enrollment (self) or first for that beneficiary — otherwise
        admission fee is omitted from totals.
        """
        try:
            db = get_db()

            if db is None:
                raise HTTPException(status_code=500, detail="Database connection not available")

            # Get course details
            course = await db.courses.find_one({"id": course_id})
            if not course:
                raise HTTPException(status_code=404, detail="Course not found")

            # Get branch details
            branch = await db.branches.find_one({"id": branch_id})
            if not branch:
                raise HTTPException(status_code=404, detail="Branch not found")

            await assert_course_available_at_branch(
                db,
                branch_id,
                course_id,
                require_active_branch=False,
                require_active_course=False,
                allow_if_enrolled_student_id=optional_student_id,
            )

            # Get category details
            category = await db.categories.find_one({"id": course.get("category_id")}) if course.get("category_id") else None

            # Get duration details for pricing multiplier
            # Try to find by ID first, then by code
            duration_info = await db.durations.find_one({"id": duration})
            if not duration_info:
                duration_info = await db.durations.find_one({"code": duration})

            pricing_multiplier = 1.0
            months_int = 0
            if duration_info:
                try:
                    pm = float(duration_info.get("pricing_multiplier", 1.0))
                except (TypeError, ValueError):
                    pm = 1.0
                dm = duration_info.get("duration_months")
                try:
                    months_int = int(dm) if dm is not None else 0
                except (TypeError, ValueError):
                    months_int = 0
                # Master data often stores multiplier as 1 for every tenure; scale by month count
                # so base/month fee * tenure length (unless an explicit non-1 multiplier was set).
                if pm == 1.0 and months_int > 0:
                    pricing_multiplier = float(months_int)
                else:
                    pricing_multiplier = pm
            duration_name = duration_info.get("name", duration) if duration_info else duration

            # Admission is always the configured branch/system amount for this checkout — never multiplied by duration.
            # (Course tuition may scale by tenure via batch/monthly pricing or linear multi-month estimate.)
            admission_fee = await SettingsController.get_default_registration_fee()
            branch_adm = branch.get("admission_fee")
            if branch_adm is not None:
                try:
                    admission_fee = float(branch_adm)
                except (TypeError, ValueError):
                    pass
            course_fee = None
            total_amount = None
            batch_is_flat = False
            explicit_duration_fee_found = False

            dur_keys = _duration_price_keys(duration, duration_info)

            batches = _batches_for_course_on_branch(branch, course_id)
            branch_pricing = _course_branch_pricing_map(course)
            # Always price from the current super-admin batch setup when one can be resolved.
            # Course-level fee_per_duration / branch_pricing often still hold leftover amounts
            # (₹1500 / ₹1600) that are no longer shown or edited in the admin UI.
            batch_ref = await resolve_checkout_batch_ref(
                db,
                course_id=course_id,
                branch_id=branch_id,
                batches=batches,
                requested_batch_ref=batch_ref,
                student_id=optional_student_id,
                duration_keys=dur_keys,
            )
            if not batch_ref:
                for i, bdoc_candidate in enumerate(batches):
                    if _fee_from_batch_for_keys(bdoc_candidate, dur_keys) is not None:
                        # Multiple differently priced batches and no student/batch context:
                        # still prefer an admin batch fee over stale course-catalog leftovers.
                        if optional_student_id:
                            raise HTTPException(
                                status_code=400,
                                detail="Please select your batch so we can apply the correct fee set by admin.",
                            )
                        batch_ref = _batch_identity(bdoc_candidate, i)
                        break
            if batch_ref and str(batch_ref).strip():
                bdoc = _resolve_branch_batch(batches, batch_ref)
                if bdoc is None:
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid batch selection for this course at the selected branch.",
                    )
                # Check batch fee_per_duration first (per-duration pricing)
                batch_fpd = bdoc.get("fee_per_duration") or {}
                batch_ptd = bdoc.get("pricing_type_per_duration") or {}
                batch_dur_fee = None
                # Default matches course-level fee_per_duration: each tier cell is the full package price
                # for that tenure. Use pricing_type_per_duration[key] == "monthly" only when the cell is a
                # per-month rupee amount (multiply by duration_months).
                batch_dur_pricing_type = "flat"
                matched_dur_key = None
                if isinstance(batch_fpd, dict):
                    for dk in dur_keys:
                        if dk in batch_fpd and batch_fpd[dk] is not None:
                            try:
                                batch_dur_fee = float(batch_fpd[dk])
                                matched_dur_key = dk
                            except (TypeError, ValueError):
                                pass
                            if batch_dur_fee is not None:
                                break
                if matched_dur_key and isinstance(batch_ptd, dict):
                    pt_raw = batch_ptd.get(matched_dur_key)
                    if pt_raw is not None and str(pt_raw).strip():
                        batch_dur_pricing_type = str(pt_raw).strip().lower()
                if batch_dur_fee is not None:
                    explicit_duration_fee_found = True
                    if batch_dur_pricing_type == "monthly":
                        course_fee = batch_dur_fee * pricing_multiplier
                        batch_is_flat = False
                    else:
                        # Package total for this duration (flat / unset / any non-monthly label)
                        course_fee = batch_dur_fee
                        batch_is_flat = batch_dur_pricing_type == "flat"
                    total_amount = course_fee + admission_fee
                else:
                    bf = _batch_fee_from_doc(bdoc)
                    if bf is not None:
                        # Legacy batch_fee is per-month; multiply by tenure months
                        course_fee = bf * pricing_multiplier
                        total_amount = course_fee + admission_fee

            # 0) Flat/offer price check — overrides all calculated pricing
            flat_price_per_duration = course.get("flat_price_per_duration") or {}
            if isinstance(course.get("pricing"), dict):
                flat_price_per_duration = flat_price_per_duration or course["pricing"].get("flat_price_per_duration") or {}
            flat_price_value = None
            original_calculated_total = None
            for dk in dur_keys:
                if dk in flat_price_per_duration and flat_price_per_duration[dk] is not None:
                    flat_price_value = float(flat_price_per_duration[dk])
                    break
            # Also check branch-specific flat prices
            branch_flat_prices = {}
            raw_bp = course.get("branch_pricing") or {}
            if branch_id in raw_bp and isinstance(raw_bp[branch_id], dict):
                branch_flat_prices = raw_bp[branch_id].get("flat_price_per_duration") or {}
            for dk in dur_keys:
                if dk in branch_flat_prices and branch_flat_prices[dk] is not None:
                    flat_price_value = float(branch_flat_prices[dk])
                    break

            # 1) Branch-specific duration fees (dict may use duration id, code, or slug)
            bp_val = None
            if total_amount is None and branch_id in branch_pricing:
                bp_val = branch_pricing[branch_id]
                if isinstance(bp_val, dict):
                    picked = None
                    for dk in dur_keys:
                        if dk in bp_val and bp_val[dk] is not None:
                            picked = float(bp_val[dk])
                            break
                    if picked is not None:
                        course_fee = picked
                        total_amount = course_fee + admission_fee
                        pricing_multiplier = 1.0
                        explicit_duration_fee_found = True
                    # dict but no matching key: fall through to fee_per_duration / legacy (no 404)
                elif isinstance(bp_val, (int, float)):
                    base_price = float(bp_val)
                    course_fee = base_price * pricing_multiplier
                    total_amount = course_fee + admission_fee
                else:
                    bp_val = None

            if total_amount is None:
                # 2) Default fee_per_duration
                fee_per_duration = _course_fee_per_duration_map(course)
                picked_fd = None
                for dk in dur_keys:
                    if dk in fee_per_duration and fee_per_duration[dk] is not None:
                        picked_fd = float(fee_per_duration[dk])
                        break
                if picked_fd is not None:
                    course_fee = picked_fd
                    total_amount = course_fee + admission_fee
                    pricing_multiplier = 1.0
                    explicit_duration_fee_found = True
                else:
                    # 3) Legacy: base_fee * multiplier (even if maps exist but lack this tenure)
                    base_price = course.get("base_fee")
                    if base_price is None:
                        if course.get("pricing"):
                            pr = course["pricing"]
                            if isinstance(pr, dict):
                                base_price = pr.get("fee_1_month") or pr.get("amount")
                            elif isinstance(pr, (int, float)):
                                base_price = pr
                        if base_price is None:
                            base_price = course.get("price") or course.get("fee")
                    if base_price is None:
                        raise HTTPException(
                            status_code=400,
                            detail="No pricing configured for this course. Please contact the admin to set up pricing.",
                        )
                    base_price = float(base_price)
                    if branch_id in branch_pricing and isinstance(branch_pricing[branch_id], (int, float)):
                        base_price = float(branch_pricing[branch_id])
                    course_fee = base_price * pricing_multiplier
                    total_amount = course_fee + admission_fee

            # Import PaymentCalculation
            from models.student_models import PaymentCalculation

            # If flat price applies, use it and track the discount
            is_flat_price = batch_is_flat
            if flat_price_value is not None and total_amount is not None:
                original_calculated_total = total_amount
                course_fee = flat_price_value
                total_amount = flat_price_value + admission_fee
                is_flat_price = True

            # Multi-month estimate = same rules as 1-month × tenure months (matches registration UX).
            # Skip when an explicit flat-price campaign applies (admin-set package for that tenure).
            scale_months = (
                months_int
                if months_int > 1
                else int(pricing_multiplier)
                if pricing_multiplier > 1
                else 1
            )
            applied_linear_tenure_scale = False
            if flat_price_value is None and not explicit_duration_fee_found and scale_months > 1:
                one_ref = await PaymentController._duration_ref_for_months(db, 1)
                if one_ref and str(one_ref) != str(duration):
                    try:
                        base_info = await PaymentController.get_course_payment_info(
                            course_id,
                            branch_id,
                            str(one_ref),
                            batch_ref=batch_ref,
                            optional_student_id=optional_student_id,
                            admission_fee_beneficiary=admission_fee_beneficiary,
                        )
                        bp = base_info.pricing
                        base_total = float(bp.total_amount or 0)
                        if base_total > 0:
                            sf = float(scale_months)
                            course_fee = round(float(bp.course_fee or 0) * sf, 2)
                            # Admission is branch/setup-defined once; never scaled by tenure.
                            admission_fee = round(float(bp.admission_fee or 0), 2)
                            total_amount = round(course_fee + admission_fee, 2)
                            pricing_multiplier = sf
                            applied_linear_tenure_scale = True
                    except HTTPException:
                        pass

            if applied_linear_tenure_scale:
                is_flat_price = False
                original_calculated_total = None

            # Logged-in students: admission only on first successful payment (never on renewals).
            if optional_student_id:
                ben = admission_fee_beneficiary if admission_fee_beneficiary is not None else {"beneficiary_type": "self"}
                charge_admission = await should_charge_admission_fee_for_checkout(
                    db, optional_student_id, ben
                )
                if not charge_admission:
                    admission_fee = 0.0
                    total_amount = round(float(course_fee or 0), 2)

            # Create pricing calculation
            pricing = PaymentCalculation(
                course_fee=course_fee,
                admission_fee=admission_fee,
                total_amount=total_amount,
                currency="INR",
                duration_multiplier=pricing_multiplier,
                original_price=original_calculated_total if is_flat_price else None,
                discount_amount=round(original_calculated_total - total_amount, 2) if is_flat_price and original_calculated_total else None,
                is_flat_price=is_flat_price,
            )

            return CoursePaymentInfo(
                course_id=course_id,
                course_name=course.get("title", course.get("name", "Course")),
                category_name=category.get("name", "Category") if category else "Category",
                branch_name=branch.get("name", branch.get("branch", {}).get("name", "Branch")),
                duration=duration_name,
                pricing=pricing
            )

        except HTTPException:
            raise
        except Exception as e:
            print(f"Error in get_course_payment_info: {e}")
            raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

    @staticmethod
    def _parse_registration_dob(val: Any):
        from datetime import date as date_cls
        if not val:
            return None
        if isinstance(val, date_cls):
            return val
        try:
            return date_cls.fromisoformat(str(val)[:10])
        except ValueError:
            return None

    @staticmethod
    async def _pay_and_activate_enrollment(
        db,
        *,
        student_id: str,
        enrollment_id: Optional[str],
        course_id: str,
        branch_id: str,
        category_id: str,
        duration: str,
        duration_months: Optional[int],
        batch_ref: Optional[str],
        payment_method: PaymentMethod,
        transaction_id: str,
        student_data: dict,
    ) -> Tuple[str, float, CoursePaymentInfo, str]:
        payment_info = await PaymentController.get_course_payment_info(
            course_id,
            branch_id,
            duration,
            batch_ref=batch_ref,
        )
        months_hint = duration_months
        if not enrollment_id:
            start_date = datetime.utcnow()
            end_date = await resolve_enrollment_end_date(
                db, duration, start_date, months_hint=months_hint
            )
            enrollment = EnrollmentModel(
                student_id=student_id,
                course_id=course_id,
                branch_id=branch_id,
                start_date=start_date,
                end_date=end_date,
                fee_amount=payment_info.pricing.course_fee,
                admission_fee=payment_info.pricing.admission_fee,
                payment_status="paid",
                enrollment_date=start_date,
                is_active=True,
            )
            enrollment_doc = enrollment.dict()
            if duration:
                enrollment_doc["duration_id"] = duration
            await db.enrollments.insert_one(enrollment_doc)
            enrollment_id = enrollment.id
        else:
            enroll = await db.enrollments.find_one({"id": enrollment_id})
            if enroll:
                st = enroll.get("start_date") or enroll.get("enrollment_date")
                if isinstance(st, str):
                    try:
                        st = datetime.fromisoformat(st.replace("Z", "+00:00")).replace(tzinfo=None)
                    except ValueError:
                        st = datetime.utcnow()
                elif isinstance(st, datetime):
                    st = st.replace(tzinfo=None) if st.tzinfo else st
                else:
                    st = datetime.utcnow()
                end_date = await resolve_enrollment_end_date(
                    db, duration, st, months_hint=months_hint
                )
                await db.enrollments.update_one(
                    {"id": enrollment_id},
                    {
                        "$set": {
                            "end_date": end_date,
                            "fee_amount": payment_info.pricing.course_fee,
                            "admission_fee": payment_info.pricing.admission_fee,
                            "payment_status": PaymentStatus.PAID.value,
                            "duration_id": duration,
                        }
                    },
                )

        payment = Payment(
            student_id=student_id,
            enrollment_id=enrollment_id,
            amount=payment_info.pricing.total_amount,
            payment_type=PaymentType.REGISTRATION_FEE,
            payment_method=payment_method,
            payment_status=PaymentStatus.PAID,
            transaction_id=transaction_id,
            payment_date=datetime.utcnow(),
            due_date=datetime.utcnow() + timedelta(days=7),
            registration_data=student_data,
            course_details={
                "course_id": course_id,
                "course_name": payment_info.course_name,
                "category_id": category_id,
                "duration": duration,
            },
            branch_details={
                "branch_id": branch_id,
                "branch_name": payment_info.branch_name,
            },
        )
        await db.payments.insert_one(payment.dict())
        return enrollment_id, payment_info.pricing.total_amount, payment_info, payment.id

    @staticmethod
    async def _process_family_registration(payment_data: RegistrationPaymentCreate):
        db = get_db()
        family: List[FamilyStudentEnrollment] = list(payment_data.family_students or [])
        if not family:
            raise HTTPException(status_code=400, detail="family_students is required for family registration")

        from controllers.auth_controller import AuthController
        from models.user_models import CourseInfo, BranchInfo
        from utils.family_accounts import ensure_account_for_student

        holder = dict(payment_data.student_data or {})
        if not holder.get("password"):
            holder["password"] = secrets.token_urlsafe(8)

        first = family[0]
        first_payload = {
            **holder,
            "first_name": first.first_name or holder.get("first_name"),
            "last_name": first.last_name or holder.get("last_name"),
            "full_name": f"{first.first_name or holder.get('first_name', '')} {first.last_name or holder.get('last_name', '')}".strip(),
            "date_of_birth": PaymentController._parse_registration_dob(
                first.date_of_birth or holder.get("date_of_birth")
            ),
            "gender": first.gender or holder.get("gender"),
            "role": "student",
            "relationship": first.relationship or "self",
            "course": {
                "category_id": first.category_id,
                "course_id": first.course_id,
                "duration": first.duration,
            },
            "branch": {
                "location_id": first.location_id or first.branch_id,
                "branch_id": first.branch_id,
            },
        }
        user_create = UserCreate(**first_payload)
        user_result = await AuthController.register_user(user_create, None)
        first_student_id = user_result["user_id"]

        transaction_id = f"TXN{datetime.utcnow().strftime('%Y%m%d')}{secrets.token_hex(4).upper()}"
        total_amount = 0.0
        last_payment_id = ""
        last_info = None

        _, amt, info, pay_id = await PaymentController._pay_and_activate_enrollment(
            db,
            student_id=first_student_id,
            enrollment_id=user_result.get("enrollment_id"),
            course_id=first.course_id,
            branch_id=first.branch_id,
            category_id=first.category_id,
            duration=first.duration,
            duration_months=first.duration_months,
            batch_ref=first.batch_ref,
            payment_method=payment_data.payment_method,
            transaction_id=f"{transaction_id}-1",
            student_data=holder,
        )
        total_amount += amt
        last_payment_id = pay_id
        last_info = info

        first_user = await db.users.find_one({"id": first_student_id})
        acc = await ensure_account_for_student(db, first_user)

        for idx, extra in enumerate(family[1:], start=2):
            created = await AuthController.create_student_under_account(
                acc,
                first_name=extra.first_name,
                last_name=extra.last_name,
                date_of_birth=PaymentController._parse_registration_dob(extra.date_of_birth),
                gender=extra.gender,
                relationship=extra.relationship or "child",
                course=CourseInfo(
                    category_id=extra.category_id,
                    course_id=extra.course_id,
                    duration=extra.duration,
                    batch_ref=extra.batch_ref,
                ),
                branch=BranchInfo(
                    location_id=extra.location_id or extra.branch_id,
                    branch_id=extra.branch_id,
                ),
                send_welcome_sms=False,
            )
            _, amt, info, pay_id = await PaymentController._pay_and_activate_enrollment(
                db,
                student_id=created["user_id"],
                enrollment_id=created.get("enrollment_id"),
                course_id=extra.course_id,
                branch_id=extra.branch_id,
                category_id=extra.category_id,
                duration=extra.duration,
                duration_months=extra.duration_months,
                batch_ref=extra.batch_ref,
                payment_method=payment_data.payment_method,
                transaction_id=f"{transaction_id}-{idx}",
                student_data=holder,
            )
            total_amount += amt
            last_payment_id = pay_id
            last_info = info

        phone = holder.get("phone", "")
        if phone:
            message = (
                f"Welcome! Family registration is complete. Payment of ₹{total_amount} received. "
                f"Transaction ID: {transaction_id}"
            )
            await send_whatsapp(phone, message)

        if last_info:
            await PaymentController.create_payment_notification(
                last_payment_id,
                first_student_id,
                last_info,
                {
                    "id": first_student_id,
                    "full_name": first_payload.get("full_name", ""),
                    "email": holder.get("email", ""),
                    "phone": holder.get("phone", ""),
                },
            )

        return RegistrationPaymentResponse(
            payment_id=last_payment_id,
            student_id=first_student_id,
            transaction_id=transaction_id,
            amount=total_amount,
            status=PaymentStatus.PAID,
            message=f"Family registration completed for {len(family)} students",
        )

    @staticmethod
    async def process_registration_payment(payment_data: RegistrationPaymentCreate):
        """Process payment for student registration"""
        if payment_data.family_students:
            try:
                return await PaymentController._process_family_registration(payment_data)
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Payment processing failed: {str(e)}",
                )

        db = get_db()

        try:
            # Get payment information
            payment_info = await PaymentController.get_course_payment_info(
                payment_data.course_id,
                payment_data.branch_id,
                payment_data.duration,
                batch_ref=payment_data.batch_ref,
            )

            # Generate transaction ID
            transaction_id = f"TXN{datetime.utcnow().strftime('%Y%m%d')}{secrets.token_hex(4).upper()}"

            # Create user first
            from controllers.auth_controller import AuthController

            # Generate password if not provided
            if not payment_data.student_data.get("password"):
                payment_data.student_data["password"] = secrets.token_urlsafe(8)

            # Create user account
            user_create_data = UserCreate(**payment_data.student_data)
            user_result = await AuthController.register_user(user_create_data, None)
            student_id = user_result["user_id"]

            # Create enrollment record first to get enrollment_id
            enrollment_id = user_result.get("enrollment_id")
            months_hint = payment_data.duration_months

            if not enrollment_id:
                from models.enrollment_models import Enrollment

                start_date = datetime.utcnow()
                end_date = await resolve_enrollment_end_date(
                    db, payment_data.duration, start_date, months_hint=months_hint
                )

                enrollment = Enrollment(
                    student_id=student_id,
                    course_id=payment_data.course_id,
                    branch_id=payment_data.branch_id,
                    start_date=start_date,
                    end_date=end_date,
                    fee_amount=payment_info.pricing.course_fee,
                    admission_fee=payment_info.pricing.admission_fee,
                    payment_status="paid",
                    enrollment_date=start_date,
                    is_active=True
                )

                enrollment_doc = enrollment.dict()
                if payment_data.duration:
                    enrollment_doc["duration_id"] = payment_data.duration
                await db.enrollments.insert_one(enrollment_doc)
                enrollment_id = enrollment.id
            else:
                # register_user already inserted enrollment (often with wrong end_date if duration lookup failed)
                enroll = await db.enrollments.find_one({"id": enrollment_id})
                if enroll:
                    st = enroll.get("start_date") or enroll.get("enrollment_date")
                    if isinstance(st, str):
                        try:
                            st = datetime.fromisoformat(st.replace("Z", "+00:00")).replace(tzinfo=None)
                        except ValueError:
                            st = datetime.utcnow()
                    elif isinstance(st, datetime):
                        st = st.replace(tzinfo=None) if st.tzinfo else st
                    else:
                        st = datetime.utcnow()
                    end_date = await resolve_enrollment_end_date(
                        db, payment_data.duration, st, months_hint=months_hint
                    )
                    await db.enrollments.update_one(
                        {"id": enrollment_id},
                        {
                            "$set": {
                                "end_date": end_date,
                                "fee_amount": payment_info.pricing.course_fee,
                                "admission_fee": payment_info.pricing.admission_fee,
                                "payment_status": PaymentStatus.PAID.value,
                                "duration_id": payment_data.duration,
                            }
                        },
                    )

            # Create payment record with proper enrollment linking
            payment = Payment(
                student_id=student_id,
                enrollment_id=enrollment_id,  # Link payment to enrollment
                amount=payment_info.pricing.total_amount,
                payment_type=PaymentType.REGISTRATION_FEE,
                payment_method=payment_data.payment_method,
                payment_status=PaymentStatus.PAID,  # Simulate successful payment
                transaction_id=transaction_id,
                payment_date=datetime.utcnow(),
                due_date=datetime.utcnow() + timedelta(days=7),
                registration_data=payment_data.student_data,
                course_details={
                    "course_id": payment_data.course_id,
                    "course_name": payment_info.course_name,
                    "category_id": payment_data.category_id,
                    "duration": payment_data.duration
                },
                branch_details={
                    "branch_id": payment_data.branch_id,
                    "branch_name": payment_info.branch_name
                }
            )

            await db.payments.insert_one(payment.dict())

            # M07-S01: automatic invoice (non-blocking)
            try:
                from utils.invoice_service import safe_generate_invoice_for_payment

                await safe_generate_invoice_for_payment(
                    payment_doc=payment.dict(),
                    source_hint="registration",
                )
            except Exception:
                pass
            try:
                from utils.billing_cycle_service import safe_upsert_billing_cycles_for_payment

                await safe_upsert_billing_cycles_for_payment(payment_doc=payment.dict())
            except Exception:
                pass

            # Create notification for superadmin
            student_data = {
                "id": student_id,
                "full_name": payment_data.student_data.get("full_name", ""),
                "email": payment_data.student_data.get("email", ""),
                "phone": payment_data.student_data.get("phone", "")
            }
            await PaymentController.create_payment_notification(
                payment.id, student_id, payment_info, student_data
            )

            # Send confirmation message
            phone = payment_data.student_data.get("phone", "")
            if phone:
                message = f"Welcome! Your registration is complete. Payment of ₹{payment_info.pricing.total_amount} received. Transaction ID: {transaction_id}"
                await send_whatsapp(phone, message)

            return RegistrationPaymentResponse(
                payment_id=payment.id,
                student_id=student_id,
                transaction_id=transaction_id,
                amount=payment_info.pricing.total_amount,
                status=PaymentStatus.PAID,
                message="Registration and payment completed successfully"
            )

        except Exception as e:
            # Handle payment failure
            raise HTTPException(
                status_code=400,
                detail=f"Payment processing failed: {str(e)}"
            )

    @staticmethod
    async def create_payment_notification(payment_id: str, student_id: str, payment_info: CoursePaymentInfo, student_data: dict):
        """Create notification for superadmin about new payment"""
        db = get_db()

        notification = PaymentNotification(
            payment_id=payment_id,
            student_id=student_id,
            notification_type="registration_payment",
            title="New Student Registration",
            message=f"New student {student_data.get('full_name', 'Unknown')} registered for {payment_info.course_name} with payment of ₹{payment_info.pricing.total_amount}",
            amount=payment_info.pricing.total_amount,
            course_name=payment_info.course_name,
            branch_name=payment_info.branch_name,
            priority="high"
        )

        await db.payment_notifications.insert_one(notification.dict())
        return notification

    @staticmethod
    async def get_payment_notifications(skip: int = 0, limit: int = 50):
        """Get payment notifications for superadmin dashboard"""
        try:
            db = get_db()

            if db is None:
                raise HTTPException(status_code=500, detail="Database connection not available")

            # Get notifications with proper error handling
            notifications = await db.payment_notifications.find(
                {},
                sort=[("created_at", -1)]
            ).skip(skip).limit(limit).to_list(limit)

            # Convert MongoDB documents to JSON-serializable format
            serialized_notifications = []
            for notification in notifications:
                # Convert ObjectId and datetime objects to strings
                serialized_notif = {}
                for key, value in notification.items():
                    if key == "_id":
                        continue  # Skip MongoDB ObjectId
                    elif hasattr(value, 'isoformat'):  # datetime objects
                        serialized_notif[key] = value.isoformat()
                    else:
                        serialized_notif[key] = value
                serialized_notifications.append(serialized_notif)

            return serialized_notifications

        except Exception as e:
            print(f"Error in get_payment_notifications: {e}")
            import traceback
            traceback.print_exc()
            # Return empty list instead of raising error for better UX
            return []

    @staticmethod
    async def mark_notification_read(notification_id: str):
        """Mark a notification as read"""
        db = get_db()

        result = await db.payment_notifications.update_one(
            {"id": notification_id},
            {
                "$set": {
                    "is_read": True,
                    "read_at": datetime.utcnow()
                }
            }
        )

        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Notification not found")

        return {"message": "Notification marked as read"}

    @staticmethod
    def _payment_stats_parse_period(
        start_date: Optional[str], end_date: Optional[str]
    ):
        if not start_date and not end_date:
            return None, None
        try:
            if start_date and end_date:
                s = datetime.strptime(str(start_date)[:10], "%Y-%m-%d")
                e = datetime.strptime(str(end_date)[:10], "%Y-%m-%d").replace(
                    hour=23, minute=59, second=59, microsecond=999999
                )
                if s > e:
                    s, e = e, s
                return s, e
            if start_date:
                s = datetime.strptime(str(start_date)[:10], "%Y-%m-%d")
                e = datetime.utcnow().replace(hour=23, minute=59, second=59, microsecond=999999)
                return s, e
            e = datetime.strptime(str(end_date)[:10], "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, microsecond=999999
            )
            s = datetime(1970, 1, 1)
            return s, e
        except (ValueError, TypeError):
            return None, None

    @staticmethod
    def _merge_collected_revenue_filters(base: dict) -> dict:
        """
        Successful collections only (excludes pending/processing/cancelled/refunded).
        Uses payment_status and legacy top-level status used by some gateways.
        """
        conj = [
            {"payment_status": {"$nin": ["cancelled", "canceled", "failed", "refunded", "pending", "processing"]}},
            {
                "$or": [
                    {"payment_status": {"$in": [PaymentStatus.PAID.value, "completed", "success", "captured"]}},
                    {"status": {"$in": ["success", "completed"]}},
                ]
            },
        ]
        out = dict(base or {})
        if "$and" in out:
            out["$and"] = list(out["$and"]) + conj
        else:
            out["$and"] = conj
        return out

    @staticmethod
    def _pipeline_add_net_revenue_inr():
        """Prepare gross INR minus Razorpay refunds (partial refunds reduce net)."""
        return [
            {
                "$addFields": {
                    "_gross_inr": {
                        "$convert": {"input": "$amount", "to": "double", "onError": 0.0, "onNull": 0.0}
                    },
                    "_refund_inr": {
                        "$divide": [
                            {
                                "$convert": {
                                    "input": {"$ifNull": ["$razorpay_refunded_amount_paise", 0]},
                                    "to": "double",
                                    "onError": 0.0,
                                }
                            },
                            100.0,
                        ]
                    },
                }
            },
            {
                "$addFields": {
                    "_net_inr": {"$max": [0.0, {"$subtract": ["$_gross_inr", "$_refund_inr"]}]},
                }
            },
        ]

    @staticmethod
    async def get_revenue_by_branch(
        current_user: dict,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        branch_id: Optional[str] = None,
    ):
        """
        Branch-wise revenue from payments for a given period.
        Uses payment_date when available, else created_at.
        """
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        ps, pe = PaymentController._payment_stats_parse_period(start_date, end_date)
        match: dict = PaymentController._merge_collected_revenue_filters({})
        if ps and pe:
            match["$or"] = [
                {"payment_date": {"$gte": ps, "$lte": pe}},
                {
                    "payment_date": {"$exists": False},
                    "created_at": {"$gte": ps, "$lte": pe},
                },
                {
                    "payment_date": None,
                    "created_at": {"$gte": ps, "$lte": pe},
                },
            ]

        # Branch id can be on payment.branch_details.branch_id, or derived from enrollment.branch_id.
        pipeline = [
            {"$match": match},
            {
                "$lookup": {
                    "from": "enrollments",
                    "localField": "enrollment_id",
                    "foreignField": "id",
                    "as": "enr",
                }
            },
            {"$unwind": {"path": "$enr", "preserveNullAndEmptyArrays": True}},
            {
                "$addFields": {
                    "resolved_branch_id": {
                        "$ifNull": ["$branch_details.branch_id", "$enr.branch_id"]
                    },
                    "resolved_branch_name": {
                        "$ifNull": ["$branch_details.branch_name", None]
                    },
                }
            },
            {
                "$lookup": {
                    "from": "branches",
                    "localField": "resolved_branch_id",
                    "foreignField": "id",
                    "as": "br",
                }
            },
            {"$unwind": {"path": "$br", "preserveNullAndEmptyArrays": True}},
            {
                "$addFields": {
                    "resolved_branch_name": {
                        "$ifNull": [
                            "$resolved_branch_name",
                            {"$ifNull": ["$br.name", "$br.branch.name"]},
                        ]
                    }
                }
            },
            *PaymentController._pipeline_add_net_revenue_inr(),
            {
                "$group": {
                    "_id": "$resolved_branch_id",
                    "branch_name": {"$first": "$resolved_branch_name"},
                    "total_revenue": {"$sum": "$_net_inr"},
                    "transactions": {"$sum": 1},
                }
            },
            {"$sort": {"total_revenue": -1}},
        ]

        rows = await db.payments.aggregate(pipeline).to_list(length=200)
        # Normalize None branch bucket
        out = []
        for r in rows:
            bid = r.get("_id")
            out.append(
                {
                    "branch_id": bid if bid else "unassigned",
                    "branch_name": r.get("branch_name") or ("No branch on record" if not bid else "Branch"),
                    "total_revenue": float(r.get("total_revenue") or 0),
                    "transactions": int(r.get("transactions") or 0),
                }
            )

        if branch_id and branch_id != "all":
            want = "unassigned" if branch_id == "unassigned" else branch_id
            out = [r for r in out if r.get("branch_id") == want]

        return {
            "start_date": start_date,
            "end_date": end_date,
            "branches": out,
            "generated_at": datetime.utcnow().isoformat(),
        }

    @staticmethod
    async def get_payment_stats(
        current_user: dict = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        branch_id: Optional[str] = None,
    ):
        """Get payment statistics for dashboard"""
        db = get_db()

        # Build base filter for role-based access
        base_filter = {}
        if current_user:
            current_role = current_user.get("role")
            if current_role == "student":
                # Students can only see their own payment stats
                student_id = current_user.get("id")
                if not student_id:
                    raise HTTPException(status_code=403, detail="Student ID not found")
                base_filter["student_id"] = student_id
            elif current_role == "branch_manager":
                # Branch managers can only see stats from their managed branches
                branch_manager_id = current_user.get("id")
                if not branch_manager_id:
                    raise HTTPException(status_code=403, detail="Branch manager ID not found")

                # Find all branches managed by this branch manager
                managed_branches = await db.branches.find({"manager_id": branch_manager_id, "is_active": True}).to_list(length=None)

                if not managed_branches:
                    return {
                        "total_collected": 0,
                        "pending_payments": 0,
                        "this_month_collection": 0,
                        "total_students": 0,
                        "period_payment_count": 0,
                        "monthly_revenue": 0,
                        "payment_count": 0,
                        "average_payment": 0,
                    }

                # Get all branch IDs managed by this branch manager
                managed_branch_ids = [branch["id"] for branch in managed_branches]
                print(f"Branch manager {branch_manager_id} manages branches for payment stats: {managed_branch_ids}")

                # Filter by branch_id in branch_details
                base_filter["branch_details.branch_id"] = {"$in": managed_branch_ids}
            elif current_role == "coach" or current_role == "coach_admin":
                # Coaches can see stats from their assigned branch
                coach_id = current_user.get("id")
                if not coach_id:
                    raise HTTPException(status_code=403, detail="Coach ID not found")

                # Find coach's assigned branch
                coach_data = await db.coaches.find_one({"id": coach_id})
                if not coach_data:
                    return {
                        "total_collected": 0,
                        "pending_payments": 0,
                        "this_month_collection": 0,
                        "total_students": 0,
                        "period_payment_count": 0,
                        "monthly_revenue": 0,
                        "payment_count": 0,
                        "average_payment": 0,
                    }

                # Get assigned branch from coach data
                assigned_branch = coach_data.get("branch_id")
                if not assigned_branch:
                    return {
                        "total_collected": 0,
                        "pending_payments": 0,
                        "this_month_collection": 0,
                        "total_students": 0,
                        "period_payment_count": 0,
                        "monthly_revenue": 0,
                        "payment_count": 0,
                        "average_payment": 0,
                    }

                print(f"Coach {coach_id} has access to branch for payment stats: {assigned_branch}")

                # Filter by branch_id in branch_details
                base_filter["branch_details.branch_id"] = assigned_branch

        # Optional superadmin branch filter (do not affect other roles)
        branch_filter = None
        if current_user:
            r = str(current_user.get("role") or "").lower()
            is_super = r in ["super_admin", "superadmin"]
            if is_super and branch_id and branch_id != "all":
                branch_filter = branch_id

        # Build a robust match that:
        # - uses payment_date when present
        # - falls back to created_at when payment_date is missing/null
        period_start, period_end = PaymentController._payment_stats_parse_period(start_date, end_date)

        all_time_match_base: dict = {**base_filter}
        period_match_base: dict = {**base_filter}
        if period_start and period_end:
            period_match_base["$or"] = [
                {"payment_date": {"$gte": period_start, "$lte": period_end}},
                {"payment_date": {"$exists": False}, "created_at": {"$gte": period_start, "$lte": period_end}},
                {"payment_date": None, "created_at": {"$gte": period_start, "$lte": period_end}},
            ]

        # If branch_filter is requested, resolve branch via payment.branch_details OR enrollment.branch_id
        # using an aggregation pipeline (keeps totals consistent with branch-wise revenue endpoint).
        def _branch_resolution_pipeline(extra_match: dict):
            pipeline = [
                {"$match": extra_match},
                {
                    "$lookup": {
                        "from": "enrollments",
                        "localField": "enrollment_id",
                        "foreignField": "id",
                        "as": "enr",
                    }
                },
                {"$unwind": {"path": "$enr", "preserveNullAndEmptyArrays": True}},
                {
                    "$addFields": {
                        "resolved_branch_id": {"$ifNull": ["$branch_details.branch_id", "$enr.branch_id"]}
                    }
                },
            ]
            if branch_filter:
                want = None if branch_filter == "unassigned" else branch_filter
                pipeline.append(
                    {"$match": {"resolved_branch_id": want}}
                )
            return pipeline

        # total collected (ALL TIME; should not change with date filters) — net of Razorpay refunds
        total_match = PaymentController._merge_collected_revenue_filters(all_time_match_base)
        total_pipeline = (
            _branch_resolution_pipeline(total_match)
            + PaymentController._pipeline_add_net_revenue_inr()
            + [{"$group": {"_id": None, "total": {"$sum": "$_net_inr"}}}]
        )
        total_collected_result = await db.payments.aggregate(total_pipeline).to_list(1)
        total_collected = float(total_collected_result[0]["total"]) if total_collected_result else 0.0

        # pending payments (ALL TIME; should not change with date filters)
        pending_match = {**all_time_match_base, "payment_status": PaymentStatus.PENDING.value}
        pending_pipeline = _branch_resolution_pipeline(pending_match) + [
            {
                "$group": {
                    "_id": None,
                    "total": {
                        "$sum": {
                            "$convert": {"input": "$amount", "to": "double", "onError": 0.0, "onNull": 0.0}
                        }
                    },
                }
            }
        ]
        pending_payments_result = await db.payments.aggregate(pending_pipeline).to_list(1)
        pending_payments = float(pending_payments_result[0]["total"]) if pending_payments_result else 0.0

        # Period collection (matches dashboard date filter) vs calendar month fallback
        if period_start and period_end:
            period_match = PaymentController._merge_collected_revenue_filters(period_match_base)
            period_pipeline = (
                _branch_resolution_pipeline(period_match)
                + PaymentController._pipeline_add_net_revenue_inr()
                + [{"$group": {"_id": None, "total": {"$sum": "$_net_inr"}, "cnt": {"$sum": 1}}}]
            )
            period_result = await db.payments.aggregate(period_pipeline).to_list(1)
            this_month_collection = float(period_result[0]["total"]) if period_result else 0.0
            period_payment_count = int(period_result[0]["cnt"]) if period_result else 0
            distinct_students = await db.payments.distinct(
                "student_id",
                period_match,
            )
            total_students_period = len([x for x in distinct_students if x])
        else:
            current_month_start = datetime.utcnow().replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
            month_match = PaymentController._merge_collected_revenue_filters(
                {
                    **base_filter,
                    "$or": [
                        {"payment_date": {"$gte": current_month_start}},
                        {"payment_date": {"$exists": False}, "created_at": {"$gte": current_month_start}},
                        {"payment_date": None, "created_at": {"$gte": current_month_start}},
                    ],
                }
            )
            this_month_pipeline = (
                _branch_resolution_pipeline(month_match)
                + PaymentController._pipeline_add_net_revenue_inr()
                + [{"$group": {"_id": None, "total": {"$sum": "$_net_inr"}, "cnt": {"$sum": 1}}}]
            )
            this_month_result = await db.payments.aggregate(this_month_pipeline).to_list(1)
            this_month_collection = float(this_month_result[0]["total"]) if this_month_result else 0.0
            period_payment_count = int(this_month_result[0]["cnt"]) if this_month_result else 0
            total_students_period = None

        # Get total students count (for branch managers, count students in their branches)
        if current_user and current_user.get("role") == "branch_manager":
            # Count students enrolled in courses at managed branches
            managed_branch_ids = base_filter.get("branch_details.branch_id", {}).get("$in", [])
            if managed_branch_ids:
                student_count = await db.enrollments.distinct("student_id", {"branch_id": {"$in": managed_branch_ids}, "is_active": True})
                total_students = len(student_count)
            else:
                total_students = 0
        else:
            total_students = await db.users.count_documents({"role": "student"})

        if total_students_period is not None:
            display_students_for_period = total_students_period
        else:
            display_students_for_period = total_students

        avg_pay = (
            (this_month_collection / period_payment_count) if period_payment_count else 0
        )

        last_sync = await get_last_reconciled_at(db)
        return {
            "total_collected": total_collected,
            "pending_payments": pending_payments,
            "this_month_collection": this_month_collection,
            "total_students": total_students,
            "students_with_payments_in_period": display_students_for_period,
            "period_payment_count": period_payment_count,
            "monthly_revenue": this_month_collection,
            "payment_count": period_payment_count,
            "average_payment": avg_pay,
            "last_razorpay_sync_at": last_sync.isoformat() if last_sync else None,
        }

    @staticmethod
    async def get_payments(
        skip: int = 0,
        limit: int = 50,
        status: str = None,
        payment_type: str = None,
        current_user: dict = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        search: Optional[str] = None,
        branch_id: Optional[str] = None,
    ):
        """Get payments with filtering and student information"""
        try:
            db = get_db()

            if db is None:
                raise HTTPException(status_code=500, detail="Database connection not available")

            # Build filter query
            filter_query = {}
            if status and status != "all":
                filter_query["payment_status"] = status
            if payment_type and payment_type != "all":
                filter_query["payment_type"] = payment_type

            ps, pe = PaymentController._payment_stats_parse_period(start_date, end_date)
            if ps and pe:
                filter_query["$or"] = [
                    {"payment_date": {"$gte": ps, "$lte": pe}},
                    {"payment_date": {"$exists": False}, "created_at": {"$gte": ps, "$lte": pe}},
                    {"payment_date": None, "created_at": {"$gte": ps, "$lte": pe}},
                ]

            # Optional branch filter (requested by UI).
            # For real branch IDs we must resolve from payment.branch_details OR enrollment.branch_id
            # (some historical rows don't have branch_details populated).
            branch_filter_id = None
            if branch_id and branch_id != "all":
                if branch_id == "unassigned":
                    filter_query["$and"] = filter_query.get("$and", []) + [
                        {
                            "$or": [
                                {"branch_details.branch_id": {"$exists": False}},
                                {"branch_details.branch_id": None},
                                {"branch_details.branch_id": ""},
                            ]
                        }
                    ]
                else:
                    branch_filter_id = branch_id

            # Optional search: student name/email/phone + transaction + notes + course/branch names.
            # Implemented in a backward-compatible way without changing the response structure.
            if search and str(search).strip():
                term = str(search).strip()
                # Find matching students first (so search by name works even though payment docs store student_id).
                student_ids = []
                try:
                    user_q = {
                        "$or": [
                            {"full_name": {"$regex": term, "$options": "i"}},
                            {"first_name": {"$regex": term, "$options": "i"}},
                            {"last_name": {"$regex": term, "$options": "i"}},
                            {"email": {"$regex": term, "$options": "i"}},
                            {"phone": {"$regex": term, "$options": "i"}},
                        ]
                    }
                    users = await db.users.find(user_q, {"id": 1}).limit(50).to_list(length=50)
                    student_ids = [u.get("id") for u in users if u.get("id")]
                except Exception:
                    logger.exception("payment search user lookup failed")

                payment_text_q = {
                    "$or": [
                        {"transaction_id": {"$regex": term, "$options": "i"}},
                        {"razorpay_payment_id": {"$regex": term, "$options": "i"}},
                        {"razorpay_order_id": {"$regex": term, "$options": "i"}},
                        {"notes": {"$regex": term, "$options": "i"}},
                        {"course_details.course_name": {"$regex": term, "$options": "i"}},
                        {"branch_details.branch_name": {"$regex": term, "$options": "i"}},
                    ]
                }
                if student_ids:
                    payment_text_q["$or"].append({"student_id": {"$in": student_ids}})

                if filter_query:
                    filter_query = {"$and": [filter_query, payment_text_q]}
                else:
                    filter_query = payment_text_q

            # Apply role-based filtering
            managed_branch_ids = None
            if current_user:
                current_role = current_user.get("role")
                if current_role == "student":
                    # Students can only see their own payments
                    student_id = current_user.get("id")
                    if not student_id:
                        raise HTTPException(status_code=403, detail="Student ID not found")
                    filter_query["student_id"] = student_id
                    if not status:
                        # Hide cancelled attempts from student dashboard by default.
                        filter_query["payment_status"] = {"$ne": "cancelled"}
                elif current_role == "branch_manager":
                    # Branch managers can only see payments from their managed branches
                    branch_manager_id = current_user.get("id")
                    if not branch_manager_id:
                        raise HTTPException(status_code=403, detail="Branch manager ID not found")

                    # Find all branches managed by this branch manager
                    managed_branches = await db.branches.find({"manager_id": branch_manager_id, "is_active": True}).to_list(length=None)

                    if not managed_branches:
                        return {"payments": []}

                    # Get all branch IDs managed by this branch manager
                    managed_branch_ids = [branch["id"] for branch in managed_branches]
                    print(f"Branch manager {branch_manager_id} manages branches for payments: {managed_branch_ids}")

                    # Filter payments by branch_id in branch_details
                    filter_query["branch_details.branch_id"] = {"$in": managed_branch_ids}
                elif current_role == "coach" or current_role == "coach_admin":
                    # Coaches can see payments from their assigned branch
                    coach_id = current_user.get("id")
                    if not coach_id:
                        raise HTTPException(status_code=403, detail="Coach ID not found")

                    # Find coach's assigned branch
                    coach_data = await db.coaches.find_one({"id": coach_id})
                    if not coach_data:
                        return {"payments": []}

                    # Get assigned branch from coach data
                    assigned_branch = coach_data.get("branch_id")
                    if not assigned_branch:
                        return {"payments": []}

                    print(f"Coach {coach_id} has access to branch for payments: {assigned_branch}")

                    # Filter payments by branch_id in branch_details
                    filter_query["branch_details.branch_id"] = assigned_branch

            # Cap work before expensive lookups. Dedupe still runs on this recent window.
            pre_dedupe_fetch_limit = max(400, (skip + limit) * 8)

            pipeline = [
                {"$match": filter_query},
            ]
            if branch_filter_id:
                pipeline.extend([
                    {
                        "$lookup": {
                            "from": "enrollments",
                            "localField": "enrollment_id",
                            "foreignField": "id",
                            "as": "enr",
                        }
                    },
                    {"$unwind": {"path": "$enr", "preserveNullAndEmptyArrays": True}},
                    {
                        "$addFields": {
                            "resolved_branch_id": {"$ifNull": ["$branch_details.branch_id", "$enr.branch_id"]}
                        }
                    },
                    {"$match": {"resolved_branch_id": branch_filter_id}},
                ])
            pipeline.extend([
                {"$sort": {"created_at": -1}},
                {"$limit": pre_dedupe_fetch_limit},
                {
                    "$group": {
                        "_id": "$id",
                        "doc": {"$first": "$$ROOT"},
                    }
                },
                {"$replaceRoot": {"newRoot": "$doc"}},
                {
                    "$lookup": {
                        "from": "users",
                        "localField": "student_id",
                        "foreignField": "id",
                        "as": "student_info",
                    }
                },
                {"$unwind": {"path": "$student_info", "preserveNullAndEmptyArrays": True}},
                {
                    "$project": {
                        "id": 1,
                        "student_id": 1,
                        "enrollment_id": 1,
                        "student_name": {"$ifNull": ["$student_info.full_name", {"$concat": ["$student_info.first_name", " ", "$student_info.last_name"]}]},
                        "amount": 1,
                        "payment_type": 1,
                        "payment_method": 1,
                        "gateway_method": 1,
                        "gateway_payment_label": 1,
                        "status": 1,
                        "payment_status": 1,
                        "transaction_id": 1,
                        "razorpay_order_id": 1,
                        "payment_date": 1,
                        "notes": 1,
                        "course_name": {"$ifNull": ["$course_details.course_name", None]},
                        "branch_name": {"$ifNull": ["$branch_details.branch_name", None]},
                        "branch_id": {"$ifNull": ["$branch_details.branch_id", "$resolved_branch_id"]},
                        "created_at": 1,
                    }
                },
                {"$sort": {"created_at": -1}},
            ])

            payments = await db.payments.aggregate(pipeline).to_list(pre_dedupe_fetch_limit)

            # Convert MongoDB documents to JSON-serializable format
            serialized_payments = []
            for payment in payments:
                # Convert ObjectId and datetime objects to strings
                serialized_payment = {}
                for key, value in payment.items():
                    if key == "_id":
                        continue  # Skip MongoDB ObjectId
                    elif hasattr(value, 'isoformat'):  # datetime objects
                        serialized_payment[key] = value.isoformat()
                    else:
                        serialized_payment[key] = value
                serialized_payments.append(serialized_payment)

            # Strong deduplication by logical payment attempt key.
            deduped_map: Dict[str, dict] = {}
            for sp in serialized_payments:
                key = PaymentController._payment_dedup_key(sp)
                existing = deduped_map.get(key)
                deduped_map[key] = sp if existing is None else PaymentController._pick_better_payment(existing, sp)
            serialized_payments = list(deduped_map.values())
            serialized_payments.sort(
                key=lambda p: (
                    p.get("created_at") or "",
                    p.get("updated_at") or "",
                ),
                reverse=True,
            )
            total_after_dedupe = len(serialized_payments)
            serialized_payments = serialized_payments[skip: skip + limit]

            _METHOD_DISPLAY = {
                "digital_wallet": "Online (UPI / card / wallet)",
                "credit_card": "Credit card",
                "debit_card": "Debit card",
                "upi": "UPI",
                "net_banking": "Net banking",
                "cash": "Cash",
                "bank_transfer": "Bank transfer",
            }
            _GATEWAY_METHOD_DISPLAY = {
                "upi": "UPI",
                "card": "Card",
                "netbanking": "Net Banking",
                "wallet": "Wallet",
                "emi": "EMI",
                "paylater": "Pay Later",
                "bank_transfer": "Bank transfer",
                "razorpay": "Razorpay",
            }
            _RAW_STATUS_TO_PAYMENT_STATUS = {
                "success": PaymentStatus.PAID.value,
                "captured": PaymentStatus.PAID.value,
                "paid": PaymentStatus.PAID.value,
                "completed": PaymentStatus.PAID.value,
                "authorized": PaymentStatus.PROCESSING.value,
                "processing": PaymentStatus.PROCESSING.value,
                "initiated": PaymentStatus.PENDING.value,
                "created": PaymentStatus.PENDING.value,
                "pending": PaymentStatus.PENDING.value,
                "failed": PaymentStatus.FAILED.value,
                "error": PaymentStatus.FAILED.value,
                "cancelled": PaymentStatus.CANCELLED.value,
                "canceled": PaymentStatus.CANCELLED.value,
                "refunded": PaymentStatus.CANCELLED.value,
            }
            # Razorpay pay_* ids are not "digital wallet"; older rows may lack gateway_payment_label
            for sp in serialized_payments:
                raw_ps = str(sp.get("payment_status") or "").strip().lower()
                raw_status = str(sp.get("status") or "").strip().lower()
                if not raw_ps and raw_status:
                    mapped = _RAW_STATUS_TO_PAYMENT_STATUS.get(raw_status)
                    if mapped:
                        sp["payment_status"] = mapped
                        raw_ps = mapped
                elif raw_ps == PaymentStatus.PENDING.value and raw_status in {"success", "captured", "paid", "completed"}:
                    # Keep tracking view truthful when legacy rows have stale payment_status but final status success.
                    sp["payment_status"] = PaymentStatus.PAID.value
                    raw_ps = PaymentStatus.PAID.value

                if sp.get("gateway_payment_label"):
                    continue
                tid = sp.get("transaction_id") or ""
                pm = (sp.get("payment_method") or "").lower()
                gm = (sp.get("gateway_method") or "").lower()
                if gm:
                    sp["gateway_payment_label"] = _GATEWAY_METHOD_DISPLAY.get(gm) or gm.replace("_", " ").title()
                elif str(tid).startswith("pay_") and pm == "digital_wallet":
                    sp["gateway_payment_label"] = "Razorpay (online)"
                elif pm:
                    sp["gateway_payment_label"] = _METHOD_DISPLAY.get(pm) or pm.replace("_", " ").title()

            # Enrich missing course names from enrollment → course (older rows may lack course_details)
            missing_course = [sp for sp in serialized_payments if not sp.get("course_name") and sp.get("enrollment_id")]
            if missing_course:
                try:
                    eids = list({sp["enrollment_id"] for sp in missing_course})
                    ens = await db.enrollments.find(
                        {"id": {"$in": eids}},
                        {"_id": 0, "id": 1, "course_id": 1},
                    ).to_list(length=len(eids))
                    cid_by_eid = {e["id"]: e.get("course_id") for e in ens if e.get("id")}
                    cids = list({cid for cid in cid_by_eid.values() if cid})
                    course_map = {}
                    if cids:
                        courses = await db.courses.find(
                            {"id": {"$in": cids}},
                            {"_id": 0, "id": 1, "title": 1, "name": 1},
                        ).to_list(length=len(cids))
                        course_map = {
                            c["id"]: (c.get("title") or c.get("name"))
                            for c in courses
                            if c.get("id")
                        }
                    for sp in missing_course:
                        cid = cid_by_eid.get(sp.get("enrollment_id"))
                        if cid and course_map.get(cid):
                            sp["course_name"] = course_map[cid]
                except Exception:
                    logger.exception("enrich course_name for payment list")

            print(f"🔍 Payment query debug - Student ID: {filter_query.get('student_id', 'N/A')}")
            print(f"🔍 Total payments found: {len(payments)}, After deduplication: {len(serialized_payments)}")

            return {
                "payments": serialized_payments,
                "total": total_after_dedupe,
                "skip": skip,
                "limit": limit,
            }

        except Exception as e:
            print(f"Error in get_payments: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

    @staticmethod
    async def cancel_payment_attempt(payment_id: str, current_user: dict):
        """Cancel mistaken/pending payment attempts from admin payment tracking."""
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        payment = await db.payments.find_one({"id": payment_id})
        if not payment:
            raise HTTPException(status_code=404, detail="Payment not found")

        status_lc = str(payment.get("payment_status") or "").strip().lower()
        if status_lc in {"paid", "completed"}:
            raise HTTPException(status_code=400, detail="Paid transactions cannot be cancelled")
        if status_lc in {"cancelled", "canceled"}:
            return {"message": "Payment attempt already cancelled"}

        now = datetime.utcnow()
        actor = current_user.get("id") if current_user else None
        enrollment_id = payment.get("enrollment_id")

        update_filter = {"id": payment_id}
        update_many_filter = None
        if enrollment_id:
            # Cancel duplicate pending attempts created for the same checkout/enrollment.
            update_many_filter = {
                "student_id": payment.get("student_id"),
                "enrollment_id": enrollment_id,
                "payment_status": {"$in": ["pending", "processing", "failed", "overdue"]},
            }

        cancel_update = {
            "$set": {
                "payment_status": "cancelled",
                "status": "cancelled",
                "updated_at": now,
                "notes": f"Cancelled by super admin ({actor or 'unknown'})",
            }
        }

        if update_many_filter:
            await db.payments.update_many(update_many_filter, cancel_update)
        else:
            await db.payments.update_one(update_filter, cancel_update)

        if enrollment_id:
            has_paid = await db.payments.find_one(
                {
                    "enrollment_id": enrollment_id,
                    "payment_status": {"$in": ["paid", "completed"]},
                }
            )
            if not has_paid:
                await db.enrollments.update_one(
                    {"id": enrollment_id, "payment_status": {"$in": ["pending", "processing", "failed"]}},
                    {
                        "$set": {
                            "payment_status": "cancelled",
                            "status": "cancelled",
                            "is_active": False,
                            "updated_at": now,
                        }
                    },
                )

        return {"message": "Payment attempt cancelled successfully"}

    @staticmethod
    async def recover_cancelled_payment_attempt(
        payment_id: str,
        body: AdminPaymentRecoveryBody,
        current_user: dict,
    ):
        """
        Undo admin cancellation: restore pending checkout, or mark as paid / waived (super admin only).
        """
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        payment = await db.payments.find_one({"id": payment_id})
        if not payment:
            raise HTTPException(status_code=404, detail="Payment not found")

        status_lc = str(payment.get("payment_status") or "").strip().lower()
        if status_lc not in {"cancelled", "canceled"}:
            raise HTTPException(
                status_code=400,
                detail="Only cancelled payment rows can be recovered",
            )

        enrollment_id = payment.get("enrollment_id")
        if not enrollment_id:
            raise HTTPException(
                status_code=400,
                detail="Payment has no enrollment link; cannot recover",
            )

        if not await db.enrollments.find_one({"id": enrollment_id}):
            raise HTTPException(status_code=404, detail="Enrollment not found")

        now = datetime.utcnow()
        actor = current_user.get("id") if current_user else None
        actor_label = str(actor or "unknown")

        other_paid = await db.payments.find_one(
            {
                "enrollment_id": enrollment_id,
                "id": {"$ne": payment_id},
                "payment_status": {"$in": ["paid", "completed"]},
            }
        )
        if other_paid and body.action in {"mark_received", "waive"}:
            raise HTTPException(
                status_code=400,
                detail="This enrollment already has another paid payment; resolve that record first.",
            )

        if body.action == "restore_checkout":
            if other_paid:
                raise HTTPException(
                    status_code=400,
                    detail="This enrollment already has a paid payment; use a different recovery action or reconcile data.",
                )
            note = (body.note or "").strip()
            note_suffix = f" {note}" if note else ""
            await db.payments.update_one(
                {"id": payment_id},
                {
                    "$set": {
                        "payment_status": PaymentStatus.PENDING.value,
                        "status": "initiated",
                        "updated_at": now,
                        "notes": f"Restored to pending checkout by admin ({actor_label}){note_suffix}".strip(),
                        "payment_date": None,
                        "transaction_id": None,
                    },
                    "$unset": {
                        "razorpay_payment_id": "",
                        "razorpay_order_id": "",
                    },
                },
            )
            await db.enrollments.update_one(
                {"id": enrollment_id},
                {
                    "$set": {
                        "payment_status": EnrollmentPaymentStatus.PENDING.value,
                        "is_active": True,
                        "updated_at": now,
                    },
                    "$unset": {"status": "", "razorpay_last_order_id": ""},
                },
            )
            return {"message": "Enrollment restored to pending checkout; student can pay again."}

        # mark_received or waive — activate enrollment as paid on this row
        extra = (body.note or "").strip()
        if body.action == "waive":
            pm_label = "waived"
            base_note = f"Complimentary / waived by admin ({actor_label})"
            notes = f"{base_note}. {extra}" if extra else base_note
            txn = f"WAIVE-{uuid.uuid4().hex[:16].upper()}"
        else:
            pm_label = PaymentMethod.CASH.value
            base_note = f"Marked received by admin ({actor_label})"
            notes = f"{base_note}. {extra}" if extra else base_note
            txn = f"MANUAL-{uuid.uuid4().hex[:16].upper()}"

        await db.payments.update_one(
            {"id": payment_id},
            {
                "$set": {
                    "payment_status": PaymentStatus.PAID.value,
                    "status": "success",
                    "payment_method": pm_label,
                    "updated_at": now,
                    "payment_date": now,
                    "transaction_id": txn,
                    "notes": notes,
                },
                "$unset": {
                    "razorpay_payment_id": "",
                    "razorpay_order_id": "",
                },
            },
        )
        await db.enrollments.update_one(
            {"id": enrollment_id},
            {
                "$set": {
                    "payment_status": EnrollmentPaymentStatus.PAID.value,
                    "is_active": True,
                    "updated_at": now,
                },
                "$unset": {"status": ""},
            },
        )
        if body.action == "waive":
            return {"message": "Course marked complimentary; enrollment is active."}
        return {"message": "Payment marked received; enrollment is active."}

    @staticmethod
    async def export_payments(
        status: Optional[str] = None,
        payment_type: Optional[str] = None,
        branch_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        format: str = "csv",
        current_user: dict = None
    ):
        """Export payment reports"""
        try:
            # Build filter query
            filter_query = {}
            if status and status != "all":
                filter_query["payment_status"] = status
            if payment_type and payment_type != "all":
                filter_query["payment_type"] = payment_type

            # Add date range filter if provided
            if start_date or end_date:
                date_filter = {}
                if start_date:
                    try:
                        start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                        date_filter["$gte"] = start_dt
                    except ValueError:
                        raise HTTPException(status_code=400, detail="Invalid start_date format")
                if end_date:
                    try:
                        end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                        date_filter["$lte"] = end_dt
                    except ValueError:
                        raise HTTPException(status_code=400, detail="Invalid end_date format")
                filter_query["payment_date"] = date_filter

            # Store role-based filtering info for pipeline
            role_based_branches = None
            additional_branch_filter = None

            if current_user:
                user_role = current_user.get("role")
                if user_role == UserRole.BRANCH_MANAGER.value:
                    # Branch managers can only see payments from their managed branches
                    managed_branches = current_user.get("managed_branches", [])
                    if managed_branches:
                        role_based_branches = managed_branches
                    else:
                        # If no managed branches, return empty result
                        filter_query["_id"] = {"$exists": False}
                elif user_role == UserRole.COACH_ADMIN.value:
                    # Coach admins can see payments from their assigned branches
                    assigned_branches = current_user.get("assigned_branches", [])
                    if assigned_branches:
                        role_based_branches = assigned_branches
                    else:
                        # If no assigned branches, return empty result
                        filter_query["_id"] = {"$exists": False}
                elif user_role == UserRole.STUDENT.value:
                    # Students can only see their own payments
                    filter_query["student_id"] = current_user.get("user_id")

            # Additional branch filter if specified
            if branch_id and branch_id != "all":
                additional_branch_filter = branch_id

            db = get_db()
            if db is None:
                raise HTTPException(status_code=500, detail="Database connection not available")

            # Get payments with student information for export
            pipeline = [
                {"$match": filter_query},
                {
                    "$lookup": {
                        "from": "users",
                        "localField": "student_id",
                        "foreignField": "id",
                        "as": "student_info"
                    }
                },
                {"$unwind": {"path": "$student_info", "preserveNullAndEmptyArrays": True}},
                {
                    "$lookup": {
                        "from": "enrollments",
                        "localField": "enrollment_id",
                        "foreignField": "id",
                        "as": "enrollment_info"
                    }
                },
                {"$unwind": {"path": "$enrollment_info", "preserveNullAndEmptyArrays": True}},
                {
                    "$lookup": {
                        "from": "courses",
                        "localField": "enrollment_info.course_id",
                        "foreignField": "id",
                        "as": "course_info"
                    }
                },
                {"$unwind": {"path": "$course_info", "preserveNullAndEmptyArrays": True}},
                {
                    "$lookup": {
                        "from": "branches",
                        "localField": "enrollment_info.branch_id",
                        "foreignField": "id",
                        "as": "branch_info"
                    }
                },
                {"$unwind": {"path": "$branch_info", "preserveNullAndEmptyArrays": True}},
            ]

            # Add branch filtering after lookups if needed
            branch_filter_conditions = []
            if role_based_branches:
                branch_filter_conditions.extend([
                    {"branch_details.branch_id": {"$in": role_based_branches}},
                    {"enrollment_info.branch_id": {"$in": role_based_branches}}
                ])
            if additional_branch_filter:
                branch_filter_conditions.extend([
                    {"branch_details.branch_id": additional_branch_filter},
                    {"enrollment_info.branch_id": additional_branch_filter}
                ])

            if branch_filter_conditions:
                pipeline.append({"$match": {"$or": branch_filter_conditions}})

            # Add projection stage
            pipeline.extend([
                {
                    "$project": {
                        "student_name": {"$ifNull": ["$student_info.full_name", {"$concat": ["$student_info.first_name", " ", "$student_info.last_name"]}]},
                        "student_email": "$student_info.email",
                        "student_phone": "$student_info.phone",
                        "amount": 1,
                        "payment_type": 1,
                        "payment_method": 1,
                        "payment_status": 1,
                        "transaction_id": 1,
                        "payment_date": {"$dateToString": {"format": "%Y-%m-%d %H:%M:%S", "date": "$payment_date"}},
                        "due_date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$due_date"}},
                        "course_name": {
                            "$ifNull": [
                                "$course_details.course_name",
                                {"$ifNull": ["$course_info.name", "N/A"]}
                            ]
                        },
                        "branch_name": {
                            "$ifNull": [
                                "$branch_details.branch_name",
                                {"$ifNull": ["$branch_info.branch.name", "N/A"]}
                            ]
                        },
                        "notes": {"$ifNull": ["$notes", ""]},
                        "created_at": {"$dateToString": {"format": "%Y-%m-%d %H:%M:%S", "date": "$created_at"}}
                    }
                },
                {"$sort": {"payment_date": -1, "created_at": -1}}
            ])

            payments = await db.payments.aggregate(pipeline).to_list(None)  # Get all matching records

            if format == "csv":
                # Create CSV content
                output = io.StringIO()
                fieldnames = [
                    'student_name', 'student_email', 'student_phone', 'amount', 'payment_type',
                    'payment_method', 'payment_status', 'transaction_id', 'payment_date',
                    'due_date', 'course_name', 'branch_name', 'notes', 'created_at'
                ]
                writer = csv.DictWriter(output, fieldnames=fieldnames)
                writer.writeheader()

                # Convert payments to CSV-friendly format
                for payment in payments:
                    csv_row = {}
                    for field in fieldnames:
                        value = payment.get(field, '')
                        # Handle None values and convert to string
                        csv_row[field] = str(value) if value is not None else ''
                    writer.writerow(csv_row)

                return {
                    "content": output.getvalue(),
                    "filename": f"payment_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                    "content_type": "text/csv"
                }
            else:
                raise HTTPException(status_code=400, detail="Unsupported export format")

        except HTTPException:
            raise
        except Exception as e:
            print(f"Error in export_payments: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Failed to export payment reports: {str(e)}")
