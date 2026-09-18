"""
M21-S06 Collaboration Partner Testimonials controller.
Partner-scoped CMS — does not mutate student_testimonials or CMS homepage docs.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import HTTPException

from models.collaboration_partner_testimonial_models import (
    CollaborationPartnerTestimonial,
    PartnerTestimonialAuthorRole,
    PartnerTestimonialCreate,
    PartnerTestimonialReorderRequest,
    PartnerTestimonialStatus,
    PartnerTestimonialUpdate,
)
from utils.collaboration_partner import (
    branch_is_collaboration_partner,
    get_partner_branch_for_cms,
)
from utils.database import get_db
from utils.helpers import serialize_doc


def _dump(model) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_unset=False, mode="python")
    return model.dict()


def _dump_unset(model) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_unset=True, mode="python")
    return model.dict(exclude_unset=True)


def _enum_val(v, default: str) -> str:
    if v is None:
        return default
    if hasattr(v, "value"):
        return v.value
    return str(v)


async def ensure_partner_testimonial_indexes(db=None) -> None:
    db = db if db is not None else get_db()
    if db is None:
        return
    try:
        await db.collaboration_partner_testimonials.create_index("id", unique=True)
        await db.collaboration_partner_testimonials.create_index(
            [("branch_id", 1), ("display_order", 1)]
        )
        await db.collaboration_partner_testimonials.create_index(
            [("branch_id", 1), ("status", 1), ("is_active", 1)]
        )
    except Exception:
        pass


def _branch_snapshot(branch: dict) -> dict:
    bi = branch.get("branch") or {}
    return {
        "id": branch.get("id"),
        "name": bi.get("name"),
        "code": bi.get("code"),
    }


def _public_row(doc: dict) -> dict:
    """Shape compatible with branch landing testimonial cards."""
    role = doc.get("person_role") or "student"
    related = doc.get("related_student_name")
    role_label = role.replace("_", " ").title()
    if role == PartnerTestimonialAuthorRole.PARENT.value and related:
        role_label = f"Parent of {related}"
    elif role == PartnerTestimonialAuthorRole.GUARDIAN.value and related:
        role_label = f"Guardian of {related}"
    return {
        "id": doc.get("id"),
        "student_name": doc.get("person_name"),
        "name": doc.get("person_name"),
        "role": role_label,
        "person_role": role,
        "related_student_name": related,
        "testimonial_text": doc.get("testimonial_text"),
        "content": doc.get("testimonial_text"),
        "quote": doc.get("testimonial_text"),
        "image": doc.get("photo_url"),
        "student_photo": doc.get("photo_url"),
        "photo_url": doc.get("photo_url"),
        "rating": doc.get("rating"),
        "display_order": doc.get("display_order", 100),
    }


class CollaborationPartnerTestimonialController:
    @staticmethod
    async def list_items(
        branch_id: str,
        *,
        include_inactive: bool = False,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
        current_user: dict = None,
    ) -> dict:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        db = get_db()
        branch = await get_partner_branch_for_cms(db, branch_id, current_user)
        await ensure_partner_testimonial_indexes(db)
        if limit > 200:
            limit = 200

        query: Dict[str, Any] = {"branch_id": branch_id}
        if not include_inactive:
            query["is_active"] = True
        if status:
            query["status"] = status.strip().lower()

        total = await db.collaboration_partner_testimonials.count_documents(query)
        docs = (
            await db.collaboration_partner_testimonials.find(query)
            .sort([("display_order", 1), ("created_at", -1)])
            .skip(skip)
            .limit(limit)
            .to_list(limit)
        )
        return {
            "branch_id": branch_id,
            "branch_snapshot": _branch_snapshot(branch),
            "testimonials": serialize_doc(docs),
            "total": total,
            "skip": skip,
            "limit": limit,
        }

    @staticmethod
    async def get_item(
        branch_id: str,
        item_id: str,
        current_user: dict = None,
    ) -> dict:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        db = get_db()
        branch = await get_partner_branch_for_cms(db, branch_id, current_user)
        doc = await db.collaboration_partner_testimonials.find_one(
            {"id": item_id, "branch_id": branch_id}
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Testimonial not found")
        out = serialize_doc(doc)
        out["branch_snapshot"] = _branch_snapshot(branch)
        return out

    @staticmethod
    async def create_item(
        branch_id: str,
        payload: PartnerTestimonialCreate,
        current_user: dict = None,
    ) -> dict:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        db = get_db()
        branch = await get_partner_branch_for_cms(db, branch_id, current_user)
        await ensure_partner_testimonial_indexes(db)

        now = datetime.utcnow()
        item = CollaborationPartnerTestimonial(
            id=str(uuid4()),
            branch_id=branch_id,
            person_name=payload.person_name,
            person_role=_enum_val(
                payload.person_role, PartnerTestimonialAuthorRole.STUDENT.value
            ),
            related_student_name=payload.related_student_name,
            photo_url=payload.photo_url,
            testimonial_text=payload.testimonial_text,
            rating=payload.rating,
            status=payload.resolved_status(),
            display_order=payload.display_order,
            is_active=bool(payload.is_active),
            created_at=now,
            updated_at=now,
        )
        doc = _dump(item)
        await db.collaboration_partner_testimonials.insert_one(doc)
        return {
            "message": "Partner testimonial created",
            "testimonial": serialize_doc(doc),
        }

    @staticmethod
    async def update_item(
        branch_id: str,
        item_id: str,
        payload: PartnerTestimonialUpdate,
        current_user: dict = None,
    ) -> dict:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        db = get_db()
        branch = await get_partner_branch_for_cms(db, branch_id, current_user)

        existing = await db.collaboration_partner_testimonials.find_one(
            {"id": item_id, "branch_id": branch_id}
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Testimonial not found")

        data = _dump_unset(payload)
        clear_photo = bool(data.pop("clear_photo", False))
        clear_rating = bool(data.pop("clear_rating", False))
        publish = data.pop("publish", None)
        patch: Dict[str, Any] = {"updated_at": datetime.utcnow()}

        for key in (
            "person_name",
            "related_student_name",
            "testimonial_text",
            "display_order",
            "is_active",
        ):
            if key in data:
                patch[key] = data[key]

        if "person_role" in data and data["person_role"] is not None:
            patch["person_role"] = _enum_val(
                data["person_role"], PartnerTestimonialAuthorRole.STUDENT.value
            )

        if clear_photo:
            patch["photo_url"] = None
        elif "photo_url" in data:
            patch["photo_url"] = data["photo_url"]

        if clear_rating:
            patch["rating"] = None
        elif "rating" in data:
            patch["rating"] = data["rating"]

        if publish is True:
            patch["status"] = PartnerTestimonialStatus.PUBLISHED.value
        elif publish is False:
            patch["status"] = PartnerTestimonialStatus.UNPUBLISHED.value
        elif "status" in data and data["status"] is not None:
            patch["status"] = _enum_val(
                data["status"], PartnerTestimonialStatus.DRAFT.value
            )

        if len(patch) <= 1:
            raise HTTPException(status_code=400, detail="No update data provided")

        await db.collaboration_partner_testimonials.update_one(
            {"id": item_id, "branch_id": branch_id},
            {"$set": patch},
        )
        doc = await db.collaboration_partner_testimonials.find_one(
            {"id": item_id, "branch_id": branch_id}
        )
        return {
            "message": "Partner testimonial updated",
            "testimonial": serialize_doc(doc),
        }

    @staticmethod
    async def delete_item(
        branch_id: str,
        item_id: str,
        *,
        hard: bool = False,
        current_user: dict = None,
    ) -> dict:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        db = get_db()
        branch = await get_partner_branch_for_cms(db, branch_id, current_user)

        existing = await db.collaboration_partner_testimonials.find_one(
            {"id": item_id, "branch_id": branch_id}
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Testimonial not found")

        if hard:
            await db.collaboration_partner_testimonials.delete_one(
                {"id": item_id, "branch_id": branch_id}
            )
            return {"message": "Partner testimonial deleted", "testimonial_id": item_id}

        await db.collaboration_partner_testimonials.update_one(
            {"id": item_id, "branch_id": branch_id},
            {
                "$set": {
                    "is_active": False,
                    "status": PartnerTestimonialStatus.UNPUBLISHED.value,
                    "updated_at": datetime.utcnow(),
                }
            },
        )
        return {
            "message": "Partner testimonial deactivated",
            "testimonial_id": item_id,
            "is_active": False,
        }

    @staticmethod
    async def reorder_items(
        branch_id: str,
        payload: PartnerTestimonialReorderRequest,
        current_user: dict = None,
    ) -> dict:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        db = get_db()
        branch = await get_partner_branch_for_cms(db, branch_id, current_user)

        items = payload.items or []
        if not items:
            raise HTTPException(status_code=400, detail="No reorder items provided")

        now = datetime.utcnow()
        updated = 0
        for row in items:
            result = await db.collaboration_partner_testimonials.update_one(
                {"id": row.id, "branch_id": branch_id},
                {"$set": {"display_order": row.display_order, "updated_at": now}},
            )
            if result.matched_count:
                updated += 1

        docs = (
            await db.collaboration_partner_testimonials.find(
                {"branch_id": branch_id, "is_active": True}
            )
            .sort([("display_order", 1)])
            .to_list(200)
        )
        return {
            "message": "Partner testimonials order updated",
            "updated": updated,
            "testimonials": serialize_doc(docs),
        }

    @staticmethod
    async def list_public(branch_id: str, *, limit: int = 24) -> dict:
        """
        Published partner testimonials for public landing / branch page.
        Only when branch is a collaboration partner; otherwise empty list (not 403).
        """
        db = get_db()
        if limit > 50:
            limit = 50
        branch = await db.branches.find_one({"id": branch_id})
        if not branch or not branch_is_collaboration_partner(branch):
            return {
                "branch_id": branch_id,
                "is_collaboration_partner": False,
                "testimonials": [],
                "total": 0,
            }

        query = {
            "branch_id": branch_id,
            "is_active": True,
            "status": PartnerTestimonialStatus.PUBLISHED.value,
        }
        total = await db.collaboration_partner_testimonials.count_documents(query)
        docs = (
            await db.collaboration_partner_testimonials.find(query)
            .sort([("display_order", 1), ("created_at", -1)])
            .limit(limit)
            .to_list(limit)
        )
        return {
            "branch_id": branch_id,
            "is_collaboration_partner": True,
            "testimonials": [_public_row(d) for d in docs],
            "total": total,
        }
