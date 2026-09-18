"""
M14-S02 Coach approval & status service.

Additive: does not replace admin coach CRUD or existing is_active toggles.
Uses coaches collection + coach_approval_history for audit trail.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.coach_models import (
    CoachActiveStatusBody,
    CoachApprovalActionBody,
    CoachApprovalStatus,
    CoachRejectBody,
)
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.student_status_service import get_managed_branch_ids_for_user

logger = logging.getLogger(__name__)

COL = "coaches"
COL_HISTORY = "coach_approval_history"

# Allowed approval_status transitions
ALLOWED_APPROVAL_TRANSITIONS = {
    CoachApprovalStatus.PENDING.value: {
        CoachApprovalStatus.APPROVED.value,
        CoachApprovalStatus.REJECTED.value,
    },
    CoachApprovalStatus.REJECTED.value: {
        CoachApprovalStatus.APPROVED.value,
        CoachApprovalStatus.PENDING.value,
    },
    CoachApprovalStatus.APPROVED.value: {
        CoachApprovalStatus.REJECTED.value,
    },
}


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


def _effective_approval(coach: dict) -> str:
    raw = coach.get("approval_status")
    if raw is None or str(raw).strip() == "":
        return CoachApprovalStatus.APPROVED.value
    return str(raw).lower()


async def ensure_coach_approval_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL].create_index(
            [("approval_status", 1), ("created_at", -1)]
        )
        await database[COL].create_index(
            [("is_active", 1), ("approval_status", 1)]
        )
        await database[COL_HISTORY].create_index("id", unique=True)
        await database[COL_HISTORY].create_index(
            [("coach_id", 1), ("created_at", -1)]
        )
    except Exception:
        logger.exception("Failed ensuring coach approval indexes")


async def append_approval_history(
    db,
    *,
    coach_id: str,
    from_status: Optional[str],
    to_status: str,
    action: str,
    actor: dict,
    note: Optional[str] = None,
    is_active_before: Optional[bool] = None,
    is_active_after: Optional[bool] = None,
) -> Dict[str, Any]:
    entry = {
        "id": str(uuid.uuid4()),
        "coach_id": coach_id,
        "from_status": from_status,
        "to_status": to_status,
        "action": action,
        "note": (note or "").strip() or None,
        "is_active_before": is_active_before,
        "is_active_after": is_active_after,
        "actor_id": actor.get("id"),
        "actor_name": actor.get("name"),
        "actor_role": actor.get("role"),
        "created_at": datetime.utcnow(),
    }
    await db[COL_HISTORY].insert_one(entry)
    return entry


async def record_registration_submitted(db, coach_id: str, actor: Optional[dict] = None) -> None:
    """Called from self-registration to seed history (best-effort)."""
    try:
        await append_approval_history(
            db,
            coach_id=coach_id,
            from_status=None,
            to_status=CoachApprovalStatus.PENDING.value,
            action="registration_submitted",
            actor=_actor(actor),
            note="Self-registration submitted",
            is_active_before=False,
            is_active_after=False,
        )
    except Exception:
        logger.exception("Failed to record registration history for %s", coach_id)


def _public_coach(doc: dict) -> Dict[str, Any]:
    safe = serialize_doc(doc) or {}
    safe.pop("password_hash", None)
    if "contact_info" in safe and isinstance(safe["contact_info"], dict):
        safe["contact_info"].pop("password", None)
    safe["approval_status"] = _effective_approval(doc)
    return safe


async def _assert_can_manage_coach(db, coach: dict, current_user: dict) -> None:
    role = _role(current_user)
    if role in {"superadmin", "super_admin", "coach_admin", "coachadmin"}:
        return
    if role not in {"branch_manager", "branch_admin", "branchmanager"}:
        raise HTTPException(status_code=403, detail="Not allowed to manage coach approvals")

    managed = await get_managed_branch_ids_for_user(db, current_user)
    if not managed:
        raise HTTPException(status_code=403, detail="No managed branches assigned")

    branch_id = coach.get("branch_id")
    locations = coach.get("service_location_ids") or []
    if isinstance(locations, str):
        locations = [locations]
    location_set = {str(x) for x in locations if x}
    if branch_id:
        location_set.add(str(branch_id))

    if not location_set.intersection({str(m) for m in managed}):
        raise HTTPException(
            status_code=403,
            detail="You can only manage coaches in your managed branches.",
        )


async def _bm_scope_filter(db, current_user: dict) -> Optional[Dict[str, Any]]:
    role = _role(current_user)
    if role in {"superadmin", "super_admin", "coach_admin", "coachadmin"}:
        return None
    managed = await get_managed_branch_ids_for_user(db, current_user)
    if not managed:
        return {"id": {"$in": []}}
    return {
        "$or": [
            {"branch_id": {"$in": managed}},
            {"service_location_ids": {"$in": managed}},
        ]
    }


async def list_approval_coaches(
    *,
    current_user: dict,
    approval_status: Optional[str] = None,
    is_active: Optional[bool] = None,
    registration_source: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 25,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    clauses: List[Dict[str, Any]] = []
    scope = await _bm_scope_filter(db, current_user)
    if scope:
        clauses.append(scope)

    if approval_status and approval_status.lower() != "all":
        status = approval_status.lower().strip()
        if status == CoachApprovalStatus.APPROVED.value:
            # Legacy coaches without field count as approved
            clauses.append(
                {
                    "$or": [
                        {"approval_status": "approved"},
                        {"approval_status": {"$exists": False}},
                        {"approval_status": None},
                        {"approval_status": ""},
                    ]
                }
            )
        else:
            clauses.append({"approval_status": status})

    if is_active is not None:
        clauses.append({"is_active": bool(is_active)})

    if registration_source and registration_source.lower() != "all":
        clauses.append({"registration_source": registration_source.lower().strip()})

    q = (search or "").strip()
    if q:
        rx = {"$regex": re.escape(q), "$options": "i"}
        clauses.append(
            {
                "$or": [
                    {"full_name": rx},
                    {"email": rx},
                    {"contact_info.email": rx},
                    {"contact_info.phone": rx},
                    {"phone": rx},
                    {"id": rx},
                ]
            }
        )

    filter_query: Dict[str, Any] = {"$and": clauses} if clauses else {}
    total = await db[COL].count_documents(filter_query)
    rows = (
        await db[COL]
        .find(filter_query)
        .sort([("created_at", -1)])
        .skip(max(0, skip))
        .limit(max(1, min(limit, 100)))
        .to_list(length=max(1, min(limit, 100)))
    )
    return {
        "coaches": [_public_coach(r) for r in rows],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


async def approval_summary(*, current_user: dict) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    match: Dict[str, Any] = {}
    scope = await _bm_scope_filter(db, current_user)
    if scope:
        match = scope

    pipeline: List[Dict[str, Any]] = []
    if match:
        pipeline.append({"$match": match})
    pipeline.extend(
        [
            {
                "$addFields": {
                    "_approval": {
                        "$let": {
                            "vars": {
                                "raw": {"$ifNull": ["$approval_status", ""]},
                            },
                            "in": {
                                "$cond": [
                                    {"$eq": ["$$raw", ""]},
                                    "approved",
                                    "$$raw",
                                ]
                            },
                        }
                    }
                }
            },
            {
                "$group": {
                    "_id": "$_approval",
                    "count": {"$sum": 1},
                    "active": {
                        "$sum": {"$cond": [{"$eq": ["$is_active", True]}, 1, 0]}
                    },
                    "inactive": {
                        "$sum": {"$cond": [{"$ne": ["$is_active", True]}, 1, 0]}
                    },
                }
            },
        ]
    )
    rows = await db[COL].aggregate(pipeline).to_list(length=20)
    by_status: Dict[str, int] = {}
    active_total = 0
    inactive_total = 0
    for r in rows:
        key = str(r.get("_id") or "approved").lower()
        by_status[key] = int(r.get("count") or 0)
        active_total += int(r.get("active") or 0)
        inactive_total += int(r.get("inactive") or 0)

    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
        "pending": by_status.get("pending", 0),
        "approved": by_status.get("approved", 0),
        "rejected": by_status.get("rejected", 0),
        "active": active_total,
        "inactive": inactive_total,
    }


async def get_coach_for_approval(coach_id: str, *, current_user: dict) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    coach = await db[COL].find_one({"id": coach_id})
    if not coach:
        raise HTTPException(status_code=404, detail="Coach not found")
    await _assert_can_manage_coach(db, coach, current_user)
    history = (
        await db[COL_HISTORY]
        .find({"coach_id": coach_id})
        .sort([("created_at", -1)])
        .to_list(length=100)
    )
    return {
        "coach": _public_coach(coach),
        "approval_history": serialize_doc(history),
    }


async def approve_coach(
    coach_id: str,
    body: Optional[CoachApprovalActionBody],
    *,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    coach = await db[COL].find_one({"id": coach_id})
    if not coach:
        raise HTTPException(status_code=404, detail="Coach not found")
    await _assert_can_manage_coach(db, coach, current_user)

    current = _effective_approval(coach)
    target = CoachApprovalStatus.APPROVED.value
    if current == target and coach.get("is_active") is True:
        return {
            "message": "Coach already approved and active",
            "coach": _public_coach(coach),
        }

    allowed = ALLOWED_APPROVAL_TRANSITIONS.get(current) or set()
    if current != target and target not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot approve coach from status '{current}'",
        )

    now = datetime.utcnow()
    was_active = bool(coach.get("is_active", False))
    await db[COL].update_one(
        {"id": coach_id},
        {
            "$set": {
                "approval_status": target,
                "is_active": True,
                "updated_at": now,
                "approved_at": now,
                "approved_by": current_user.get("id"),
            }
        },
    )
    note = (body.note if body else None) or None
    await append_approval_history(
        db,
        coach_id=coach_id,
        from_status=current,
        to_status=target,
        action="approve",
        actor=_actor(current_user),
        note=note,
        is_active_before=was_active,
        is_active_after=True,
    )
    updated = await db[COL].find_one({"id": coach_id})
    return {"message": "Coach approved successfully", "coach": _public_coach(updated)}


async def reject_coach(
    coach_id: str,
    body: Optional[CoachRejectBody],
    *,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    coach = await db[COL].find_one({"id": coach_id})
    if not coach:
        raise HTTPException(status_code=404, detail="Coach not found")
    await _assert_can_manage_coach(db, coach, current_user)

    current = _effective_approval(coach)
    target = CoachApprovalStatus.REJECTED.value
    if current == target:
        return {
            "message": "Coach already rejected",
            "coach": _public_coach(coach),
        }

    allowed = ALLOWED_APPROVAL_TRANSITIONS.get(current) or set()
    if target not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reject coach from status '{current}'",
        )

    now = datetime.utcnow()
    was_active = bool(coach.get("is_active", False))
    await db[COL].update_one(
        {"id": coach_id},
        {
            "$set": {
                "approval_status": target,
                "is_active": False,
                "updated_at": now,
                "rejected_at": now,
                "rejected_by": current_user.get("id"),
            }
        },
    )
    note = (body.note if body else None) or None
    await append_approval_history(
        db,
        coach_id=coach_id,
        from_status=current,
        to_status=target,
        action="reject",
        actor=_actor(current_user),
        note=note,
        is_active_before=was_active,
        is_active_after=False,
    )
    updated = await db[COL].find_one({"id": coach_id})
    return {"message": "Coach registration rejected", "coach": _public_coach(updated)}


async def set_coach_active_status(
    coach_id: str,
    body: CoachActiveStatusBody,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    coach = await db[COL].find_one({"id": coach_id})
    if not coach:
        raise HTTPException(status_code=404, detail="Coach not found")
    await _assert_can_manage_coach(db, coach, current_user)

    approval = _effective_approval(coach)
    if approval != CoachApprovalStatus.APPROVED.value:
        raise HTTPException(
            status_code=400,
            detail="Only approved coaches can be activated or deactivated.",
        )

    was_active = bool(coach.get("is_active", False))
    new_active = bool(body.is_active)
    if was_active == new_active:
        return {
            "message": "Active status unchanged",
            "coach": _public_coach(coach),
        }

    now = datetime.utcnow()
    await db[COL].update_one(
        {"id": coach_id},
        {"$set": {"is_active": new_active, "updated_at": now}},
    )
    await append_approval_history(
        db,
        coach_id=coach_id,
        from_status=approval,
        to_status=approval,
        action="activate" if new_active else "deactivate",
        actor=_actor(current_user),
        note=body.note,
        is_active_before=was_active,
        is_active_after=new_active,
    )
    updated = await db[COL].find_one({"id": coach_id})
    return {
        "message": "Coach activated" if new_active else "Coach deactivated",
        "coach": _public_coach(updated),
    }


async def get_approval_history(
    coach_id: str, *, current_user: dict
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    coach = await db[COL].find_one({"id": coach_id})
    if not coach:
        raise HTTPException(status_code=404, detail="Coach not found")
    await _assert_can_manage_coach(db, coach, current_user)
    history = (
        await db[COL_HISTORY]
        .find({"coach_id": coach_id})
        .sort([("created_at", -1)])
        .to_list(length=200)
    )
    return {
        "coach_id": coach_id,
        "approval_history": serialize_doc(history),
    }
