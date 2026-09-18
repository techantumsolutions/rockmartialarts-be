"""
M15-S05 Generate bookable coach session slots from weekly availability.

Read-only against coach_availability; subtracts active coach_session_bookings.
Does not modify the availability editor or demo availability.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import HTTPException

from models.coach_availability_models import WEEKDAY_VALUES, time_to_minutes
from models.coach_session_booking_models import ACTIVE_SESSION_STATUSES
from utils.database import get_db

logger = logging.getLogger(__name__)

COL_AVAIL = "coach_availability"
COL_BOOKINGS = "coach_session_bookings"
COL_COACHES = "coaches"

DEFAULT_RANGE_DAYS = 14
MAX_RANGE_DAYS = 60


def _parse_ymd(value: Optional[str], *, field: str) -> date:
    if not value:
        raise HTTPException(status_code=400, detail=f"{field} is required (YYYY-MM-DD)")
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field} must be YYYY-MM-DD") from exc


def _weekday_name(d: date) -> str:
    return WEEKDAY_VALUES[d.weekday()]


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


async def list_coach_session_slots(
    *,
    coach_id: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    branch_id: Optional[str] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    cid = (coach_id or "").strip()
    if not cid:
        raise HTTPException(status_code=400, detail="coach_id is required")

    coach = await db[COL_COACHES].find_one({"id": cid})
    if not coach:
        raise HTTPException(status_code=404, detail="Coach not found")

    today = date.today()
    start = _parse_ymd(date_from, field="from") if date_from else today
    end = (
        _parse_ymd(date_to, field="to")
        if date_to
        else start + timedelta(days=DEFAULT_RANGE_DAYS - 1)
    )
    if end < start:
        raise HTTPException(status_code=400, detail="to must be on or after from")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=400, detail=f"Date range cannot exceed {MAX_RANGE_DAYS} days"
        )

    avail = await db[COL_AVAIL].find_one({"coach_id": cid})
    weekly = list((avail or {}).get("weekly_slots") or [])
    if not weekly:
        return {
            "coach_id": cid,
            "from": start.isoformat(),
            "to": end.isoformat(),
            "slots": [],
            "message": "Coach has no weekly availability set.",
        }

    # Load conflicting bookings in range
    held = (
        await db[COL_BOOKINGS]
        .find(
            {
                "coach_id": cid,
                "status": {"$in": list(ACTIVE_SESSION_STATUSES)},
                "session_date": {
                    "$gte": start.isoformat(),
                    "$lte": end.isoformat(),
                },
            }
        )
        .to_list(length=500)
    )
    by_date: Dict[str, List[Tuple[int, int]]] = {}
    for b in held:
        dkey = str(b.get("session_date") or "")
        try:
            bs = time_to_minutes(str(b.get("start_time") or "00:00"))
            be = time_to_minutes(str(b.get("end_time") or "00:00"))
        except Exception:
            continue
        by_date.setdefault(dkey, []).append((bs, be))

    branch_filter = (branch_id or "").strip() or None
    slots: List[Dict[str, Any]] = []
    d = start
    while d <= end:
        wd = _weekday_name(d)
        dkey = d.isoformat()
        taken = by_date.get(dkey, [])
        for ws in weekly:
            if str(ws.get("weekday") or "").lower() != wd:
                continue
            loc = (ws.get("service_location_id") or "").strip() or None
            if branch_filter and loc and loc != branch_filter:
                continue
            st = str(ws.get("start_time") or "").strip()
            et = str(ws.get("end_time") or "").strip()
            if not st or not et:
                continue
            try:
                sm = time_to_minutes(st)
                em = time_to_minutes(et)
            except Exception:
                continue
            conflict = any(_overlaps(sm, em, ts, te) for ts, te in taken)
            if conflict:
                continue
            slots.append(
                {
                    "coach_id": cid,
                    "session_date": dkey,
                    "weekday": wd,
                    "start_time": st,
                    "end_time": et,
                    "branch_id": loc,
                    "branch_name": ws.get("service_location_name"),
                    "availability_slot_id": ws.get("id"),
                }
            )
        d += timedelta(days=1)

    slots.sort(key=lambda x: (x["session_date"], x["start_time"]))
    return {
        "coach_id": cid,
        "coach_name": coach.get("full_name")
        or f"{coach.get('first_name', '')} {coach.get('last_name', '')}".strip(),
        "from": start.isoformat(),
        "to": end.isoformat(),
        "slots": slots,
        "total": len(slots),
    }


async def assert_slot_available(
    db,
    *,
    coach_id: str,
    session_date: str,
    start_time: str,
    end_time: str,
    exclude_booking_id: Optional[str] = None,
) -> None:
    """Raise 400 if slot outside availability or overlaps an active booking."""
    avail = await db[COL_AVAIL].find_one({"coach_id": coach_id})
    weekly = list((avail or {}).get("weekly_slots") or [])
    try:
        d = date.fromisoformat(session_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid session_date") from exc

    wd = _weekday_name(d)
    sm = time_to_minutes(start_time)
    em = time_to_minutes(end_time)
    covered = False
    for ws in weekly:
        if str(ws.get("weekday") or "").lower() != wd:
            continue
        try:
            ws_s = time_to_minutes(str(ws.get("start_time")))
            ws_e = time_to_minutes(str(ws.get("end_time")))
        except Exception:
            continue
        if sm >= ws_s and em <= ws_e:
            covered = True
            break
    if not covered:
        raise HTTPException(
            status_code=400,
            detail="Selected time is outside the coach's weekly availability.",
        )

    q: Dict[str, Any] = {
        "coach_id": coach_id,
        "session_date": session_date,
        "status": {"$in": list(ACTIVE_SESSION_STATUSES)},
    }
    if exclude_booking_id:
        q["id"] = {"$ne": exclude_booking_id}
    existing = await db[COL_BOOKINGS].find(q).to_list(length=100)
    for b in existing:
        try:
            bs = time_to_minutes(str(b.get("start_time")))
            be = time_to_minutes(str(b.get("end_time")))
        except Exception:
            continue
        if _overlaps(sm, em, bs, be):
            raise HTTPException(
                status_code=409,
                detail="Coach already has a session overlapping this time.",
            )
