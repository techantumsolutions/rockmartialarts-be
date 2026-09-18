"""
M20-S02 Champion Achievements & Media service.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.champion_achievement_models import (
    ChampionAchievementCreate,
    ChampionAchievementStatus,
    ChampionAchievementUpdate,
    RecognitionLevel,
)
from utils.database import get_db
from utils.helpers import serialize_doc

logger = logging.getLogger(__name__)

COL = "champion_achievements"
CHAMPIONS_COL = "champions"


async def ensure_champion_achievement_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL].create_index("id", unique=True)
        await database[COL].create_index(
            [("champion_id", 1), ("display_order", 1)]
        )
        await database[COL].create_index(
            [("champion_id", 1), ("status", 1)]
        )
        await database[COL].create_index("recognition_level")
        await database[COL].create_index([("updated_at", -1)])
    except Exception:
        logger.exception("Failed ensuring champion achievement indexes")


def enrich_champion_achievement(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    out = serialize_doc(doc)
    out["status"] = (
        out.get("status") or ChampionAchievementStatus.ACTIVE.value
    ).lower()
    out["recognition_level"] = (
        out.get("recognition_level") or RecognitionLevel.OTHER.value
    ).lower()
    out["images"] = list(out.get("images") or [])
    out["videos"] = list(out.get("videos") or [])
    out["documents"] = list(out.get("documents") or [])
    out["display_order"] = int(out.get("display_order") or 100)
    out["title"] = out.get("title") or ""
    out["description"] = out.get("description") or None
    out["competition_name"] = out.get("competition_name") or None
    out["award_title"] = out.get("award_title") or None
    out["place"] = out.get("place") or None
    out["event_year"] = out.get("event_year")
    out["event_date"] = out.get("event_date") or None
    out["media_count"] = (
        len(out["images"]) + len(out["videos"]) + len(out["documents"])
    )
    return out


async def _require_champion(db, champion_id: str) -> dict:
    doc = await db[CHAMPIONS_COL].find_one({"id": champion_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Champion not found")
    return doc


async def list_champion_achievements(
    champion_id: str,
    *,
    include_archived: bool = False,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_champion_achievement_indexes(db)
    await _require_champion(db, champion_id)

    q: Dict[str, Any] = {"champion_id": champion_id}
    if status and status != "all":
        q["status"] = status
    elif not include_archived:
        q["status"] = {"$ne": ChampionAchievementStatus.ARCHIVED.value}

    rows = (
        await db[COL]
        .find(q)
        .sort([("display_order", 1), ("event_year", -1), ("updated_at", -1)])
        .to_list(length=500)
    )
    achievements = [enrich_champion_achievement(r) for r in rows]
    return {
        "champion_id": champion_id,
        "achievements": achievements,
        "total": len(achievements),
    }


async def get_champion_achievement(
    champion_id: str, achievement_id: str
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await _require_champion(db, champion_id)
    doc = await db[COL].find_one(
        {"id": achievement_id, "champion_id": champion_id}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Achievement not found")
    return {"achievement": enrich_champion_achievement(doc)}


async def create_champion_achievement(
    champion_id: str,
    body: ChampionAchievementCreate,
    *,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_champion_achievement_indexes(db)
    await _require_champion(db, champion_id)

    now = datetime.utcnow()
    doc = {
        "id": str(uuid.uuid4()),
        "champion_id": champion_id,
        "title": body.title,
        "description": body.description,
        "recognition_level": body.recognition_level.value,
        "competition_name": body.competition_name,
        "award_title": body.award_title,
        "place": body.place,
        "event_year": body.event_year,
        "event_date": body.event_date,
        "images": list(body.images or []),
        "videos": list(body.videos or []),
        "documents": list(body.documents or []),
        "display_order": body.display_order,
        "status": body.status.value,
        "created_by": (current_user or {}).get("id"),
        "created_at": now,
        "updated_at": now,
    }
    await db[COL].insert_one(doc)
    return {
        "message": "Achievement created",
        "achievement": enrich_champion_achievement(doc),
    }


async def update_champion_achievement(
    champion_id: str,
    achievement_id: str,
    body: ChampionAchievementUpdate,
    *,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await _require_champion(db, champion_id)
    doc = await db[COL].find_one(
        {"id": achievement_id, "champion_id": champion_id}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Achievement not found")

    data = body.model_dump(exclude_unset=True)
    patch: Dict[str, Any] = {"updated_at": datetime.utcnow()}
    if current_user and current_user.get("id"):
        patch["updated_by"] = current_user["id"]

    if data.pop("clear_event_year", None):
        patch["event_year"] = None

    for key in (
        "title",
        "description",
        "competition_name",
        "award_title",
        "place",
        "event_year",
        "event_date",
        "images",
        "videos",
        "documents",
        "display_order",
    ):
        if key in data:
            patch[key] = data[key]

    if "recognition_level" in data and data["recognition_level"] is not None:
        rl = data["recognition_level"]
        patch["recognition_level"] = rl.value if hasattr(rl, "value") else str(rl)

    if "status" in data and data["status"] is not None:
        st = data["status"]
        patch["status"] = st.value if hasattr(st, "value") else str(st)

    await db[COL].update_one(
        {"id": achievement_id, "champion_id": champion_id}, {"$set": patch}
    )
    updated = await db[COL].find_one(
        {"id": achievement_id, "champion_id": champion_id}
    )
    return {
        "message": "Achievement updated",
        "achievement": enrich_champion_achievement(updated),
    }


async def archive_champion_achievement(
    champion_id: str,
    achievement_id: str,
    *,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await _require_champion(db, champion_id)
    doc = await db[COL].find_one(
        {"id": achievement_id, "champion_id": champion_id}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Achievement not found")
    patch = {
        "status": ChampionAchievementStatus.ARCHIVED.value,
        "updated_at": datetime.utcnow(),
    }
    if current_user and current_user.get("id"):
        patch["updated_by"] = current_user["id"]
    await db[COL].update_one(
        {"id": achievement_id, "champion_id": champion_id}, {"$set": patch}
    )
    updated = await db[COL].find_one(
        {"id": achievement_id, "champion_id": champion_id}
    )
    return {
        "message": "Achievement archived",
        "achievement": enrich_champion_achievement(updated),
    }
