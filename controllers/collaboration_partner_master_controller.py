"""
M21-S04 Collaboration Partner Masters / Experts controller.
Branch-scoped CRUD — does not touch global champions or branch master docs.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import HTTPException

from models.collaboration_partner_master_models import (
    CollaborationPartnerMaster,
    PartnerMasterCreate,
    PartnerMasterUpdate,
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


async def ensure_partner_master_indexes(db=None) -> None:
    db = db if db is not None else get_db()
    if db is None:
        return
    try:
        await db.collaboration_partner_masters.create_index("id", unique=True)
        await db.collaboration_partner_masters.create_index(
            [("branch_id", 1), ("display_order", 1)]
        )
        await db.collaboration_partner_masters.create_index(
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


class CollaborationPartnerMasterController:
    @staticmethod
    async def list_masters(
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
        await ensure_partner_master_indexes(db)
        if limit > 200:
            limit = 200

        query: Dict[str, Any] = {"branch_id": branch_id}
        if not include_inactive:
            query["is_active"] = True

        total = await db.collaboration_partner_masters.count_documents(query)
        cursor = (
            db.collaboration_partner_masters.find(query)
            .sort([("display_order", 1), ("name", 1)])
            .skip(skip)
            .limit(limit)
        )
        docs = await cursor.to_list(limit)
        return {
            "branch_id": branch_id,
            "branch_snapshot": _branch_snapshot(branch),
            "masters": serialize_doc(docs),
            "total": total,
            "skip": skip,
            "limit": limit,
        }

    @staticmethod
    async def get_master(
        branch_id: str,
        master_id: str,
        current_user: dict = None,
    ) -> dict:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        db = get_db()
        branch = await get_partner_branch_for_cms(db, branch_id, current_user)
        doc = await db.collaboration_partner_masters.find_one(
            {"id": master_id, "branch_id": branch_id}
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Master/expert not found")
        out = serialize_doc(doc)
        out["branch_snapshot"] = _branch_snapshot(branch)
        return out

    @staticmethod
    async def create_master(
        branch_id: str,
        payload: PartnerMasterCreate,
        current_user: dict = None,
    ) -> dict:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        db = get_db()
        branch = await get_partner_branch_for_cms(db, branch_id, current_user)
        await ensure_partner_master_indexes(db)

        now = datetime.utcnow()
        master = CollaborationPartnerMaster(
            id=str(uuid4()),
            branch_id=branch_id,
            name=payload.name,
            designation=payload.designation,
            photo_url=payload.photo_url,
            biography=payload.biography,
            experience_years=payload.experience_years,
            experience_summary=payload.experience_summary,
            specializations=list(payload.specializations or []),
            achievements=list(payload.achievements or []),
            display_order=payload.display_order,
            is_active=bool(payload.is_active),
            created_at=now,
            updated_at=now,
        )
        doc = _dump(master)
        await db.collaboration_partner_masters.insert_one(doc)
        return {
            "message": "Master/expert created",
            "master": serialize_doc(doc),
        }

    @staticmethod
    async def update_master(
        branch_id: str,
        master_id: str,
        payload: PartnerMasterUpdate,
        current_user: dict = None,
    ) -> dict:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        db = get_db()
        branch = await get_partner_branch_for_cms(db, branch_id, current_user)

        existing = await db.collaboration_partner_masters.find_one(
            {"id": master_id, "branch_id": branch_id}
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Master/expert not found")

        data = _dump_unset(payload)
        clear_photo = bool(data.pop("clear_photo", False))
        patch: Dict[str, Any] = {"updated_at": datetime.utcnow()}

        for key in (
            "name",
            "designation",
            "biography",
            "experience_years",
            "experience_summary",
            "display_order",
            "is_active",
        ):
            if key in data:
                patch[key] = data[key]

        if "specializations" in data:
            patch["specializations"] = list(data["specializations"] or [])
        if "achievements" in data:
            patch["achievements"] = list(data["achievements"] or [])

        if clear_photo:
            patch["photo_url"] = None
        elif "photo_url" in data:
            patch["photo_url"] = data["photo_url"]

        if len(patch) <= 1:
            raise HTTPException(status_code=400, detail="No update data provided")

        await db.collaboration_partner_masters.update_one(
            {"id": master_id, "branch_id": branch_id},
            {"$set": patch},
        )
        doc = await db.collaboration_partner_masters.find_one(
            {"id": master_id, "branch_id": branch_id}
        )
        return {
            "message": "Master/expert updated",
            "master": serialize_doc(doc),
        }

    @staticmethod
    async def delete_master(
        branch_id: str,
        master_id: str,
        *,
        hard: bool = False,
        current_user: dict = None,
    ) -> dict:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        db = get_db()
        branch = await get_partner_branch_for_cms(db, branch_id, current_user)

        existing = await db.collaboration_partner_masters.find_one(
            {"id": master_id, "branch_id": branch_id}
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Master/expert not found")

        if hard:
            await db.collaboration_partner_masters.delete_one(
                {"id": master_id, "branch_id": branch_id}
            )
            return {"message": "Master/expert deleted", "master_id": master_id}

        await db.collaboration_partner_masters.update_one(
            {"id": master_id, "branch_id": branch_id},
            {"$set": {"is_active": False, "updated_at": datetime.utcnow()}},
        )
        return {
            "message": "Master/expert deactivated",
            "master_id": master_id,
            "is_active": False,
        }

    @staticmethod
    async def masters_count(branch_id: str, *, active_only: bool = True) -> int:
        db = get_db()
        query: Dict[str, Any] = {"branch_id": branch_id}
        if active_only:
            query["is_active"] = True
        return await db.collaboration_partner_masters.count_documents(query)
