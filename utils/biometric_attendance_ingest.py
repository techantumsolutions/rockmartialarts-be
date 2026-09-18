"""
M09-S03 biometric attendance ingest.

Normalizes vendor punches → upserts `attendance` (check-in/out).
Dedupes via `biometric_punch_events`. Unmatched → `biometric_unmatched_punches`.
Does not modify manual `/mark` flows.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from pydantic import BaseModel, Field

from models.attendance_models import AttendanceMethod
from utils.biometric_device_service import (
    assert_event_branch_matches_device,
    resolve_device_branch,
)
from utils.database import get_db
from utils.helpers import serialize_doc

logger = logging.getLogger(__name__)

PUNCH_EVENTS = "biometric_punch_events"
UNMATCHED = "biometric_unmatched_punches"


class BiometricPunchEvent(BaseModel):
    external_event_id: str = Field(..., min_length=1, max_length=128)
    external_user_id: str = Field(..., min_length=1, max_length=128)
    vendor_device_id: str = Field(..., min_length=1, max_length=128)
    event_type: str = Field(default="IN", description="IN | OUT | UNKNOWN")
    occurred_at: datetime
    vendor: str = Field(default="essl", max_length=64)
    branch_id: Optional[str] = Field(
        default=None,
        description="Optional claimed branch; must match device registry when set",
    )
    raw: Optional[Dict[str, Any]] = None


class BiometricIngestRequest(BaseModel):
    events: List[BiometricPunchEvent] = Field(default_factory=list)
    # Single-event convenience (also accepted)
    event: Optional[BiometricPunchEvent] = None


async def ensure_ingest_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[PUNCH_EVENTS].create_index("id", unique=True)
        await database[PUNCH_EVENTS].create_index("external_event_id", unique=True)
        await database[PUNCH_EVENTS].create_index(
            [("external_user_id", 1), ("occurred_at", 1), ("event_type", 1)],
            name="idx_user_ts_type",
        )
        await database[PUNCH_EVENTS].create_index([("occurred_at", -1)])
        await database[UNMATCHED].create_index([("occurred_at", -1)])
        await database[UNMATCHED].create_index("external_user_id")
        await database.attendance.create_index(
            [("student_id", 1), ("branch_id", 1), ("course_id", 1), ("attendance_date", 1)],
            name="idx_attendance_student_day",
        )
    except Exception:
        logger.exception("Failed ensuring biometric ingest indexes")


def _norm_event_type(value: str) -> str:
    t = str(value or "IN").strip().upper()
    if t in {"IN", "CHECKIN", "CHECK_IN", "0"}:
        return "IN"
    if t in {"OUT", "CHECKOUT", "CHECK_OUT", "1"}:
        return "OUT"
    return "UNKNOWN"


def _day_bounds(dt: datetime) -> Tuple[datetime, datetime]:
    start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start, end


async def _resolve_student(db, external_user_id: str) -> Optional[dict]:
    uid = str(external_user_id).strip()
    if not uid:
        return None
    student = await db.users.find_one(
        {
            "role": "student",
            "$or": [{"biometric_id": uid}, {"essl_user_id": uid}],
        }
    )
    return student


async def _resolve_course_for_branch(db, student_id: str, branch_id: str) -> Optional[str]:
    enr = await db.enrollments.find_one(
        {"student_id": student_id, "branch_id": branch_id, "is_active": True},
        sort=[("created_at", -1)],
    )
    if not enr:
        enr = await db.enrollments.find_one(
            {"student_id": student_id, "branch_id": branch_id},
            sort=[("created_at", -1)],
        )
    if not enr:
        enr = await db.enrollments.find_one(
            {"student_id": student_id, "is_active": True},
            sort=[("created_at", -1)],
        )
    return (enr or {}).get("course_id")


async def _store_unmatched(db, event: BiometricPunchEvent, reason: str) -> None:
    await db[UNMATCHED].insert_one(
        {
            "id": str(uuid.uuid4()),
            "external_event_id": event.external_event_id,
            "external_user_id": event.external_user_id,
            "vendor_device_id": event.vendor_device_id,
            "vendor": event.vendor,
            "event_type": _norm_event_type(event.event_type),
            "occurred_at": event.occurred_at,
            "reason": reason,
            "raw": event.raw,
            "created_at": datetime.utcnow(),
        }
    )


async def _upsert_attendance_from_punch(
    db,
    *,
    student: dict,
    branch_id: str,
    course_id: str,
    event_type: str,
    occurred_at: datetime,
    device_name: str,
    external_event_id: str,
) -> Dict[str, Any]:
    student_id = student["id"]
    day_start, day_end = _day_bounds(occurred_at)

    existing = await db.attendance.find_one(
        {
            "student_id": student_id,
            "branch_id": branch_id,
            "course_id": course_id,
            "attendance_date": {"$gte": day_start, "$lt": day_end},
        },
        sort=[("updated_at", -1), ("created_at", -1)],
    )

    now = datetime.utcnow()
    notes = f"Biometric {event_type} from device {device_name} (event {external_event_id})"

    if existing:
        check_in = existing.get("check_in_time")
        check_out = existing.get("check_out_time")
        if event_type == "OUT":
            check_out = occurred_at
            if not check_in:
                check_in = occurred_at
        else:
            # IN / UNKNOWN: keep earliest check-in
            if not check_in or occurred_at < check_in:
                check_in = occurred_at

        update = {
            "check_in_time": check_in,
            "check_out_time": check_out,
            "is_present": True,
            "status": "present",
            "method": AttendanceMethod.BIOMETRIC.value,
            "notes": notes,
            "updated_at": now,
            "biometric_event_id": external_event_id,
            "source_device_id": device_name,
        }
        await db.attendance.update_one({"id": existing["id"]}, {"$set": update})
        return {"action": "updated", "attendance_id": existing["id"]}

    attendance_id = str(uuid.uuid4())
    check_in = occurred_at if event_type != "OUT" else occurred_at
    check_out = occurred_at if event_type == "OUT" else None
    doc = {
        "id": attendance_id,
        "student_id": student_id,
        "course_id": course_id,
        "branch_id": branch_id,
        "attendance_date": day_start,
        "check_in_time": check_in,
        "check_out_time": check_out,
        "method": AttendanceMethod.BIOMETRIC.value,
        "is_present": True,
        "status": "present",
        "notes": notes,
        "marked_by": None,
        "created_at": now,
        "updated_at": now,
        "admin_adjusted": False,
        "biometric_event_id": external_event_id,
        "source_device_id": device_name,
    }
    await db.attendance.insert_one(doc)
    return {"action": "created", "attendance_id": attendance_id}


async def process_punch_event(event: BiometricPunchEvent) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_ingest_indexes(db)

    external_event_id = str(event.external_event_id).strip()
    event_type = _norm_event_type(event.event_type)

    # Primary dedup
    existing_evt = await db[PUNCH_EVENTS].find_one({"external_event_id": external_event_id})
    if existing_evt:
        return {
            "status": "duplicate",
            "external_event_id": external_event_id,
            "message": "Event already processed",
            "attendance_id": existing_evt.get("attendance_id"),
        }

    # Secondary dedup (same user/time/type)
    secondary = await db[PUNCH_EVENTS].find_one(
        {
            "external_user_id": str(event.external_user_id).strip(),
            "occurred_at": event.occurred_at,
            "event_type": event_type,
        }
    )
    if secondary:
        return {
            "status": "duplicate",
            "external_event_id": external_event_id,
            "message": "Matching punch already processed",
            "attendance_id": secondary.get("attendance_id"),
        }

    vendor = (event.vendor or "essl").strip().lower()
    branch_id, device = await resolve_device_branch(vendor, event.vendor_device_id)
    if not branch_id or not device:
        await _store_unmatched(db, event, "unknown_device")
        return {
            "status": "unmatched",
            "reason": "unknown_device",
            "external_event_id": external_event_id,
            "message": "Device not registered or inactive. Map it under Biometric Devices.",
        }

    claimed_branch = (event.branch_id or "").strip() or None
    if not assert_event_branch_matches_device(branch_id, claimed_branch):
        await _store_unmatched(db, event, "branch_mismatch")
        return {
            "status": "rejected",
            "reason": "branch_mismatch",
            "external_event_id": external_event_id,
            "message": "Event rejected: claimed branch does not match device registry",
        }

    student = await _resolve_student(db, event.external_user_id)
    if not student:
        await _store_unmatched(db, event, "unknown_student")
        return {
            "status": "unmatched",
            "reason": "unknown_student",
            "external_event_id": external_event_id,
            "message": "No student mapped to this biometric ID",
        }

    if not bool(student.get("is_active", True)):
        await _store_unmatched(db, event, "inactive_student")
        return {
            "status": "unmatched",
            "reason": "inactive_student",
            "external_event_id": external_event_id,
            "message": "Student account is inactive",
        }

    course_id = await _resolve_course_for_branch(db, student["id"], branch_id)
    if not course_id:
        course_id = ""  # still record attendance at branch if no enrollment

    result = await _upsert_attendance_from_punch(
        db,
        student=student,
        branch_id=branch_id,
        course_id=course_id or "biometric-unassigned",
        event_type=event_type if event_type != "UNKNOWN" else "IN",
        occurred_at=event.occurred_at,
        device_name=device.get("name") or event.vendor_device_id,
        external_event_id=external_event_id,
    )

    punch_doc = {
        "id": str(uuid.uuid4()),
        "external_event_id": external_event_id,
        "external_user_id": str(event.external_user_id).strip(),
        "vendor": vendor,
        "vendor_device_id": event.vendor_device_id,
        "device_id": device.get("id"),
        "branch_id": branch_id,
        "student_id": student["id"],
        "event_type": event_type,
        "occurred_at": event.occurred_at,
        "attendance_id": result.get("attendance_id"),
        "status": "processed",
        "raw": event.raw,
        "created_at": datetime.utcnow(),
    }
    try:
        await db[PUNCH_EVENTS].insert_one(punch_doc)
    except Exception:
        # Race: unique index hit
        return {
            "status": "duplicate",
            "external_event_id": external_event_id,
            "message": "Event already processed (race)",
            "attendance_id": result.get("attendance_id"),
        }

    return {
        "status": "processed",
        "external_event_id": external_event_id,
        "student_id": student["id"],
        "branch_id": branch_id,
        "event_type": event_type,
        "attendance_id": result.get("attendance_id"),
        "action": result.get("action"),
        "message": "Attendance updated from biometric punch",
    }


async def ingest_events(payload: BiometricIngestRequest) -> Dict[str, Any]:
    events: List[BiometricPunchEvent] = list(payload.events or [])
    if payload.event:
        events.append(payload.event)
    if not events:
        raise HTTPException(status_code=400, detail="No events provided")

    results = []
    summary = {"processed": 0, "duplicate": 0, "unmatched": 0, "rejected": 0, "errors": 0}
    for ev in events:
        try:
            out = await process_punch_event(ev)
            results.append(out)
            st = out.get("status") or "errors"
            if st in summary:
                summary[st] += 1
            else:
                summary["errors"] += 1
        except Exception as exc:
            logger.exception("Ingest event failed")
            summary["errors"] += 1
            results.append(
                {
                    "status": "error",
                    "external_event_id": getattr(ev, "external_event_id", None),
                    "message": str(exc),
                }
            )

    return {
        "summary": summary,
        "results": results,
        "count": len(results),
        "message": "Ingest complete",
    }


async def list_unmatched(
    *,
    skip: int = 0,
    limit: int = 50,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    skip = max(0, skip)
    limit = max(1, min(limit, 200))
    total = await db[UNMATCHED].count_documents({})
    rows = (
        await db[UNMATCHED]
        .find({})
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {"unmatched": serialize_doc(rows), "total": total, "count": len(rows)}


async def ingest_stats() -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    since = datetime.utcnow() - timedelta(days=7)
    processed_7d = await db[PUNCH_EVENTS].count_documents({"created_at": {"$gte": since}})
    unmatched_7d = await db[UNMATCHED].count_documents({"created_at": {"$gte": since}})
    unmatched_total = await db[UNMATCHED].count_documents({})
    return {
        "processed_last_7_days": processed_7d,
        "unmatched_last_7_days": unmatched_7d,
        "unmatched_total": unmatched_total,
        "ingest_key_configured": bool(os.getenv("BIOMETRIC_INGEST_API_KEY", "").strip()),
    }


def legacy_biometric_to_event(device_id: str, biometric_id: str, timestamp: datetime) -> BiometricPunchEvent:
    """Convert old BiometricAttendance payload into normalized event."""
    stamp = timestamp.strftime("%Y%m%d%H%M%S")
    return BiometricPunchEvent(
        external_event_id=f"legacy-{device_id}-{biometric_id}-{stamp}",
        external_user_id=biometric_id,
        vendor_device_id=device_id,
        event_type="IN",
        occurred_at=timestamp,
        vendor="essl",
        raw={"legacy": True, "device_id": device_id, "biometric_id": biometric_id},
    )
