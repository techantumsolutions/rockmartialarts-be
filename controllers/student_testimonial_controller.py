from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.student_content_models import (
    StudentTestimonialCreate,
    StudentTestimonialUpdate,
    new_doc_id,
    stamp_created_by,
)
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.partner_tenant import (
    is_partner_super_admin,
    merge_and_clause,
    partner_branch_ids,
    partner_org_id_of,
)
from utils.student_content_access import (
    is_super_admin,
    is_branch_manager,
    assert_branch_manager_can_set_global,
    assert_can_modify_testimonial,
)


COLLECTION = "student_testimonials"


def _creator_user_id(doc: dict) -> str:
    created_by = doc.get("created_by")
    if isinstance(created_by, dict):
        return str(created_by.get("user_id") or "")
    return str(created_by or "")


async def _partner_creator_ids(db, org_id: str) -> List[str]:
    ids = await db.users.distinct("id", {"partner_org_id": org_id})
    return [str(i) for i in (ids or []) if i]


async def _partner_testimonial_scope(db, user: dict) -> Dict[str, Any]:
    """Testimonials for one partner: stamp, their branches, or created by their staff."""
    org_id = partner_org_id_of(user)
    if not org_id:
        return {"_id": {"$exists": False}}
    clauses: List[Dict[str, Any]] = [{"partner_org_id": org_id}]
    bids = [str(i) for i in (await partner_branch_ids(db, user) or []) if i]
    if bids:
        clauses.append({"branch_id": {"$in": bids}, "is_global": {"$ne": True}})
    creators = await _partner_creator_ids(db, org_id)
    uid = str((user or {}).get("id") or "")
    if uid and uid not in creators:
        creators.append(uid)
    if creators:
        clauses.append({"created_by.user_id": {"$in": creators}})
    return {"$or": clauses}


async def _rock_testimonial_scope(db) -> Dict[str, Any]:
    """Rock testimonials: exclude partner-stamped, partner-branch, and partner-created rows."""
    partner_bids = [
        str(i)
        for i in await db.branches.distinct(
            "id",
            {"partner_org_id": {"$exists": True, "$nin": [None, ""]}},
        )
        if i
    ]
    partner_users = [
        str(i)
        for i in await db.users.distinct(
            "id",
            {"partner_org_id": {"$exists": True, "$nin": [None, ""]}},
        )
        if i
    ]
    nor: List[Dict[str, Any]] = [
        {"partner_org_id": {"$exists": True, "$nin": [None, ""]}}
    ]
    if partner_bids:
        nor.append({"branch_id": {"$in": partner_bids}})
    if partner_users:
        nor.append({"created_by.user_id": {"$in": partner_users}})
    return {"$nor": nor}


async def _testimonial_belongs_to_partner(db, doc: Optional[dict], user: dict) -> bool:
    org_id = partner_org_id_of(user)
    if not doc or not org_id:
        return False
    if str(doc.get("partner_org_id") or "") == org_id:
        return True
    bids = {str(i) for i in (await partner_branch_ids(db, user) or []) if i}
    if str(doc.get("branch_id") or "") in bids and not doc.get("is_global"):
        return True
    creators = set(await _partner_creator_ids(db, org_id))
    uid = str(user.get("id") or "")
    if uid:
        creators.add(uid)
    return _creator_user_id(doc) in creators


async def _testimonial_is_partner_owned(db, doc: Optional[dict]) -> bool:
    if not doc:
        return False
    if str(doc.get("partner_org_id") or "").strip():
        return True
    bid = str(doc.get("branch_id") or "").strip()
    if bid and not doc.get("is_global"):
        branch = await db.branches.find_one({"id": bid}, {"partner_org_id": 1})
        if branch and str(branch.get("partner_org_id") or "").strip():
            return True
    created_by = _creator_user_id(doc)
    if created_by:
        creator = await db.users.find_one(
            {"id": created_by},
            {"partner_org_id": 1, "role": 1},
        )
        if creator and (
            str(creator.get("partner_org_id") or "").strip()
            or str(creator.get("role") or "") == "partner_super_admin"
        ):
            return True
    return False


async def _owned_partner_branch_id(db, user: dict, branch_id: Optional[str]) -> Optional[str]:
    """Keep a partner-owned branch id. Drop a foreign id so a stale header branch cannot block create."""
    bid = str(branch_id or "").strip()
    if not bid:
        return None
    owned = {str(i) for i in (await partner_branch_ids(db, user) or []) if i}
    return bid if bid in owned else None


def _normalize_testimonial_row(doc: dict) -> dict:
    """Public/API shape: name, content, image alongside legacy keys."""
    if not doc:
        return doc
    row = dict(doc)
    row["name"] = row.get("student_name")
    row["content"] = row.get("testimonial_text")
    photo = row.get("image") or row.get("student_photo")
    row["image"] = photo
    row["student_photo"] = row.get("student_photo") or photo
    return row


def _sort_course():
    return [("display_order", 1), ("created_at", -1)]


async def list_public(
    branch_id: Optional[str] = None,
    is_global: Optional[bool] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    db = get_db()
    q: Dict[str, Any] = {"status": "active"}
    # Branch pages: only testimonials for that branch (no global/inherit merge).
    if branch_id:
        q["branch_id"] = branch_id
    elif is_global is True:
        q["is_global"] = True
    else:
        q["is_global"] = True
        q = merge_and_clause(q, await _rock_testimonial_scope(db))
    cur = db[COLLECTION].find(q).sort(_sort_course()).limit(max(1, min(limit, 50)))
    docs = await cur.to_list(length=50)
    return [_normalize_testimonial_row(serialize_doc(d)) for d in docs]


async def list_manage(current_user: dict, status: Optional[str] = None) -> List[Dict[str, Any]]:
    db = get_db()
    q: Dict[str, Any] = {}
    if status in ("active", "inactive"):
        q["status"] = status
    if is_super_admin(current_user):
        q = merge_and_clause(q, await _rock_testimonial_scope(db))
    elif is_partner_super_admin(current_user):
        if not partner_org_id_of(current_user):
            raise HTTPException(status_code=403, detail="Partner organization missing")
        q = merge_and_clause(q, await _partner_testimonial_scope(db, current_user))
    elif is_branch_manager(current_user):
        from utils.student_status_service import get_managed_branch_ids_for_user

        managed = await get_managed_branch_ids_for_user(db, current_user)
        q["branch_id"] = {"$in": managed or ["__none__"]}
        q["is_global"] = False
    else:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    cur = db[COLLECTION].find(q).sort(_sort_course())
    docs = await cur.to_list(length=500)
    return [_normalize_testimonial_row(serialize_doc(d)) for d in docs]


async def create(data: StudentTestimonialCreate, current_user: dict) -> Dict[str, Any]:
    partner_org_id = None
    if is_partner_super_admin(current_user):
        partner_org_id = partner_org_id_of(current_user)
        if not partner_org_id:
            raise HTTPException(status_code=403, detail="Partner organization missing")
        if data.is_global:
            raise HTTPException(
                status_code=403,
                detail="Partners cannot create global testimonials",
            )
        data.is_global = False
        data.branch_id = await _owned_partner_branch_id(
            get_db(), current_user, data.branch_id
        )
    elif not is_super_admin(current_user) and not is_branch_manager(current_user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    if is_branch_manager(current_user):
        from utils.student_status_service import get_managed_branch_ids_for_user

        assert_branch_manager_can_set_global(data.is_global)
        managed = await get_managed_branch_ids_for_user(get_db(), current_user)
        if len(managed) == 1:
            data.branch_id = managed[0]
        if not data.branch_id or data.branch_id not in managed:
            raise HTTPException(status_code=403, detail="You can only manage content for your selected branch")
    elif is_super_admin(current_user):
        if not data.is_global and not data.branch_id:
            raise HTTPException(status_code=400, detail="branch_id is required when is_global is false")
    now = datetime.utcnow()
    photo = data.student_photo or data.image
    doc = {
        "id": new_doc_id(),
        "student_name": data.student_name,
        "student_photo": photo,
        "image": photo,
        "testimonial_text": data.testimonial_text,
        "rating": data.rating,
        "branch_id": data.branch_id,
        "is_global": data.is_global,
        "status": data.status,
        "display_order": data.display_order,
        "created_by": stamp_created_by(current_user),
        "created_at": now,
        "updated_at": now,
    }
    if partner_org_id:
        doc["partner_org_id"] = partner_org_id
        doc["is_global"] = False
    await get_db()[COLLECTION].insert_one(doc)
    return _normalize_testimonial_row(serialize_doc(doc))


async def update(tid: str, data: StudentTestimonialUpdate, current_user: dict) -> Dict[str, Any]:
    db = get_db()
    doc = await db[COLLECTION].find_one({"id": tid})
    if is_partner_super_admin(current_user):
        if not await _testimonial_belongs_to_partner(db, doc, current_user):
            raise HTTPException(status_code=404, detail="Testimonial not found")
        if data.is_global is True:
            raise HTTPException(status_code=403, detail="Partners cannot create global testimonials")
        if data.branch_id is not None:
            data.branch_id = await _owned_partner_branch_id(db, current_user, data.branch_id)
    elif is_super_admin(current_user):
        if await _testimonial_is_partner_owned(db, doc):
            raise HTTPException(status_code=404, detail="Testimonial not found")
        assert_can_modify_testimonial(doc, current_user)
    else:
        assert_can_modify_testimonial(doc, current_user)
    if is_branch_manager(current_user):
        from utils.student_status_service import get_managed_branch_ids_for_user

        if data.is_global is True:
            raise HTTPException(status_code=403, detail="Cannot set global")
        managed = await get_managed_branch_ids_for_user(db, current_user)
        if str((doc or {}).get("branch_id") or "") not in managed:
            raise HTTPException(status_code=403, detail="Not allowed to modify this testimonial")
        if data.branch_id is not None and data.branch_id not in managed:
            raise HTTPException(status_code=403, detail="Invalid branch")
    update_fields: Dict[str, Any] = {"updated_at": datetime.utcnow()}
    payload = data.dict(exclude_unset=True)
    if "image" in payload or "student_photo" in payload:
        merged_photo = payload.get("student_photo")
        if payload.get("image") is not None:
            merged_photo = payload.get("image")
        if merged_photo is not None:
            payload["student_photo"] = merged_photo
            payload["image"] = merged_photo
    for k, v in payload.items():
        update_fields[k] = v
    org_id = partner_org_id_of(current_user)
    if org_id:
        update_fields["partner_org_id"] = org_id
        update_fields["is_global"] = False
    await db[COLLECTION].update_one({"id": tid}, {"$set": update_fields})
    updated = await db[COLLECTION].find_one({"id": tid})
    return _normalize_testimonial_row(serialize_doc(updated))


async def delete_testimonial(tid: str, current_user: dict) -> Dict[str, str]:
    db = get_db()
    doc = await db[COLLECTION].find_one({"id": tid})
    if is_partner_super_admin(current_user):
        if not await _testimonial_belongs_to_partner(db, doc, current_user):
            raise HTTPException(status_code=404, detail="Testimonial not found")
    elif is_super_admin(current_user):
        if await _testimonial_is_partner_owned(db, doc):
            raise HTTPException(status_code=404, detail="Testimonial not found")
        assert_can_modify_testimonial(doc, current_user)
    else:
        assert_can_modify_testimonial(doc, current_user)
    if is_branch_manager(current_user):
        from utils.student_status_service import get_managed_branch_ids_for_user

        managed = await get_managed_branch_ids_for_user(db, current_user)
        if str((doc or {}).get("branch_id") or "") not in managed:
            raise HTTPException(status_code=403, detail="Not allowed to modify this testimonial")
    await db[COLLECTION].delete_one({"id": tid})
    return {"message": "Testimonial deleted"}
