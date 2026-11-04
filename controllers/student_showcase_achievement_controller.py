from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.student_content_models import (
    StudentShowcaseAchievementCreate,
    StudentShowcaseAchievementUpdate,
    new_doc_id,
    stamp_created_by,
)
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.student_content_access import (
    is_super_admin,
    is_branch_manager,
    is_partner_super_admin,
    partner_org_id_of,
    assert_branch_manager_can_set_global,
    assert_branch_manager_branch,
    assert_can_modify_showcase,
)


COLLECTION = "student_showcase_achievements"


def _sort():
    return [("display_order", 1), ("created_at", -1)]


async def list_public(
    branch_id: Optional[str] = None,
    course_id: Optional[str] = None,
    is_global: Optional[bool] = None,
    limit: int = 10,
    skip: int = 0,
) -> List[Dict[str, Any]]:
    db = get_db()
    q: Dict[str, Any] = {"status": "active"}
    if course_id:
        q["course_id"] = course_id
    elif branch_id:
        q["$or"] = [{"branch_id": branch_id}, {"is_global": True}]
    elif is_global is True:
        q["is_global"] = True
    else:
        q["is_global"] = True
    safe_skip = max(0, skip)
    safe_limit = max(1, min(limit, 50))
    cur = db[COLLECTION].find(q).sort(_sort()).skip(safe_skip).limit(safe_limit)
    docs = await cur.to_list(length=safe_limit)
    return [serialize_doc(d) for d in docs]


async def list_for_course_with_fallback(
    course_id: str,
    fallback_branch_id: Optional[str] = None,
    limit: int = 30,
) -> List[Dict[str, Any]]:
    """Course-specific showcase first; then branch+global like branch page."""
    db = get_db()
    q_course: Dict[str, Any] = {"status": "active", "course_id": course_id}
    cur = db[COLLECTION].find(q_course).sort(_sort()).limit(limit)
    docs = await cur.to_list(length=limit)
    if docs:
        return [serialize_doc(d) for d in docs]
    if fallback_branch_id:
        q_b: Dict[str, Any] = {"status": "active", "$or": [{"branch_id": fallback_branch_id}, {"is_global": True}]}
        cur2 = db[COLLECTION].find(q_b).sort(_sort()).limit(limit)
        docs2 = await cur2.to_list(length=limit)
        return [serialize_doc(d) for d in docs2]
    q_g = {"status": "active", "is_global": True}
    cur3 = db[COLLECTION].find(q_g).sort(_sort()).limit(limit)
    docs3 = await cur3.to_list(length=limit)
    return [serialize_doc(d) for d in docs3]


async def _partner_owned_branch_ids(db, partner_org_id: str) -> List[str]:
    rows = await db.branches.find(
        {"partner_org_id": partner_org_id}, {"id": 1}
    ).to_list(500)
    return [r["id"] for r in rows if r.get("id")]


async def list_manage(current_user: dict, status: Optional[str] = None) -> List[Dict[str, Any]]:
    db = get_db()
    q: Dict[str, Any] = {}
    if status in ("active", "inactive"):
        q["status"] = status
    if is_super_admin(current_user):
        pass
    elif is_partner_super_admin(current_user):
        org_id = partner_org_id_of(current_user)
        if not org_id:
            raise HTTPException(status_code=403, detail="Partner organization missing")
        branch_ids = await _partner_owned_branch_ids(db, org_id)
        q["$or"] = [
            {"partner_org_id": org_id},
            {"branch_id": {"$in": branch_ids}},
        ]
        q["is_global"] = False
    elif is_branch_manager(current_user):
        managed = current_user.get("managed_branches") or []
        q["branch_id"] = {"$in": managed}
        q["is_global"] = False
    else:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    cur = db[COLLECTION].find(q).sort(_sort())
    docs = await cur.to_list(length=500)
    return [serialize_doc(d) for d in docs]


async def create(data: StudentShowcaseAchievementCreate, current_user: dict) -> Dict[str, Any]:
    if (
        not is_super_admin(current_user)
        and not is_branch_manager(current_user)
        and not is_partner_super_admin(current_user)
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    partner_org_id = None
    if is_partner_super_admin(current_user):
        partner_org_id = partner_org_id_of(current_user)
        if not partner_org_id:
            raise HTTPException(status_code=403, detail="Partner organization missing")
        if data.is_global:
            raise HTTPException(status_code=403, detail="Partners cannot create global showcase items")
        db = get_db()
        owned = await _partner_owned_branch_ids(db, partner_org_id)
        if data.branch_id and data.branch_id not in owned:
            raise HTTPException(status_code=403, detail="Branch is not in your organization")
    elif is_branch_manager(current_user):
        assert_branch_manager_can_set_global(data.is_global)
        assert_branch_manager_branch(data.branch_id, current_user)
    elif is_super_admin(current_user):
        if not data.is_global and not data.branch_id:
            raise HTTPException(status_code=400, detail="branch_id is required when is_global is false")
    now = datetime.utcnow()
    doc = {
        "id": new_doc_id(),
        "student_name": data.student_name,
        "student_photo": data.student_photo,
        "achievement_title": data.achievement_title,
        "description": data.description,
        "image": data.image,
        "branch_id": data.branch_id,
        "course_id": data.course_id,
        "is_global": data.is_global,
        "status": data.status,
        "display_order": data.display_order,
        "created_by": stamp_created_by(current_user),
        "created_at": now,
        "updated_at": now,
    }
    if partner_org_id:
        doc["partner_org_id"] = partner_org_id
    await get_db()[COLLECTION].insert_one(doc)
    return serialize_doc(doc)


async def update(aid: str, data: StudentShowcaseAchievementUpdate, current_user: dict) -> Dict[str, Any]:
    db = get_db()
    doc = await db[COLLECTION].find_one({"id": aid})
    # Partner may also edit showcase tied to their branches
    if is_partner_super_admin(current_user) and doc:
        org_id = partner_org_id_of(current_user)
        owned = await _partner_owned_branch_ids(db, org_id or "")
        if doc.get("partner_org_id") != org_id and doc.get("branch_id") not in owned:
            raise HTTPException(status_code=403, detail="Not allowed to modify this achievement")
    else:
        assert_can_modify_showcase(doc, current_user)
    if is_partner_super_admin(current_user):
        if data.is_global is True:
            raise HTTPException(status_code=403, detail="Cannot set global")
        org_id = partner_org_id_of(current_user)
        owned = await _partner_owned_branch_ids(db, org_id or "")
        if data.branch_id is not None and data.branch_id not in owned:
            raise HTTPException(status_code=403, detail="Invalid branch")
    if is_branch_manager(current_user):
        if data.is_global is True:
            raise HTTPException(status_code=403, detail="Cannot set global")
        if data.branch_id is not None and data.branch_id not in (current_user.get("managed_branches") or []):
            raise HTTPException(status_code=403, detail="Invalid branch")
    update_fields: Dict[str, Any] = {"updated_at": datetime.utcnow()}
    for k, v in data.dict(exclude_unset=True).items():
        update_fields[k] = v
    await db[COLLECTION].update_one({"id": aid}, {"$set": update_fields})
    updated = await db[COLLECTION].find_one({"id": aid})
    return serialize_doc(updated)


async def delete_showcase(aid: str, current_user: dict) -> Dict[str, str]:
    db = get_db()
    doc = await db[COLLECTION].find_one({"id": aid})
    if is_partner_super_admin(current_user) and doc:
        org_id = partner_org_id_of(current_user)
        owned = await _partner_owned_branch_ids(db, org_id or "")
        if doc.get("partner_org_id") != org_id and doc.get("branch_id") not in owned:
            raise HTTPException(status_code=403, detail="Not allowed to modify this achievement")
    else:
        assert_can_modify_showcase(doc, current_user)
    await db[COLLECTION].delete_one({"id": aid})
    return {"message": "Achievement deleted"}
