"""
M15-S04 Lead coach assignment service.

Additive: separate from training_request assign_coach (no accept/decline there).
Does not modify training request flows.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.lead_coach_assignment_models import (
    ACTIVE_ASSIGNMENT_STATUSES,
    ASSIGNMENT_STATUSES,
    LeadCoachAcceptBody,
    LeadCoachAssignBody,
    LeadCoachAssignmentStatus,
    LeadCoachDeclineBody,
)
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.lead_service import _assert_lead_access, lead_to_response_dict
from utils.student_status_service import get_managed_branch_ids_for_user

logger = logging.getLogger(__name__)

COL = "lead_coach_assignments"
COL_LEADS = "leads"
COL_COACHES = "coaches"


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


def _coach_display_name(coach: dict) -> str:
    return (
        coach.get("full_name")
        or f"{coach.get('first_name', '')} {coach.get('last_name', '')}".strip()
        or coach.get("id")
        or "Coach"
    )


def _coach_eligible(coach: dict) -> bool:
    if coach.get("is_active") is False:
        return False
    approval = str(coach.get("approval_status") or "approved").lower().strip()
    return approval in {"", "approved"}


async def ensure_lead_coach_assignment_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL].create_index("id", unique=True)
        await database[COL].create_index([("lead_id", 1), ("status", 1)])
        await database[COL].create_index([("coach_id", 1), ("status", 1)])
        await database[COL].create_index([("assigned_at", -1)])
        await database[COL_LEADS].create_index("assigned_coach_id")
        await database[COL_LEADS].create_index("coach_assignment_status")
    except Exception:
        logger.exception("Failed ensuring lead coach assignment indexes")


async def _resolve_coach(db, coach_id: str) -> dict:
    coach = await db[COL_COACHES].find_one({"id": coach_id})
    if not coach:
        user = await db.users.find_one({"id": coach_id, "role": "coach"})
        if not user:
            raise HTTPException(status_code=404, detail="Coach not found")
        coach = user
    if not _coach_eligible(coach):
        raise HTTPException(
            status_code=400,
            detail="Coach must be active and approved to receive assignments.",
        )
    return coach


def _coach_matches_branch(coach: dict, branch_id: Optional[str]) -> bool:
    if not branch_id:
        return True
    bid = str(branch_id)
    coach_branch = coach.get("branch_id") or (coach.get("branch_assignment") or {}).get(
        "branch_id"
    )
    if coach_branch and str(coach_branch) == bid:
        return True
    locs = coach.get("service_location_ids") or []
    if isinstance(locs, list) and any(str(x) == bid for x in locs):
        return True
    # No location set → treat as eligible (wide pool)
    if not coach_branch and not locs:
        return True
    return False


async def list_eligible_coaches(
    lead_id: str, *, current_user: dict
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    lead = await db[COL_LEADS].find_one({"id": lead_id})
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    await _assert_lead_access(db, lead, current_user)

    branch_id = (lead.get("branch_id") or "").strip() or None
    cursor = db[COL_COACHES].find({})
    rows = await cursor.to_list(length=500)
    preferred: List[Dict[str, Any]] = []
    others: List[Dict[str, Any]] = []
    for c in rows:
        if not _coach_eligible(c):
            continue
        item = {
            "id": c.get("id"),
            "full_name": _coach_display_name(c),
            "email": (c.get("contact_info") or {}).get("email") or c.get("email"),
            "phone": (c.get("contact_info") or {}).get("phone") or c.get("phone"),
            "branch_id": c.get("branch_id"),
            "service_location_ids": c.get("service_location_ids") or [],
            "area_of_expertise": c.get("area_of_expertise"),
            "preferred": _coach_matches_branch(c, branch_id),
        }
        if item["preferred"]:
            preferred.append(item)
        else:
            others.append(item)

    preferred.sort(key=lambda x: (x.get("full_name") or "").lower())
    others.sort(key=lambda x: (x.get("full_name") or "").lower())
    return {
        "lead_id": lead_id,
        "branch_id": branch_id,
        "coaches": preferred + others,
        "preferred_count": len(preferred),
    }


async def get_lead_assignment(
    lead_id: str, *, current_user: dict
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    lead = await db[COL_LEADS].find_one({"id": lead_id})
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    await _assert_lead_access(db, lead, current_user)

    current = await db[COL].find_one(
        {
            "lead_id": lead_id,
            "status": {"$in": list(ACTIVE_ASSIGNMENT_STATUSES)},
        },
        sort=[("assigned_at", -1)],
    )
    history = (
        await db[COL]
        .find({"lead_id": lead_id})
        .sort([("assigned_at", -1)])
        .limit(20)
        .to_list(length=20)
    )
    return {
        "lead": lead_to_response_dict(serialize_doc(lead) or {}),
        "assignment": _public(current) if current else None,
        "history": [_public(h) for h in history],
    }


async def _set_lead_denorm(db, lead_id: str, assignment: Optional[dict]) -> None:
    now = datetime.utcnow()
    if not assignment:
        await db[COL_LEADS].update_one(
            {"id": lead_id},
            {
                "$set": {
                    "coach_assignment_id": None,
                    "assigned_coach_id": None,
                    "assigned_coach_name": None,
                    "coach_assignment_status": None,
                    "updated_at": now,
                }
            },
        )
        return
    await db[COL_LEADS].update_one(
        {"id": lead_id},
        {
            "$set": {
                "coach_assignment_id": assignment.get("id"),
                "assigned_coach_id": assignment.get("coach_id"),
                "assigned_coach_name": assignment.get("coach_name"),
                "coach_assignment_status": assignment.get("status"),
                "updated_at": now,
            }
        },
    )


async def assign_coach_to_lead(
    lead_id: str,
    body: LeadCoachAssignBody,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_lead_coach_assignment_indexes(db)

    lead = await db[COL_LEADS].find_one({"id": lead_id})
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    await _assert_lead_access(db, lead, current_user)

    status = str(lead.get("status") or "new").lower()
    if status in {"converted", "lost"}:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot assign coach when lead status is {status}.",
        )

    coach = await _resolve_coach(db, body.coach_id)
    coach_name = _coach_display_name(coach)
    now = datetime.utcnow()
    actor = _actor(current_user)

    # Supersede any active assignment
    await db[COL].update_many(
        {
            "lead_id": lead_id,
            "status": {"$in": list(ACTIVE_ASSIGNMENT_STATUSES)},
        },
        {
            "$set": {
                "status": LeadCoachAssignmentStatus.SUPERSEDED.value,
                "updated_at": now,
                "superseded_at": now,
                "superseded_by": actor.get("id"),
            }
        },
    )

    doc = {
        "id": str(uuid.uuid4()),
        "lead_id": lead_id,
        "coach_id": body.coach_id,
        "coach_name": coach_name,
        "branch_id": lead.get("branch_id"),
        "branch_name": lead.get("branch_name"),
        "lead_name": lead.get("name"),
        "lead_phone": lead.get("phone"),
        "status": LeadCoachAssignmentStatus.PENDING.value,
        "note": body.note,
        "assigned_by": actor.get("id"),
        "assigned_by_name": actor.get("name"),
        "assigned_by_role": actor.get("role"),
        "assigned_at": now,
        "updated_at": now,
        "responded_at": None,
        "response_note": None,
        "decline_reason": None,
    }
    await db[COL].insert_one(doc)
    await _set_lead_denorm(db, lead_id, doc)

    # Soft pipeline nudge: new → contacted when first assigned
    if status == "new":
        await db[COL_LEADS].update_one(
            {"id": lead_id},
            {"$set": {"status": "contacted", "updated_at": now}},
        )

    updated_lead = await db[COL_LEADS].find_one({"id": lead_id})
    return {
        "message": "Coach assigned. Waiting for coach response.",
        "assignment": _public(doc),
        "lead": lead_to_response_dict(serialize_doc(updated_lead) or {}),
    }


async def cancel_lead_assignment(
    lead_id: str,
    *,
    current_user: dict,
    assignment_id: Optional[str] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    lead = await db[COL_LEADS].find_one({"id": lead_id})
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    await _assert_lead_access(db, lead, current_user)

    q: Dict[str, Any] = {
        "lead_id": lead_id,
        "status": LeadCoachAssignmentStatus.PENDING.value,
    }
    if assignment_id:
        q["id"] = assignment_id

    doc = await db[COL].find_one(q, sort=[("assigned_at", -1)])
    if not doc:
        raise HTTPException(status_code=404, detail="No pending assignment to cancel")

    now = datetime.utcnow()
    actor = _actor(current_user)
    await db[COL].update_one(
        {"id": doc["id"]},
        {
            "$set": {
                "status": LeadCoachAssignmentStatus.CANCELLED.value,
                "updated_at": now,
                "cancelled_at": now,
                "cancelled_by": actor.get("id"),
            }
        },
    )
    await _set_lead_denorm(db, lead_id, None)
    updated = await db[COL].find_one({"id": doc["id"]})
    lead2 = await db[COL_LEADS].find_one({"id": lead_id})
    return {
        "message": "Assignment cancelled",
        "assignment": _public(updated),
        "lead": lead_to_response_dict(serialize_doc(lead2) or {}),
    }


async def list_my_assignments(
    *,
    current_user: dict,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 25,
) -> Dict[str, Any]:
    if not _is_coach(current_user):
        raise HTTPException(status_code=403, detail="Coach access only")
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    coach_id = current_user.get("id")
    clauses: List[Dict[str, Any]] = [{"coach_id": coach_id}]
    st = (status or "").strip().lower()
    if st and st != "all":
        if st not in ASSIGNMENT_STATUSES:
            raise HTTPException(status_code=400, detail=f"Invalid status: {st}")
        clauses.append({"status": st})

    q: Dict[str, Any] = {"$and": clauses} if len(clauses) > 1 else clauses[0]
    limit = min(max(limit, 1), 100)
    skip = max(skip, 0)
    total = await db[COL].count_documents(q)
    rows = (
        await db[COL]
        .find(q)
        .sort([("assigned_at", -1)])
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {
        "assignments": [_public(r) for r in rows],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


async def _get_assignment_for_coach(db, assignment_id: str, current_user: dict) -> dict:
    if not _is_coach(current_user):
        raise HTTPException(status_code=403, detail="Coach access only")
    doc = await db[COL].find_one({"id": assignment_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Assignment not found")
    if str(doc.get("coach_id")) != str(current_user.get("id")):
        raise HTTPException(
            status_code=403, detail="You can only respond to your own assignments."
        )
    return doc


async def accept_assignment(
    assignment_id: str,
    body: Optional[LeadCoachAcceptBody] = None,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await _get_assignment_for_coach(db, assignment_id, current_user)
    if doc.get("status") != LeadCoachAssignmentStatus.PENDING.value:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot accept assignment with status {doc.get('status')}",
        )

    now = datetime.utcnow()
    note = (body.note if body else None) or None
    await db[COL].update_one(
        {"id": assignment_id},
        {
            "$set": {
                "status": LeadCoachAssignmentStatus.ACCEPTED.value,
                "responded_at": now,
                "updated_at": now,
                "response_note": note,
            }
        },
    )
    updated = await db[COL].find_one({"id": assignment_id})
    await _set_lead_denorm(db, doc["lead_id"], updated)

    # Soft nudge lead toward qualified when coach accepts
    lead = await db[COL_LEADS].find_one({"id": doc["lead_id"]})
    if lead and str(lead.get("status") or "").lower() in {"new", "contacted"}:
        await db[COL_LEADS].update_one(
            {"id": doc["lead_id"]},
            {"$set": {"status": "qualified", "updated_at": now}},
        )
        lead = await db[COL_LEADS].find_one({"id": doc["lead_id"]})

    return {
        "message": "Assignment accepted",
        "assignment": _public(updated),
        "lead": lead_to_response_dict(serialize_doc(lead) or {}) if lead else None,
    }


async def decline_assignment(
    assignment_id: str,
    body: Optional[LeadCoachDeclineBody] = None,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await _get_assignment_for_coach(db, assignment_id, current_user)
    if doc.get("status") != LeadCoachAssignmentStatus.PENDING.value:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot decline assignment with status {doc.get('status')}",
        )

    now = datetime.utcnow()
    reason = body.reason if body else None
    note = body.note if body else None
    await db[COL].update_one(
        {"id": assignment_id},
        {
            "$set": {
                "status": LeadCoachAssignmentStatus.DECLINED.value,
                "responded_at": now,
                "updated_at": now,
                "decline_reason": reason,
                "response_note": note,
            }
        },
    )
    updated = await db[COL].find_one({"id": assignment_id})
    # Clear denorm so admin can reassign
    await _set_lead_denorm(db, doc["lead_id"], None)
    # Keep declined status visible via last assignment history; optionally stamp declined on lead
    await db[COL_LEADS].update_one(
        {"id": doc["lead_id"]},
        {
            "$set": {
                "coach_assignment_status": LeadCoachAssignmentStatus.DECLINED.value,
                "updated_at": now,
            }
        },
    )
    lead = await db[COL_LEADS].find_one({"id": doc["lead_id"]})
    return {
        "message": "Assignment declined",
        "assignment": _public(updated),
        "lead": lead_to_response_dict(serialize_doc(lead) or {}) if lead else None,
    }
