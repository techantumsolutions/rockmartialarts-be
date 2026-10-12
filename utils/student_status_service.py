"""
M08-S01 student account status helpers.

Updates users.is_active only (not enrollments.is_active).
Records structured status history for audit.
"""
from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from datetime import datetime
from typing import Any, Dict, List, Optional

from utils.database import get_db
from utils.helpers import serialize_doc

logger = logging.getLogger(__name__)

_BM_ROLES = {"branch_manager", "branch_admin", "branchmanager"}
_request_active_branch_id: ContextVar[Optional[str]] = ContextVar(
    "request_active_branch_id", default=None
)


def set_request_active_branch_id(branch_id: Optional[str]):
    return _request_active_branch_id.set((branch_id or "").strip() or None)


def reset_request_active_branch_id(token) -> None:
    _request_active_branch_id.reset(token)


def _is_branch_manager(current_user: Optional[dict]) -> bool:
    return str((current_user or {}).get("role") or "").lower() in _BM_ROLES


async def ensure_student_status_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database.student_status_history.create_index("id", unique=True)
        await database.student_status_history.create_index(
            [("student_id", 1), ("created_at", -1)]
        )
        await database.student_status_history.create_index("actor_id")
    except Exception:
        logger.exception("Failed ensuring student_status_history indexes")


async def get_assigned_branch_ids_for_user(db, current_user: dict) -> List[str]:
    """Every live branch this manager is assigned to (branches.manager_id).

    Used by the header switch. Data queries use get_managed_branch_ids_for_user,
    which narrows this list to the selected branch.
    """
    manager_id = (current_user or {}).get("id")
    ids: List[str] = []
    if manager_id and db is not None:
        rows = await db.branches.find(
            {"manager_id": manager_id, "is_active": True}, {"id": 1}
        ).to_list(length=None)
        ids = [str(r["id"]) for r in rows if r.get("id")]
    if not ids:
        raw = (current_user or {}).get("managed_branches") or (current_user or {}).get("branch_ids") or []
        if isinstance(raw, str):
            raw = [raw]
        ids = [str(x) for x in raw if x]
    if not ids:
        ba = (current_user or {}).get("branch_assignment") or {}
        bid = ba.get("branch_id") or (current_user or {}).get("branch_id")
        if bid:
            ids = [str(bid)]
    return ids


async def get_managed_branch_ids_for_user(db, current_user: dict) -> List[str]:
    """Branch ids a data query may use.

    Branch managers get exactly one id: the X-Active-Branch-Id request header
    when it is one of their assigned branches, otherwise the first assigned id.
    Other roles get the full assigned list (unchanged).
    """
    ids = await get_assigned_branch_ids_for_user(db, current_user)
    if not _is_branch_manager(current_user) or not ids:
        return ids
    requested = _request_active_branch_id.get()
    if requested and requested in ids:
        return [requested]
    return [sorted(ids)[0]]


async def assert_can_manage_student_status(
    db,
    student: dict,
    current_user: dict,
) -> None:
    """Raise HTTPException-compatible dict via caller — returns None if allowed."""
    from fastapi import HTTPException
    from models.user_models import UserRole

    role = str(current_user.get("role") or "").lower()
    if role == UserRole.SUPER_ADMIN.value:
        return
    if role == UserRole.COACH_ADMIN.value:
        return
    if role != UserRole.BRANCH_MANAGER.value:
        raise HTTPException(status_code=403, detail="Not allowed to update student status")

    if str(student.get("role") or "").lower() != "student":
        raise HTTPException(status_code=400, detail="Status updates apply to students only")

    managed = await get_managed_branch_ids_for_user(db, current_user)
    if not managed:
        raise HTTPException(status_code=403, detail="No managed branches assigned")

    student_id = student.get("id")
    # Direct branch on user
    if student.get("branch_id") and str(student.get("branch_id")) in managed:
        return
    # Any enrollment (active or not) in managed branches — BM can deactivate even if expired
    enr = await db.enrollments.find_one(
        {"student_id": student_id, "branch_id": {"$in": managed}},
        {"id": 1},
    )
    if enr:
        return
    raise HTTPException(
        status_code=403,
        detail="You can only update status for students in your managed branches.",
    )


async def append_status_history(
    *,
    student_id: str,
    previous_status: bool,
    new_status: bool,
    actor_id: Optional[str],
    actor_name: Optional[str],
    actor_role: Optional[str],
    reason: Optional[str] = None,
    source: str = "status_api",
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise RuntimeError("Database connection not available")
    await ensure_student_status_indexes(db)
    now = datetime.utcnow()
    entry = {
        "id": str(uuid.uuid4()),
        "student_id": student_id,
        "previous_status": bool(previous_status),
        "previous_label": "active" if previous_status else "inactive",
        "new_status": bool(new_status),
        "new_label": "active" if new_status else "inactive",
        "actor_id": actor_id,
        "actor_name": actor_name,
        "actor_role": actor_role,
        "reason": (reason or "").strip() or None,
        "source": source,
        "created_at": now,
    }
    await db.student_status_history.insert_one(dict(entry))
    # Soft denormalize on user for quick UI
    try:
        await db.users.update_one(
            {"id": student_id},
            {
                "$set": {
                    "last_status_change_at": now,
                    "last_status_change_reason": entry["reason"],
                    "last_status_changed_by": actor_id,
                }
            },
        )
    except Exception:
        logger.exception("Failed denormalizing status change on user %s", student_id)
    entry.pop("_id", None)
    return serialize_doc(entry)


async def update_student_account_status(
    *,
    student_id: str,
    is_active: bool,
    reason: Optional[str],
    current_user: dict,
    source: str = "status_api",
) -> Dict[str, Any]:
    from fastapi import HTTPException

    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    student = await db.users.find_one({"id": student_id})
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    if str(student.get("role") or "").lower() != "student":
        raise HTTPException(status_code=400, detail="Target user is not a student")

    await assert_can_manage_student_status(db, student, current_user)

    previous = bool(student.get("is_active", True))
    new_val = bool(is_active)
    if previous == new_val:
        return {
            "message": "Status unchanged",
            "student_id": student_id,
            "is_active": previous,
            "changed": False,
        }

    if new_val is False and not (reason or "").strip():
        raise HTTPException(
            status_code=400,
            detail="A reason is required when deactivating a student.",
        )

    now = datetime.utcnow()
    result = await db.users.update_one(
        {"id": student_id},
        {"$set": {"is_active": new_val, "updated_at": now}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Student not found")

    history = await append_status_history(
        student_id=student_id,
        previous_status=previous,
        new_status=new_val,
        actor_id=current_user.get("id"),
        actor_name=current_user.get("full_name") or current_user.get("email"),
        actor_role=str(current_user.get("role") or ""),
        reason=reason,
        source=source,
    )
    return {
        "message": f"Student marked as {'active' if new_val else 'inactive'}",
        "student_id": student_id,
        "is_active": new_val,
        "previous_status": previous,
        "changed": True,
        "history": history,
    }


async def list_student_status_history(
    student_id: str, *, limit: int = 50
) -> List[Dict[str, Any]]:
    db = get_db()
    if db is None:
        return []
    await ensure_student_status_indexes(db)
    cursor = (
        db.student_status_history.find({"student_id": student_id})
        .sort("created_at", -1)
        .limit(max(1, min(int(limit or 50), 200)))
    )
    rows = await cursor.to_list(length=limit)
    out = []
    for r in rows:
        r.pop("_id", None)
        out.append(serialize_doc(r))
    return out
