"""
M21-S05 Collaboration Partner Gallery controller.
Branch-scoped CRUD — does not write to branches.gallery_images.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import HTTPException

from models.collaboration_partner_gallery_models import (
    CollaborationPartnerGalleryItem,
    GalleryMediaType,
    PartnerGalleryItemCreate,
    PartnerGalleryItemUpdate,
    PartnerGalleryReorderRequest,
)
from utils.collaboration_partner import (
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


def _media_type_value(v) -> str:
    if isinstance(v, GalleryMediaType):
        return v.value
    return str(v or GalleryMediaType.IMAGE.value)


async def ensure_partner_gallery_indexes(db=None) -> None:
    db = db if db is not None else get_db()
    if db is None:
        return
    try:
        await db.collaboration_partner_gallery.create_index("id", unique=True)
        await db.collaboration_partner_gallery.create_index(
            [("branch_id", 1), ("display_order", 1)]
        )
        await db.collaboration_partner_gallery.create_index(
            [("branch_id", 1), ("is_active", 1)]
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


class CollaborationPartnerGalleryController:
    @staticmethod
    async def list_items(
        branch_id: str,
        *,
        include_inactive: bool = False,
        skip: int = 0,
        limit: int = 100,
        current_user: dict = None,
    ) -> dict:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        db = get_db()
        branch = await get_partner_branch_for_cms(db, branch_id, current_user)
        await ensure_partner_gallery_indexes(db)
        if limit > 200:
            limit = 200

        query: Dict[str, Any] = {"branch_id": branch_id}
        if not include_inactive:
            query["is_active"] = True

        total = await db.collaboration_partner_gallery.count_documents(query)
        docs = (
            await db.collaboration_partner_gallery.find(query)
            .sort([("display_order", 1), ("created_at", 1)])
            .skip(skip)
            .limit(limit)
            .to_list(limit)
        )
        return {
            "branch_id": branch_id,
            "branch_snapshot": _branch_snapshot(branch),
            "items": serialize_doc(docs),
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
        doc = await db.collaboration_partner_gallery.find_one(
            {"id": item_id, "branch_id": branch_id}
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Gallery item not found")
        out = serialize_doc(doc)
        out["branch_snapshot"] = _branch_snapshot(branch)
        return out

    @staticmethod
    async def create_item(
        branch_id: str,
        payload: PartnerGalleryItemCreate,
        current_user: dict = None,
    ) -> dict:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        db = get_db()
        branch = await get_partner_branch_for_cms(db, branch_id, current_user)
        await ensure_partner_gallery_indexes(db)

        now = datetime.utcnow()
        item = CollaborationPartnerGalleryItem(
            id=str(uuid4()),
            branch_id=branch_id,
            media_type=_media_type_value(payload.media_type),
            media_url=payload.media_url,
            thumbnail_url=payload.thumbnail_url,
            video_url=payload.video_url,
            caption=payload.caption,
            display_order=payload.display_order,
            is_active=bool(payload.is_active),
            created_at=now,
            updated_at=now,
        )
        doc = _dump(item)
        await db.collaboration_partner_gallery.insert_one(doc)
        return {"message": "Gallery item created", "item": serialize_doc(doc)}

    @staticmethod
    async def update_item(
        branch_id: str,
        item_id: str,
        payload: PartnerGalleryItemUpdate,
        current_user: dict = None,
    ) -> dict:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        db = get_db()
        branch = await get_partner_branch_for_cms(db, branch_id, current_user)

        existing = await db.collaboration_partner_gallery.find_one(
            {"id": item_id, "branch_id": branch_id}
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Gallery item not found")

        data = _dump_unset(payload)
        clear_media = bool(data.pop("clear_media", False))
        clear_thumbnail = bool(data.pop("clear_thumbnail", False))
        clear_video = bool(data.pop("clear_video_url", False))
        patch: Dict[str, Any] = {"updated_at": datetime.utcnow()}

        if "media_type" in data and data["media_type"] is not None:
            patch["media_type"] = _media_type_value(data["media_type"])
        if "caption" in data:
            patch["caption"] = data["caption"]
        if "display_order" in data:
            patch["display_order"] = data["display_order"]
        if "is_active" in data:
            patch["is_active"] = data["is_active"]

        if clear_media:
            patch["media_url"] = None
        elif "media_url" in data:
            patch["media_url"] = data["media_url"]

        if clear_thumbnail:
            patch["thumbnail_url"] = None
        elif "thumbnail_url" in data:
            patch["thumbnail_url"] = data["thumbnail_url"]

        if clear_video:
            patch["video_url"] = None
        elif "video_url" in data:
            patch["video_url"] = data["video_url"]

        if len(patch) <= 1:
            raise HTTPException(status_code=400, detail="No update data provided")

        # Validate resulting media after patch
        merged = dict(existing)
        merged.update(patch)
        mt = merged.get("media_type") or GalleryMediaType.IMAGE.value
        if mt == GalleryMediaType.IMAGE.value and not merged.get("media_url"):
            raise HTTPException(
                status_code=400, detail="media_url required for image gallery items"
            )
        if mt == GalleryMediaType.VIDEO.value and not (
            merged.get("media_url") or merged.get("video_url")
        ):
            raise HTTPException(
                status_code=400,
                detail="media_url or video_url required for video gallery items",
            )

        await db.collaboration_partner_gallery.update_one(
            {"id": item_id, "branch_id": branch_id},
            {"$set": patch},
        )
        doc = await db.collaboration_partner_gallery.find_one(
            {"id": item_id, "branch_id": branch_id}
        )
        return {"message": "Gallery item updated", "item": serialize_doc(doc)}

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

        existing = await db.collaboration_partner_gallery.find_one(
            {"id": item_id, "branch_id": branch_id}
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Gallery item not found")

        if hard:
            await db.collaboration_partner_gallery.delete_one(
                {"id": item_id, "branch_id": branch_id}
            )
            return {"message": "Gallery item deleted", "item_id": item_id}

        await db.collaboration_partner_gallery.update_one(
            {"id": item_id, "branch_id": branch_id},
            {"$set": {"is_active": False, "updated_at": datetime.utcnow()}},
        )
        return {
            "message": "Gallery item deactivated",
            "item_id": item_id,
            "is_active": False,
        }

    @staticmethod
    async def reorder_items(
        branch_id: str,
        payload: PartnerGalleryReorderRequest,
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
            result = await db.collaboration_partner_gallery.update_one(
                {"id": row.id, "branch_id": branch_id},
                {"$set": {"display_order": row.display_order, "updated_at": now}},
            )
            if result.matched_count:
                updated += 1

        docs = (
            await db.collaboration_partner_gallery.find(
                {"branch_id": branch_id, "is_active": True}
            )
            .sort([("display_order", 1), ("created_at", 1)])
            .to_list(200)
        )
        return {
            "message": "Gallery order updated",
            "updated": updated,
            "items": serialize_doc(docs),
        }
