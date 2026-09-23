"""
M17-S03 Academy Event Registration service.

Collection: academy_event_registrations
Free confirm or Razorpay paid flow (mirrors demo bookings). Capacity held while pending.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import HTTPException

from models.academy_event_models import AcademyEventStatus
from models.academy_event_registration_models import (
    HELD_REGISTRATION_STATUSES,
    AcademyEventRegistrationCreate,
    AcademyEventRegistrationPaymentVerify,
    AcademyEventRegistrationStatus,
    normalize_registration_fields,
)
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.indian_phone import canonical_indian_phone

logger = logging.getLogger(__name__)

COL = "academy_event_registrations"
COL_EVENTS = "academy_events"
PENDING_TTL_MINUTES = 20


async def ensure_academy_event_registration_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL].create_index("id", unique=True)
        await database[COL].create_index([("event_id", 1), ("status", 1)])
        await database[COL].create_index("razorpay_order_id")
        await database[COL].create_index([("created_at", -1)])
        await database[COL].create_index("participant_phone")
    except Exception:
        logger.exception("Failed ensuring academy_event_registration indexes")


def _normalize_phone(phone: str) -> str:
    canonical = canonical_indian_phone(phone)
    if not canonical:
        raise HTTPException(status_code=400, detail="Invalid phone number.")
    return canonical


async def expire_stale_pending_registrations(db=None) -> int:
    database = db if db is not None else get_db()
    if database is None:
        return 0
    cutoff = datetime.utcnow() - timedelta(minutes=PENDING_TTL_MINUTES)
    try:
        stale = (
            await database[COL]
            .find(
                {
                    "status": AcademyEventRegistrationStatus.PENDING_PAYMENT.value,
                    "created_at": {"$lt": cutoff},
                },
                {"id": 1, "event_id": 1},
            )
            .to_list(length=500)
        )
        if not stale:
            return 0
        result = await database[COL].update_many(
            {
                "id": {"$in": [s["id"] for s in stale]},
                "status": AcademyEventRegistrationStatus.PENDING_PAYMENT.value,
            },
            {
                "$set": {
                    "status": AcademyEventRegistrationStatus.EXPIRED.value,
                    "payment_status": "failed",
                    "updated_at": datetime.utcnow(),
                    "expired_at": datetime.utcnow(),
                }
            },
        )
        event_ids = {s.get("event_id") for s in stale if s.get("event_id")}
        for eid in event_ids:
            await _sync_event_registrations_count(database, eid)
        return int(getattr(result, "modified_count", 0) or 0)
    except Exception:
        logger.debug("expire_stale_pending_registrations failed", exc_info=True)
        return 0


async def _held_count(db, event_id: str) -> int:
    return int(
        await db[COL].count_documents(
            {
                "event_id": event_id,
                "status": {"$in": list(HELD_REGISTRATION_STATUSES)},
            }
        )
    )


async def _sync_event_registrations_count(db, event_id: str) -> int:
    n = await _held_count(db, event_id)
    await db[COL_EVENTS].update_one(
        {"id": event_id},
        {"$set": {"registrations_count": n, "updated_at": datetime.utcnow()}},
    )
    return n


def _public_registration(doc: Optional[dict]) -> Dict[str, Any]:
    out = serialize_doc(doc) or {}
    # Never expose signature to clients
    out.pop("razorpay_signature", None)
    return out


async def _resolve_event(db, body: AcademyEventRegistrationCreate) -> dict:
    key = (body.event_id or body.event_slug or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="event_id or event_slug is required")
    event = await db[COL_EVENTS].find_one(
        {
            "status": AcademyEventStatus.PUBLISHED.value,
            "$or": [{"id": key}, {"slug": key}],
        }
    )
    if not event:
        raise HTTPException(status_code=404, detail="Event not found or not published")
    return event


def _validate_against_fields(body: AcademyEventRegistrationCreate, fields: list) -> None:
    enabled = {f["key"]: f for f in fields if f.get("enabled")}
    values = {
        "participant_name": body.participant_name,
        "participant_phone": body.participant_phone,
        "participant_email": body.participant_email,
        "participant_age": body.participant_age,
        "notes": body.notes,
    }
    for key, cfg in enabled.items():
        val = values.get(key)
        empty = val is None or (isinstance(val, str) and not str(val).strip())
        if cfg.get("required") and empty:
            raise HTTPException(
                status_code=400,
                detail=f"{cfg.get('label') or key} is required",
            )
    # Disabled fields should not be submitted as required — strip is handled on write
    for key in list(values.keys()):
        if key not in enabled and key not in ("participant_name", "participant_phone"):
            pass


async def create_academy_event_registration(
    body: AcademyEventRegistrationCreate, *, current_user: Optional[dict] = None
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_academy_event_registration_indexes(db)
    await expire_stale_pending_registrations(db)

    event = await _resolve_event(db, body)
    if not event.get("registration_enabled", True):
        raise HTTPException(status_code=400, detail="Registration is closed for this event")

    fields = normalize_registration_fields(event.get("registration_fields"))
    _validate_against_fields(body, fields)
    enabled_keys = {f["key"] for f in fields if f.get("enabled")}

    fee_inr = float(event.get("fee_inr") or 0)
    if fee_inr < 0:
        fee_inr = 0.0
    amount_paise = int(round(fee_inr * 100))

    capacity = event.get("capacity")
    capacity_int = int(capacity) if capacity is not None else None
    event_id = event["id"]

    if capacity_int is not None:
        held = await _held_count(db, event_id)
        if held >= capacity_int:
            raise HTTPException(status_code=409, detail="This event is fully booked")

    needs_payment = amount_paise >= 100
    now = datetime.utcnow()
    reg_id = str(uuid.uuid4())
    actor_id = (current_user or {}).get("id") if current_user else None

    if needs_payment:
        status = AcademyEventRegistrationStatus.PENDING_PAYMENT.value
        payment_status = "pending"
    else:
        status = AcademyEventRegistrationStatus.CONFIRMED.value
        payment_status = "not_required"

    doc = {
        "id": reg_id,
        "event_id": event_id,
        "event_slug": event.get("slug"),
        "event_title": event.get("title"),
        "event_type": event.get("event_type"),
        "event_start_at": event.get("start_at"),
        "event_venue": event.get("venue"),
        "branch_id": event.get("branch_id"),
        "participant_name": body.participant_name.strip(),
        "participant_phone": _normalize_phone(body.participant_phone),
        "participant_email": (
            (body.participant_email or "").strip() or None
            if "participant_email" in enabled_keys
            else None
        ),
        "participant_age": (
            body.participant_age if "participant_age" in enabled_keys else None
        ),
        "notes": (
            (body.notes or "").strip() or None if "notes" in enabled_keys else None
        ),
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
        "confirmed_at": now if status == AcademyEventRegistrationStatus.CONFIRMED.value else None,
    }
    await db[COL].insert_one(doc)

    if capacity_int is not None:
        held_after = await _held_count(db, event_id)
        if held_after > capacity_int:
            await db[COL].delete_one({"id": reg_id})
            raise HTTPException(status_code=409, detail="This event is fully booked")

    await _sync_event_registrations_count(db, event_id)

    try:
        from utils.lead_service import upsert_from_source

        await upsert_from_source(
            source_type="academy_event_registration",
            source_ref_type="academy_event_registration",
            source_ref_id=reg_id,
            name=body.participant_name,
            phone=body.participant_phone,
            email=body.participant_email,
            course=event.get("title") or "",
            branch_id=event.get("branch_id"),
            branch_name=None,
            source_label="academy_event_registration",
        )
    except Exception:
        logger.exception("Lead hook after academy event registration failed")

    return {
        "message": "Registration reserved" if needs_payment else "Registration confirmed",
        "registration": _public_registration(doc),
        "payment_required": needs_payment,
    }


async def get_academy_event_registration(registration_id: str) -> Dict[str, Any]:
    """Legacy unscoped get — prefer get_academy_event_registration_owned (S04)."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": registration_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Registration not found")
    return {"registration": _public_registration(doc)}


async def assert_registration_phone_access(
    registration_id: str, *, phone: str
) -> dict:
    """Ensure phone owns registration; return raw doc."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    from utils.indian_phone import subscriber_phone_matches

    doc = await db[COL].find_one({"id": registration_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Registration not found")
    if not subscriber_phone_matches(doc.get("participant_phone"), phone):
        raise HTTPException(status_code=403, detail="You do not own this registration.")
    return doc


async def create_academy_event_registration_payment_order(
    registration_id: str, *, current_user: Optional[dict] = None
) -> Dict[str, Any]:
    from utils.razorpay_client import get_razorpay_client

    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await expire_stale_pending_registrations(db)

    doc = await db[COL].find_one({"id": registration_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Registration not found")

    if doc.get("payment_status") == "paid" or (
        doc.get("status") == AcademyEventRegistrationStatus.CONFIRMED.value
        and doc.get("payment_status") in ("paid", "not_required")
    ):
        raise HTTPException(status_code=409, detail="This registration is already confirmed")

    if doc.get("status") in {
        AcademyEventRegistrationStatus.CANCELLED.value,
        AcademyEventRegistrationStatus.EXPIRED.value,
        AcademyEventRegistrationStatus.FAILED.value,
    }:
        raise HTTPException(status_code=400, detail="Cannot pay for a closed registration")

    if doc.get("status") != AcademyEventRegistrationStatus.PENDING_PAYMENT.value:
        raise HTTPException(status_code=400, detail="Registration is not awaiting payment")

    amount_paise = int(doc.get("amount_paise") or 0)
    if amount_paise < 100:
        amount_paise = int(round(float(doc.get("fee_inr") or 0) * 100))
    if amount_paise < 100:
        raise HTTPException(status_code=400, detail="Invalid payment amount")

    event = await db[COL_EVENTS].find_one({"id": doc.get("event_id")})
    if event and event.get("capacity") is not None:
        held = await _held_count(db, str(doc.get("event_id")))
        if held > int(event.get("capacity")):
            raise HTTPException(status_code=409, detail="This event is fully booked")

    client = get_razorpay_client()
    try:
        order = client.order.create(
            {
                "amount": amount_paise,
                "currency": "INR",
                "payment_capture": 1,
                "notes": {
                    "academy_event_registration_id": registration_id,
                    "type": "academy_event_registration",
                    "event_id": str(doc.get("event_id") or ""),
                    "participant": str(doc.get("participant_name") or "")[:80],
                },
            }
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Razorpay academy event registration order.create failed")
        raise HTTPException(
            status_code=502,
            detail="We could not reach the payment service. Please try again in a moment.",
        )

    now = datetime.utcnow()
    await db[COL].update_one(
        {"id": registration_id},
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
        "registration_id": registration_id,
    }


async def verify_academy_event_registration_payment(
    registration_id: str,
    body: AcademyEventRegistrationPaymentVerify,
    *,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    from utils.razorpay_client import verify_razorpay_signature

    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    doc = await db[COL].find_one({"id": registration_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Registration not found")

    if (
        doc.get("payment_status") == "paid"
        and (doc.get("razorpay_payment_id") or "") == body.razorpay_payment_id
    ):
        return {
            "message": "Already confirmed",
            "registration": _public_registration(doc),
        }

    if doc.get("status") in {
        AcademyEventRegistrationStatus.CANCELLED.value,
        AcademyEventRegistrationStatus.EXPIRED.value,
    }:
        raise HTTPException(status_code=400, detail="Registration is no longer payable")

    stored_order = (doc.get("razorpay_order_id") or "").strip()
    if stored_order and stored_order != body.razorpay_order_id:
        raise HTTPException(status_code=400, detail="Order does not match this registration")

    if not verify_razorpay_signature(
        body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature
    ):
        await db[COL].update_one(
            {"id": registration_id},
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
        {"id": registration_id},
        {
            "$set": {
                "status": AcademyEventRegistrationStatus.CONFIRMED.value,
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
    updated = await db[COL].find_one({"id": registration_id})
    if updated and updated.get("event_id"):
        await _sync_event_registrations_count(db, updated["event_id"])
    return {
        "message": "Registration confirmed",
        "registration": _public_registration(updated),
    }


async def mark_academy_event_registration_payment_failed(
    registration_id: str,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": registration_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Registration not found")
    if doc.get("status") != AcademyEventRegistrationStatus.PENDING_PAYMENT.value:
        return {"message": "No change", "registration": _public_registration(doc)}
    await db[COL].update_one(
        {"id": registration_id},
        {"$set": {"payment_status": "failed", "updated_at": datetime.utcnow()}},
    )
    updated = await db[COL].find_one({"id": registration_id})
    return {
        "message": "Payment marked failed",
        "registration": _public_registration(updated),
    }
