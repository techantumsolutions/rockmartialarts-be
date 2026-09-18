"""
M15-S05 Coach session booking service.

Additive collection coach_session_bookings. Does not touch demo_bookings
or training_requests. Uses coach weekly availability for slot validation.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.coach_session_booking_models import (
    ACTIVE_SESSION_STATUSES,
    SESSION_STATUSES,
    CoachSessionBookingCreate,
    CoachSessionReschedule,
    CoachSessionStatus,
    CoachSessionStatusUpdate,
)
from utils.coach_session_slot_service import assert_slot_available, list_coach_session_slots
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.lead_service import _assert_lead_access, lead_to_response_dict
from utils.student_status_service import get_managed_branch_ids_for_user

logger = logging.getLogger(__name__)

COL = "coach_session_bookings"
COL_LEADS = "leads"
COL_COACHES = "coaches"
COL_ASSIGN = "lead_coach_assignments"


def _role(user: Optional[dict]) -> str:
    return str((user or {}).get("role") or "").lower()


def _is_admin(user: Optional[dict]) -> bool:
    return _role(user) in {"superadmin", "super_admin", "coach_admin", "coachadmin"}


def _is_bm(user: Optional[dict]) -> bool:
    return _role(user) in {"branch_manager", "branch_admin", "branchmanager"}


def _is_coach(user: Optional[dict]) -> bool:
    return _role(user) == "coach"


def _actor(user: Optional[dict]) -> Dict[str, Any]:
    u = user or {}
    return {
        "id": u.get("id"),
        "name": u.get("full_name")
        or u.get("name")
        or f"{u.get('first_name', '')} {u.get('last_name', '')}".strip()
        or None,
        "role": u.get("role"),
    }


def _public(doc: Optional[dict]) -> Dict[str, Any]:
    return serialize_doc(doc) or {}


def _coach_name(coach: dict) -> str:
    return (
        coach.get("full_name")
        or f"{coach.get('first_name', '')} {coach.get('last_name', '')}".strip()
        or coach.get("id")
        or "Coach"
    )


async def ensure_coach_session_booking_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL].create_index("id", unique=True)
        await database[COL].create_index(
            [("coach_id", 1), ("session_date", 1), ("start_time", 1)]
        )
        await database[COL].create_index([("lead_id", 1), ("status", 1)])
        await database[COL].create_index("lead_coach_assignment_id")
        await database[COL].create_index([("status", 1), ("session_date", 1)])
        await database[COL].create_index("branch_id")
        await database[COL_LEADS].create_index("next_session_at")
        await database[COL_LEADS].create_index("session_booking_status")
    except Exception:
        logger.exception("Failed ensuring coach session booking indexes")


async def _bm_scope(db, current_user: dict) -> Optional[Dict[str, Any]]:
    if _is_admin(current_user):
        return None
    if not _is_bm(current_user):
        raise HTTPException(status_code=403, detail="Not allowed")
    managed = await get_managed_branch_ids_for_user(db, current_user)
    if not managed:
        return {"branch_id": {"$in": []}}
    return {
        "$or": [
            {"branch_id": {"$in": managed}},
            {"branch_id": {"$exists": False}},
            {"branch_id": None},
            {"branch_id": ""},
        ]
    }


async def _assert_booking_access(db, doc: dict, current_user: dict) -> None:
    if _is_admin(current_user):
        return
    if _is_coach(current_user):
        if str(doc.get("coach_id")) != str(current_user.get("id")):
            raise HTTPException(status_code=403, detail="Not your booking")
        return
    if _is_bm(current_user):
        managed = await get_managed_branch_ids_for_user(db, current_user)
        bid = doc.get("branch_id")
        if bid and str(bid) not in {str(m) for m in managed}:
            raise HTTPException(status_code=403, detail="Outside managed branches")
        return
    raise HTTPException(status_code=403, detail="Not allowed")


async def _set_lead_session_denorm(db, lead_id: Optional[str]) -> None:
    if not lead_id:
        return
    upcoming = await db[COL].find_one(
        {
            "lead_id": lead_id,
            "status": {"$in": list(ACTIVE_SESSION_STATUSES)},
        },
        sort=[("session_date", 1), ("start_time", 1)],
    )
    now = datetime.utcnow()
    if not upcoming:
        await db[COL_LEADS].update_one(
            {"id": lead_id},
            {
                "$set": {
                    "next_session_booking_id": None,
                    "next_session_at": None,
                    "session_booking_status": None,
                    "updated_at": now,
                }
            },
        )
        return
    next_at = f"{upcoming.get('session_date')}T{upcoming.get('start_time')}:00"
    await db[COL_LEADS].update_one(
        {"id": lead_id},
        {
            "$set": {
                "next_session_booking_id": upcoming.get("id"),
                "next_session_at": next_at,
                "session_booking_status": upcoming.get("status"),
                "updated_at": now,
            }
        },
    )


async def create_session_booking(
    body: CoachSessionBookingCreate,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_coach_session_booking_indexes(db)

    coach = await db[COL_COACHES].find_one({"id": body.coach_id})
    if not coach:
        raise HTTPException(status_code=404, detail="Coach not found")

    lead = None
    assignment_id = body.lead_coach_assignment_id
    if body.lead_id:
        lead = await db[COL_LEADS].find_one({"id": body.lead_id})
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
        await _assert_lead_access(db, lead, current_user)
        st = str(lead.get("status") or "").lower()
        if st in {"converted", "lost"}:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot book session when lead status is {st}.",
            )
        # Prefer accepted assignment for this coach
        asn = await db[COL_ASSIGN].find_one(
            {
                "lead_id": body.lead_id,
                "coach_id": body.coach_id,
                "status": "accepted",
            },
            sort=[("assigned_at", -1)],
        )
        if not asn:
            # Allow if denorm says accepted for this coach
            if not (
                lead.get("assigned_coach_id") == body.coach_id
                and lead.get("coach_assignment_status") == "accepted"
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Coach must accept the lead assignment before booking a session.",
                )
        else:
            assignment_id = asn.get("id")

    if _is_coach(current_user) and str(current_user.get("id")) != body.coach_id:
        raise HTTPException(status_code=403, detail="Coaches can only book their own sessions")

    await assert_slot_available(
        db,
        coach_id=body.coach_id,
        session_date=body.session_date,
        start_time=body.start_time,
        end_time=body.end_time,
    )

    branch_id = body.branch_id or (lead.get("branch_id") if lead else None)
    branch_name = body.branch_name or (lead.get("branch_name") if lead else None)
    if branch_id and not branch_name:
        br = await db.branches.find_one({"id": branch_id})
        if br:
            branch_name = (br.get("branch") or {}).get("name") or br.get("name")

    actor = _actor(current_user)
    now = datetime.utcnow()
    doc = {
        "id": str(uuid.uuid4()),
        "coach_id": body.coach_id,
        "coach_name": _coach_name(coach),
        "branch_id": branch_id,
        "branch_name": branch_name,
        "session_date": body.session_date,
        "start_time": body.start_time,
        "end_time": body.end_time,
        "status": CoachSessionStatus.SCHEDULED.value,
        "source_type": (body.source_type or "lead").strip() or "lead",
        "lead_id": body.lead_id,
        "lead_coach_assignment_id": assignment_id,
        "participant_name": body.participant_name
        or (lead.get("name") if lead else None),
        "participant_phone": body.participant_phone
        or (lead.get("phone") if lead else None),
        "participant_email": body.participant_email
        or (lead.get("email") if lead else None),
        "notes": body.notes,
        "cancellation_reason": None,
        "created_at": now,
        "updated_at": now,
        "created_by": actor.get("id"),
        "created_by_name": actor.get("name"),
        "confirmed_at": None,
        "completed_at": None,
        "cancelled_at": None,
    }
    await db[COL].insert_one(doc)
    await _set_lead_session_denorm(db, body.lead_id)
    return {"message": "Session booked", "booking": _public(doc)}


async def list_session_bookings(
    *,
    current_user: dict,
    status: Optional[str] = None,
    coach_id: Optional[str] = None,
    lead_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 25,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    clauses: List[Dict[str, Any]] = []
    if _is_coach(current_user):
        clauses.append({"coach_id": current_user.get("id")})
    else:
        scope = await _bm_scope(db, current_user)
        if scope:
            clauses.append(scope)

    st = (status or "").strip().lower()
    if st and st != "all":
        if st not in SESSION_STATUSES:
            raise HTTPException(status_code=400, detail=f"Invalid status: {st}")
        clauses.append({"status": st})

    if coach_id and coach_id != "all":
        clauses.append({"coach_id": coach_id.strip()})
    if lead_id:
        clauses.append({"lead_id": lead_id.strip()})
    if branch_id and branch_id != "all":
        clauses.append({"branch_id": branch_id.strip()})
    if date_from:
        clauses.append({"session_date": {"$gte": date_from.strip()}})
    if date_to:
        # merge date range carefully
        if date_from:
            clauses[-1] = {
                "session_date": {
                    "$gte": date_from.strip(),
                    "$lte": date_to.strip(),
                }
            }
        else:
            clauses.append({"session_date": {"$lte": date_to.strip()}})

    q_search = (search or "").strip()
    if q_search:
        rx = {"$regex": q_search, "$options": "i"}
        clauses.append(
            {
                "$or": [
                    {"participant_name": rx},
                    {"participant_phone": rx},
                    {"coach_name": rx},
                    {"branch_name": rx},
                    {"id": rx},
                ]
            }
        )

    q: Dict[str, Any] = {"$and": clauses} if clauses else {}
    limit = min(max(limit, 1), 100)
    skip = max(skip, 0)
    total = await db[COL].count_documents(q)
    rows = (
        await db[COL]
        .find(q)
        .sort([("session_date", 1), ("start_time", 1)])
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {
        "bookings": [_public(r) for r in rows],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


async def session_booking_summary(*, current_user: dict) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    match: Dict[str, Any] = {}
    if _is_coach(current_user):
        match = {"coach_id": current_user.get("id")}
    elif _is_bm(current_user):
        scope = await _bm_scope(db, current_user)
        if scope:
            match = scope

    pipeline: List[Dict[str, Any]] = []
    if match:
        pipeline.append({"$match": match})
    pipeline.append(
        {
            "$group": {
                "_id": None,
                "total": {"$sum": 1},
                "by_status": {"$push": "$status"},
            }
        }
    )
    rows = await db[COL].aggregate(pipeline).to_list(length=1)
    by_status = {s: 0 for s in SESSION_STATUSES}
    total = 0
    if rows:
        total = int(rows[0].get("total") or 0)
        for s in rows[0].get("by_status") or []:
            key = str(s)
            if key in by_status:
                by_status[key] += 1
    upcoming = await db[COL].count_documents(
        {
            **(match if match else {}),
            "status": {"$in": list(ACTIVE_SESSION_STATUSES)},
            "session_date": {"$gte": datetime.utcnow().date().isoformat()},
        }
    )
    return {
        "total": total,
        "upcoming": upcoming,
        "by_status": by_status,
    }


async def get_session_booking(booking_id: str, *, current_user: dict) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": booking_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Booking not found")
    await _assert_booking_access(db, doc, current_user)
    return {"booking": _public(doc)}


async def update_session_status(
    booking_id: str,
    body: CoachSessionStatusUpdate,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": booking_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Booking not found")
    await _assert_booking_access(db, doc, current_user)

    current = str(doc.get("status") or "")
    new = body.status.value
    allowed = {
        CoachSessionStatus.SCHEDULED.value: {
            CoachSessionStatus.CONFIRMED.value,
            CoachSessionStatus.CANCELLED.value,
            CoachSessionStatus.COMPLETED.value,
        },
        CoachSessionStatus.CONFIRMED.value: {
            CoachSessionStatus.COMPLETED.value,
            CoachSessionStatus.CANCELLED.value,
        },
        CoachSessionStatus.COMPLETED.value: set(),
        CoachSessionStatus.CANCELLED.value: set(),
    }
    if new == current:
        return {"message": "No change", "booking": _public(doc)}
    if new not in allowed.get(current, set()):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot change status from {current} to {new}",
        )

    now = datetime.utcnow()
    patch: Dict[str, Any] = {
        "status": new,
        "updated_at": now,
        "updated_by": current_user.get("id"),
    }
    if body.notes is not None:
        patch["notes"] = body.notes
    if new == CoachSessionStatus.CONFIRMED.value:
        patch["confirmed_at"] = now
    if new == CoachSessionStatus.COMPLETED.value:
        patch["completed_at"] = now
    if new == CoachSessionStatus.CANCELLED.value:
        patch["cancelled_at"] = now
        patch["cancellation_reason"] = body.cancellation_reason

    await db[COL].update_one({"id": booking_id}, {"$set": patch})
    updated = await db[COL].find_one({"id": booking_id})
    await _set_lead_session_denorm(db, doc.get("lead_id"))
    return {"message": "Status updated", "booking": _public(updated)}


async def reschedule_session(
    booking_id: str,
    body: CoachSessionReschedule,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": booking_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Booking not found")
    await _assert_booking_access(db, doc, current_user)
    if doc.get("status") not in ACTIVE_SESSION_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reschedule a {doc.get('status')} booking",
        )

    await assert_slot_available(
        db,
        coach_id=doc["coach_id"],
        session_date=body.session_date,
        start_time=body.start_time,
        end_time=body.end_time,
        exclude_booking_id=booking_id,
    )

    now = datetime.utcnow()
    patch: Dict[str, Any] = {
        "session_date": body.session_date,
        "start_time": body.start_time,
        "end_time": body.end_time,
        "updated_at": now,
        "updated_by": current_user.get("id"),
        "status": CoachSessionStatus.SCHEDULED.value,
    }
    if body.branch_id is not None:
        patch["branch_id"] = body.branch_id or None
    if body.notes is not None:
        patch["notes"] = body.notes

    await db[COL].update_one({"id": booking_id}, {"$set": patch})
    updated = await db[COL].find_one({"id": booking_id})
    await _set_lead_session_denorm(db, doc.get("lead_id"))
    return {"message": "Session rescheduled", "booking": _public(updated)}


async def list_lead_session_bookings(
    lead_id: str, *, current_user: dict
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    lead = await db[COL_LEADS].find_one({"id": lead_id})
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    await _assert_lead_access(db, lead, current_user)
    rows = (
        await db[COL]
        .find({"lead_id": lead_id})
        .sort([("session_date", -1), ("start_time", -1)])
        .limit(50)
        .to_list(length=50)
    )
    return {
        "lead": lead_to_response_dict(serialize_doc(lead) or {}),
        "bookings": [_public(r) for r in rows],
    }


async def coach_schedule(
    *,
    current_user: dict,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, Any]:
    if not _is_coach(current_user) and not _is_admin(current_user):
        # BM can also view via list; schedule is primarily coach
        if not _is_bm(current_user):
            raise HTTPException(status_code=403, detail="Not allowed")
    # For coach, force own id
    coach_id = current_user.get("id") if _is_coach(current_user) else None
    data = await list_session_bookings(
        current_user=current_user,
        coach_id=coach_id,
        date_from=date_from,
        date_to=date_to,
        status="all",
        skip=0,
        limit=100,
    )
    # Also include open slots for context (coach only)
    slots = None
    if _is_coach(current_user):
        try:
            slots = await list_coach_session_slots(
                coach_id=current_user.get("id"),
                date_from=date_from,
                date_to=date_to,
            )
        except Exception:
            slots = None
    return {
        "bookings": data.get("bookings") or [],
        "total": data.get("total") or 0,
        "open_slots": (slots or {}).get("slots") if slots else [],
    }
