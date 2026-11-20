"""Centralized public academy (branch) visibility rules.

An academy appears on the public website only when:
- it is a genuine academy/branch record (not partner_org_auto_branch stub),
- it is active,
- it is publicly enabled (is_public != False; missing → True for backward compat),
- if linked to a partner org, that partner is effectively active (not disabled/expired).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


AUTO_STUB_CREATED_VIA = "partner_org_auto_branch"


def is_genuine_academy(branch: Optional[dict]) -> bool:
    if not branch:
        return False
    return str(branch.get("created_via") or "").strip() != AUTO_STUB_CREATED_VIA


def genuine_academy_mongo_clause() -> Dict[str, Any]:
    """Exclude Partner Organization auto-created stub branches."""
    return {"created_via": {"$ne": AUTO_STUB_CREATED_VIA}}


def is_academy_publicly_enabled(branch: Optional[dict]) -> bool:
    """Missing is_public defaults to True so existing academies stay visible."""
    if not branch:
        return False
    if branch.get("is_public") is False:
        return False
    return True


def is_academy_publicly_visible(branch: Optional[dict], *, require_active: bool = True) -> bool:
    """Branch-level public checks only (does not load partner org status)."""
    if not is_genuine_academy(branch):
        return False
    if require_active and branch.get("is_active", True) is False:
        return False
    return is_academy_publicly_enabled(branch)


def public_academy_mongo_clause(*, active_only: bool = True) -> Dict[str, Any]:
    """Mongo filter for public academy listings (branch fields only).

    Prefer ``public_academy_mongo_clause_for_db`` so disabled/expired partners
    are also excluded.
    """
    clauses: List[Dict[str, Any]] = [
        genuine_academy_mongo_clause(),
        # Missing is_public ⇒ treat as public (backward compatible).
        {"is_public": {"$ne": False}},
    ]
    if active_only:
        clauses.append({"is_active": True})
    return {"$and": clauses}


def partner_org_public_ownership_clause(active_org_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Rock-owned academies always; partner-owned only when org is effectively active."""
    ids = [str(i).strip() for i in (active_org_ids or []) if str(i).strip()]
    return {
        "$or": [
            {"partner_org_id": {"$exists": False}},
            {"partner_org_id": None},
            {"partner_org_id": ""},
            {"partner_org_id": {"$in": ids}},
        ]
    }


async def active_partner_org_ids(db) -> List[str]:
    """Partner org ids that may show academies on the public website."""
    from utils.partner_org_access import compute_partner_effective_status

    ids: List[str] = []
    cursor = db.partner_organizations.find(
        {
            "is_active": {"$ne": False},
            "status": {"$ne": "disabled"},
        },
        {"_id": 0, "id": 1, "is_active": 1, "status": 1, "valid_until": 1},
    )
    async for org in cursor:
        if compute_partner_effective_status(org) != "active":
            continue
        oid = str(org.get("id") or "").strip()
        if oid:
            ids.append(oid)
    return ids


async def public_academy_mongo_clause_for_db(db, *, active_only: bool = True) -> Dict[str, Any]:
    """Public listing filter including partner-org effective status."""
    base = public_academy_mongo_clause(active_only=active_only)
    active_ids = await active_partner_org_ids(db)
    return {"$and": [base, partner_org_public_ownership_clause(active_ids)]}


def merge_public_academy_clause(query: Optional[dict], *, active_only: bool = True) -> Dict[str, Any]:
    pub = public_academy_mongo_clause(active_only=active_only)
    if not query:
        return pub
    return {"$and": [query, pub]}


async def merge_public_academy_clause_for_db(
    db, query: Optional[dict] = None, *, active_only: bool = True
) -> Dict[str, Any]:
    pub = await public_academy_mongo_clause_for_db(db, active_only=active_only)
    if not query:
        return pub
    return {"$and": [query, pub]}


async def is_academy_publicly_visible_db(
    db, branch: Optional[dict], *, require_active: bool = True
) -> bool:
    """Full public visibility including parent partner org status."""
    if not is_academy_publicly_visible(branch, require_active=require_active):
        return False
    org_id = str((branch or {}).get("partner_org_id") or "").strip()
    if not org_id:
        return True
    from utils.partner_org_access import compute_partner_effective_status

    org = await db.partner_organizations.find_one(
        {"id": org_id},
        {"_id": 0, "id": 1, "is_active": 1, "status": 1, "valid_until": 1},
    )
    return compute_partner_effective_status(org) == "active"
