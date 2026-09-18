"""
M21-S07 Collaboration Partner Team controller.
Partner-scoped staff directory — does not mutate coaches collection.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import HTTPException

from models.collaboration_partner_team_models import (
    CollaborationPartnerTeamMember,
    PartnerTeamMemberCreate,
    PartnerTeamMemberUpdate,
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


async def ensure_partner_team_indexes(db=None) -> None:
    db = db if db is not None else get_db()
    if db is None:
        return
    try:
        await db.collaboration_partner_team.create_index("id", unique=True)
        await db.collaboration_partner_team.create_index(
            [("branch_id", 1), ("display_order", 1)]
        )
        await db.collaboration_partner_team.create_index(
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


def _public_row(doc: dict) -> dict:
    approved = bool(doc.get("contact_approved"))
    return {
        "id": doc.get("id"),
        "name": doc.get("name"),
        "designation": doc.get("designation"),
        "role": doc.get("role"),
        "photo_url": doc.get("photo_url"),
        "bio": doc.get("bio"),
        "display_order": doc.get("display_order", 100),
        "contact_approved": approved,
        "contact_email": doc.get("contact_email") if approved else None,
        "contact_phone": doc.get("contact_phone") if approved else None,
    }


class CollaborationPartnerTeamController:
    @staticmethod
    async def list_members(
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
        await ensure_partner_team_indexes(db)
        if limit > 200:
            limit = 200

        query: Dict[str, Any] = {"branch_id": branch_id}
        if not include_inactive:
            query["is_active"] = True

        total = await db.collaboration_partner_team.count_documents(query)
        docs = (
            await db.collaboration_partner_team.find(query)
            .sort([("display_order", 1), ("name", 1)])
            .skip(skip)
            .limit(limit)
            .to_list(limit)
        )
        return {
            "branch_id": branch_id,
            "branch_snapshot": _branch_snapshot(branch),
            "members": serialize_doc(docs),
            "total": total,
            "skip": skip,
            "limit": limit,
        }

    @staticmethod
    async def get_member(
        branch_id: str,
        member_id: str,
        current_user: dict = None,
    ) -> dict:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        db = get_db()
        branch = await get_partner_branch_for_cms(db, branch_id, current_user)
        doc = await db.collaboration_partner_team.find_one(
            {"id": member_id, "branch_id": branch_id}
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Team member not found")
        out = serialize_doc(doc)
        out["branch_snapshot"] = _branch_snapshot(branch)
        return out

    @staticmethod
    async def create_member(
        branch_id: str,
        payload: PartnerTeamMemberCreate,
        current_user: dict = None,
    ) -> dict:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        db = get_db()
        branch = await get_partner_branch_for_cms(db, branch_id, current_user)
        await ensure_partner_team_indexes(db)

        now = datetime.utcnow()
        member = CollaborationPartnerTeamMember(
            id=str(uuid4()),
            branch_id=branch_id,
            name=payload.name,
            designation=payload.designation,
            role=payload.role,
            photo_url=payload.photo_url,
            contact_email=payload.contact_email,
            contact_phone=payload.contact_phone,
            contact_approved=bool(payload.contact_approved),
            bio=payload.bio,
            display_order=payload.display_order,
            is_active=bool(payload.is_active),
            created_at=now,
            updated_at=now,
        )
        doc = _dump(member)
        await db.collaboration_partner_team.insert_one(doc)
        return {
            "message": "Team member created",
            "member": serialize_doc(doc),
        }

    @staticmethod
    async def update_member(
        branch_id: str,
        member_id: str,
        payload: PartnerTeamMemberUpdate,
        current_user: dict = None,
    ) -> dict:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        db = get_db()
        branch = await get_partner_branch_for_cms(db, branch_id, current_user)

        existing = await db.collaboration_partner_team.find_one(
            {"id": member_id, "branch_id": branch_id}
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Team member not found")

        data = _dump_unset(payload)
        clear_photo = bool(data.pop("clear_photo", False))
        clear_email = bool(data.pop("clear_contact_email", False))
        clear_phone = bool(data.pop("clear_contact_phone", False))
        patch: Dict[str, Any] = {"updated_at": datetime.utcnow()}

        for key in (
            "name",
            "designation",
            "role",
            "bio",
            "display_order",
            "is_active",
            "contact_approved",
        ):
            if key in data:
                patch[key] = data[key]

        if clear_photo:
            patch["photo_url"] = None
        elif "photo_url" in data:
            patch["photo_url"] = data["photo_url"]

        if clear_email:
            patch["contact_email"] = None
        elif "contact_email" in data:
            patch["contact_email"] = data["contact_email"]

        if clear_phone:
            patch["contact_phone"] = None
        elif "contact_phone" in data:
            patch["contact_phone"] = data["contact_phone"]

        if len(patch) <= 1:
            raise HTTPException(status_code=400, detail="No update data provided")

        await db.collaboration_partner_team.update_one(
            {"id": member_id, "branch_id": branch_id},
            {"$set": patch},
        )
        doc = await db.collaboration_partner_team.find_one(
            {"id": member_id, "branch_id": branch_id}
        )
        return {
            "message": "Team member updated",
            "member": serialize_doc(doc),
        }

    @staticmethod
    async def delete_member(
        branch_id: str,
        member_id: str,
        *,
        hard: bool = False,
        current_user: dict = None,
    ) -> dict:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        db = get_db()
        branch = await get_partner_branch_for_cms(db, branch_id, current_user)

        existing = await db.collaboration_partner_team.find_one(
            {"id": member_id, "branch_id": branch_id}
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Team member not found")

        if hard:
            await db.collaboration_partner_team.delete_one(
                {"id": member_id, "branch_id": branch_id}
            )
            return {"message": "Team member deleted", "member_id": member_id}

        await db.collaboration_partner_team.update_one(
            {"id": member_id, "branch_id": branch_id},
            {"$set": {"is_active": False, "updated_at": datetime.utcnow()}},
        )
        return {
            "message": "Team member deactivated",
            "member_id": member_id,
            "is_active": False,
        }

    @staticmethod
    async def list_public(branch_id: str, *, limit: int = 48) -> dict:
        """Active team members for partner public surfaces. Contact only if approved."""
        db = get_db()
        if limit > 100:
            limit = 100
        branch = await db.branches.find_one({"id": branch_id})
        if not branch or not branch_is_collaboration_partner(branch):
            return {
                "branch_id": branch_id,
                "is_collaboration_partner": False,
                "members": [],
                "total": 0,
            }

        query = {"branch_id": branch_id, "is_active": True}
        total = await db.collaboration_partner_team.count_documents(query)
        docs = (
            await db.collaboration_partner_team.find(query)
            .sort([("display_order", 1), ("name", 1)])
            .limit(limit)
            .to_list(limit)
        )
        return {
            "branch_id": branch_id,
            "is_collaboration_partner": True,
            "members": [_public_row(d) for d in docs],
            "total": total,
        }
