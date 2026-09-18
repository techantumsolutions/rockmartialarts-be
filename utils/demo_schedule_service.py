"""
M13-S01 Demo schedule service — admin CRUD for recurring demo availability.

Does not modify branch course_schedule, events, leads, training requests, or camp.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from fastapi import HTTPException

from models.demo_schedule_models import (
    DEFAULT_DEMO_FEE_INR,
    WEEKDAY_VALUES,
    DemoRecurrenceType,
    DemoScheduleCreate,
    DemoScheduleUpdate,
    time_to_minutes,
)
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.student_status_service import get_managed_branch_ids_for_user

logger = logging.getLogger(__name__)

COL = "demo_schedules"


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


async def ensure_demo_schedule_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL].create_index("id", unique=True)
        await database[COL].create_index(
            [("branch_id", 1), ("course_id", 1), ("is_active", 1)]
        )
        await database[COL].create_index([("is_active", 1), ("created_at", -1)])
        await database[COL].create_index("recurrence")
    except Exception:
        logger.exception("Failed ensuring demo_schedule indexes")


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
                status_code=403, detail="Schedule outside managed branches"
            )
        return
    raise HTTPException(status_code=403, detail="Not authorized for demo schedules")


async def _assert_branch_writable(db, current_user: dict, branch_id: str) -> None:
    role = _role(current_user)
    if role in {"super_admin", "superadmin", "coach_admin"}:
        return
    if role == "branch_manager":
        managed = await get_managed_branch_ids_for_user(db, current_user)
        if str(branch_id) not in {str(b) for b in managed}:
            raise HTTPException(
                status_code=403, detail="Branch outside managed branches"
            )
        return
    raise HTTPException(status_code=403, detail="Not authorized for demo schedules")


async def _resolve_branch(db, branch_id: str) -> tuple[str, str]:
    bid = (branch_id or "").strip()
    if not bid:
        raise HTTPException(status_code=400, detail="branch_id is required")
    branch = await db.branches.find_one({"id": bid})
    if not branch:
        raise HTTPException(status_code=400, detail="Invalid branch_id")
    name = (
        (branch.get("branch") or {}).get("name")
        or branch.get("name")
        or bid
    )
    return bid, str(name)


async def _resolve_course(db, course_id: str) -> tuple[str, str]:
    cid = (course_id or "").strip()
    if not cid:
        raise HTTPException(status_code=400, detail="course_id is required")
    course = await db.courses.find_one({"id": cid})
    if not course:
        raise HTTPException(status_code=400, detail="Invalid course_id")
    name = course.get("name") or course.get("title") or cid
    return cid, str(name)


def _weekdays_for_doc(recurrence: str, weekdays: List[str]) -> List[str]:
    if recurrence == DemoRecurrenceType.DAILY.value:
        return list(WEEKDAY_VALUES)
    return list(weekdays or [])


def _overlap_minutes(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def _shared_weekdays(a: List[str], b: List[str]) -> Set[str]:
    return set(a or []) & set(b or [])


async def _find_overlapping_schedule(
    db,
    *,
    branch_id: str,
    course_id: str,
    weekdays: List[str],
    start_time: str,
    end_time: str,
    exclude_id: Optional[str] = None,
) -> Optional[dict]:
    """Detect active overlapping schedule for same branch + course."""
    q: Dict[str, Any] = {
        "branch_id": branch_id,
        "course_id": course_id,
        "is_active": True,
    }
    if exclude_id:
        q["id"] = {"$ne": exclude_id}

    rows = await db[COL].find(q).to_list(length=500)
    a_start = time_to_minutes(start_time)
    a_end = time_to_minutes(end_time)
    for row in rows:
        shared = _shared_weekdays(weekdays, row.get("weekdays") or [])
        if not shared:
            continue
        try:
            b_start = time_to_minutes(str(row.get("start_time") or ""))
            b_end = time_to_minutes(str(row.get("end_time") or ""))
        except ValueError:
            continue
        if _overlap_minutes(a_start, a_end, b_start, b_end):
            return row
    return None


async def create_demo_schedule(
    body: DemoScheduleCreate, *, current_user: dict
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_demo_schedule_indexes(db)
    await _assert_admin_access(db, current_user)

    branch_id, branch_name = await _resolve_branch(db, body.branch_id)
    await _assert_branch_writable(db, current_user, branch_id)
    course_id, course_name = await _resolve_course(db, body.course_id)

    weekdays = _weekdays_for_doc(body.recurrence.value, body.weekdays)
    overlap = await _find_overlapping_schedule(
        db,
        branch_id=branch_id,
        course_id=course_id,
        weekdays=weekdays,
        start_time=body.start_time,
        end_time=body.end_time,
    )
    if overlap:
        raise HTTPException(
            status_code=409,
            detail=(
                "Overlapping active demo schedule exists for this branch/course "
                f"(id={overlap.get('id')}, {overlap.get('start_time')}-{overlap.get('end_time')})"
            ),
        )

    actor = _actor(current_user)
    now = datetime.utcnow()
    fee = float(body.fee_inr if body.fee_inr is not None else DEFAULT_DEMO_FEE_INR)
    doc = {
        "id": str(uuid.uuid4()),
        "title": (body.title or "").strip() or None,
        "branch_id": branch_id,
        "branch_name": branch_name,
        "course_id": course_id,
        "course_name": course_name,
        "recurrence": body.recurrence.value,
        "weekdays": weekdays,
        "start_time": body.start_time,
        "end_time": body.end_time,
        "capacity": body.capacity,
        "fee_inr": fee,
        "effective_from": body.effective_from,
        "effective_until": body.effective_until,
        "is_active": bool(body.is_active),
        "notes": (body.notes or "").strip() or None,
        "created_at": now,
        "updated_at": now,
        "created_by": actor.get("id"),
        "updated_by": actor.get("id"),
    }
    await db[COL].insert_one(doc)
    return {"message": "Demo schedule created", "schedule": serialize_doc(doc)}


async def list_demo_schedules(
    *,
    current_user: dict,
    branch_id: Optional[str] = None,
    course_id: Optional[str] = None,
    is_active: Optional[bool] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_demo_schedule_indexes(db)
    await _assert_admin_access(db, current_user)

    q: Dict[str, Any] = {}
    role = _role(current_user)
    managed: List[str] = []
    if role == "branch_manager":
        managed = await get_managed_branch_ids_for_user(db, current_user)
        if not managed:
            return {"schedules": [], "total": 0, "count": 0}
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
    if is_active is not None:
        q["is_active"] = bool(is_active)

    if search:
        term = search.strip()
        if term:
            rx = {"$regex": term, "$options": "i"}
            q["$or"] = [
                {"title": rx},
                {"branch_name": rx},
                {"course_name": rx},
                {"notes": rx},
            ]

    skip = max(0, skip)
    limit = max(1, min(limit, 200))
    total = await db[COL].count_documents(q)
    rows = (
        await db[COL]
        .find(q)
        .sort([("created_at", -1)])
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {"schedules": serialize_doc(rows), "total": total, "count": len(rows)}


async def get_demo_schedule(schedule_id: str, *, current_user: dict) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": schedule_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Demo schedule not found")
    await _assert_admin_access(db, current_user, doc)
    return {"schedule": serialize_doc(doc)}


async def update_demo_schedule(
    schedule_id: str, body: DemoScheduleUpdate, *, current_user: dict
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": schedule_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Demo schedule not found")
    await _assert_admin_access(db, current_user, doc)

    updates: Dict[str, Any] = {}

    if body.branch_id is not None:
        branch_id, branch_name = await _resolve_branch(db, body.branch_id)
        await _assert_branch_writable(db, current_user, branch_id)
        updates["branch_id"] = branch_id
        updates["branch_name"] = branch_name

    if body.course_id is not None:
        course_id, course_name = await _resolve_course(db, body.course_id)
        updates["course_id"] = course_id
        updates["course_name"] = course_name

    if body.title is not None:
        updates["title"] = (body.title or "").strip() or None
    if body.notes is not None:
        updates["notes"] = (body.notes or "").strip() or None
    if body.fee_inr is not None:
        updates["fee_inr"] = float(body.fee_inr)
    if body.is_active is not None:
        updates["is_active"] = bool(body.is_active)
    if body.effective_from is not None:
        updates["effective_from"] = body.effective_from
    if body.clear_effective_until:
        updates["effective_until"] = None
    elif body.effective_until is not None:
        updates["effective_until"] = body.effective_until
    if body.clear_capacity:
        updates["capacity"] = None
    elif body.capacity is not None:
        updates["capacity"] = body.capacity

    recurrence = body.recurrence.value if body.recurrence else doc.get("recurrence")
    if body.recurrence is not None:
        updates["recurrence"] = body.recurrence.value
        weekdays = _weekdays_for_doc(
            body.recurrence.value, body.weekdays or (doc.get("weekdays") or [])
        )
        updates["weekdays"] = weekdays
    elif body.weekdays is not None:
        if recurrence == DemoRecurrenceType.WEEKLY.value and not body.weekdays:
            raise HTTPException(
                status_code=400, detail="weekdays required for weekly recurrence"
            )
        updates["weekdays"] = _weekdays_for_doc(str(recurrence), body.weekdays)

    if body.start_time is not None:
        updates["start_time"] = body.start_time
    if body.end_time is not None:
        updates["end_time"] = body.end_time

    merged_start = updates.get("start_time", doc.get("start_time"))
    merged_end = updates.get("end_time", doc.get("end_time"))
    try:
        if time_to_minutes(str(merged_start)) >= time_to_minutes(str(merged_end)):
            raise HTTPException(
                status_code=400, detail="end_time must be after start_time"
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    merged_from = updates.get("effective_from", doc.get("effective_from"))
    merged_until = updates.get("effective_until", doc.get("effective_until"))
    if merged_from and merged_until and str(merged_until) < str(merged_from):
        raise HTTPException(
            status_code=400, detail="effective_until must be on or after effective_from"
        )

    will_be_active = updates.get("is_active", doc.get("is_active", True))
    if will_be_active:
        overlap = await _find_overlapping_schedule(
            db,
            branch_id=str(updates.get("branch_id", doc.get("branch_id"))),
            course_id=str(updates.get("course_id", doc.get("course_id"))),
            weekdays=list(updates.get("weekdays", doc.get("weekdays") or [])),
            start_time=str(merged_start),
            end_time=str(merged_end),
            exclude_id=schedule_id,
        )
        if overlap:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Overlapping active demo schedule exists for this branch/course "
                    f"(id={overlap.get('id')})"
                ),
            )

    if not updates:
        return {"message": "No changes", "schedule": serialize_doc(doc)}

    actor = _actor(current_user)
    updates["updated_at"] = datetime.utcnow()
    updates["updated_by"] = actor.get("id")
    await db[COL].update_one({"id": schedule_id}, {"$set": updates})
    updated = await db[COL].find_one({"id": schedule_id})
    return {"message": "Demo schedule updated", "schedule": serialize_doc(updated)}


async def delete_demo_schedule(
    schedule_id: str, *, current_user: dict, hard: bool = False
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": schedule_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Demo schedule not found")
    await _assert_admin_access(db, current_user, doc)

    if hard:
        await db[COL].delete_one({"id": schedule_id})
        return {"message": "Demo schedule deleted", "id": schedule_id}

    actor = _actor(current_user)
    await db[COL].update_one(
        {"id": schedule_id},
        {
            "$set": {
                "is_active": False,
                "updated_at": datetime.utcnow(),
                "updated_by": actor.get("id"),
            }
        },
    )
    updated = await db[COL].find_one({"id": schedule_id})
    return {
        "message": "Demo schedule deactivated",
        "schedule": serialize_doc(updated),
    }
