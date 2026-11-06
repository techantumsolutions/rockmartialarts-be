"""White-label partner tenant scoping helpers.

Rock roles see rock-owned rows (no partner_org_id).
Partner Super Admin / partner staff see their tenant rows.
Student ownership is stamped from the *creator* (partner staff), not from the branch.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException

from models.user_models import UserRole


def is_partner_super_admin(user: Optional[dict]) -> bool:
    return str((user or {}).get("role") or "").lower() == UserRole.PARTNER_SUPER_ADMIN.value


def partner_org_id_of(user: Optional[dict]) -> Optional[str]:
    """Partner Super Admin org from the JWT/session only."""
    if not is_partner_super_admin(user):
        return None
    org_id = str((user or {}).get("partner_org_id") or "").strip()
    return org_id or None


async def resolve_staff_partner_org_id(db, user: Optional[dict]) -> Optional[str]:
    """Partner org for Partner SA, or for BM/coach docs stamped with partner_org_id.

    Used when creating students so ownership follows the creator, not the branch.
    """
    if not user:
        return None
    direct = str(user.get("partner_org_id") or "").strip()
    if direct and is_partner_super_admin(user):
        return direct
    if direct and str(user.get("role") or "").lower() in {
        "branch_manager",
        "branch_admin",
        "branchmanager",
        "coach",
        "coach_admin",
    }:
        return direct

    uid = str(user.get("id") or "").strip()
    if not uid:
        return None

    role = str(user.get("role") or "").lower()
    if role in {"branch_manager", "branch_admin", "branchmanager"}:
        manager = await db.branch_managers.find_one(
            {"id": uid},
            {"partner_org_id": 1},
        )
        org = str((manager or {}).get("partner_org_id") or "").strip()
        return org or None

    if role in {"coach", "coach_admin"}:
        coach = await db.coaches.find_one(
            {"$or": [{"id": uid}, {"user_id": uid}]},
            {"partner_org_id": 1},
        )
        org = str((coach or {}).get("partner_org_id") or "").strip()
        return org or None

    if is_partner_super_admin(user):
        return direct or None
    return None


def require_partner_org_id(user: Optional[dict]) -> str:
    org_id = partner_org_id_of(user)
    if not org_id:
        raise HTTPException(
            status_code=403,
            detail="Partner organization is missing on this account",
        )
    return org_id


def apply_tenant_filter(query: Dict[str, Any], user: Optional[dict]) -> Dict[str, Any]:
    """Restrict list queries for partner users. Rock SA is unscoped."""
    org_id = partner_org_id_of(user)
    if not org_id:
        return query
    q = dict(query or {})
    q["partner_org_id"] = org_id
    return q


def stamp_tenant(doc: Dict[str, Any], user: Optional[dict]) -> Dict[str, Any]:
    """Stamp partner_org_id on create payloads for partner users."""
    org_id = partner_org_id_of(user)
    if org_id:
        doc["partner_org_id"] = org_id
    return doc


def assert_tenant_doc_access(doc: Optional[dict], user: Optional[dict], *, entity: str = "record") -> None:
    """Deny partner access to another tenant's document. Rock SA always allowed."""
    if not doc:
        return
    org_id = partner_org_id_of(user)
    if not org_id:
        return
    if doc.get("partner_org_id") != org_id:
        raise HTTPException(status_code=403, detail=f"You don't have permission to access this {entity}")


def tenant_match_clause(user: Optional[dict]) -> Optional[Dict[str, Any]]:
    """Return a Mongo clause for partner scope, or None for unscoped (Rock)."""
    org_id = partner_org_id_of(user)
    if not org_id:
        return None
    return {"partner_org_id": org_id}


def rock_owned_clause() -> Dict[str, Any]:
    """Rows that are not stamped to a partner tenant (Rock master data)."""
    return {
        "$or": [
            {"partner_org_id": {"$exists": False}},
            {"partner_org_id": None},
            {"partner_org_id": ""},
        ]
    }


def student_rock_owned_clause() -> Dict[str, Any]:
    """Students not owned by a partner tenant (visible to Rock dashboards)."""
    return rock_owned_clause()


def partner_student_visibility_clause(
    org_id: str,
    *,
    partner_branch_ids: Optional[list] = None,
    enrolled_student_ids: Optional[list] = None,
) -> Dict[str, Any]:
    """Partner dashboard students: own stamped students OR Rock students at partner branches."""
    org = str(org_id or "").strip()
    clauses: list = [{"partner_org_id": org}]
    bids = [str(b) for b in (partner_branch_ids or []) if b]
    if bids:
        clauses.append({"branch_id": {"$in": bids}})
        clauses.append({"branch.branch_id": {"$in": bids}})
    sids = [str(s) for s in (enrolled_student_ids or []) if s]
    if sids:
        clauses.append({"id": {"$in": sids}})
    return {"$or": clauses}


def merge_and_clause(query: Dict[str, Any], clause: Dict[str, Any]) -> Dict[str, Any]:
    """AND an extra clause into a Mongo query without clobbering existing keys."""
    q = dict(query or {})
    if not clause:
        return q
    if not q:
        return dict(clause)
    return {"$and": [q, clause]}


def tenant_or_rock_owned_filter(query: Dict[str, Any], user: Optional[dict]) -> Dict[str, Any]:
    """Partner → own rows only; Rock/other → rock-owned rows only (excludes partner tenants)."""
    org_id = partner_org_id_of(user)
    if org_id:
        q = dict(query or {})
        q["partner_org_id"] = org_id
        return q
    return merge_and_clause(query or {}, rock_owned_clause())


def uniqueness_scope_for_user(user: Optional[dict]) -> Dict[str, Any]:
    """Scope unique code/slug checks to the caller's tenant."""
    org_id = partner_org_id_of(user)
    if org_id:
        return {"partner_org_id": org_id}
    return rock_owned_clause()


async def partner_branch_ids(db, user: Optional[dict]):
    """Branch ids owned by the partner tenant, or None when caller is not a partner."""
    org_id = partner_org_id_of(user)
    if not org_id:
        return None
    return await db.branches.distinct("id", {"partner_org_id": org_id})


async def partner_report_branch_ids(
    db,
    user: Optional[dict],
    requested_branch_id: Optional[str] = None,
):
    """Branch ids a partner may see in reports.

    None when the caller is not a partner (reports stay unchanged).
    A list (possibly empty) for partner super admins. A requested branch
    outside the org yields an empty list so Rock rows are never returned.
    """
    if not is_partner_super_admin(user):
        return None
    owned = [str(i) for i in (await partner_branch_ids(db, user) or []) if i]
    req = str(requested_branch_id or "").strip()
    if req and req.lower() not in {"all", ""}:
        return [req] if req in owned else []
    return owned


async def resolve_partner_org_id_for_branch(db, branch_id: Optional[str]) -> Optional[str]:
    """Return partner_org_id stamped on a branch, or None for Rock-owned / unknown branches."""
    bid = str(branch_id or "").strip()
    if not bid:
        return None
    branch = await db.branches.find_one({"id": bid}, {"partner_org_id": 1})
    if not branch:
        return None
    org = str(branch.get("partner_org_id") or "").strip()
    return org or None


def apply_partner_org_stamp(doc: Dict[str, Any], partner_org_id: Optional[str]) -> Dict[str, Any]:
    """Stamp partner_org_id onto a payment/enrollment doc when known."""
    org = str(partner_org_id or "").strip()
    if org:
        doc["partner_org_id"] = org
    return doc


def partner_payments_match_clause(org_id: str, branch_ids: Optional[list] = None) -> Dict[str, Any]:
    """Match payments for one partner: stamped rows OR branch ownership (legacy rows)."""
    org = str(org_id or "").strip()
    clauses: list = [{"partner_org_id": org}]
    bids = [str(b) for b in (branch_ids or []) if b]
    if bids:
        clauses.append({"branch_details.branch_id": {"$in": bids}})
        clauses.append({"branch_id": {"$in": bids}})
    return {"$or": clauses}


async def apply_payment_list_partner_scope(
    db,
    query: Dict[str, Any],
    user: Optional[dict],
    *,
    partner_org_id: Optional[str] = None,
    partner_scope: Optional[str] = None,
) -> Dict[str, Any]:
    """Scope payment list/stats.

    - Partner SA: always restricted to their org (stamp or owned branches).
    - Rock SA: optional partner_org_id / partner_scope=rock|partners|all.
    """
    q = dict(query or {})

    # Partner SA without org id must never see Rock-wide revenue.
    if is_partner_super_admin(user) and not partner_org_id_of(user):
        return merge_and_clause(q, {"_id": {"$exists": False}})

    # Partner SA — always tenant-scoped (stamp OR legacy branch ownership).
    caller_org = partner_org_id_of(user)
    if caller_org:
        p_bids = await partner_branch_ids(db, user) or []
        return merge_and_clause(q, partner_payments_match_clause(caller_org, list(p_bids)))

    # Rock / other roles — optional partner filters for Rock SA UIs.
    scope = str(partner_scope or "").strip().lower()
    filter_org = str(partner_org_id or "").strip()

    if filter_org:
        bids = await db.branches.distinct("id", {"partner_org_id": filter_org})
        return merge_and_clause(q, partner_payments_match_clause(filter_org, list(bids or [])))

    if scope in ("rock", "rock_only", "non_partner"):
        return merge_and_clause(q, rock_owned_clause())

    if scope in ("partners", "partner", "partner_only"):
        return merge_and_clause(
            q,
            {
                "partner_org_id": {"$exists": True, "$nin": [None, ""]},
            },
        )

    return q


async def assert_partner_owns_branch(db, user: Optional[dict], branch_id: Optional[str]) -> None:
    """Partners may only attach resources to their own branches."""
    org_id = partner_org_id_of(user)
    if not org_id or not branch_id:
        return
    branch = await db.branches.find_one({"id": branch_id}, {"partner_org_id": 1})
    if not branch or branch.get("partner_org_id") != org_id:
        raise HTTPException(
            status_code=403,
            detail="You can only use branches that belong to your partner organization",
        )


async def apply_partner_branch_scope(
    db,
    query: Dict[str, Any],
    user: Optional[dict],
    *,
    branch_id: Optional[str] = None,
    field: str = "branch_id",
) -> Dict[str, Any]:
    """Scope queries by partner-owned branch ids.

    Attendance and similar collections often lack partner_org_id; use branch ownership.
    Rock SA / non-partners: unchanged. Partners: force branch ∈ owned set.
    """
    p_bids = await partner_branch_ids(db, user)
    if p_bids is None:
        return query
    q = dict(query or {})
    if branch_id:
        await assert_partner_owns_branch(db, user, branch_id)
        q[field] = branch_id
        return q
    # Empty owned set → match nothing (do not fall through to Rock data)
    q[field] = {"$in": list(p_bids or [])}
    return q


async def partner_student_ids(db, user: Optional[dict]):
    """Active student ids enrolled in partner branches, or None when not a partner."""
    p_bids = await partner_branch_ids(db, user)
    if p_bids is None:
        return None
    if not p_bids:
        return []
    return await db.enrollments.distinct(
        "student_id",
        {"branch_id": {"$in": list(p_bids)}, "status": {"$in": ["active", "Active", "enrolled"]}},
    )


async def partner_coach_ids(db, user: Optional[dict]):
    """Coach ids belonging to partner org / assigned to partner branches, or None."""
    org_id = partner_org_id_of(user)
    if not org_id:
        return None
    p_bids = await partner_branch_ids(db, user) or []
    q: Dict[str, Any] = {
        "$or": [
            {"partner_org_id": org_id},
            {"assigned_branches": {"$in": list(p_bids)}},
            {"branch_id": {"$in": list(p_bids)}},
            {"branch_ids": {"$in": list(p_bids)}},
        ]
    }
    return await db.coaches.distinct("id", q)
