"""
M15-S02 Callback request service.

Additive: separate collection lead_callbacks; also upserts a lead (source_type=callback).
Does not modify website popup or registration lead UX.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.callback_models import (
    CALLBACK_PRIORITIES,
    CALLBACK_STATUSES,
    OPEN_CALLBACK_STATUSES,
    CallbackCreate,
    CallbackPriority,
    CallbackStatus,
    CallbackStatusUpdate,
    CallbackUpdate,
)
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.indian_phone import canonical_indian_phone
from utils.student_status_service import get_managed_branch_ids_for_user

logger = logging.getLogger(__name__)

COL = "lead_callbacks"


def _role(user: Optional[dict]) -> str:
    return str((user or {}).get("role") or "").lower()


def _is_admin(user: Optional[dict]) -> bool:
    return _role(user) in {"superadmin", "super_admin", "coach_admin", "coachadmin"}


def _is_bm(user: Optional[dict]) -> bool:
    return _role(user) in {"branch_manager", "branch_admin", "branchmanager"}


def _derive_highlight(priority: str, explicit: Optional[bool] = None) -> bool:
    if explicit is not None:
        return bool(explicit)
    return priority in {CallbackPriority.HIGH.value, CallbackPriority.URGENT.value}


async def ensure_callback_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL].create_index("id", unique=True)
        await database[COL].create_index([("status", 1), ("created_at", -1)])
        await database[COL].create_index([("priority", 1), ("created_at", -1)])
        await database[COL].create_index([("highlight", 1), ("created_at", -1)])
        await database[COL].create_index("branch_id")
        await database[COL].create_index("phone_canonical")
        await database[COL].create_index("lead_id")
    except Exception:
        logger.exception("Failed ensuring callback indexes")


async def _resolve_branch(
    db, branch_id: Optional[str], branch_name: Optional[str]
) -> tuple[Optional[str], Optional[str]]:
    bid = (branch_id or "").strip() or None
    bname = (branch_name or "").strip() or None
    if bid:
        branch = await db.branches.find_one({"id": bid})
        if not branch:
            raise HTTPException(status_code=400, detail="Invalid branch")
        if not bname:
            bname = (
                (branch.get("branch") or {}).get("name")
                or branch.get("name")
                or bid
            )
    return bid, bname


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


def _public(doc: dict) -> Dict[str, Any]:
    return serialize_doc(doc) or {}


async def create_callback(body: CallbackCreate) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_callback_indexes(db)

    bid, bname = await _resolve_branch(db, body.branch_id, body.branch_name)
    phone_raw = body.phone.strip()
    phone_c = canonical_indian_phone(phone_raw) or re.sub(r"\D+", "", phone_raw) or None
    priority = body.priority.value if isinstance(body.priority, CallbackPriority) else str(body.priority)
    now = datetime.utcnow()
    cb_id = str(uuid.uuid4())

    doc = {
        "id": cb_id,
        "name": body.name.strip(),
        "phone": phone_raw,
        "phone_canonical": phone_c,
        "email": (body.email or "").strip().lower() or None,
        "branch_id": bid,
        "branch_name": bname,
        "preferred_time": body.preferred_time,
        "preferred_date": body.preferred_date,
        "message": body.message,
        "course_interest": body.course_interest,
        "priority": priority,
        "highlight": _derive_highlight(priority),
        "status": CallbackStatus.NEW.value,
        "admin_note": None,
        "lead_id": None,
        "created_at": now,
        "updated_at": now,
        "contacted_at": None,
        "completed_at": None,
    }

    # Link / create lead (best-effort)
    try:
        from utils.lead_service import upsert_from_source

        lead = await upsert_from_source(
            source_type="callback",
            source_ref_type="callback",
            source_ref_id=cb_id,
            name=doc["name"],
            phone=phone_raw,
            email=doc["email"],
            course=body.course_interest or "Callback request",
            branch_id=bid,
            branch_name=bname,
            source_label="callback",
        )
        if lead and lead.get("id"):
            doc["lead_id"] = lead["id"]
    except Exception:
        logger.exception("Lead upsert for callback failed")

    await db[COL].insert_one(doc)
    return {
        "message": "Callback request submitted. Our team will contact you soon.",
        "callback": _public(doc),
    }


async def list_callbacks(
    *,
    current_user: dict,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    highlight_only: bool = False,
    search: Optional[str] = None,
    branch_id: Optional[str] = None,
    skip: int = 0,
    limit: int = 25,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    clauses: List[Dict[str, Any]] = []
    scope = await _bm_scope(db, current_user)
    if scope:
        clauses.append(scope)

    st = (status or "").strip().lower()
    if st and st != "all":
        if st not in CALLBACK_STATUSES:
            raise HTTPException(status_code=400, detail=f"Invalid status: {st}")
        clauses.append({"status": st})

    pr = (priority or "").strip().lower()
    if pr and pr != "all":
        if pr not in CALLBACK_PRIORITIES:
            raise HTTPException(status_code=400, detail=f"Invalid priority: {pr}")
        clauses.append({"priority": pr})

    if highlight_only:
        clauses.append({"highlight": True})

    bid = (branch_id or "").strip()
    if bid and bid.lower() != "all":
        clauses.append({"branch_id": bid})

    q_search = (search or "").strip()
    if q_search:
        rx = {"$regex": re.escape(q_search), "$options": "i"}
        clauses.append(
            {
                "$or": [
                    {"name": rx},
                    {"phone": rx},
                    {"email": rx},
                    {"branch_name": rx},
                    {"message": rx},
                    {"id": rx},
                ]
            }
        )

    filter_q: Dict[str, Any] = {"$and": clauses} if clauses else {}
    limit = min(max(limit, 1), 100)
    skip = max(skip, 0)
    total = await db[COL].count_documents(filter_q)
    # Highlighted open items first, then newest
    rows = (
        await db[COL]
        .find(filter_q)
        .sort([("highlight", -1), ("priority", -1), ("created_at", -1)])
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {
        "callbacks": [_public(r) for r in rows],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


async def callback_summary(*, current_user: dict) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    match: Dict[str, Any] = {}
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
                "highlighted": {
                    "$sum": {"$cond": [{"$eq": ["$highlight", True]}, 1, 0]}
                },
                "by_status": {"$push": "$status"},
                "by_priority": {"$push": "$priority"},
            }
        }
    )
    rows = await db[COL].aggregate(pipeline).to_list(length=1)
    if not rows:
        return {
            "total": 0,
            "highlighted": 0,
            "open": 0,
            "by_status": {},
            "by_priority": {},
        }
    row = rows[0]
    by_status: Dict[str, int] = {}
    for s in row.get("by_status") or []:
        by_status[str(s)] = by_status.get(str(s), 0) + 1
    by_priority: Dict[str, int] = {}
    for p in row.get("by_priority") or []:
        by_priority[str(p)] = by_priority.get(str(p), 0) + 1
    open_n = sum(by_status.get(s, 0) for s in OPEN_CALLBACK_STATUSES)
    return {
        "total": int(row.get("total") or 0),
        "highlighted": int(row.get("highlighted") or 0),
        "open": open_n,
        "by_status": by_status,
        "by_priority": by_priority,
    }


async def get_callback(callback_id: str, *, current_user: dict) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": callback_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Callback not found")
    await _assert_can_access(db, doc, current_user)
    return {"callback": _public(doc)}


async def _assert_can_access(db, doc: dict, current_user: dict) -> None:
    if _is_admin(current_user):
        return
    if not _is_bm(current_user):
        raise HTTPException(status_code=403, detail="Not allowed")
    managed = await get_managed_branch_ids_for_user(db, current_user)
    bid = doc.get("branch_id")
    if bid and str(bid) not in {str(m) for m in managed}:
        raise HTTPException(
            status_code=403,
            detail="You can only manage callbacks for your managed branches.",
        )


async def update_callback_status(
    callback_id: str,
    body: CallbackStatusUpdate,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": callback_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Callback not found")
    await _assert_can_access(db, doc, current_user)

    new_status = body.status.value
    now = datetime.utcnow()
    patch: Dict[str, Any] = {
        "status": new_status,
        "updated_at": now,
        "updated_by": current_user.get("id"),
    }
    if body.admin_note is not None:
        note = (body.admin_note or "").strip() or None
        patch["admin_note"] = note
    if new_status == CallbackStatus.CONTACTED.value and not doc.get("contacted_at"):
        patch["contacted_at"] = now
    if new_status in {CallbackStatus.COMPLETED.value, CallbackStatus.CANCELLED.value}:
        patch["completed_at"] = now

    await db[COL].update_one({"id": callback_id}, {"$set": patch})
    updated = await db[COL].find_one({"id": callback_id})
    return {"message": "Status updated", "callback": _public(updated)}


async def update_callback(
    callback_id: str,
    body: CallbackUpdate,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": callback_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Callback not found")
    await _assert_can_access(db, doc, current_user)

    patch: Dict[str, Any] = {"updated_at": datetime.utcnow(), "updated_by": current_user.get("id")}
    data = body.dict(exclude_unset=True)
    if "priority" in data and data["priority"] is not None:
        pr = data["priority"].value if hasattr(data["priority"], "value") else str(data["priority"])
        patch["priority"] = pr
        if "highlight" not in data:
            patch["highlight"] = _derive_highlight(pr)
    if "highlight" in data and data["highlight"] is not None:
        patch["highlight"] = bool(data["highlight"])
    if "admin_note" in data:
        patch["admin_note"] = (data["admin_note"] or "").strip() or None
    if "preferred_time" in data:
        patch["preferred_time"] = (data["preferred_time"] or "").strip() or None
    if "preferred_date" in data:
        patch["preferred_date"] = (data["preferred_date"] or "").strip() or None

    await db[COL].update_one({"id": callback_id}, {"$set": patch})
    updated = await db[COL].find_one({"id": callback_id})
    return {"message": "Callback updated", "callback": _public(updated)}
