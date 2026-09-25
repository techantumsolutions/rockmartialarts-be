"""CRUD + public active picker for website campaign popups."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status

from models.campaign_popup_models import (
    CampaignPopupCreate,
    CampaignPopupEnabledPatch,
    CampaignPopupUpdate,
    new_campaign_popup_id,
)
from utils.database import get_db
from utils.helpers import serialize_doc

COLLECTION = "campaign_popups"


def _today_utc_ymd() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _assert_date_order(start_date: str, end_date: str) -> None:
    if start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_date must be on or before end_date",
        )


def _to_row(doc: Optional[dict]) -> Optional[Dict[str, Any]]:
    if not doc:
        return None
    row = serialize_doc(doc)
    # Prefer explicit string id over ObjectId mapping
    if doc.get("id"):
        row["id"] = doc["id"]
    return row


async def list_all() -> List[Dict[str, Any]]:
    db = get_db()
    cur = db[COLLECTION].find({}).sort([("priority", -1), ("created_at", -1)])
    docs = await cur.to_list(length=500)
    return [_to_row(d) for d in docs if d]


async def get_by_id(popup_id: str) -> Dict[str, Any]:
    db = get_db()
    doc = await db[COLLECTION].find_one({"id": popup_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Campaign popup not found")
    return _to_row(doc)  # type: ignore[return-value]


async def create(data: CampaignPopupCreate, current_user: dict) -> Dict[str, Any]:
    _assert_date_order(data.start_date, data.end_date)
    db = get_db()
    now = datetime.utcnow()
    doc = {
        "id": new_campaign_popup_id(),
        "event_title": data.event_title,
        "description": data.description or "",
        "image_url": data.image_url or "",
        "start_date": data.start_date,
        "end_date": data.end_date,
        "location": data.location or "",
        "cta_label": data.cta_label or "Learn more",
        "cta_url": data.cta_url or "",
        "delay_minutes": data.delay_minutes,
        "priority": data.priority,
        "enabled": data.enabled,
        "created_at": now,
        "updated_at": now,
        "created_by": current_user.get("id") or current_user.get("user_id"),
    }
    await db[COLLECTION].insert_one(doc)
    return _to_row(doc)  # type: ignore[return-value]


async def update(popup_id: str, data: CampaignPopupUpdate, current_user: dict) -> Dict[str, Any]:
    db = get_db()
    existing = await db[COLLECTION].find_one({"id": popup_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Campaign popup not found")

    patch = data.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(status_code=400, detail="No fields to update")

    start = patch.get("start_date", existing.get("start_date"))
    end = patch.get("end_date", existing.get("end_date"))
    _assert_date_order(str(start), str(end))

    patch["updated_at"] = datetime.utcnow()
    patch["updated_by"] = current_user.get("id") or current_user.get("user_id")

    await db[COLLECTION].update_one({"id": popup_id}, {"$set": patch})
    return await get_by_id(popup_id)


async def set_enabled(popup_id: str, data: CampaignPopupEnabledPatch, current_user: dict) -> Dict[str, Any]:
    return await update(
        popup_id,
        CampaignPopupUpdate(enabled=data.enabled),
        current_user,
    )


async def delete(popup_id: str, current_user: dict) -> Dict[str, Any]:
    db = get_db()
    result = await db[COLLECTION].delete_one({"id": popup_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Campaign popup not found")
    return {"ok": True, "id": popup_id}


async def get_public_active() -> Dict[str, Any]:
    """
    Return the single best eligible campaign popup for the public site:
    enabled + today (UTC date) within [start_date, end_date] inclusive.
    Highest priority wins; then newest created_at.
    """
    db = get_db()
    today = _today_utc_ymd()
    q = {
        "enabled": True,
        "start_date": {"$lte": today},
        "end_date": {"$gte": today},
    }
    cur = db[COLLECTION].find(q).sort([("priority", -1), ("created_at", -1)]).limit(1)
    docs = await cur.to_list(length=1)
    if not docs:
        return {"popup": None}
    return {"popup": _to_row(docs[0])}
