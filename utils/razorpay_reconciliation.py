"""
Razorpay reconciliation utilities.

Goals:
- Treat Razorpay as the source of truth for gateway state.
- Keep existing LMS flows intact (client confirm endpoint still works).
- Provide safe, idempotent updates to Mongo for webhook + scheduled sync.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests

from utils.razorpay_client import get_razorpay_client

logger = logging.getLogger(__name__)


class RazorpayGatewayStatus:
    # Razorpay payment entity statuses: created/authorized/captured/failed/refunded
    CREATED = "created"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    FAILED = "failed"
    REFUNDED = "refunded"


def verify_razorpay_webhook_signature(raw_body: bytes, signature: str) -> bool:
    """
    Verify Razorpay webhook signature.
    Razorpay docs: signature = HMAC_SHA256(raw_body, webhook_secret)
    """
    secret = (os.getenv("RAZORPAY_WEBHOOK_SECRET") or "").strip()
    if not secret or not raw_body or not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _requests_session() -> requests.Session:
    sess = requests.Session()
    sess.trust_env = False
    return sess


def _auth_tuple() -> Optional[Tuple[str, str]]:
    key = (os.getenv("RAZORPAY_KEY_ID") or "").strip()
    sec = (os.getenv("RAZORPAY_KEY_SECRET") or "").strip()
    if not key or not sec:
        return None
    return key, sec


def fetch_payment(payment_id: str) -> Optional[dict]:
    """Fetch Razorpay payment entity."""
    auth = _auth_tuple()
    if not auth or not payment_id:
        return None
    url = f"https://api.razorpay.com/v1/payments/{payment_id}"
    try:
        r = _requests_session().get(url, auth=auth, timeout=25)
        if r.status_code == 200:
            return r.json()
        logger.warning("Razorpay fetch_payment HTTP %s payment_id=%s", r.status_code, payment_id)
    except Exception:
        logger.exception("Razorpay fetch_payment failed payment_id=%s", payment_id)
    return None


def fetch_order_payments(order_id: str) -> List[dict]:
    """Fetch Razorpay payments for an order."""
    auth = _auth_tuple()
    if not auth or not order_id:
        return []
    url = f"https://api.razorpay.com/v1/orders/{order_id}/payments"
    try:
        r = _requests_session().get(url, auth=auth, timeout=25)
        if r.status_code == 200:
            js = r.json() or {}
            items = js.get("items") or []
            return [x for x in items if isinstance(x, dict)]
        logger.warning("Razorpay fetch_order_payments HTTP %s order_id=%s", r.status_code, order_id)
    except Exception:
        logger.exception("Razorpay fetch_order_payments failed order_id=%s", order_id)
    return []


def fetch_order(order_id: str) -> Optional[dict]:
    """Fetch Razorpay order entity (status, amount_paid, etc.)."""
    auth = _auth_tuple()
    if not auth or not order_id:
        return None
    url = f"https://api.razorpay.com/v1/orders/{order_id}"
    try:
        r = _requests_session().get(url, auth=auth, timeout=25)
        if r.status_code == 200:
            return r.json()
        logger.warning("Razorpay fetch_order HTTP %s order_id=%s", r.status_code, order_id)
    except Exception:
        logger.exception("Razorpay fetch_order failed order_id=%s", order_id)
    return None


def fetch_refunds_for_payment(payment_id: str) -> List[dict]:
    """Fetch refunds list for a payment (helps compute partial refunds)."""
    auth = _auth_tuple()
    if not auth or not payment_id:
        return []
    url = f"https://api.razorpay.com/v1/payments/{payment_id}/refunds"
    try:
        r = _requests_session().get(url, auth=auth, timeout=25)
        if r.status_code == 200:
            js = r.json() or {}
            items = js.get("items") or []
            return [x for x in items if isinstance(x, dict)]
        # 404 can happen for non-existent payments; treat as no refunds.
        if r.status_code not in (404,):
            logger.warning("Razorpay fetch_refunds HTTP %s payment_id=%s", r.status_code, payment_id)
    except Exception:
        logger.exception("Razorpay fetch_refunds failed payment_id=%s", payment_id)
    return []


def map_gateway_to_internal_status(
    gateway_status: str,
    captured: Optional[bool],
    refunded_amount_paise: Optional[int],
    amount_paise: Optional[int],
) -> Dict[str, Any]:
    """
    Backward-compatible mapping:
    - Keep existing `payment_status` enum values (pending/processing/paid/failed/cancelled).
    - Add `refund_status` to represent refunded vs partial refund.
    """
    gs = (gateway_status or "").strip().lower()

    internal = "pending"
    refund_status = None
    if gs == RazorpayGatewayStatus.CREATED:
        internal = "pending"
    elif gs == RazorpayGatewayStatus.AUTHORIZED:
        internal = "processing"
    elif gs == RazorpayGatewayStatus.CAPTURED:
        internal = "paid"
    elif gs == RazorpayGatewayStatus.FAILED:
        internal = "failed"
    elif gs == RazorpayGatewayStatus.REFUNDED:
        # Preserve compatibility: payment_status cannot become a new enum without front-end changes.
        internal = "cancelled"
        refund_status = "refunded"
    else:
        # Unknown → keep as pending (safe); reconciliation will not mark paid unless captured.
        internal = "pending"

    # If captured flag exists and is false, keep as processing unless failed.
    if internal == "paid" and captured is False:
        internal = "processing"

    if refunded_amount_paise is not None and amount_paise is not None and amount_paise > 0:
        if refunded_amount_paise <= 0:
            pass
        elif refunded_amount_paise >= amount_paise:
            refund_status = refund_status or "refunded"
            # keep payment_status as cancelled in refund case to avoid overstating revenue
            internal = "cancelled"
        else:
            refund_status = "partially_refunded"
            # Keep status paid (money was captured) but revenue calc must subtract refunded_amount
            internal = "paid"

    return {"payment_status": internal, "refund_status": refund_status}


def normalize_gateway_fields(payment_entity: dict) -> Dict[str, Any]:
    """
    Normalize the subset of Razorpay payment fields we persist.
    All values are safe JSON primitives for Mongo.
    """
    if not payment_entity:
        return {}

    created_at = payment_entity.get("created_at")
    created_dt = None
    if isinstance(created_at, (int, float)) and created_at > 0:
        try:
            created_dt = datetime.utcfromtimestamp(int(created_at))
        except Exception:
            created_dt = None

    notes = payment_entity.get("notes") if isinstance(payment_entity.get("notes"), dict) else None
    return {
        "razorpay_payment_id": payment_entity.get("id"),
        "razorpay_order_id": payment_entity.get("order_id") or payment_entity.get("orderId"),
        "razorpay_status": payment_entity.get("status"),
        "razorpay_amount_paise": payment_entity.get("amount"),
        "razorpay_currency": payment_entity.get("currency"),
        "razorpay_method": payment_entity.get("method"),
        "razorpay_captured": payment_entity.get("captured"),
        "razorpay_refunded_amount_paise": payment_entity.get("refunded_amount"),
        "razorpay_email": payment_entity.get("email"),
        "razorpay_contact": payment_entity.get("contact"),
        "razorpay_created_at": created_dt,
        "razorpay_notes": notes,
    }


async def ensure_collections_indexes(db) -> None:
    """
    Create additive indexes for reconciliation/webhook collections.
    Safe to call repeatedly.
    """
    try:
        await db.razorpay_webhook_events.create_index("event_id", unique=True, sparse=True)
        await db.razorpay_webhook_events.create_index([("created_at", -1)])
        # Unique sparse gateway id prevents double-credit races (confirm + webhook)
        try:
            await db.payments.create_index(
                "razorpay_payment_id",
                unique=True,
                sparse=True,
                name="razorpay_payment_id_unique_sparse",
            )
        except Exception:
            await db.payments.create_index("razorpay_payment_id", sparse=True)
        await db.payments.create_index("razorpay_order_id", sparse=True)
        await db.payments.create_index([("payment_status", 1), ("created_at", -1)])
        await db.payments.create_index([("enrollment_id", 1), ("created_at", -1)], sparse=True)
        await db.payments.create_index([("student_id", 1), ("created_at", -1)], sparse=True)
        await db.payments.create_index("id", unique=True, sparse=True)
        await db.payment_reconciliation_runs.create_index([("created_at", -1)])
        await db.system_meta.create_index("key", unique=True)
        # Hot secondary indexes for related collections
        try:
            await db.attendance.create_index(
                [("student_id", 1), ("date", -1)],
                sparse=True,
                name="attendance_student_date",
            )
            await db.attendance.create_index(
                [("branch_id", 1), ("date", -1)],
                sparse=True,
                name="attendance_branch_date",
            )
        except Exception:
            logger.exception("attendance index ensure failed (non-fatal)")
        try:
            await db.reg_checkout_payments.create_index(
                "razorpay_order_id", sparse=True, name="reg_checkout_order"
            )
            await db.reg_checkout_payments.create_index(
                "razorpay_payment_id", sparse=True, name="reg_checkout_payment"
            )
            await db.reg_checkout_payments.create_index(
                [("status", 1), ("created_at", -1)], name="reg_checkout_status_created"
            )
        except Exception:
            logger.exception("reg_checkout index ensure failed (non-fatal)")
        try:
            await db.export_jobs.create_index([("created_at", -1)])
            await db.export_jobs.create_index("id", unique=True, sparse=True)
            await db.export_jobs.create_index([("status", 1), ("created_at", -1)])
        except Exception:
            logger.exception("export_jobs index ensure failed (non-fatal)")
        try:
            await db.users.create_index("email", unique=True, sparse=True, name="users_email_unique")
        except Exception:
            # Duplicates may exist historically — non-unique fallback
            try:
                await db.users.create_index("email", sparse=True, name="users_email")
            except Exception:
                logger.exception("users email index ensure failed (non-fatal)")
    except Exception:
        # Index creation can fail on insufficient permissions; continue safely.
        logger.exception("Index ensure failed (non-fatal)")


def _now() -> datetime:
    return datetime.utcnow()


async def set_last_reconciled_at(db, ts: datetime) -> None:
    try:
        await db.system_meta.update_one(
            {"key": "payments_last_reconciled_at"},
            {"$set": {"key": "payments_last_reconciled_at", "value": ts, "updated_at": _now()}},
            upsert=True,
        )
    except Exception:
        logger.exception("Failed to store payments_last_reconciled_at")


async def get_last_reconciled_at(db) -> Optional[datetime]:
    try:
        doc = await db.system_meta.find_one({"key": "payments_last_reconciled_at"})
        v = (doc or {}).get("value")
        return v if isinstance(v, datetime) else None
    except Exception:
        return None


def _enrollment_id_from_notes(notes: Optional[dict]) -> Optional[str]:
    if not isinstance(notes, dict):
        return None
    v = notes.get("enrollment_id") or notes.get("enrollmentId")
    if v is None:
        return None
    s = str(v).strip()
    return s or None


async def reconcile_one_payment_row(
    db,
    payment_row: dict,
    *,
    actor: str,
    reason: str,
) -> Dict[str, Any]:
    """
    Reconcile a single Mongo `payments` row against Razorpay.

    Returns summary info; does not raise unless DB is unavailable.
    """
    if not payment_row:
        return {"updated": False, "reason": "missing_row"}

    now = _now()

    pid = str(payment_row.get("id") or "").strip()
    if not pid:
        pid = str(payment_row.get("_id") or "").strip()

    order_id = (payment_row.get("razorpay_order_id") or "").strip() if payment_row.get("razorpay_order_id") else None
    payment_id = (payment_row.get("razorpay_payment_id") or payment_row.get("transaction_id") or "").strip()
    if payment_id and not str(payment_id).startswith("pay_"):
        # transaction_id can be internal TXN... for registration; do not treat it as Razorpay payment id.
        payment_id = payment_row.get("razorpay_payment_id") or ""
        payment_id = str(payment_id).strip()

    gateway_entity = None
    if payment_id:
        gateway_entity = fetch_payment(payment_id)
    if gateway_entity is None and order_id:
        payments = fetch_order_payments(order_id)
        # pick best candidate: captured > authorized > created; latest created_at as tie-breaker
        def _rank(e: dict) -> Tuple[int, int]:
            st = str(e.get("status") or "").lower()
            rank = {"captured": 3, "authorized": 2, "created": 1, "failed": 0, "refunded": 3}.get(st, -1)
            ts = int(e.get("created_at") or 0) if isinstance(e.get("created_at"), (int, float)) else 0
            return rank, ts

        if payments:
            payments.sort(key=_rank, reverse=True)
            gateway_entity = payments[0]

    if gateway_entity is None:
        # No payment entity found — check if the Razorpay order itself is stale/abandoned.
        # Orders with status "created" (no attempt) or "attempted" (all failed, amount_paid=0)
        # that are older than 30 minutes are considered abandoned.
        if order_id and payment_row.get("payment_status") in ("pending", "processing"):
            rz_order = fetch_order(order_id)
            if rz_order:
                rz_order_status = str(rz_order.get("status") or "").lower()
                rz_amount_paid = int(rz_order.get("amount_paid") or 0)
                created_at = payment_row.get("created_at")
                age_seconds = (now - created_at).total_seconds() if isinstance(created_at, datetime) else 0
                abandoned_threshold = 30 * 60  # 30 minutes
                if rz_amount_paid == 0 and rz_order_status in ("created", "attempted") and age_seconds > abandoned_threshold:
                    cancel_patch = {
                        "payment_status": "cancelled",
                        "status": "cancelled",
                        "updated_at": now,
                        "reconciled_at": now,
                        "reconciled_by": actor,
                        "reconciliation_reason": f"{reason}:abandoned_{rz_order_status}",
                        "notes": f"Auto-cancelled: abandoned checkout (Razorpay order {rz_order_status}, no payment received)",
                    }
                    await db.payments.update_one({"_id": payment_row["_id"]}, {"$set": cancel_patch})
                    logger.info(
                        "Reconciled abandoned payment row=%s order=%s rz_status=%s age_min=%.0f",
                        pid, order_id, rz_order_status, age_seconds / 60,
                    )
                    return {"updated": True, "payment_row_id": pid, "razorpay_order_id": order_id, "reason": f"abandoned_{rz_order_status}"}
        return {"updated": False, "reason": "no_gateway_entity", "payment_row_id": pid}

    norm = normalize_gateway_fields(gateway_entity)
    mapped = map_gateway_to_internal_status(
        str(norm.get("razorpay_status") or ""),
        norm.get("razorpay_captured"),
        norm.get("razorpay_refunded_amount_paise"),
        norm.get("razorpay_amount_paise"),
    )

    update = {
        **norm,
        "payment_status": mapped["payment_status"],
        "refund_status": mapped["refund_status"],
        # Keep legacy `status` field consistent with payment_status where useful
        "status": "success" if mapped["payment_status"] == "paid" else (mapped["payment_status"] or payment_row.get("status")),
        "reconciled_at": now,
        "reconciled_by": actor,
        "reconciliation_reason": reason,
        "updated_at": now,
    }

    # If gateway notes contains enrollment_id and our row lacks it, backfill it.
    enr_from_notes = _enrollment_id_from_notes(norm.get("razorpay_notes"))
    if enr_from_notes and not payment_row.get("enrollment_id"):
        update["enrollment_id"] = enr_from_notes

    # If we found a payment_id but row doesn't have transaction_id, set it (Razorpay IDs start with pay_)
    if norm.get("razorpay_payment_id") and not payment_row.get("transaction_id"):
        update["transaction_id"] = norm.get("razorpay_payment_id")

    # Avoid overwriting user-entered notes for offline payments
    if payment_row.get("payment_method") not in ("digital_wallet",) and payment_row.get("gateway_method") not in ("razorpay", None, ""):
        update.pop("razorpay_notes", None)

    res = await db.payments.update_one({"_id": payment_row["_id"]}, {"$set": update})
    updated = bool(res.modified_count)

    # Subscription auto-repair: if payment is now paid, ensure enrollment is marked paid/active.
    enrollment_id = update.get("enrollment_id") or payment_row.get("enrollment_id")
    if mapped["payment_status"] == "paid" and enrollment_id:
        await db.enrollments.update_one(
            {"id": enrollment_id},
            {
                "$set": {"payment_status": "paid", "is_active": True, "updated_at": now},
                # If an admin cancelled a pending attempt earlier, clear the cancelled marker
                # once Razorpay is the source of truth for success.
                "$unset": {"status": ""},
            },
        )
        # Cancel sibling pending attempts for the same enrollment to prevent stale pending in history.
        await db.payments.update_many(
            {
                "enrollment_id": enrollment_id,
                "payment_status": {"$in": ["pending", "processing", "failed"]},
                "_id": {"$ne": payment_row["_id"]},
            },
            {"$set": {"payment_status": "cancelled", "status": "cancelled", "updated_at": now}},
        )

    return {
        "updated": updated,
        "payment_row_id": pid,
        "razorpay_payment_id": norm.get("razorpay_payment_id"),
        "razorpay_order_id": norm.get("razorpay_order_id"),
        "razorpay_status": norm.get("razorpay_status"),
        "payment_status": mapped["payment_status"],
        "refund_status": mapped["refund_status"],
    }


async def reconcile_payments_batch(
    db,
    *,
    actor: str,
    reason: str,
    lookback_days: int = 30,
    pending_stuck_minutes: int = 5,
    limit: int = 300,
) -> Dict[str, Any]:
    """
    Reconcile a batch of likely-mismatched rows:
    - pending/processing older than pending_stuck_minutes
    - rows missing razorpay ids but having order id
    - recent rows within lookback_days
    """
    now = _now()
    cutoff_recent = now - timedelta(days=max(1, int(lookback_days)))
    cutoff_stuck = now - timedelta(minutes=max(1, int(pending_stuck_minutes)))

    q = {
        "$or": [
            # stuck pending attempts
            {
                "payment_status": {"$in": ["pending", "processing"]},
                "created_at": {"$lte": cutoff_stuck},
            },
            # mismatched: status says success but payment_status still pending
            {
                "payment_status": {"$in": ["pending", "processing"]},
                "status": {"$in": ["success", "captured", "paid", "completed"]},
            },
            # missing gateway ids but has order id
            {
                "razorpay_order_id": {"$exists": True, "$ne": None},
                "$or": [
                    {"razorpay_payment_id": {"$exists": False}},
                    {"razorpay_payment_id": None},
                    {"razorpay_payment_id": ""},
                ],
            },
            # recent activity
            {"created_at": {"$gte": cutoff_recent}},
        ]
    }

    cursor = db.payments.find(q).sort("created_at", -1).limit(int(limit))
    rows = await cursor.to_list(length=int(limit))

    updated = 0
    checked = 0
    results: List[Dict[str, Any]] = []
    for row in rows:
        checked += 1
        try:
            out = await reconcile_one_payment_row(db, row, actor=actor, reason=reason)
            results.append(out)
            if out.get("updated"):
                updated += 1
        except Exception:
            logger.exception("Reconcile row failed payment_id=%s", row.get("id"))
            results.append({"updated": False, "error": "exception", "payment_row_id": row.get("id")})

    run_doc = {
        "created_at": now,
        "actor": actor,
        "reason": reason,
        "checked": checked,
        "updated": updated,
        "lookback_days": lookback_days,
        "pending_stuck_minutes": pending_stuck_minutes,
        "limit": limit,
    }
    try:
        await db.payment_reconciliation_runs.insert_one(run_doc)
    except Exception:
        logger.exception("Failed to write reconciliation run log")

    await set_last_reconciled_at(db, now)
    return {"checked": checked, "updated": updated, "run": run_doc, "sample": results[:25]}

