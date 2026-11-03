"""Branch–course availability mapping (M02-S04). Dual-writes assignments.courses."""
import logging
import re
import uuid
from datetime import datetime, timezone
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


async def assert_batch_has_capacity(
    db,
    branch_id: str,
    course_id: str,
    batch_ref: Optional[str]
) -> None:
    """Verifies that the requested batch at branch has not exceeded max_students capacity."""
    if not batch_ref or not branch_id or not course_id or db is None:
        return
    branch = await db.branches.find_one({"id": str(branch_id)})
    if not branch:
        return
    course_schedules = (branch.get("assignments") or {}).get("course_schedule") or []
    found_batch = None
    for cs in course_schedules:
        if not isinstance(cs, dict):
            continue
        cid = str(cs.get("course_id") or cs.get("courseId") or "")
        if cid != str(course_id):
            continue
        for b in cs.get("batches") or []:
            if not isinstance(b, dict):
                continue
            bid = str(b.get("batch_id") or b.get("id") or "").strip()
            bname = str(b.get("batch_name") or b.get("name") or "").strip()
            if bid == str(batch_ref) or bname == str(batch_ref):
                found_batch = b
                break
        if found_batch:
            break

    if not found_batch:
        return

    max_cap = found_batch.get("max_students")
    if max_cap is None:
        return
    try:
        max_cap = int(max_cap)
    except (ValueError, TypeError):
        return

    if max_cap <= 0:
        return

    bid_to_check = str(found_batch.get("batch_id") or found_batch.get("id") or batch_ref)
    batch_filters = [{"batch_id": bid_to_check}, {"slot_id": bid_to_check}]
    if str(batch_ref) != bid_to_check:
        batch_filters.append({"batch_ref": str(batch_ref)})

    enr_count = await db.enrollments.count_documents({
        "branch_id": str(branch_id),
        "course_id": str(course_id),
        "$or": batch_filters,
        "is_active": True
    })

    if enr_count >= max_cap:
        batch_title = found_batch.get("batch_name") or "Selected batch"
        raise HTTPException(
            status_code=400,
            detail=f"{batch_title} is full ({enr_count}/{max_cap} students). Please choose another batch."
        )


WEEKDAY_CANONICAL = {
    "mon": "monday", "monday": "monday",
    "tue": "tuesday", "tues": "tuesday", "tuesday": "tuesday",
    "wed": "wednesday", "wednesday": "wednesday",
    "thu": "thursday", "thur": "thursday", "thurs": "thursday", "thursday": "thursday",
    "fri": "friday", "friday": "friday",
    "sat": "saturday", "saturday": "saturday",
    "sun": "sunday", "sunday": "sunday"
}


def _normalize_weekday(day_str: Optional[str]) -> str:
    if not day_str:
        return ""
    clean = str(day_str).strip().lower()
    return WEEKDAY_CANONICAL.get(clean, clean)


def _parse_time_to_minutes(time_str: Optional[str]) -> Optional[int]:
    if not time_str:
        return None
    s = str(time_str).strip().lower()
    m = re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?\s*(am|pm)?$", s)
    if not m:
        return None
    hours = int(m.group(1))
    minutes = int(m.group(2))
    ampm = m.group(3)
    if ampm == "pm" and hours < 12:
        hours += 12
    elif ampm == "am" and hours == 12:
        hours = 0
    return hours * 60 + minutes


def _is_enrollment_currently_active(enr: dict, now: datetime) -> bool:
    if not enr.get("is_active", True):
        return False
    p_status = str(enr.get("payment_status") or "").lower()
    if p_status in ("failed", "cancelled", "refunded"):
        return False
    end_date = enr.get("end_date")
    if not end_date:
        return True
    if isinstance(end_date, datetime):
        return end_date >= now
    if isinstance(end_date, str):
        try:
            clean_str = end_date.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_str)
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt >= now
        except Exception:
            return True
    return True


def _find_batch_in_branch_schedule(branch: dict, course_id: str, batch_ref: str) -> Optional[dict]:
    sched = (branch.get("assignments") or {}).get("course_schedule") or []
    for entry in sched:
        if not isinstance(entry, dict):
            continue
        cid = str(entry.get("course_id") or entry.get("courseId") or "")
        if cid != str(course_id):
            continue
        for b in entry.get("batches") or []:
            if not isinstance(b, dict):
                continue
            bid = str(b.get("batch_id") or b.get("id") or "").strip()
            bname = str(b.get("batch_name") or b.get("name") or "").strip()
            if bid == str(batch_ref) or bname == str(batch_ref):
                return b
    return None


async def assert_student_no_batch_schedule_conflict(
    db,
    student_id: str,
    branch_id: str,
    course_id: str,
    batch_ref: Optional[str],
) -> None:
    """
    Verifies that the requested batch at branch_id for course_id does not collide in
    day and time with any active course enrollment the student already has.
    """
    if not student_id or not branch_id or not course_id or not batch_ref or db is None:
        return

    branch = await db.branches.find_one({"id": str(branch_id)})
    if not branch:
        return

    target_batch = _find_batch_in_branch_schedule(branch, course_id, batch_ref)
    if not target_batch:
        return

    raw_days = target_batch.get("days") or []
    target_days = {_normalize_weekday(d) for d in raw_days if _normalize_weekday(d)}
    target_start = _parse_time_to_minutes(target_batch.get("start_time"))
    target_end = _parse_time_to_minutes(target_batch.get("end_time"))

    if not target_days or target_start is None or target_end is None or target_end <= target_start:
        return

    active_enrollments = await db.enrollments.find({
        "student_id": str(student_id),
        "is_active": True,
        "course_id": {"$ne": str(course_id)},
    }).to_list(100)

    now = datetime.utcnow()
    for enr in active_enrollments:
        if not _is_enrollment_currently_active(enr, now):
            continue

        enr_batch_ref = enr.get("batch_ref") or enr.get("batch_id") or enr.get("slot_id")
        enr_branch_id = enr.get("branch_id")
        enr_course_id = enr.get("course_id")
        if not enr_batch_ref or not enr_branch_id or not enr_course_id:
            continue

        enr_branch = await db.branches.find_one({"id": str(enr_branch_id)})
        if not enr_branch:
            continue

        enr_batch = _find_batch_in_branch_schedule(enr_branch, enr_course_id, enr_batch_ref)
        if not enr_batch:
            continue

        enr_days = {_normalize_weekday(d) for d in (enr_batch.get("days") or []) if _normalize_weekday(d)}
        common_days = target_days & enr_days
        if not common_days:
            continue

        enr_start = _parse_time_to_minutes(enr_batch.get("start_time"))
        enr_end = _parse_time_to_minutes(enr_batch.get("end_time"))
        if enr_start is None or enr_end is None or enr_end <= enr_start:
            continue

        if target_start < enr_end and enr_start < target_end:
            course_doc = await db.courses.find_one({"id": str(enr_course_id)})
            c_name = (course_doc or {}).get("title") or (course_doc or {}).get("name") or "another course"
            b_name = enr_batch.get("batch_name") or enr_batch.get("name") or "Batch"
            days_label = ", ".join(sorted([d.capitalize() for d in common_days]))
            time_label = f"{enr_batch.get('start_time')} - {enr_batch.get('end_time')}"
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Schedule conflict: You are already enrolled in '{c_name}' ({b_name}) "
                    f"on {days_label} ({time_label}). Please choose another batch or time."
                ),
            )
