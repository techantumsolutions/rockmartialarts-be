"""Branch–course availability mapping (M02-S04). Dual-writes assignments.courses."""
import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException

from utils.branch_geography import assert_branch_accepts_enrollment


def unique_course_ids(raw) -> List[str]:
    seen = set()
    out: List[str] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        cid = ""
        if isinstance(item, str):
            cid = item.strip()
        elif isinstance(item, dict):
            cid = str(item.get("course_id") or item.get("courseId") or "").strip()
        if cid and cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


def _schedule_rows(branch: Optional[dict]) -> List[dict]:
    if not branch:
        return []
    sched = (branch.get("assignments") or {}).get("course_schedule") or []
    return [row for row in sched if isinstance(row, dict)]


def schedule_availability_map(branch: Optional[dict]) -> Dict[str, bool]:
    out: Dict[str, bool] = {}
    for row in _schedule_rows(branch):
        cid = str(row.get("course_id") or row.get("courseId") or "").strip()
        if cid:
            out[cid] = row.get("is_available", True) is not False
    return out


def merge_schedule_fees(branch: Optional[dict], course_id: str) -> Optional[Dict[str, float]]:
    merged: Dict[str, float] = {}
    for row in _schedule_rows(branch):
        cid = str(row.get("course_id") or row.get("courseId") or "").strip()
        if cid != course_id:
            continue
        for batch in row.get("batches") or []:
            if not isinstance(batch, dict):
                continue
            fpd = batch.get("fee_per_duration") or {}
            if isinstance(fpd, dict):
                for key, value in fpd.items():
                    try:
                        if value is not None:
                            merged[str(key)] = float(value)
                    except (TypeError, ValueError):
                        continue
            raw_fee = batch.get("batch_fee")
            if raw_fee is not None and not merged:
                try:
                    merged["default"] = float(raw_fee)
                except (TypeError, ValueError):
                    pass
    return merged or None


def prepare_assignments_for_write(assignments: Optional[dict]) -> Optional[dict]:
    """Dedupe course ids and keep course_schedule aligned (unique course_id)."""
    if not isinstance(assignments, dict):
        return assignments
    course_ids = unique_course_ids(assignments.get("courses"))
    assignments["courses"] = course_ids
    sched = assignments.get("course_schedule")
    if isinstance(sched, list):
        seen = set()
        cleaned = []
        for row in sched:
            if not isinstance(row, dict):
                continue
            cid = str(row.get("course_id") or row.get("courseId") or "").strip()
            if not cid or cid in seen:
                continue
            seen.add(cid)
            row["course_id"] = cid
            if "is_available" not in row:
                row["is_available"] = True
            cleaned.append(row)
        assignments["course_schedule"] = cleaned
    return assignments


async def available_course_ids_for_branch(db, branch: dict) -> List[str]:
    assigned = unique_course_ids((branch.get("assignments") or {}).get("courses"))
    if not assigned:
        return []
    mappings = await db.branch_courses.find({"branch_id": branch["id"]}).to_list(500)
    mapped = {m.get("course_id"): m.get("is_available", True) is not False for m in mappings if m.get("course_id")}
    sched_avail = schedule_availability_map(branch)
    out = []
    for cid in assigned:
        if cid in mapped:
            if mapped[cid]:
                out.append(cid)
            continue
        if sched_avail.get(cid, True):
            out.append(cid)
    return out


async def assert_course_available_at_branch(
    db,
    branch_id: str,
    course_id: str,
    *,
    require_active_branch: bool = True,
    require_active_course: bool = True,
    allow_if_enrolled_student_id: Optional[str] = None,
) -> Tuple[dict, dict]:
    """Reject new admissions when the pair is missing or unavailable. Existing enrollments can still pay."""
    cid = (course_id or "").strip()
    if not cid:
        raise HTTPException(status_code=400, detail="Course is required")

    if require_active_branch:
        branch = await assert_branch_accepts_enrollment(db, branch_id)
    else:
        branch = await db.branches.find_one({"id": branch_id})
        if not branch:
            raise HTTPException(status_code=404, detail="Branch not found")

    course = await db.courses.find_one({"id": cid})
    if not course:
        raise HTTPException(status_code=400, detail="Please select a valid course")
    if require_active_course and course.get("settings", {}).get("active", True) is False:
        raise HTTPException(status_code=400, detail="Selected course is inactive")

    assigned = unique_course_ids((branch.get("assignments") or {}).get("courses"))
    mapping = await db.branch_courses.find_one({"branch_id": branch["id"], "course_id": cid})
    available = True
    offered = cid in assigned or mapping is not None
    if mapping is not None:
        available = mapping.get("is_available", True) is not False
    else:
        available = schedule_availability_map(branch).get(cid, cid in assigned)

    if offered and available:
        return branch, course

    if allow_if_enrolled_student_id:
        existing = await db.enrollments.find_one({
            "student_id": allow_if_enrolled_student_id,
            "course_id": cid,
            "branch_id": branch["id"],
            "is_active": True,
        })
        if existing:
            return branch, course

    raise HTTPException(
        status_code=400,
        detail="This course is not available at the selected branch",
    )


async def sync_branch_courses_from_assignments(db, branch_id: str, branch_doc: Optional[dict] = None) -> None:
    branch = branch_doc or await db.branches.find_one({"id": branch_id})
    if not branch:
        return
    course_ids = unique_course_ids((branch.get("assignments") or {}).get("courses"))
    sched_avail = schedule_availability_map(branch)
    now = datetime.utcnow()

    for cid in course_ids:
        course = await db.courses.find_one({"id": cid})
        if not course:
            raise HTTPException(status_code=400, detail="Please select a valid course")
        fees = merge_schedule_fees(branch, cid)
        existing = await db.branch_courses.find_one({"branch_id": branch_id, "course_id": cid})
        payload = {
            "branch_id": branch_id,
            "course_id": cid,
            "is_available": sched_avail.get(cid, True),
            "fee_per_duration": fees,
            "updated_at": now,
        }
        if existing:
            await db.branch_courses.update_one({"id": existing["id"]}, {"$set": payload})
        else:
            payload["id"] = str(uuid.uuid4())
            payload["created_at"] = now
            await db.branch_courses.insert_one(payload)

    await db.branch_courses.update_many(
        {"branch_id": branch_id, "course_id": {"$nin": course_ids or ["__none__"]}},
        {"$set": {"is_available": False, "updated_at": now}},
    )


async def upsert_branch_course(
    db,
    branch_id: str,
    course_id: str,
    *,
    is_available: bool = True,
    fee_per_duration: Optional[Dict[str, float]] = None,
) -> dict:
    branch = await db.branches.find_one({"id": branch_id})
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")
    course = await db.courses.find_one({"id": course_id})
    if not course:
        raise HTTPException(status_code=400, detail="Please select a valid course")

    assignments = branch.get("assignments") or {}
    course_ids = unique_course_ids(assignments.get("courses"))
    if course_id not in course_ids:
        course_ids.append(course_id)
    assignments["courses"] = course_ids
    sched = assignments.get("course_schedule") or []
    found = False
    for row in sched:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("course_id") or "").strip()
        if cid == course_id:
            row["is_available"] = is_available
            found = True
            break
    if not found:
        sched.append({"course_id": course_id, "is_available": is_available, "batches": []})
    assignments["course_schedule"] = sched
    await db.branches.update_one(
        {"id": branch_id},
        {"$set": {"assignments": assignments, "updated_at": datetime.utcnow()}},
    )
    refreshed = await db.branches.find_one({"id": branch_id})
    await sync_branch_courses_from_assignments(db, branch_id, refreshed)
    if fee_per_duration is not None:
        await db.branch_courses.update_one(
            {"branch_id": branch_id, "course_id": course_id},
            {"$set": {"fee_per_duration": fee_per_duration, "updated_at": datetime.utcnow()}},
        )
    mapping = await db.branch_courses.find_one({"branch_id": branch_id, "course_id": course_id})
    return mapping or {}


async def ensure_branch_course_indexes(mongo_db) -> None:
    try:
        await mongo_db.branch_courses.create_index(
            [("branch_id", 1), ("course_id", 1)],
            unique=True,
            name="branch_courses_pair_unique",
        )
    except Exception:
        logging.exception("Failed to create branch_courses unique index")
