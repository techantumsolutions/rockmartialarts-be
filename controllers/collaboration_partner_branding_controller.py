"""
M21-S03 Collaboration Partner branding controller.
Partner-scoped only — never writes to generic branches collection fields for CMS content.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.collaboration_partner_branding_models import (
    CollaborationPartnerBranding,
    PartnerBrandingContent,
    PartnerBrandingFacilities,
    PartnerBrandingMedia,
    PartnerBrandingUpsert,
    PartnerDayHours,
    PartnerSocialLinks,
    WEEKDAYS,
    default_operating_hours,
    empty_branding_response,
)
from utils.collaboration_partner import (
    get_partner_branch_for_cms,
    enrich_collaboration_flag,
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


def _merge_section(existing: Optional[dict], incoming: Optional[dict], factory) -> dict:
    base = _dump(factory())
    if existing:
        for k, v in existing.items():
            if k in base:
                base[k] = v
    if incoming is not None:
        for k, v in incoming.items():
            if k in base:
                base[k] = v
    return base


def _normalize_hours(raw: Optional[List[Any]]) -> List[dict]:
    by_day: Dict[str, dict] = {
        h.day: _dump(h) for h in default_operating_hours()
    }
    if not raw:
        return [by_day[d] for d in WEEKDAYS]
    for item in raw:
        if hasattr(item, "model_dump"):
            d = item.model_dump(mode="python")
        elif hasattr(item, "dict"):
            d = item.dict()
        elif isinstance(item, dict):
            d = dict(item)
        else:
            continue
        day = str(d.get("day") or "").strip().lower()
        if day not in by_day:
            continue
        by_day[day] = {
            "day": day,
            "open_time": d.get("open_time"),
            "close_time": d.get("close_time"),
            "is_closed": bool(d.get("is_closed", False)),
        }
    return [by_day[d] for d in WEEKDAYS]


async def ensure_branding_indexes(db=None) -> None:
    db = db if db is not None else get_db()
    if db is None:
        return
    try:
        await db.collaboration_partner_branding.create_index("branch_id", unique=True)
    except Exception:
        pass


def _enrich_branding_doc(doc: dict, branch: Optional[dict] = None) -> dict:
    out = serialize_doc(doc) if doc else {}
    out["has_branding"] = True
    if branch:
        enrich_collaboration_flag(branch)
        bi = branch.get("branch") or {}
        out["branch_snapshot"] = {
            "id": branch.get("id"),
            "name": bi.get("name"),
            "code": bi.get("code"),
        }
    return out


class CollaborationPartnerBrandingController:
    @staticmethod
    async def get_branding(branch_id: str, current_user: dict = None) -> dict:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        db = get_db()
        branch = await get_partner_branch_for_cms(db, branch_id, current_user)
        await ensure_branding_indexes(db)

        doc = await db.collaboration_partner_branding.find_one({"branch_id": branch_id})
        if not doc:
            return empty_branding_response(branch_id, branch)
        return _enrich_branding_doc(doc, branch)

    @staticmethod
    async def upsert_branding(
        branch_id: str,
        payload: PartnerBrandingUpsert,
        current_user: dict = None,
    ) -> dict:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        db = get_db()
        branch = await get_partner_branch_for_cms(db, branch_id, current_user)
        await ensure_branding_indexes(db)

        incoming = _dump_unset(payload)
        existing = await db.collaboration_partner_branding.find_one(
            {"branch_id": branch_id}
        )
        now = datetime.utcnow()

        media = _merge_section(
            (existing or {}).get("media"),
            incoming.get("media"),
            PartnerBrandingMedia,
        )
        content = _merge_section(
            (existing or {}).get("content"),
            incoming.get("content"),
            PartnerBrandingContent,
        )
        # Lists in content
        if incoming.get("content") is not None:
            pts = incoming["content"].get("why_choose_us_points")
            if pts is not None:
                content["why_choose_us_points"] = [
                    str(x).strip() for x in pts if str(x).strip()
                ]

        facilities = _merge_section(
            (existing or {}).get("facilities"),
            incoming.get("facilities"),
            PartnerBrandingFacilities,
        )
        if incoming.get("facilities") is not None:
            fac = incoming["facilities"]
            if fac.get("facilities") is not None:
                facilities["facilities"] = [
                    str(x).strip() for x in fac["facilities"] if str(x).strip()
                ]
            if fac.get("location_highlights") is not None:
                facilities["location_highlights"] = [
                    str(x).strip()
                    for x in fac["location_highlights"]
                    if str(x).strip()
                ]

        social = _merge_section(
            (existing or {}).get("social_links"),
            incoming.get("social_links"),
            PartnerSocialLinks,
        )

        if "operating_hours" in incoming:
            hours = _normalize_hours(incoming.get("operating_hours"))
        else:
            hours = _normalize_hours((existing or {}).get("operating_hours"))

        if "hours_notes" in incoming:
            hours_notes = incoming.get("hours_notes")
        else:
            hours_notes = (existing or {}).get("hours_notes")

        if existing:
            await db.collaboration_partner_branding.update_one(
                {"branch_id": branch_id},
                {
                    "$set": {
                        "media": media,
                        "content": content,
                        "operating_hours": hours,
                        "hours_notes": hours_notes,
                        "facilities": facilities,
                        "social_links": social,
                        "updated_at": now,
                    }
                },
            )
            doc = await db.collaboration_partner_branding.find_one(
                {"branch_id": branch_id}
            )
            return {
                "message": "Partner branding updated",
                "branding": _enrich_branding_doc(doc, branch),
            }

        branding = CollaborationPartnerBranding(
            branch_id=branch_id,
            media=PartnerBrandingMedia(**media),
            content=PartnerBrandingContent(**content),
            operating_hours=[PartnerDayHours(**h) for h in hours],
            hours_notes=hours_notes,
            facilities=PartnerBrandingFacilities(**facilities),
            social_links=PartnerSocialLinks(**social),
            created_at=now,
            updated_at=now,
        )
        doc = _dump(branding)
        await db.collaboration_partner_branding.insert_one(doc)
        return {
            "message": "Partner branding created",
            "branding": _enrich_branding_doc(doc, branch),
        }

    @staticmethod
    async def has_branding(branch_id: str) -> bool:
        db = get_db()
        doc = await db.collaboration_partner_branding.find_one(
            {"branch_id": branch_id}, {"_id": 1}
        )
        return bool(doc)
