"""
M14-S03 Coach weekly availability service.

Additive: does not change class schedule / attendance / existing coach CRUD.
Collection: coach_availability (one doc per coach_id).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.coach_availability_models import (
    CoachAvailabilityUpdate,
    time_to_minutes,
)
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.student_status_service import get_managed_branch_ids_for_user

logger = logging.getLogger(__name__)

COL = "coach_availability"
COL_COACHES = "coaches"


def _role(user: Optional[dict]) -> str:
    return str((user or {}).get("role") or "").lower()


async def ensure_coach_availability_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL].create_index("coach_id", unique=True)
        await database[COL].create_index("service_location_ids")
        await database[COL].create_index([("updated_at", -1)])
    except Exception:
        logger.exception("Failed ensuring coach availability indexes")


async def _get_coach(db, coach_id: str) -> dict:
    coach = await db[COL_COACHES].find_one({"id": coach_id})
    if not coach:
        raise HTTPException(status_code=404, detail="Coach not found")
    return coach


async def _assert_can_view(db, coach: dict, current_user: dict) -> None:
    role = _role(current_user)
    if role in {"superadmin", "super_admin", "coach_admin", "coachadmin"}:
        return
    if role == "coach":
        if current_user.get("id") != coach.get("id"):
            raise HTTPException(status_code=403, detail="You can only view your own availability")
        return
    if role in {"branch_manager", "branch_admin", "branchmanager"}:
        managed = await get_managed_branch_ids_for_user(db, current_user)
        if not managed:
            raise HTTPException(status_code=403, detail="No managed branches assigned")
        locs = set()
        if coach.get("branch_id"):
            locs.add(str(coach["branch_id"]))
        for x in coach.get("service_location_ids") or []:
            locs.add(str(x))
        if not locs.intersection({str(m) for m in managed}):
            raise HTTPException(
                status_code=403,
                detail="You can only view coaches in your managed branches.",
            )
        return
    raise HTTPException(status_code=403, detail="Not allowed")


async def _assert_can_edit(db, coach: dict, current_user: dict) -> None:
    role = _role(current_user)
    if role in {"superadmin", "super_admin", "coach_admin", "coachadmin"}:
        return
    if role == "coach":
        if current_user.get("id") != coach.get("id"):
            raise HTTPException(status_code=403, detail="You can only update your own availability")
        approval = str(coach.get("approval_status") or "approved").lower()
        if approval != "approved":
            raise HTTPException(
                status_code=403,
                detail="Only approved coaches can set availability.",
            )
        if not coach.get("is_active", True):
            raise HTTPException(
                status_code=403,
                detail="Inactive coaches cannot update availability.",
            )
        return
    if role in {"branch_manager", "branch_admin", "branchmanager"}:
        await _assert_can_view(db, coach, current_user)
        return
    raise HTTPException(status_code=403, detail="Not allowed to update availability")


async def _resolve_locations(
    db, location_ids: List[str]
) -> List[Dict[str, str]]:
    resolved = []
    for bid in location_ids:
        branch = await db.branches.find_one({"id": bid})
        if not branch:
            raise HTTPException(status_code=400, detail=f"Invalid service location: {bid}")
        if branch.get("is_active") is False:
            raise HTTPException(
                status_code=400, detail=f"Service location is inactive: {bid}"
            )
        name = (
            (branch.get("branch") or {}).get("name")
            or branch.get("name")
            or bid
        )
        resolved.append({"id": bid, "name": str(name)})
    return resolved


def _coach_allowed_location_ids(coach: dict) -> List[str]:
    ids: List[str] = []
    seen = set()
    for x in coach.get("service_location_ids") or []:
        s = str(x).strip()
        if s and s not in seen:
            seen.add(s)
            ids.append(s)
    bid = coach.get("branch_id")
    if bid and str(bid) not in seen:
        ids.append(str(bid))
    return ids


def _detect_slot_overlaps(slots: List[dict]) -> Optional[str]:
    by_day_loc: Dict[tuple, List[dict]] = {}
    for s in slots:
        key = (s["weekday"], s.get("service_location_id") or "*")
        by_day_loc.setdefault(key, []).append(s)
    for (day, loc), group in by_day_loc.items():
        ordered = sorted(group, key=lambda x: time_to_minutes(x["start_time"]))
        for i in range(1, len(ordered)):
            prev = ordered[i - 1]
            cur = ordered[i]
            if time_to_minutes(cur["start_time"]) < time_to_minutes(prev["end_time"]):
                return (
                    f"Overlapping slots on {day}"
                    + (f" at location {loc}" if loc != "*" else "")
                    + f": {prev['start_time']}-{prev['end_time']} and "
                    + f"{cur['start_time']}-{cur['end_time']}"
                )
    return None


def _empty_availability(coach_id: str) -> Dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "coach_id": coach_id,
        "timezone": "Asia/Kolkata",
        "service_location_ids": [],
        "service_location_names": [],
        "weekly_slots": [],
        "notes": None,
        "created_at": None,
        "updated_at": None,
        "updated_by": None,
    }


async def _enrich(db, doc: dict) -> Dict[str, Any]:
    safe = serialize_doc(doc) or {}
    loc_ids = safe.get("service_location_ids") or []
    names = []
    for bid in loc_ids:
        branch = await db.branches.find_one({"id": bid})
        if branch:
            names.append(
                (branch.get("branch") or {}).get("name")
                or branch.get("name")
                or bid
            )
        else:
            names.append(bid)
    safe["service_location_names"] = names

    # Enrich slot location names
    slots = []
    for s in safe.get("weekly_slots") or []:
        slot = dict(s)
        lid = slot.get("service_location_id")
        if lid:
            branch = await db.branches.find_one({"id": lid})
            slot["service_location_name"] = (
                ((branch.get("branch") or {}).get("name") or branch.get("name") or lid)
                if branch
                else lid
            )
        else:
            slot["service_location_name"] = None
        slots.append(slot)
    safe["weekly_slots"] = slots
    return safe


async def get_availability_options(
    *, coach_id: str, current_user: dict
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    coach = await _get_coach(db, coach_id)
    await _assert_can_view(db, coach, current_user)

    role = _role(current_user)
    allowed = _coach_allowed_location_ids(coach)

    # Admins/BM can pick any active branch; coaches limited to their locations
    # (if none registered, fall back to all active so they can set locations)
    if role in {"superadmin", "super_admin", "coach_admin", "coachadmin"} or role in {
        "branch_manager",
        "branch_admin",
        "branchmanager",
    }:
        q: Dict[str, Any] = {"is_active": {"$ne": False}}
        if role in {"branch_manager", "branch_admin", "branchmanager"}:
            managed = await get_managed_branch_ids_for_user(db, current_user)
            q["id"] = {"$in": managed or []}
        branches = await db.branches.find(q).sort([("branch.name", 1)]).to_list(length=300)
    elif allowed:
        branches = (
            await db.branches.find({"id": {"$in": allowed}, "is_active": {"$ne": False}})
            .sort([("branch.name", 1)])
            .to_list(length=100)
        )
    else:
        branches = (
            await db.branches.find({"is_active": {"$ne": False}})
            .sort([("branch.name", 1)])
            .to_list(length=300)
        )

    locations = [
        {
            "id": b.get("id"),
            "name": (b.get("branch") or {}).get("name") or b.get("name") or b.get("id"),
        }
        for b in branches
        if b.get("id")
    ]

    weekdays = [{"value": d, "label": d.capitalize()} for d in (
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
    )]
    return {
        "coach_id": coach_id,
        "locations": locations,
        "weekdays": weekdays,
        "coach_service_location_ids": allowed,
        "timezone_default": "Asia/Kolkata",
    }


async def get_availability(
    *, coach_id: str, current_user: dict
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    coach = await _get_coach(db, coach_id)
    await _assert_can_view(db, coach, current_user)

    doc = await db[COL].find_one({"coach_id": coach_id})
    if not doc:
        empty = _empty_availability(coach_id)
        # Prefill locations from coach profile
        empty["service_location_ids"] = _coach_allowed_location_ids(coach)
        return await _enrich(db, empty)

    return await _enrich(db, doc)


async def update_availability(
    *,
    coach_id: str,
    body: CoachAvailabilityUpdate,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    coach = await _get_coach(db, coach_id)
    await _assert_can_edit(db, coach, current_user)

    location_ids = list(body.service_location_ids or [])
    # Collect location ids referenced by slots
    for slot in body.weekly_slots or []:
        if slot.service_location_id and slot.service_location_id not in location_ids:
            location_ids.append(slot.service_location_id)

    role = _role(current_user)
    if role == "coach":
        allowed = set(_coach_allowed_location_ids(coach))
        if allowed:
            bad = [x for x in location_ids if x not in allowed]
            if bad:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Service locations must be within your registered locations: "
                        + ", ".join(bad)
                    ),
                )

    resolved = await _resolve_locations(db, location_ids) if location_ids else []
    resolved_ids = [r["id"] for r in resolved]
    resolved_names = [r["name"] for r in resolved]

    weekly_slots: List[dict] = []
    for slot in body.weekly_slots or []:
        lid = slot.service_location_id
        if lid and lid not in resolved_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Slot location must be in service_location_ids: {lid}",
            )
        weekly_slots.append(
            {
                "id": slot.id or str(uuid.uuid4()),
                "weekday": slot.weekday,
                "start_time": slot.start_time,
                "end_time": slot.end_time,
                "service_location_id": lid,
                "notes": (slot.notes or "").strip() or None,
            }
        )

    overlap = _detect_slot_overlaps(weekly_slots)
    if overlap:
        raise HTTPException(status_code=400, detail=overlap)

    now = datetime.utcnow()
    existing = await db[COL].find_one({"coach_id": coach_id})
    doc = {
        "id": (existing or {}).get("id") or str(uuid.uuid4()),
        "coach_id": coach_id,
        "timezone": (body.timezone or "Asia/Kolkata").strip() or "Asia/Kolkata",
        "service_location_ids": resolved_ids,
        "service_location_names": resolved_names,
        "weekly_slots": weekly_slots,
        "notes": (body.notes or "").strip() or None,
        "updated_at": now,
        "updated_by": current_user.get("id"),
        "created_at": (existing or {}).get("created_at") or now,
    }

    await db[COL].update_one(
        {"coach_id": coach_id},
        {"$set": doc},
        upsert=True,
    )

    # Keep coach.service_location_ids in sync when locations provided (additive)
    if resolved_ids:
        await db[COL_COACHES].update_one(
            {"id": coach_id},
            {
                "$set": {
                    "service_location_ids": resolved_ids,
                    "service_location_names": resolved_names,
                    "updated_at": now,
                }
            },
        )

    updated = await db[COL].find_one({"coach_id": coach_id})
    return {
        "message": "Availability updated",
        "availability": await _enrich(db, updated),
    }
