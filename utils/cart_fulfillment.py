"""
M05-S05 cart checkout fulfillment — create enrollments after confirmed payment.

Duplicate-safe: unique (cart_checkout_id, cart_item_id) on enrollments and
status transition pending_payment → fulfilled via atomic update.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.cart_checkout_models import CartCheckoutStatus
from models.enrollment_models import Enrollment, PaymentStatus as EnrollmentPaymentStatus
from models.cart_models import CartStatus
from utils.database import get_db
from utils.enrollment_dates import resolve_enrollment_end_date

logger = logging.getLogger(__name__)


def fulfillment_key_for_checkout(checkout_id: str) -> str:
    return f"cart_checkout:{checkout_id}"


def enrollment_idempotency_key(checkout_id: str, cart_item_id: str) -> str:
    return f"{checkout_id}:{cart_item_id}"


async def ensure_cart_checkout_indexes(mongo_db) -> None:
    try:
        await mongo_db.cart_checkouts.create_index("id", unique=True, name="cart_checkouts_id")
    except Exception:
        logging.exception("Failed cart_checkouts id index")
    try:
        await mongo_db.cart_checkouts.create_index(
            [("owner_user_id", 1), ("status", 1), ("created_at", -1)],
            name="cart_checkouts_owner_status",
        )
    except Exception:
        logging.exception("Failed cart_checkouts owner index")
    try:
        await mongo_db.cart_checkouts.create_index(
            "razorpay_order_id",
            unique=True,
            sparse=True,
            name="cart_checkouts_razorpay_order",
        )
    except Exception:
        logging.exception("Failed cart_checkouts razorpay_order index")
    try:
        await mongo_db.enrollments.create_index(
            [("cart_checkout_id", 1), ("cart_item_id", 1)],
            unique=True,
            partialFilterExpression={
                "cart_checkout_id": {"$type": "string"},
                "cart_item_id": {"$type": "string"},
            },
            name="enrollments_cart_checkout_item",
        )
    except Exception:
        logging.exception("Failed enrollments cart checkout unique index")
    try:
        await mongo_db.enrollments.create_index(
            "enrollment_source_key",
            unique=True,
            sparse=True,
            name="enrollments_source_key",
        )
    except Exception:
        logging.exception("Failed enrollments source key index")


async def resolve_student_id_for_line(
    db,
    *,
    line: dict,
    owner: dict,
) -> str:
    """Map a cart student line to a real student user id (create dependent if needed)."""
    existing = (line.get("student_id") or "").strip()
    owner_id = owner.get("id")
    if existing:
        if existing == owner_id:
            return existing
        user = await db.users.find_one({"id": existing, "role": "student"})
        if not user:
            raise HTTPException(status_code=400, detail=f"Student profile not found for {line.get('label')}")
        # Allow owner-linked dependents created earlier via cart
        if user.get("primary_account_id") and user.get("primary_account_id") != owner_id:
            raise HTTPException(status_code=403, detail="Cannot enroll a student linked to another account")
        if not user.get("primary_account_id") and existing != owner_id:
            # Other full accounts: only owner themselves
            raise HTTPException(
                status_code=403,
                detail="You can only checkout enrollments for your account or family profiles you added",
            )
        return existing

    # Create a dependent student profile under the paying account
    line_id = line.get("student_line_id") or str(uuid.uuid4())
    label = (line.get("label") or "Student").strip() or "Student"
    parts = label.split(None, 1)
    first_name = parts[0][:80]
    last_name = (parts[1] if len(parts) > 1 else "")[:80]
    email = f"cart+{line_id.replace('-', '')[:16]}@family.rock.local"
    phone = (line.get("phone") or owner.get("phone") or "").strip() or f"000{line_id.replace('-', '')[:10]}"
    student_id = str(uuid.uuid4())
    now = datetime.utcnow()
    doc = {
        "id": student_id,
        "email": email,
        "phone": phone,
        "first_name": first_name,
        "last_name": last_name,
        "full_name": label,
        "role": "student",
        "is_active": True,
        "primary_account_id": owner_id,
        "created_via": "cart_checkout",
        "cart_student_line_id": line_id,
        "password_hash": None,
        "created_at": now,
        "updated_at": now,
    }
    # Reuse if we already created one for this line under this owner
    prior = await db.users.find_one(
        {
            "primary_account_id": owner_id,
            "cart_student_line_id": line_id,
            "role": "student",
        }
    )
    if prior:
        return prior["id"]
    await db.users.insert_one(doc)
    return student_id


async def create_pending_enrollment_for_item(
    db,
    *,
    checkout_id: str,
    item: dict,
    student_id: str,
    student_label: str,
) -> dict:
    """Create one pending enrollment for a cart line; skip if already exists for this checkout item."""
    cart_item_id = item.get("id")
    source_key = enrollment_idempotency_key(checkout_id, cart_item_id)
    existing = await db.enrollments.find_one({"enrollment_source_key": source_key})
    if existing:
        return existing

    duration_id = item.get("duration_id")
    start_date = datetime.utcnow()
    duration_row = await db.durations.find_one({"id": duration_id})
    if not duration_row:
        duration_row = await db.durations.find_one({"code": duration_id})
    months_hint = None
    if duration_row and duration_row.get("duration_months") is not None:
        try:
            months_hint = int(duration_row["duration_months"])
        except (TypeError, ValueError):
            months_hint = None
    end_date = await resolve_enrollment_end_date(db, duration_id, start_date, months_hint=months_hint)

    pricing = item.get("pricing") or {}
    enrollment = Enrollment(
        student_id=student_id,
        course_id=item["course_id"],
        branch_id=item["branch_id"],
        start_date=start_date,
        end_date=end_date,
        fee_amount=float(pricing.get("course_fee") or 0),
        admission_fee=float(pricing.get("admission_fee") or 0),
        payment_status=EnrollmentPaymentStatus.PENDING,
        is_active=True,
        duration_id=duration_id,
        batch_ref=(item.get("batch_ref") or None),
    )
    doc = enrollment.dict()
    doc["enrollment_date"] = start_date
    doc["cart_id"] = item.get("_cart_id")
    doc["cart_item_id"] = cart_item_id
    doc["cart_checkout_id"] = checkout_id
    doc["cart_student_line_id"] = item.get("student_line_id")
    doc["cart_student_label"] = student_label
    doc["enrollment_source_key"] = source_key
    doc["enrollment_source"] = "cart_checkout"
    try:
        await db.enrollments.insert_one(doc)
    except Exception as exc:
        # Unique index race: fetch winner
        logger.info("Enrollment insert race for %s: %s", source_key, exc)
        existing = await db.enrollments.find_one({"enrollment_source_key": source_key})
        if existing:
            return existing
        raise
    return doc


async def fulfill_cart_checkout(
    checkout_id: str,
    *,
    razorpay_payment_id: Optional[str] = None,
    razorpay_order_id: Optional[str] = None,
    actor: str = "cart_fulfillment",
) -> Dict[str, Any]:
    """
    Mark cart checkout paid/fulfilled and activate all linked enrollments.
    Safe to call multiple times (webhooks / retries).
    """
    db = get_db()
    now = datetime.utcnow()
    checkout = await db.cart_checkouts.find_one({"id": checkout_id})
    if not checkout:
        raise HTTPException(status_code=404, detail="Cart checkout not found")

    if checkout.get("status") == CartCheckoutStatus.FULFILLED.value:
        return {
            "already_fulfilled": True,
            "cart_checkout_id": checkout_id,
            "enrollment_ids": [l.get("enrollment_id") for l in (checkout.get("enrollment_links") or [])],
            "status": checkout.get("status"),
        }

    # Atomic claim: only one worker moves pending → paid (then fulfilled)
    claim = await db.cart_checkouts.update_one(
        {
            "id": checkout_id,
            "status": {"$in": [CartCheckoutStatus.PENDING_PAYMENT.value, CartCheckoutStatus.PAID.value]},
        },
        {
            "$set": {
                "status": CartCheckoutStatus.PAID.value,
                "razorpay_payment_id": razorpay_payment_id or checkout.get("razorpay_payment_id"),
                "razorpay_order_id": razorpay_order_id or checkout.get("razorpay_order_id"),
                "paid_at": checkout.get("paid_at") or now,
                "updated_at": now,
                "fulfillment_actor": actor,
            }
        },
    )
    claimed = await db.cart_checkouts.find_one({"id": checkout_id})
    if claim.matched_count == 0:
        if claimed and claimed.get("status") == CartCheckoutStatus.FULFILLED.value:
            return {
                "already_fulfilled": True,
                "cart_checkout_id": checkout_id,
                "enrollment_ids": [l.get("enrollment_id") for l in (claimed.get("enrollment_links") or [])],
                "status": claimed.get("status"),
            }
        raise HTTPException(status_code=409, detail="Checkout cannot be fulfilled in its current state")
    if not claimed:
        raise HTTPException(status_code=404, detail="Cart checkout not found")

    enrollment_ids: List[str] = []
    for link in claimed.get("enrollment_links") or []:
        eid = link.get("enrollment_id")
        if not eid:
            continue
        enrollment_ids.append(eid)
        await db.enrollments.update_one(
            {"id": eid},
            {
                "$set": {
                    "payment_status": EnrollmentPaymentStatus.PAID.value,
                    "is_active": True,
                    "updated_at": now,
                    "paid_via": "cart_checkout",
                    "cart_checkout_id": checkout_id,
                },
                "$unset": {"status": ""},
            },
        )
        # Cancel other pending payment rows for this enrollment (not the cart payment)
        await db.payments.update_many(
            {
                "enrollment_id": eid,
                "payment_status": {"$in": ["pending", "processing", "failed"]},
                "cart_checkout_id": {"$ne": checkout_id},
            },
            {"$set": {"payment_status": "cancelled", "status": "cancelled", "updated_at": now}},
        )

    # Mark cart payment paid
    pay_filter = {"cart_checkout_id": checkout_id}
    if razorpay_order_id:
        pay_filter = {"cart_checkout_id": checkout_id, "razorpay_order_id": razorpay_order_id}
    await db.payments.update_many(
        {
            "cart_checkout_id": checkout_id,
            "payment_status": {"$in": ["pending", "processing"]},
        },
        {
            "$set": {
                "payment_status": "paid",
                "status": "success",
                "transaction_id": razorpay_payment_id,
                "razorpay_payment_id": razorpay_payment_id,
                "payment_date": now,
                "updated_at": now,
                "notes": f"Cart checkout fulfilled by {actor}",
            }
        },
    )

    # Close the source cart
    cart_id = claimed.get("cart_id")
    if cart_id:
        await db.carts.update_one(
            {"id": cart_id, "status": CartStatus.ACTIVE.value},
            {
                "$set": {
                    "status": CartStatus.CHECKED_OUT.value,
                    "checked_out_at": now,
                    "cart_checkout_id": checkout_id,
                    "updated_at": now,
                }
            },
        )

    await db.cart_checkouts.update_one(
        {"id": checkout_id},
        {
            "$set": {
                "status": CartCheckoutStatus.FULFILLED.value,
                "fulfilled_at": now,
                "updated_at": now,
                "discount_audit": claimed.get("discount_snapshot"),
            }
        },
    )

    return {
        "already_fulfilled": False,
        "cart_checkout_id": checkout_id,
        "enrollment_ids": enrollment_ids,
        "status": CartCheckoutStatus.FULFILLED.value,
        "item_count": len(enrollment_ids),
    }


async def fulfill_from_payment_row(payment_row: dict, *, actor: str = "razorpay_webhook") -> Optional[Dict[str, Any]]:
    """Webhook / reconcile entry: fulfill when payment row carries cart_checkout_id."""
    checkout_id = (payment_row or {}).get("cart_checkout_id")
    if not checkout_id:
        notes = payment_row.get("notes") or ""
        # notes may contain checkout id in structured form
        if "cart_checkout:" in str(notes):
            checkout_id = str(notes).split("cart_checkout:")[-1].split()[0].strip()
    if not checkout_id:
        return None
    return await fulfill_cart_checkout(
        checkout_id,
        razorpay_payment_id=payment_row.get("razorpay_payment_id") or payment_row.get("transaction_id"),
        razorpay_order_id=payment_row.get("razorpay_order_id"),
        actor=actor,
    )
