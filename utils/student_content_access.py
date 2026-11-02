from fastapi import HTTPException
from typing import List, Optional, Any, Dict


def managed_branch_ids(current_user: dict) -> List[str]:
    return list(current_user.get("managed_branches") or [])


def is_super_admin(current_user: dict) -> bool:
    return current_user.get("role") == "super_admin"


def is_branch_manager(current_user: dict) -> bool:
    return current_user.get("role") == "branch_manager"


def is_partner_super_admin(current_user: dict) -> bool:
    return str(current_user.get("role") or "").lower() == "partner_super_admin"


def partner_org_id_of(current_user: dict) -> Optional[str]:
    if not is_partner_super_admin(current_user):
        return None
    org_id = str(current_user.get("partner_org_id") or "").strip()
    return org_id or None


def assert_branch_manager_can_set_global(is_global: bool):
    if is_global:
        raise HTTPException(status_code=403, detail="Branch managers cannot create or mark global content")


def assert_branch_manager_branch(branch_id: Optional[str], current_user: dict):
    if not branch_id:
        raise HTTPException(status_code=400, detail="branch_id is required for branch manager")
    managed = managed_branch_ids(current_user)
    if branch_id not in managed:
        raise HTTPException(status_code=403, detail="You can only manage content for your branches")


def can_branch_manager_modify_doc(doc: Dict[str, Any], current_user: dict) -> bool:
    if doc.get("is_global"):
        return False
    bid = doc.get("branch_id")
    if not bid:
        return False
    return bid in managed_branch_ids(current_user)


def assert_can_modify_testimonial(doc: Optional[Dict[str, Any]], current_user: dict):
    if not doc:
        raise HTTPException(status_code=404, detail="Testimonial not found")
    if is_super_admin(current_user):
        return
    if is_branch_manager(current_user):
        if not can_branch_manager_modify_doc(doc, current_user):
            raise HTTPException(status_code=403, detail="Not allowed to modify this testimonial")
        return
    raise HTTPException(status_code=403, detail="Insufficient permissions")


def assert_can_modify_showcase(doc: Optional[Dict[str, Any]], current_user: dict):
    if not doc:
        raise HTTPException(status_code=404, detail="Achievement not found")
    if is_super_admin(current_user):
        return
    if is_partner_super_admin(current_user):
        org_id = partner_org_id_of(current_user)
        if not org_id:
            raise HTTPException(status_code=403, detail="Partner organization missing")
        if doc.get("partner_org_id") == org_id:
            return
        # Allow modify when showcase is tied to a branch owned by this partner
        raise HTTPException(status_code=403, detail="Not allowed to modify this achievement")
    if is_branch_manager(current_user):
        if not can_branch_manager_modify_doc(doc, current_user):
            raise HTTPException(status_code=403, detail="Not allowed to modify this achievement")
        return
    raise HTTPException(status_code=403, detail="Insufficient permissions")
