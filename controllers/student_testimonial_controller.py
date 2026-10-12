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
from utils.student_content_access import (
    is_super_admin,
    is_branch_manager,
    assert_branch_manager_can_set_global,
    assert_branch_manager_branch,
    assert_can_modify_testimonial,
)


COLLECTION = "student_testimonials"


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
    cur = db[COLLECTION].find(q).sort(_sort_course()).limit(max(1, min(limit, 50)))
    docs = await cur.to_list(length=50)
    return [_normalize_testimonial_row(serialize_doc(d)) for d in docs]


async def list_manage(current_user: dict, status: Optional[str] = None) -> List[Dict[str, Any]]:
    db = get_db()
    q: Dict[str, Any] = {}
    if status in ("active", "inactive"):
        q["status"] = status
    if is_super_admin(current_user):
        pass
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
    if not is_super_admin(current_user) and not is_branch_manager(current_user):
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
    await get_db()[COLLECTION].insert_one(doc)
    return _normalize_testimonial_row(serialize_doc(doc))


async def update(tid: str, data: StudentTestimonialUpdate, current_user: dict) -> Dict[str, Any]:
    db = get_db()
    doc = await db[COLLECTION].find_one({"id": tid})
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
    await db[COLLECTION].update_one({"id": tid}, {"$set": update_fields})
    updated = await db[COLLECTION].find_one({"id": tid})
    return _normalize_testimonial_row(serialize_doc(updated))


async def delete_testimonial(tid: str, current_user: dict) -> Dict[str, str]:
    db = get_db()
    doc = await db[COLLECTION].find_one({"id": tid})
    assert_can_modify_testimonial(doc, current_user)
    if is_branch_manager(current_user):
        from utils.student_status_service import get_managed_branch_ids_for_user

        managed = await get_managed_branch_ids_for_user(db, current_user)
        if str((doc or {}).get("branch_id") or "") not in managed:
            raise HTTPException(status_code=403, detail="Not allowed to modify this testimonial")
    await db[COLLECTION].delete_one({"id": tid})
    return {"message": "Testimonial deleted"}
