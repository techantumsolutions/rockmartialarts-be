"""
M09-S05 manual attendance correction.

Authorized roles can correct existing student attendance with a required reason.
Full before/after snapshots are stored in `attendance_audit`.
Does not change first-time `/mark` create behavior.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from models.attendance_models import AttendanceMethod, AttendanceStatus
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.student_status_service import get_managed_branch_ids_for_user

logger = logging.getLogger(__name__)

COL_AUDIT = "attendance_audit"
CORRECTOR_ROLES = {
    "super_admin",
    "superadmin",
    "coach_admin",
    "branch_manager",
}


class AttendanceCorrectionRequest(BaseModel):
    """Correct an existing attendance row. Identify by attendance_id or composite keys."""

    reason: str = Field(..., min_length=3, max_length=1000)
    attendance_id: Optional[str] = None
    student_id: Optional[str] = None
    course_id: Optional[str] = None
    branch_id: Optional[str] = None
    attendance_date: Optional[datetime] = None
    status: Optional[AttendanceStatus] = None
    check_in_time: Optional[datetime] = None
    check_out_time: Optional[datetime] = None
    notes: Optional[str] = Field(default=None, max_length=2000)
    clear_check_out: bool = False


def _role(user: dict) -> str:
    return str((user or {}).get("role") or "").lower()


def _snapshot(doc: dict) -> Dict[str, Any]:
    keys = [
        "id",
        "student_id",
        "course_id",
        "branch_id",
        "attendance_date",
        "check_in_time",
        "check_out_time",
        "is_present",
        "status",
        "method",
        "notes",
        "admin_adjusted",
        "attendance_modified_at",
        "attendance_modified_by",
        "correction_reason",
        "marked_by",
    ]
    out: Dict[str, Any] = {}
    for k in keys:
        v = doc.get(k)
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif hasattr(v, "value"):
            out[k] = v.value
        else:
            out[k] = v
    return out


async def ensure_correction_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL_AUDIT].create_index("id", unique=True)
        await database[COL_AUDIT].create_index([("attendance_id", 1), ("created_at", -1)])
        await database[COL_AUDIT].create_index("student_id")
        await database[COL_AUDIT].create_index("admin_id")
        await database[COL_AUDIT].create_index("action")
    except Exception:
        logger.exception("Failed ensuring attendance correction indexes")


async def assert_correction_permission(db, current_user: dict, branch_id: str) -> None:
    role = _role(current_user)
    if role not in CORRECTOR_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Only Super Admin, Coach Admin, or Branch Manager may correct attendance",
        )
    if role == "branch_manager":
        managed = await get_managed_branch_ids_for_user(db, current_user)
        if not managed:
            raise HTTPException(status_code=403, detail="No managed branches for this Branch Manager")
        if str(branch_id) not in {str(b) for b in managed}:
            raise HTTPException(
                status_code=403,
                detail="Branch Manager cannot correct attendance for other branches",
            )


def _validate_times(
    check_in: Optional[datetime],
    check_out: Optional[datetime],
    *,
    is_present: bool,
) -> None:
    if not is_present:
        return
    if check_in and check_out and check_out <= check_in:
        raise HTTPException(status_code=400, detail="Check-out must be after check-in")


async def _find_existing(
    db,
    payload: AttendanceCorrectionRequest,
) -> dict:
    if payload.attendance_id:
        doc = await db.attendance.find_one({"id": str(payload.attendance_id).strip()})
        if not doc:
            raise HTTPException(status_code=404, detail="Attendance record not found")
        return doc

    if not (payload.student_id and payload.course_id and payload.branch_id and payload.attendance_date):
        raise HTTPException(
            status_code=400,
            detail="Provide attendance_id or student_id + course_id + branch_id + attendance_date",
        )

    day = payload.attendance_date
    day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day.replace(hour=23, minute=59, second=59, microsecond=999999)
    doc = await db.attendance.find_one(
        {
            "student_id": payload.student_id,
            "course_id": payload.course_id,
            "branch_id": payload.branch_id,
            "attendance_date": {"$gte": day_start, "$lte": day_end},
        },
        sort=[("updated_at", -1), ("created_at", -1)],
    )
    if not doc:
        raise HTTPException(
            status_code=404,
            detail="No existing attendance to correct. Use mark attendance for first-time entry.",
        )
    return doc


async def correct_attendance(
    payload: AttendanceCorrectionRequest,
    current_user: dict,
) -> Dict[str, Any]:
    reason = (payload.reason or "").strip()
    if len(reason) < 3:
        raise HTTPException(status_code=400, detail="Correction reason is required (min 3 characters)")

    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_correction_indexes(db)

    existing = await _find_existing(db, payload)
    branch_id = str(existing.get("branch_id") or payload.branch_id or "")
    await assert_correction_permission(db, current_user, branch_id)

    before = _snapshot(existing)

    # Resolve new status
    if payload.status is not None:
        new_status = payload.status.value
        is_present = payload.status in {AttendanceStatus.PRESENT, AttendanceStatus.LATE}
    else:
        new_status = existing.get("status") or ("present" if existing.get("is_present") else "absent")
        is_present = bool(existing.get("is_present", True))
        if str(new_status).lower() in {"present", "late"}:
            is_present = True
        elif str(new_status).lower() == "absent":
            is_present = False

    if is_present:
        if payload.check_in_time is not None:
            new_in = payload.check_in_time
        else:
            new_in = existing.get("check_in_time") or datetime.utcnow()
        if payload.clear_check_out:
            new_out = None
        elif payload.check_out_time is not None:
            new_out = payload.check_out_time
        else:
            new_out = existing.get("check_out_time")
    else:
        new_in = None
        new_out = None

    _validate_times(new_in, new_out, is_present=is_present)

    now = datetime.utcnow()
    note_bits = []
    if payload.notes and str(payload.notes).strip():
        note_bits.append(str(payload.notes).strip())
    note_bits.append(f"[Correction] {reason}")
    combined_notes = " | ".join(note_bits)

    update = {
        "check_in_time": new_in,
        "check_out_time": new_out,
        "is_present": is_present,
        "status": new_status,
        "notes": combined_notes,
        "method": AttendanceMethod.MANUAL.value,
        "updated_at": now,
        "admin_adjusted": True,
        "attendance_modified_at": now,
        "attendance_modified_by": current_user.get("id"),
        "correction_reason": reason,
        "marked_by": current_user.get("id"),
    }

    await db.attendance.update_one({"id": existing["id"]}, {"$set": update})
    after_doc = {**existing, **update}
    after = _snapshot(after_doc)

    audit_id = str(uuid.uuid4())
    audit = {
        "id": audit_id,
        "attendance_id": existing["id"],
        "student_id": existing.get("student_id"),
        "course_id": existing.get("course_id"),
        "branch_id": branch_id,
        "admin_id": current_user.get("id"),
        "admin_role": _role(current_user),
        "admin_name": current_user.get("full_name")
        or current_user.get("email")
        or current_user.get("id"),
        "action": "manual_correction",
        "reason": reason,
        "before": before,
        "after": after,
        # Legacy flat fields (compatible with older /mark audit rows)
        "before_check_in": existing.get("check_in_time"),
        "before_check_out": existing.get("check_out_time"),
        "before_status": existing.get("status"),
        "after_check_in": new_in,
        "after_check_out": new_out,
        "after_status": new_status,
        "created_at": now,
    }
    try:
        await db[COL_AUDIT].insert_one(audit)
    except Exception:
        logger.exception("Failed to write attendance correction audit")
        raise HTTPException(status_code=500, detail="Correction saved but audit write failed")

    return {
        "message": "Attendance corrected successfully",
        "attendance_id": existing["id"],
        "audit_id": audit_id,
        "action": "corrected",
        "reason": reason,
        "before": before,
        "after": after,
        "corrected_by": current_user.get("id"),
        "corrected_at": now.isoformat(),
    }


async def list_correction_history(
    *,
    attendance_id: Optional[str] = None,
    student_id: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    if not attendance_id and not student_id:
        raise HTTPException(status_code=400, detail="Provide attendance_id or student_id")

    q: Dict[str, Any] = {
        "action": {"$in": ["manual_correction", "student_attendance_update"]},
    }
    if attendance_id:
        q["attendance_id"] = attendance_id
    if student_id:
        q["student_id"] = student_id

    # BM scope: only show audits for managed branches
    if current_user and _role(current_user) == "branch_manager":
        managed = await get_managed_branch_ids_for_user(db, current_user)
        if not managed:
            return {"corrections": [], "total": 0, "count": 0}
        q["branch_id"] = {"$in": managed}

    skip = max(0, skip)
    limit = max(1, min(limit, 200))
    total = await db[COL_AUDIT].count_documents(q)
    rows = (
        await db[COL_AUDIT]
        .find(q)
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {
        "corrections": serialize_doc(rows),
        "total": total,
        "count": len(rows),
    }
