"""
M21-S08 Collaboration Partner SEO controller.
Partner-scoped only — never writes SEO onto generic branch documents.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import HTTPException

from models.collaboration_partner_seo_models import (
    CollaborationPartnerSeo,
    PartnerSeoUpsert,
    empty_seo_response,
)
from utils.collaboration_partner import (
    get_partner_branch_for_cms,
    enrich_collaboration_flag,
    branch_is_collaboration_partner,
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


def _clean_str(value: Any, *, max_len: int) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:max_len]


async def ensure_seo_indexes(db=None) -> None:
    db = db if db is not None else get_db()
    if db is None:
        return
    try:
        await db.collaboration_partner_seo.create_index("branch_id", unique=True)
        await db.collaboration_partner_seo.create_index("id", unique=True)
    except Exception:
        pass


def _enrich_seo_doc(doc: dict, branch: Optional[dict] = None) -> dict:
    out = serialize_doc(doc) if doc else {}
    out["has_seo"] = True
    if branch:
        enrich_collaboration_flag(branch)
        bi = branch.get("branch") or {}
        out["branch_snapshot"] = {
            "id": branch.get("id"),
            "name": bi.get("name"),
            "code": bi.get("code"),
        }
    return out


def _public_seo_row(doc: Optional[dict], branch_id: str, is_partner: bool) -> dict:
    if not doc:
        return {
            "branch_id": branch_id,
            "is_collaboration_partner": is_partner,
            "has_seo": False,
            "meta_title": None,
            "meta_description": None,
            "keywords": None,
            "og_image": None,
        }
    return {
        "branch_id": branch_id,
        "is_collaboration_partner": is_partner,
        "has_seo": True,
        "meta_title": doc.get("meta_title"),
        "meta_description": doc.get("meta_description"),
        "keywords": doc.get("keywords"),
        "og_image": doc.get("og_image"),
    }


class CollaborationPartnerSeoController:
    @staticmethod
    async def get_seo(branch_id: str, current_user: dict = None) -> dict:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        db = get_db()
        branch = await get_partner_branch_for_cms(db, branch_id, current_user)
        await ensure_seo_indexes(db)

        doc = await db.collaboration_partner_seo.find_one({"branch_id": branch_id})
        if not doc:
            return empty_seo_response(branch_id, branch)
        return _enrich_seo_doc(doc, branch)

    @staticmethod
    async def upsert_seo(
        branch_id: str,
        payload: PartnerSeoUpsert,
        current_user: dict = None,
    ) -> dict:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        db = get_db()
        branch = await get_partner_branch_for_cms(db, branch_id, current_user)
        await ensure_seo_indexes(db)

        incoming = _dump_unset(payload)
        existing = await db.collaboration_partner_seo.find_one(
            {"branch_id": branch_id}
        )
        now = datetime.utcnow()

        def field(key: str, max_len: int) -> Optional[str]:
            if key in incoming:
                return _clean_str(incoming.get(key), max_len=max_len)
            if existing:
                return existing.get(key)
            return None

        meta_title = field("meta_title", 200)
        meta_description = field("meta_description", 500)
        keywords = field("keywords", 500)
        og_image = field("og_image", 1000)

        if existing:
            await db.collaboration_partner_seo.update_one(
                {"branch_id": branch_id},
                {
                    "$set": {
                        "meta_title": meta_title,
                        "meta_description": meta_description,
                        "keywords": keywords,
                        "og_image": og_image,
                        "updated_at": now,
                    }
                },
            )
            doc = await db.collaboration_partner_seo.find_one(
                {"branch_id": branch_id}
            )
            return {
                "message": "Partner SEO updated",
                "seo": _enrich_seo_doc(doc, branch),
            }

        seo = CollaborationPartnerSeo(
            branch_id=branch_id,
            meta_title=meta_title,
            meta_description=meta_description,
            keywords=keywords,
            og_image=og_image,
            created_at=now,
            updated_at=now,
        )
        doc = _dump(seo)
        await db.collaboration_partner_seo.insert_one(doc)
        return {
            "message": "Partner SEO created",
            "seo": _enrich_seo_doc(doc, branch),
        }

    @staticmethod
    async def get_public(branch_id: str) -> dict:
        """Public SEO for partner branch pages (no auth). Empty when not partner."""
        db = get_db()
        branch = await db.branches.find_one({"id": branch_id})
        if not branch or not branch_is_collaboration_partner(branch):
            return _public_seo_row(None, branch_id, False)
        doc = await db.collaboration_partner_seo.find_one({"branch_id": branch_id})
        return _public_seo_row(doc, branch_id, True)

    @staticmethod
    async def has_seo(branch_id: str) -> bool:
        db = get_db()
        doc = await db.collaboration_partner_seo.find_one(
            {"branch_id": branch_id}, {"_id": 1}
        )
        return bool(doc)
