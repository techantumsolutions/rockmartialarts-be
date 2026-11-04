"""Checks that a coach is free and inside their saved availability for a batch."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set

from fastapi import HTTPException

from models.coach_availability_models import normalize_weekday, time_to_minutes


def _branch_name(branch: dict) -> str:
    nested = branch.get("branch") if isinstance(branch.get("branch"), dict) else {}
    return str(nested.get("name") or branch.get("name") or "Branch")


def _minutes(value: str) -> Optional[int]:
    try:
        return time_to_minutes(str(value or ""))
    except ValueError:
        return None


def _weekday(value: str) -> Optional[str]:
    try:
        return normalize_weekday(str(value or ""))
    except ValueError:
        return None


def _slot_covers(slot: dict, weekday: str, start: int, end: int, branch_id: str) -> bool:
    slot_day = _weekday(str(slot.get("weekday") or ""))
    if slot_day != weekday:
        return False
    location = str(slot.get("service_location_id") or "").strip()
    if location and location != str(branch_id):
        return False
    slot_start = _minutes(str(slot.get("start_time") or ""))
    slot_end = _minutes(str(slot.get("end_time") or ""))
    if slot_start is None or slot_end is None or slot_end <= slot_start:
        return False
    return slot_start <= start and slot_end >= end


def _ranges_overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    return start_a < end_b and start_b < end_a


def _label(batch: dict) -> str:
    days = ", ".join(day.capitalize() for day in batch.get("day_keys") or [])
    timing = f"{batch.get('start_time') or ''} – {batch.get('end_time') or ''}".strip()
    name = batch.get("batch_name") or "Batch"
    branch = batch.get("branch_name") or "Branch"
    return f"{days} {timing} ({name}, {branch})".strip()


def iter_active_batches(branch_id: str, branch_name: str, schedule: Any) -> Iterable[dict]:
    if not isinstance(schedule, list):
        return
    for entry in schedule:
        if not isinstance(entry, dict) or entry.get("is_available") is False:
            continue
        course_id = str(entry.get("course_id") or entry.get("courseId") or "")
        for index, batch in enumerate(entry.get("batches") or []):
            if not isinstance(batch, dict):
                continue
            days = []
            for day in batch.get("days") or []:
                key = _weekday(str(day))
                if key and key not in days:
                    days.append(key)
            start_raw = str(batch.get("start_time") or batch.get("startTime") or "").strip()
            end_raw = str(batch.get("end_time") or batch.get("endTime") or "").strip()
            start = _minutes(start_raw)
            end = _minutes(end_raw)
            batch_id = str(batch.get("batch_id") or batch.get("id") or f"{course_id}:{index}")
            name = str(batch.get("batch_name") or batch.get("name") or "").strip()
            yield {
                "branch_id": str(branch_id),
                "branch_name": branch_name,
                "batch_id": batch_id,
                "batch_name": name or f"Batch {index + 1}",
                "coach_id": str(batch.get("coach_id") or batch.get("coachId") or "").strip(),
                "day_keys": days,
                "start_time": start_raw,
                "end_time": end_raw,
                "start": start,
                "end": end,
                "complete": bool(days) and start is not None and end is not None and end > start,
            }


async def coach_assignment_ids(db, coach: dict) -> Set[str]:
    ids = {str(coach.get("id") or "")}
    ids.discard("")
    user_id = str(coach.get("user_id") or "").strip()
    if user_id:
        ids.add(user_id)

    emails = set()
    contact = coach.get("contact_info") if isinstance(coach.get("contact_info"), dict) else {}
    for raw in (coach.get("email"), contact.get("email")):
        if isinstance(raw, str) and raw.strip():
            emails.add(raw.strip().lower())
    if not emails:
        return ids

    managers = await db.branch_managers.find(
        {},
        {"id": 1, "email": 1, "contact_info.email": 1},
    ).to_list(length=200)
    for manager in managers:
        manager_contact = manager.get("contact_info") if isinstance(manager.get("contact_info"), dict) else {}
        manager_emails = set()
        for raw in (manager.get("email"), manager_contact.get("email")):
            if isinstance(raw, str) and raw.strip():
                manager_emails.add(raw.strip().lower())
        if emails & manager_emails and manager.get("id"):
            ids.add(str(manager["id"]))
    return ids


def _availability_problem(batch: dict, slots: List[dict]) -> Optional[str]:
    if not batch.get("complete"):
        return None
    if not slots:
        return "Set this coach's availability before assigning a batch."
    missing = [
        day
        for day in batch["day_keys"]
        if not any(
            _slot_covers(slot, day, batch["start"], batch["end"], batch["branch_id"])
            for slot in slots
        )
    ]
    if not missing:
        return None
    days = ", ".join(day.capitalize() for day in missing)
    return (
        f"This coach is not available {days} "
        f"{batch['start_time']} – {batch['end_time']}."
    )


def _booking_problem(batch: dict, others: List[dict], match_ids: Set[str]) -> Optional[str]:
    if not batch.get("complete") or batch.get("coach_id") not in match_ids:
        return None
    for other in others:
        if not other.get("complete"):
            continue
        if other.get("coach_id") not in match_ids:
            continue
        if other["branch_id"] == batch["branch_id"] and other["batch_id"] == batch["batch_id"]:
            continue
        if not set(batch["day_keys"]) & set(other["day_keys"]):
            continue
        if _ranges_overlap(batch["start"], batch["end"], other["start"], other["end"]):
            return f"This coach is already assigned {_label(other)}."
    return None


async def assert_batch_coach_assignments(
    db,
    branch_id: str,
    branch_name: str,
    course_schedule: Any,
) -> None:
    """Reject a branch save when a selected coach is unavailable or already booked."""
    proposed = list(iter_active_batches(branch_id, branch_name, course_schedule))
    coach_ids = sorted({batch["coach_id"] for batch in proposed if batch["coach_id"]})
    if not coach_ids:
        return

    coaches = await db.coaches.find(
        {"id": {"$in": coach_ids}},
        {"id": 1, "email": 1, "user_id": 1, "contact_info.email": 1},
    ).to_list(length=500)
    by_id = {str(coach["id"]): coach for coach in coaches}
    if not by_id:
        return

    slots_by_coach: Dict[str, List[dict]] = {}
    availability_docs = await db.coach_availability.find(
        {"coach_id": {"$in": list(by_id)}}
    ).to_list(length=500)
    for doc in availability_docs:
        slots_by_coach[str(doc.get("coach_id"))] = list(doc.get("weekly_slots") or [])

    match_by_coach: Dict[str, Set[str]] = {}
    for coach_id, coach in by_id.items():
        match_by_coach[coach_id] = await coach_assignment_ids(db, coach)

    other_branches = await db.branches.find(
        {"is_active": True, "id": {"$ne": branch_id}},
        {"id": 1, "branch.name": 1, "assignments.course_schedule": 1},
    ).to_list(length=500)
    external: List[dict] = []
    for branch in other_branches:
        schedule = ((branch.get("assignments") or {}).get("course_schedule")) or []
        external.extend(iter_active_batches(branch.get("id"), _branch_name(branch), schedule))

    for batch in proposed:
        coach = by_id.get(batch["coach_id"])
        if not coach:
            continue
        slots = slots_by_coach.get(batch["coach_id"], [])
        problem = _availability_problem(batch, slots)
        if problem:
            raise HTTPException(status_code=400, detail=problem)
        others = [item for item in proposed if item is not batch] + external
        problem = _booking_problem(batch, others, match_by_coach[batch["coach_id"]])
        if problem:
            raise HTTPException(status_code=400, detail=problem)


async def assert_availability_covers_assigned_batches(
    db,
    coach: dict,
    weekly_slots: List[dict],
) -> None:
    """Reject an availability save that would leave an active batch uncovered."""
    match_ids = await coach_assignment_ids(db, coach)
    branches = await db.branches.find(
        {"is_active": True},
        {"id": 1, "branch.name": 1, "assignments.course_schedule": 1},
    ).to_list(length=500)

    missed: List[str] = []
    for branch in branches:
        schedule = ((branch.get("assignments") or {}).get("course_schedule")) or []
        for batch in iter_active_batches(branch.get("id"), _branch_name(branch), schedule):
            if batch["coach_id"] not in match_ids or not batch.get("complete"):
                continue
            if _availability_problem(batch, weekly_slots) is None and weekly_slots:
                continue
            if not weekly_slots or _availability_problem(batch, weekly_slots):
                missed.append(f"Batch '{batch['batch_name']}' ({_label(batch)})")
    if missed:
        detail_msg = (
            "Cannot remove or modify availability: The coach is currently assigned to "
            + ", ".join(missed[:6])
            + ". Please reassign or change the coach for the assigned batch(es) first before modifying availability."
        )
        raise HTTPException(status_code=400, detail=detail_msg)
