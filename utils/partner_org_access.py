"""Partner org validity, effective status, and feature permission helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

PARTNER_FEATURE_DEFAULTS: Dict[str, bool] = {
    "dashboard": True,
    "branches": True,
    "coaches": True,
    "students": True,
    "courses": True,
    "attendance": True,
    "payments": True,
    "categories": True,
    "branch-managers": True,
    "org-branding": True,
    "achievements-showcase": True,
    "reports": True,
    "settings": False,  # System Settings is Rock-only; partners do not need it
    "leads": True,
    "partners": True,
}


def _parse_partner_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    raw = str(value).strip()
    if not raw:
        return None
    try:
        if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
            dt = datetime.strptime(raw, "%Y-%m-%d")
            return dt.replace(tzinfo=timezone.utc)
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def compute_partner_effective_status(org: Optional[dict], *, now: Optional[datetime] = None) -> str:
    """Return active | expired | deactivated."""
    if not org:
        return "deactivated"
    if org.get("is_active") is False or str(org.get("status") or "").lower() == "disabled":
        return "deactivated"
    until = _parse_partner_datetime(org.get("valid_until"))
    ref = now or datetime.now(timezone.utc)
    if until is not None and ref > until:
        return "expired"
    return "active"


def partner_org_blocks_access(org: Optional[dict], *, now: Optional[datetime] = None) -> bool:
    """True when partner must not log in or use dashboard APIs (disabled or expired)."""
    return compute_partner_effective_status(org, now=now) != "active"


def normalize_feature_permissions(raw: Any) -> Dict[str, bool]:
    base = dict(PARTNER_FEATURE_DEFAULTS)
    if isinstance(raw, dict):
        for k, v in raw.items():
            key = str(k).strip()
            if not key:
                continue
            base[key] = bool(v)
    return base


def is_partner_feature_enabled(org: Optional[dict], feature_key: str) -> bool:
    if partner_org_blocks_access(org):
        return False
    perms = normalize_feature_permissions((org or {}).get("feature_permissions"))
    key = str(feature_key or "").strip()
    if not key:
        return True
    return perms.get(key, True)


def normalize_course_permissions(raw: Any) -> Dict[str, bool]:
    """Partner course mutation permissions — default OFF (view-only)."""
    data = raw if isinstance(raw, dict) else {}
    return {
        "can_create_courses": bool(
            data.get("can_create_courses")
            if "can_create_courses" in data
            else data.get("can_create", False)
        ),
        "can_edit_courses": bool(
            data.get("can_edit_courses")
            if "can_edit_courses" in data
            else data.get("can_edit", False)
        ),
    }


def get_partner_course_permissions(org: Optional[dict]) -> Dict[str, bool]:
    return normalize_course_permissions((org or {}).get("course_permissions"))


def partner_org_public_extras(org: Optional[dict]) -> Dict[str, Any]:
    if not org:
        return {}
    effective = compute_partner_effective_status(org)
    # Defaults: CMS on (partners can brand), footer off (Rock chrome hidden).
    page_cms = org.get("page_cms_enabled")
    footer_on = org.get("public_footer_enabled")
    return {
        "valid_from": org.get("valid_from"),
        "valid_until": org.get("valid_until"),
        "effective_status": effective,
        "feature_permissions": normalize_feature_permissions(org.get("feature_permissions")),
        "course_permissions": get_partner_course_permissions(org),
        "page_cms_enabled": True if page_cms is None else bool(page_cms),
        "public_footer_enabled": False if footer_on is None else bool(footer_on),
    }


async def assert_partner_course_mutation(
    db,
    current_user: Optional[dict],
    *,
    action: str,
) -> None:
    """Enforce Super-Admin-granted course create/edit flags for Partner SA."""
    if not current_user:
        return
    role = str(current_user.get("role") or "").lower()
    if role != "partner_super_admin":
        return
    org_id = str(current_user.get("partner_org_id") or "").strip()
    if not org_id:
        raise HTTPException(status_code=403, detail="Partner organization is missing on this account")
    org = await db.partner_organizations.find_one({"id": org_id})
    if partner_org_blocks_access(org):
        raise HTTPException(
            status_code=403,
            detail="This partner account is disabled or expired. Please contact administrator.",
        )
    perms = get_partner_course_permissions(org)
    key = "can_create_courses" if action == "create" else "can_edit_courses"
    if not perms.get(key):
        raise HTTPException(
            status_code=403,
            detail=(
                "Course creation is not enabled for your partner organization."
                if action == "create"
                else "Course editing is not enabled for your partner organization."
            ),
        )


async def assert_partner_feature_for_user(
    db,
    current_user: Optional[dict],
    feature_key: str,
) -> None:
    """Block partner_super_admin API access when org is expired/disabled or feature is off."""
    if not current_user:
        return
    role = str(current_user.get("role") or "").lower()
    if role != "partner_super_admin":
        return
    org_id = str(current_user.get("partner_org_id") or "").strip()
    if not org_id:
        raise HTTPException(status_code=403, detail="Partner organization is missing on this account")
    org = await db.partner_organizations.find_one({"id": org_id})
    if partner_org_blocks_access(org):
        raise HTTPException(
            status_code=403,
            detail="This partner account is disabled or expired. Please contact administrator.",
        )
    if not is_partner_feature_enabled(org, feature_key):
        raise HTTPException(
            status_code=403,
            detail="This feature is not enabled for your partner organization.",
        )


def _role_name(current_user: Optional[dict]) -> str:
    return str((current_user or {}).get("role") or "").strip().lower()


def is_platform_admin_user(current_user: Optional[dict]) -> bool:
    return _role_name(current_user) in {
        "super_admin",
        "superadmin",
        "coach_admin",
    }


def is_branch_manager_role(current_user: Optional[dict]) -> bool:
    return _role_name(current_user) in {
        "branch_manager",
        "branchadmin",
        "branch_admin",
    }


async def assert_partner_branch_manager_scope(
    db,
    current_user: dict,
    branch_id: str,
) -> List[str]:
    """
    M21-S10: Branch managers may only access assigned branches (CMS + financials).
    Platform admins pass through.
    """
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if is_platform_admin_user(current_user):
        return []
    if not is_branch_manager_role(current_user):
        raise HTTPException(
            status_code=403,
            detail="Not allowed to manage collaboration partner CMS",
        )
    from utils.student_status_service import get_managed_branch_ids_for_user

    managed = [
        str(x) for x in (await get_managed_branch_ids_for_user(db, current_user) or []) if x
    ]
    if not managed:
        raise HTTPException(status_code=403, detail="No managed branches assigned")
    if str(branch_id) not in set(managed):
        raise HTTPException(
            status_code=403,
            detail="You can only manage Partner CMS for your assigned branch",
        )
    return managed


async def assert_partner_branch_cms_access(
    db,
    current_user: dict,
    branch_id: str,
) -> dict:
    """Require collaboration partner flag + BM assigned-branch scope (M21-S10)."""
    from utils.collaboration_partner import get_partner_branch_for_cms

    return await get_partner_branch_for_cms(db, branch_id, current_user)


async def assert_partner_branch_manager_financial_access(
    db,
    current_user: dict,
    branch_id: Optional[str],
) -> None:
    """Block cross-branch financial reads for partner / Rock branch managers."""
    if not current_user or not branch_id:
        return
    if not is_branch_manager_role(current_user):
        return
    await assert_partner_branch_manager_scope(db, current_user, str(branch_id))


def require_partner_feature(feature_key: str):
    """FastAPI dependency: enforce partner feature flag (no-op for non-partner roles)."""

    async def _checker(current_user: dict = None) -> dict:
        from utils.database import get_db

        # When used as Depends after require_role_unified, current_user is injected separately.
        # Call sites should prefer assert_partner_feature_for_user inside controllers.
        db = get_db()
        if db is not None and current_user is not None:
            await assert_partner_feature_for_user(db, current_user, feature_key)
        return current_user or {}

    return _checker
