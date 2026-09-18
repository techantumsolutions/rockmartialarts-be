"""
M21-S01 / M21-S10 Collaboration Partner branch flag + CMS access helpers.

Canonical storage field: allows_collaboration (default False).
API/UI alias: is_collaboration_partner (synced).
Partner CMS features (S02+) must call require_collaboration_partner /
get_partner_branch_for_cms (S10: BM assigned-branch restriction).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from fastapi import HTTPException


def branch_is_collaboration_partner(branch: Optional[dict]) -> bool:
    if not branch:
        return False
    if "is_collaboration_partner" in branch and branch.get("is_collaboration_partner") is not None:
        return bool(branch.get("is_collaboration_partner"))
    return bool(branch.get("allows_collaboration", False))


def resolve_collaboration_flag_from_payload(
    data: Dict[str, Any],
    *,
    default: bool = False,
    existing: Optional[dict] = None,
) -> bool:
    """
    Prefer explicit is_collaboration_partner, then allows_collaboration,
    then existing doc, then default (False for new branches).
    """
    if "is_collaboration_partner" in data and data.get("is_collaboration_partner") is not None:
        return bool(data.get("is_collaboration_partner"))
    if "allows_collaboration" in data and data.get("allows_collaboration") is not None:
        return bool(data.get("allows_collaboration"))
    if existing is not None:
        return branch_is_collaboration_partner(existing)
    return bool(default)


def apply_collaboration_flag(payload: Dict[str, Any], flag: bool) -> Dict[str, Any]:
    """Write both storage + alias keys for consistent reads."""
    payload["allows_collaboration"] = bool(flag)
    payload["is_collaboration_partner"] = bool(flag)
    return payload


def enrich_collaboration_flag(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    flag = branch_is_collaboration_partner(doc)
    doc["allows_collaboration"] = flag
    doc["is_collaboration_partner"] = flag
    return doc


def require_collaboration_partner(branch: Optional[dict]) -> dict:
    """Gate partner-only functionality (M21 S02+). Normal branches stay unaffected."""
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")
    if not branch_is_collaboration_partner(branch):
        raise HTTPException(
            status_code=403,
            detail="Collaboration partner features are disabled for this branch",
        )
    return branch


async def get_branch_or_404(db, branch_id: str) -> dict:
    branch = await db.branches.find_one({"id": branch_id})
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")
    return branch


def _role_name(current_user: Optional[dict]) -> str:
    return str((current_user or {}).get("role") or "").strip().lower()


def is_platform_admin(current_user: Optional[dict]) -> bool:
    """Super Admin / Coach Admin — full partner CMS across partners."""
    return _role_name(current_user) in {
        "super_admin",
        "superadmin",
        "coach_admin",
    }


def is_branch_manager_user(current_user: Optional[dict]) -> bool:
    return _role_name(current_user) in {
        "branch_manager",
        "branchadmin",
        "branch_admin",
    }


async def get_managed_branch_ids(db, current_user: dict) -> List[str]:
    """Reuse existing BM branch resolution (DB manager_id + JWT fallbacks)."""
    from utils.student_status_service import get_managed_branch_ids_for_user

    ids = await get_managed_branch_ids_for_user(db, current_user)
    return [str(x) for x in (ids or []) if x]


async def assert_branch_manager_assigned(
    db,
    current_user: dict,
    branch_id: str,
) -> List[str]:
    """
    M21-S10-T02: Branch managers may only access their assigned branches.
    Platform admins pass through. Other roles are rejected for partner CMS.
    """
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if is_platform_admin(current_user):
        return []
    if not is_branch_manager_user(current_user):
        raise HTTPException(
            status_code=403,
            detail="Not allowed to manage collaboration partner CMS",
        )
    managed = await get_managed_branch_ids(db, current_user)
    if not managed:
        raise HTTPException(
            status_code=403,
            detail="No managed branches assigned",
        )
    if str(branch_id) not in set(managed):
        raise HTTPException(
            status_code=403,
            detail="You can only manage Partner CMS for your assigned branch",
        )
    return managed


async def get_partner_branch_for_cms(
    db,
    branch_id: str,
    current_user: Optional[dict],
) -> dict:
    """
    Load branch, require collaboration-partner flag, and enforce BM scope (S10).
    Use this for all authenticated Partner CMS reads/writes.
    """
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    branch = await get_branch_or_404(db, branch_id)
    require_collaboration_partner(branch)
    await assert_branch_manager_assigned(db, current_user, branch_id)
    return branch


async def assert_can_view_partner_status(
    db,
    current_user: Optional[dict],
    branch_id: str,
) -> dict:
    """Status endpoint: BM only for assigned branch; SA/CA unrestricted."""
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    branch = await get_branch_or_404(db, branch_id)
    await assert_branch_manager_assigned(db, current_user, branch_id)
    return branch


def assert_can_toggle_collaboration_flag(current_user: Optional[dict]) -> None:
    """
    Partner flag is a platform decision (S01/S10) — Branch Managers cannot toggle it.
    """
    if is_platform_admin(current_user):
        return
    if is_branch_manager_user(current_user):
        raise HTTPException(
            status_code=403,
            detail="Only Super Admin can change the Collaboration Partner flag",
        )


async def partner_cms_permissions(db, current_user: dict) -> Dict[str, Any]:
    """
    M21-S10: Explicit permission summary for Admin UI + QA.
    Reuses Branch Manager role; does not invent a new role.
    """
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    role = _role_name(current_user)
    platform = is_platform_admin(current_user)
    is_bm = is_branch_manager_user(current_user)
    managed: List[str] = []
    if is_bm:
        managed = await get_managed_branch_ids(db, current_user)

    allowed_partner_ids: List[str] = []
    if platform:
        # All partner branches
        cursor = db.branches.find(
            {
                "$or": [
                    {"allows_collaboration": True},
                    {"is_collaboration_partner": True},
                ]
            },
            {"id": 1},
        )
        async for row in cursor:
            if row.get("id"):
                allowed_partner_ids.append(str(row["id"]))
    elif is_bm and managed:
        cursor = db.branches.find(
            {
                "id": {"$in": managed},
                "$or": [
                    {"allows_collaboration": True},
                    {"is_collaboration_partner": True},
                ],
            },
            {"id": 1},
        )
        async for row in cursor:
            if row.get("id"):
                allowed_partner_ids.append(str(row["id"]))

    return {
        "role": role,
        "reuses_branch_manager_role": True,
        "is_platform_admin": platform,
        "is_branch_manager": is_bm,
        "managed_branch_ids": managed,
        "partner_cms_branch_ids": allowed_partner_ids,
        "can_use_partner_cms": platform or (is_bm and len(allowed_partner_ids) > 0),
        "can_toggle_collaboration_flag": platform,
        "can_access_platform_settings": platform,
        "can_manage_roles": platform,
        "can_view_other_branch_financials": platform,
        "notes": (
            "Branch Managers may manage Partner CMS only for assigned collaboration "
            "partner branches. Platform settings, role management, and other-branch "
            "financial data remain restricted."
        ),
    }


async def filter_partner_branch_query_for_user(
    db,
    current_user: dict,
    base_query: Dict[str, Any],
) -> Dict[str, Any]:
    """Scope partner-branch listings for Branch Managers to assigned branches."""
    if is_platform_admin(current_user):
        return base_query
    if not is_branch_manager_user(current_user):
        raise HTTPException(
            status_code=403,
            detail="Not allowed to list collaboration partners",
        )
    managed = await get_managed_branch_ids(db, current_user)
    managed_set: Set[str] = set(managed)
    if not managed_set:
        return {"id": {"$in": []}}
    return {
        "$and": [
            base_query,
            {"id": {"$in": list(managed_set)}},
        ]
    }
