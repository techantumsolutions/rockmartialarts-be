"""
M13-S03 Paid demo booking service — reserve slot, server-side fee, M06 Razorpay confirm.

Does not modify camp, training requests, cart, or enrollments.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.demo_booking_models import (
    ALLOWED_ADMIN_STATUS_TRANSITIONS,
    DemoBookingCreate,
    DemoBookingPaymentVerify,
    DemoBookingStatus,
    DemoBookingStatusUpdate,
)
from models.demo_schedule_models import DEFAULT_DEMO_FEE_INR, normalize_time_hhmm
from utils.database import get_db
from utils.demo_availability_service import (
    DEMO_TZ,
    HELD_BOOKING_STATUSES,
    _is_past_slot,
    _in_effective_window,
    _weekday_name,
)
from utils.helpers import serialize_doc
from utils.student_status_service import get_managed_branch_ids_for_user

logger = logging.getLogger(__name__)

COL = "demo_bookings"
COL_SCHEDULES = "demo_schedules"
PENDING_TTL_MINUTES = 20


async def ensure_demo_booking_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL].create_index("id", unique=True)
        await database[COL].create_index(
            [("schedule_id", 1), ("slot_date", 1), ("status", 1)]
        )
        await database[COL].create_index("razorpay_order_id")
        await database[COL].create_index([("created_at", -1)])
        await database[COL].create_index("participant_phone")
    except Exception:
        logger.exception("Failed ensuring demo_booking indexes")


def _normalize_phone(phone: str) -> str:
    return re.sub(r"\s+", "", (phone or "").strip())


async def expire_stale_pending_bookings(db=None) -> int:
    """Release seats held by abandoned pending_payment bookings."""
    database = db if db is not None else get_db()
    if database is None:
        return 0
    cutoff = datetime.utcnow() - timedelta(minutes=PENDING_TTL_MINUTES)
    try:
        result = await database[COL].update_many(
            {
                "status": DemoBookingStatus.PENDING_PAYMENT.value,
                "created_at": {"$lt": cutoff},
            },
            {
                "$set": {
                    "status": DemoBookingStatus.EXPIRED.value,
                    "payment_status": "failed",
                    "updated_at": datetime.utcnow(),
                    "expired_at": datetime.utcnow(),
                }
            },
        )
        return int(getattr(result, "modified_count", 0) or 0)
    except Exception:
        logger.debug("expire_stale_pending_bookings failed", exc_info=True)
        return 0


async def _held_count(db, *, schedule_id: str, slot_date: str) -> int:
    return int(
        await db[COL].count_documents(
            {
                "schedule_id": schedule_id,
                "slot_date": slot_date,
                "status": {"$in": list(HELD_BOOKING_STATUSES)},
            }
        )
    )


async def _assert_slot_bookable(db, schedule: dict, slot_date: str, start_time: str) -> str:
    """Validate date/time against schedule rules; return normalized end_time."""
    if not schedule.get("is_active", True):
        raise HTTPException(status_code=400, detail="Demo schedule is not active")

    try:
        d = date.fromisoformat(slot_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid slot_date") from exc

    if not _in_effective_window(schedule, d):
        raise HTTPException(status_code=400, detail="Slot date is outside schedule dates")

    day_name = _weekday_name(d)
    weekdays = schedule.get("weekdays") or []
    if day_name not in weekdays:
        raise HTTPException(status_code=400, detail="Slot date does not match schedule days")

    try:
        start_n = normalize_time_hhmm(start_time)
        end_n = normalize_time_hhmm(str(schedule.get("end_time") or ""))
        sched_start = normalize_time_hhmm(str(schedule.get("start_time") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if start_n != sched_start:
        raise HTTPException(status_code=400, detail="start_time does not match schedule")

    if _is_past_slot(d, start_n):
        raise HTTPException(status_code=400, detail="This slot is in the past")

    return end_n


async def create_demo_booking(
    body: DemoBookingCreate, *, current_user: Optional[dict] = None
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_demo_booking_indexes(db)
    await expire_stale_pending_bookings(db)

    schedule = await db[COL_SCHEDULES].find_one({"id": body.schedule_id})
    if not schedule:
        raise HTTPException(status_code=404, detail="Demo schedule not found")

    try:
        start_n = normalize_time_hhmm(body.start_time)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    end_n = await _assert_slot_bookable(db, schedule, body.slot_date, start_n)
    if body.end_time:
        try:
            if normalize_time_hhmm(body.end_time) != end_n:
                raise HTTPException(status_code=400, detail="end_time does not match schedule")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Server-side fee from schedule (never trust client)
    fee_inr = float(
        schedule.get("fee_inr")
        if schedule.get("fee_inr") is not None
        else DEFAULT_DEMO_FEE_INR
    )
    if fee_inr < 0:
        fee_inr = 0.0
    amount_paise = int(round(fee_inr * 100))

    capacity = schedule.get("capacity")
    capacity_int = int(capacity) if capacity is not None else None

    if capacity_int is not None:
        held = await _held_count(
            db, schedule_id=body.schedule_id, slot_date=body.slot_date
        )
        if held >= capacity_int:
            raise HTTPException(status_code=409, detail="This demo slot is fully booked")

    needs_payment = amount_paise >= 100
    now = datetime.utcnow()
    booking_id = str(uuid.uuid4())
    actor_id = (current_user or {}).get("id") if current_user else None

    if needs_payment:
        status = DemoBookingStatus.PENDING_PAYMENT.value
        payment_status = "pending"
    else:
        status = DemoBookingStatus.CONFIRMED.value
        payment_status = "not_required"

    doc = {
        "id": booking_id,
        "schedule_id": body.schedule_id,
        "slot_date": body.slot_date,
        "start_time": start_n,
        "end_time": end_n,
        "weekday": _weekday_name(date.fromisoformat(body.slot_date)),
        "branch_id": schedule.get("branch_id"),
        "branch_name": schedule.get("branch_name"),
        "course_id": schedule.get("course_id"),
        "course_name": schedule.get("course_name"),
        "schedule_title": schedule.get("title"),
        "participant_name": body.participant_name.strip(),
        "participant_phone": _normalize_phone(body.participant_phone),
        "participant_email": (body.participant_email or "").strip() or None,
        "participant_age": body.participant_age,
        "notes": (body.notes or "").strip() or None,
        "source": (body.source or "website").strip() or "website",
        "fee_inr": fee_inr,
        "amount_paise": amount_paise,
        "currency": "INR",
        "status": status,
        "payment_status": payment_status,
        "razorpay_order_id": None,
        "razorpay_payment_id": None,
        "razorpay_signature": None,
        "created_at": now,
        "updated_at": now,
        "created_by": actor_id,
        "confirmed_at": now if status == DemoBookingStatus.CONFIRMED.value else None,
        "timezone": "Asia/Kolkata",
    }
    await db[COL].insert_one(doc)

    # Capacity race: if we went over, roll back this booking
    if capacity_int is not None:
        held_after = await _held_count(
            db, schedule_id=body.schedule_id, slot_date=body.slot_date
        )
        if held_after > capacity_int:
            await db[COL].delete_one({"id": booking_id})
            raise HTTPException(status_code=409, detail="This demo slot is fully booked")

    # M15-S01: additive lead traceability (best-effort)
    try:
        from utils.lead_service import upsert_from_source

        await upsert_from_source(
            source_type="demo_booking",
            source_ref_type="demo_booking",
            source_ref_id=booking_id,
            name=body.participant_name,
            phone=body.participant_phone,
            email=body.participant_email,
            course=schedule.get("course_name") or "",
            branch_id=schedule.get("branch_id"),
            branch_name=schedule.get("branch_name"),
            source_label="demo_booking",
        )
    except Exception:
        logger.exception("Lead hook after demo booking failed")

    return {
        "message": "Demo booking reserved"
        if needs_payment
        else "Demo booking confirmed",
        "booking": serialize_doc(doc),
        "payment_required": needs_payment,
    }


async def get_demo_booking(booking_id: str) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": booking_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Demo booking not found")
    return {"booking": serialize_doc(doc)}


async def create_demo_booking_payment_order(
    booking_id: str, *, current_user: Optional[dict] = None
) -> Dict[str, Any]:
    from utils.razorpay_client import get_razorpay_client

    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await expire_stale_pending_bookings(db)

    doc = await db[COL].find_one({"id": booking_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Demo booking not found")

    if doc.get("payment_status") == "paid" or doc.get("status") == DemoBookingStatus.CONFIRMED.value:
        if doc.get("payment_status") == "paid" or doc.get("payment_status") == "not_required":
            raise HTTPException(status_code=409, detail="This booking is already confirmed")

    if doc.get("status") in {
        DemoBookingStatus.CANCELLED.value,
        DemoBookingStatus.EXPIRED.value,
        DemoBookingStatus.FAILED.value,
    }:
        raise HTTPException(status_code=400, detail="Cannot pay for a closed booking")

    if doc.get("status") != DemoBookingStatus.PENDING_PAYMENT.value:
        raise HTTPException(status_code=400, detail="Booking is not awaiting payment")

    amount_paise = int(doc.get("amount_paise") or 0)
    if amount_paise < 100:
        amount_paise = int(round(float(doc.get("fee_inr") or 0) * 100))
    if amount_paise < 100:
        raise HTTPException(status_code=400, detail="Invalid payment amount")

    # Re-check capacity still holds this pending seat (others shouldn't push over)
    schedule = await db[COL_SCHEDULES].find_one({"id": doc.get("schedule_id")})
    if schedule and schedule.get("capacity") is not None:
        held = await _held_count(
            db,
            schedule_id=str(doc.get("schedule_id")),
            slot_date=str(doc.get("slot_date")),
        )
        if held > int(schedule.get("capacity")):
            raise HTTPException(status_code=409, detail="This demo slot is fully booked")

    client = get_razorpay_client()
    try:
        order = client.order.create(
            {
                "amount": amount_paise,
                "currency": "INR",
                "payment_capture": 1,
                "notes": {
                    "demo_booking_id": booking_id,
                    "type": "demo_booking",
                    "participant": str(doc.get("participant_name") or "")[:80],
                    "slot_date": str(doc.get("slot_date") or ""),
                },
            }
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Razorpay demo booking order.create failed")
        raise HTTPException(
            status_code=502,
            detail="We could not reach the payment service. Please try again in a moment.",
        )

    now = datetime.utcnow()
    await db[COL].update_one(
        {"id": booking_id},
        {
            "$set": {
                "razorpay_order_id": order["id"],
                "amount_paise": amount_paise,
                "payment_status": "pending",
                "updated_at": now,
            }
        },
    )
    return {
        "order": {"id": order["id"], "amount": amount_paise, "currency": "INR"},
        "key": os.getenv("RAZORPAY_KEY_ID", "").strip(),
        "booking_id": booking_id,
    }


async def verify_demo_booking_payment(
    booking_id: str,
    body: DemoBookingPaymentVerify,
    *,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    from utils.razorpay_client import verify_razorpay_signature

    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    doc = await db[COL].find_one({"id": booking_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Demo booking not found")

    if (
        doc.get("payment_status") == "paid"
        and (doc.get("razorpay_payment_id") or "") == body.razorpay_payment_id
    ):
        return {"message": "Already confirmed", "booking": serialize_doc(doc)}

    if doc.get("status") in {
        DemoBookingStatus.CANCELLED.value,
        DemoBookingStatus.EXPIRED.value,
    }:
        raise HTTPException(status_code=400, detail="Booking is no longer payable")

    stored_order = (doc.get("razorpay_order_id") or "").strip()
    if stored_order and stored_order != body.razorpay_order_id:
        raise HTTPException(status_code=400, detail="Order does not match this booking")

    if not verify_razorpay_signature(
        body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature
    ):
        await db[COL].update_one(
            {"id": booking_id},
            {
                "$set": {
                    "payment_status": "failed",
                    "updated_at": datetime.utcnow(),
                }
            },
        )
        raise HTTPException(status_code=400, detail="Invalid payment signature")

    now = datetime.utcnow()
    await db[COL].update_one(
        {"id": booking_id},
        {
            "$set": {
                "status": DemoBookingStatus.CONFIRMED.value,
                "payment_status": "paid",
                "razorpay_order_id": body.razorpay_order_id,
                "razorpay_payment_id": body.razorpay_payment_id,
                "razorpay_signature": body.razorpay_signature,
                "paid_at": now,
                "confirmed_at": now,
                "updated_at": now,
            }
        },
    )
    updated = await db[COL].find_one({"id": booking_id})
    return {
        "message": "Demo booking confirmed",
        "booking": serialize_doc(updated),
    }


async def mark_demo_booking_payment_failed(booking_id: str) -> Dict[str, Any]:
    """Optional client signal when checkout is dismissed without paying (seat still held until TTL)."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": booking_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Demo booking not found")
    if doc.get("status") != DemoBookingStatus.PENDING_PAYMENT.value:
        return {"message": "No change", "booking": serialize_doc(doc)}
    await db[COL].update_one(
        {"id": booking_id},
        {"$set": {"payment_status": "failed", "updated_at": datetime.utcnow()}},
    )
    # Keep status pending_payment so seat remains held briefly; TTL expires it
    updated = await db[COL].find_one({"id": booking_id})
    return {"message": "Payment marked failed", "booking": serialize_doc(updated)}


def _role(user: Optional[dict]) -> str:
    return str((user or {}).get("role") or "").lower()


def _actor(user: Optional[dict]) -> Dict[str, Optional[str]]:
    if not user:
        return {"id": None, "name": "system", "role": "system"}
    return {
        "id": user.get("id"),
        "name": user.get("full_name") or user.get("email") or user.get("id"),
        "role": _role(user),
    }


async def _assert_admin_access(db, current_user: dict, doc: Optional[dict] = None) -> None:
    role = _role(current_user)
    if role in {"super_admin", "superadmin", "coach_admin"}:
        return
    if role == "branch_manager":
        managed = await get_managed_branch_ids_for_user(db, current_user)
        if not managed:
            raise HTTPException(status_code=403, detail="No managed branches")
        if doc is None:
            return
        bid = str(doc.get("branch_id") or "")
        if bid not in {str(b) for b in managed}:
            raise HTTPException(
                status_code=403, detail="Booking outside managed branches"
            )
        return
    raise HTTPException(status_code=403, detail="Not authorized for demo bookings")


async def _scoped_booking_query(
    db,
    current_user: dict,
    *,
    branch_id: Optional[str] = None,
    course_id: Optional[str] = None,
    status: Optional[str] = None,
    payment_status: Optional[str] = None,
    slot_date_from: Optional[str] = None,
    slot_date_to: Optional[str] = None,
    search: Optional[str] = None,
) -> Dict[str, Any]:
    await _assert_admin_access(db, current_user)
    await expire_stale_pending_bookings(db)

    q: Dict[str, Any] = {}
    role = _role(current_user)
    managed: List[str] = []
    if role == "branch_manager":
        managed = await get_managed_branch_ids_for_user(db, current_user)
        if not managed:
            return {"__empty__": True}
        q["branch_id"] = {"$in": [str(b) for b in managed]}

    if branch_id:
        bid = str(branch_id).strip()
        if role == "branch_manager" and bid not in {str(b) for b in managed}:
            raise HTTPException(
                status_code=403, detail="Branch outside managed branches"
            )
        q["branch_id"] = bid

    if course_id:
        q["course_id"] = str(course_id).strip()
    if status:
        q["status"] = str(status).strip().lower()
    if payment_status:
        q["payment_status"] = str(payment_status).strip().lower()

    date_clause: Dict[str, Any] = {}
    if slot_date_from:
        date_clause["$gte"] = str(slot_date_from).strip()[:10]
    if slot_date_to:
        date_clause["$lte"] = str(slot_date_to).strip()[:10]
    if date_clause:
        q["slot_date"] = date_clause

    if search:
        term = search.strip()
        if term:
            rx = {"$regex": term, "$options": "i"}
            q["$or"] = [
                {"participant_name": rx},
                {"participant_phone": rx},
                {"participant_email": rx},
                {"branch_name": rx},
                {"course_name": rx},
                {"schedule_title": rx},
                {"razorpay_payment_id": rx},
                {"id": rx},
            ]
    return q


async def list_demo_bookings_admin(
    *,
    current_user: dict,
    branch_id: Optional[str] = None,
    course_id: Optional[str] = None,
    status: Optional[str] = None,
    payment_status: Optional[str] = None,
    slot_date_from: Optional[str] = None,
    slot_date_to: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> Dict[str, Any]:
    """M13-S04 admin list with branch/date/course/status filters + BM scope."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_demo_booking_indexes(db)

    q = await _scoped_booking_query(
        db,
        current_user,
        branch_id=branch_id,
        course_id=course_id,
        status=status,
        payment_status=payment_status,
        slot_date_from=slot_date_from,
        slot_date_to=slot_date_to,
        search=search,
    )
    if q.get("__empty__"):
        return {"bookings": [], "total": 0, "count": 0}

    skip = max(0, skip)
    limit = max(1, min(limit, 200))
    total = await db[COL].count_documents(q)
    rows = (
        await db[COL]
        .find(q)
        .sort([("slot_date", -1), ("start_time", -1), ("created_at", -1)])
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {"bookings": serialize_doc(rows), "total": total, "count": len(rows)}


async def get_demo_booking_admin(
    booking_id: str, *, current_user: dict
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": booking_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Demo booking not found")
    await _assert_admin_access(db, current_user, doc)
    return {"booking": serialize_doc(doc)}


async def update_demo_booking_status_admin(
    booking_id: str, body: DemoBookingStatusUpdate, *, current_user: dict
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": booking_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Demo booking not found")
    await _assert_admin_access(db, current_user, doc)

    current = str(doc.get("status") or "")
    target = body.status.value
    if current == target:
        return {"message": "Status unchanged", "booking": serialize_doc(doc)}

    allowed = ALLOWED_ADMIN_STATUS_TRANSITIONS.get(current) or set()
    if target not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot change status from {current} to {target}",
        )

    actor = _actor(current_user)
    now = datetime.utcnow()
    updates: Dict[str, Any] = {
        "status": target,
        "updated_at": now,
        "updated_by": actor.get("id"),
        "status_note": (body.note or "").strip() or None,
    }
    if target == DemoBookingStatus.CANCELLED.value:
        updates["cancelled_at"] = now
        if doc.get("payment_status") == "pending":
            updates["payment_status"] = "failed"
    if target == DemoBookingStatus.EXPIRED.value:
        updates["expired_at"] = now
        if doc.get("payment_status") == "pending":
            updates["payment_status"] = "failed"

    await db[COL].update_one({"id": booking_id}, {"$set": updates})
    updated = await db[COL].find_one({"id": booking_id})
    return {"message": "Booking status updated", "booking": serialize_doc(updated)}


async def export_demo_bookings_admin(
    *,
    current_user: dict,
    branch_id: Optional[str] = None,
    course_id: Optional[str] = None,
    status: Optional[str] = None,
    payment_status: Optional[str] = None,
    slot_date_from: Optional[str] = None,
    slot_date_to: Optional[str] = None,
    search: Optional[str] = None,
) -> Dict[str, Any]:
    """CSV export for admin demo bookings (same filters as list)."""
    import csv
    import io

    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    q = await _scoped_booking_query(
        db,
        current_user,
        branch_id=branch_id,
        course_id=course_id,
        status=status,
        payment_status=payment_status,
        slot_date_from=slot_date_from,
        slot_date_to=slot_date_to,
        search=search,
    )
    rows = [] if q.get("__empty__") else (
        await db[COL]
        .find(q)
        .sort([("slot_date", -1), ("start_time", -1)])
        .to_list(length=5000)
    )

    fieldnames = [
        "id",
        "slot_date",
        "start_time",
        "end_time",
        "branch_name",
        "course_name",
        "participant_name",
        "participant_phone",
        "participant_email",
        "participant_age",
        "fee_inr",
        "status",
        "payment_status",
        "razorpay_payment_id",
        "razorpay_order_id",
        "source",
        "notes",
        "created_at",
        "confirmed_at",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        csv_row = {}
        for field in fieldnames:
            value = row.get(field, "")
            if hasattr(value, "isoformat"):
                value = value.isoformat()
            csv_row[field] = "" if value is None else str(value)
        writer.writerow(csv_row)

    return {
        "content": output.getvalue(),
        "filename": f"demo_bookings_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv",
        "content_type": "text/csv",
        "total": len(rows),
    }


async def summarize_demo_bookings_admin(
    *, current_user: dict, branch_id: Optional[str] = None
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    q = await _scoped_booking_query(db, current_user, branch_id=branch_id)
    if q.get("__empty__"):
        return {"total": 0, "by_status": {}, "by_payment_status": {}}

    pipeline = [
        {"$match": q},
        {
            "$facet": {
                "total": [{"$count": "n"}],
                "by_status": [{"$group": {"_id": "$status", "count": {"$sum": 1}}}],
                "by_payment_status": [
                    {"$group": {"_id": "$payment_status", "count": {"$sum": 1}}}
                ],
            }
        },
    ]
    agg = await db[COL].aggregate(pipeline).to_list(length=1)
    facet = (agg or [{}])[0]

    def _map(rows):
        out: Dict[str, int] = {}
        for r in rows or []:
            out[str(r.get("_id") or "unknown")] = int(r.get("count") or 0)
        return out

    total_rows = facet.get("total") or []
    return {
        "total": int((total_rows[0] or {}).get("n") or 0) if total_rows else 0,
        "by_status": _map(facet.get("by_status")),
        "by_payment_status": _map(facet.get("by_payment_status")),
    }
