"""
M13-S02 Demo slot availability — generate bookable slots from demo_schedules.

Capacity counts held seats in demo_bookings (pending_payment / confirmed / paid).
S03 creates bookings; until then remaining seats = full capacity.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, time
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from models.demo_schedule_models import WEEKDAY_VALUES, time_to_minutes
from utils.database import get_db

logger = logging.getLogger(__name__)

COL_SCHEDULES = "demo_schedules"
COL_BOOKINGS = "demo_bookings"
DEMO_TZ = ZoneInfo("Asia/Kolkata")

# Statuses that occupy a seat (S03 will write these)
HELD_BOOKING_STATUSES = ("pending_payment", "confirmed", "paid")

DEFAULT_RANGE_DAYS = 14
MAX_RANGE_DAYS = 60


def _parse_ymd(value: Optional[str], *, field: str) -> date:
    if not value:
        raise HTTPException(status_code=400, detail=f"{field} is required (YYYY-MM-DD)")
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"{field} must be YYYY-MM-DD"
        ) from exc


def _parse_hhmm(value: str) -> time:
    parts = str(value or "").strip().split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    return time(hour=hour, minute=minute)


def _weekday_name(d: date) -> str:
    # Monday=0 … Sunday=6 matches WEEKDAY_VALUES order
    return WEEKDAY_VALUES[d.weekday()]


def _in_effective_window(sched: dict, d: date) -> bool:
    ef = (sched.get("effective_from") or "").strip()
    eu = (sched.get("effective_until") or "").strip()
    if ef:
        try:
            if d < date.fromisoformat(ef):
                return False
        except ValueError:
            pass
    if eu:
        try:
            if d > date.fromisoformat(eu):
                return False
        except ValueError:
            pass
    return True


def _slot_start_dt(d: date, start_time: str) -> datetime:
    return datetime.combine(d, _parse_hhmm(start_time), tzinfo=DEMO_TZ)


def _slot_end_dt(d: date, end_time: str) -> datetime:
    return datetime.combine(d, _parse_hhmm(end_time), tzinfo=DEMO_TZ)


def _is_past_slot(d: date, start_time: str, *, now: Optional[datetime] = None) -> bool:
    """Past/expired: slot start is at or before now (IST)."""
    current = now or datetime.now(DEMO_TZ)
    try:
        return _slot_start_dt(d, start_time) <= current
    except Exception:
        return True


async def _booking_counts_for_range(
    db,
    *,
    schedule_ids: List[str],
    date_from: date,
    date_to: date,
) -> Dict[Tuple[str, str], int]:
    """
    Map (schedule_id, slot_date) -> held booking count.
    slot_date stored as YYYY-MM-DD on bookings (S03).
    """
    if not schedule_ids:
        return {}
    try:
        pipeline = [
            {
                "$match": {
                    "schedule_id": {"$in": schedule_ids},
                    "slot_date": {
                        "$gte": date_from.isoformat(),
                        "$lte": date_to.isoformat(),
                    },
                    "status": {"$in": list(HELD_BOOKING_STATUSES)},
                }
            },
            {
                "$group": {
                    "_id": {"schedule_id": "$schedule_id", "slot_date": "$slot_date"},
                    "count": {"$sum": 1},
                }
            },
        ]
        rows = await db[COL_BOOKINGS].aggregate(pipeline).to_list(length=5000)
    except Exception:
        # Collection may not exist yet — treat as zero bookings
        logger.debug("demo_bookings aggregate skipped / empty", exc_info=True)
        return {}

    out: Dict[Tuple[str, str], int] = {}
    for row in rows or []:
        key = row.get("_id") or {}
        sid = str(key.get("schedule_id") or "")
        sdate = str(key.get("slot_date") or "")
        if sid and sdate:
            out[(sid, sdate)] = int(row.get("count") or 0)
    return out


def generate_slots_from_schedules(
    schedules: List[dict],
    *,
    date_from: date,
    date_to: date,
    booking_counts: Optional[Dict[Tuple[str, str], int]] = None,
    now: Optional[datetime] = None,
    include_full: bool = False,
) -> List[Dict[str, Any]]:
    """Pure slot expansion (testable). Excludes past slots; optionally excludes full."""
    current = now or datetime.now(DEMO_TZ)
    counts = booking_counts or {}
    slots: List[Dict[str, Any]] = []

    day = date_from
    while day <= date_to:
        day_name = _weekday_name(day)
        for sched in schedules:
            if not sched.get("is_active", True):
                continue
            if not _in_effective_window(sched, day):
                continue
            weekdays = sched.get("weekdays") or []
            if day_name not in weekdays:
                continue

            start_time = str(sched.get("start_time") or "")
            end_time = str(sched.get("end_time") or "")
            if not start_time or not end_time:
                continue
            try:
                if time_to_minutes(start_time) >= time_to_minutes(end_time):
                    continue
            except ValueError:
                continue

            if _is_past_slot(day, start_time, now=current):
                continue

            sid = str(sched.get("id") or "")
            booked = int(counts.get((sid, day.isoformat()), 0))
            capacity = sched.get("capacity")
            capacity_int = int(capacity) if capacity is not None else None
            remaining = (
                None if capacity_int is None else max(0, capacity_int - booked)
            )
            is_full = capacity_int is not None and remaining == 0
            if is_full and not include_full:
                continue

            slots.append(
                {
                    "schedule_id": sid,
                    "slot_date": day.isoformat(),
                    "weekday": day_name,
                    "start_time": start_time,
                    "end_time": end_time,
                    "branch_id": sched.get("branch_id"),
                    "branch_name": sched.get("branch_name"),
                    "course_id": sched.get("course_id"),
                    "course_name": sched.get("course_name"),
                    "title": sched.get("title"),
                    "fee_inr": float(sched.get("fee_inr") or 0),
                    "capacity": capacity_int,
                    "booked_count": booked,
                    "remaining": remaining,
                    "is_full": is_full,
                    "recurrence": sched.get("recurrence"),
                }
            )
        day += timedelta(days=1)

    slots.sort(
        key=lambda s: (
            s.get("slot_date") or "",
            s.get("start_time") or "",
            s.get("branch_name") or "",
            s.get("course_name") or "",
        )
    )
    return slots


async def list_demo_availability(
    *,
    branch_id: Optional[str] = None,
    course_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    include_full: bool = False,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    # Release abandoned pending seats before capacity math
    try:
        from utils.demo_booking_service import expire_stale_pending_bookings

        await expire_stale_pending_bookings(db)
    except Exception:
        logger.debug("expire before availability skipped", exc_info=True)

    today = datetime.now(DEMO_TZ).date()
    if date_from:
        start = _parse_ymd(date_from, field="from")
    else:
        start = today
    if date_to:
        end = _parse_ymd(date_to, field="to")
    else:
        end = start + timedelta(days=DEFAULT_RANGE_DAYS - 1)

    if end < start:
        raise HTTPException(status_code=400, detail="to must be on or after from")
    if (end - start).days > MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"Date range cannot exceed {MAX_RANGE_DAYS} days",
        )

    # Never return past calendar days as bookable window start
    if start < today:
        start = today

    q: Dict[str, Any] = {"is_active": True}
    if branch_id:
        q["branch_id"] = str(branch_id).strip()
    if course_id:
        q["course_id"] = str(course_id).strip()

    schedules = await db[COL_SCHEDULES].find(q).to_list(length=500)
    schedule_ids = [str(s.get("id")) for s in schedules if s.get("id")]
    counts = await _booking_counts_for_range(
        db, schedule_ids=schedule_ids, date_from=start, date_to=end
    )
    slots = generate_slots_from_schedules(
        schedules,
        date_from=start,
        date_to=end,
        booking_counts=counts,
        include_full=include_full,
    )
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "timezone": "Asia/Kolkata",
        "count": len(slots),
        "slots": slots,
    }


async def list_demo_availability_options() -> Dict[str, Any]:
    """Public: branches/courses that currently have at least one active demo schedule."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    rows = await db[COL_SCHEDULES].find({"is_active": True}).to_list(length=1000)
    branches: Dict[str, str] = {}
    courses: Dict[str, str] = {}
    for s in rows:
        bid = str(s.get("branch_id") or "")
        cid = str(s.get("course_id") or "")
        if bid:
            branches[bid] = str(s.get("branch_name") or bid)
        if cid:
            courses[cid] = str(s.get("course_name") or cid)

    return {
        "branches": [
            {"id": k, "name": v} for k, v in sorted(branches.items(), key=lambda x: x[1])
        ],
        "courses": [
            {"id": k, "name": v} for k, v in sorted(courses.items(), key=lambda x: x[1])
        ],
    }
