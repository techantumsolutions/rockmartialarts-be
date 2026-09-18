"""
M20-S01 Champion Profile CMS service.

Admin CRUD for champions (photo, success story, student link, publish).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.champion_models import (
    ChampionCreate,
    ChampionStatus,
    ChampionUpdate,
    slugify_champion,
)
from utils.database import get_db
from utils.helpers import serialize_doc

logger = logging.getLogger(__name__)

COL = "champions"


async def ensure_champion_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL].create_index("id", unique=True)
        await database[COL].create_index("slug", unique=True)
        await database[COL].create_index([("status", 1), ("display_order", 1)])
        await database[COL].create_index("student_id")
        await database[COL].create_index([("updated_at", -1)])
    except Exception:
        logger.exception("Failed ensuring champion indexes")


def enrich_champion(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    out = serialize_doc(doc)
    status = (out.get("status") or ChampionStatus.DRAFT.value).lower()
    out["status"] = status
    out["is_published"] = status == ChampionStatus.PUBLISHED.value
    out["name"] = out.get("name") or ""
    out["headline"] = out.get("headline") or None
    out["short_bio"] = out.get("short_bio") or None
    out["success_story"] = out.get("success_story") or None
    out["photo_url"] = out.get("photo_url") or None
    out["student_id"] = out.get("student_id") or None
    out["student_name"] = out.get("student_name") or None
    out["branch_id"] = out.get("branch_id") or None
    out["display_order"] = int(out.get("display_order") or 100)
    out["published_at"] = out.get("published_at") or None
    return out


async def _unique_slug(db, base: str, *, exclude_id: Optional[str] = None) -> str:
    slug = slugify_champion(base)
    candidate = slug
    n = 2
    while True:
        q: Dict[str, Any] = {"slug": candidate}
        if exclude_id:
            q["id"] = {"$ne": exclude_id}
        exists = await db[COL].find_one(q)
        if not exists:
            return candidate
        candidate = f"{slug}-{n}"
        n += 1
        if n > 50:
            return f"{slug}-{uuid.uuid4().hex[:6]}"


async def _resolve_student_name(db, student_id: Optional[str]) -> Optional[str]:
    if not student_id:
        return None
    user = await db.users.find_one(
        {"id": student_id, "role": "student"},
        {"full_name": 1, "name": 1, "first_name": 1, "last_name": 1},
    )
    if not user:
        raise HTTPException(status_code=400, detail="Linked student not found")
    name = (
        user.get("full_name")
        or user.get("name")
        or " ".join(
            x
            for x in [user.get("first_name"), user.get("last_name")]
            if x
        ).strip()
    )
    return name or None


async def list_champions(
    *,
    status: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    include_archived: bool = False,
    published_only: bool = False,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_champion_indexes(db)

    q: Dict[str, Any] = {}
    if published_only:
        q["status"] = ChampionStatus.PUBLISHED.value
    elif status and status != "all":
        q["status"] = status
    elif not include_archived:
        q["status"] = {"$ne": ChampionStatus.ARCHIVED.value}

    if search and search.strip():
        term = search.strip()
        q["$or"] = [
            {"name": {"$regex": term, "$options": "i"}},
            {"slug": {"$regex": term, "$options": "i"}},
            {"headline": {"$regex": term, "$options": "i"}},
            {"student_name": {"$regex": term, "$options": "i"}},
            {"short_bio": {"$regex": term, "$options": "i"}},
        ]

    skip = max(0, skip)
    limit = max(1, min(limit, 200))
    total = await db[COL].count_documents(q)
    rows = (
        await db[COL]
        .find(q)
        .sort([("display_order", 1), ("updated_at", -1)])
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {
        "champions": [enrich_champion(r) for r in rows],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


async def get_champion(champion_id: str) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": champion_id})
    if not doc:
        doc = await db[COL].find_one({"slug": champion_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Champion not found")
    return {"champion": enrich_champion(doc)}


async def create_champion(
    body: ChampionCreate,
    *,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_champion_indexes(db)

    student_name = await _resolve_student_name(db, body.student_id)
    # If linked, prefer student display name when name omitted is same — name is required
    slug = await _unique_slug(db, body.slug or body.name)
    now = datetime.utcnow()
    status = body.status.value
    published_at = now if status == ChampionStatus.PUBLISHED.value else None

    doc = {
        "id": str(uuid.uuid4()),
        "name": body.name,
        "slug": slug,
        "headline": body.headline,
        "short_bio": body.short_bio,
        "success_story": body.success_story,
        "photo_url": body.photo_url,
        "student_id": body.student_id,
        "student_name": student_name,
        "branch_id": body.branch_id,
        "display_order": body.display_order,
        "status": status,
        "published_at": published_at,
        "created_by": (current_user or {}).get("id"),
        "created_at": now,
        "updated_at": now,
    }
    await db[COL].insert_one(doc)
    return {
        "message": "Champion created",
        "champion": enrich_champion(doc),
    }


async def update_champion(
    champion_id: str,
    body: ChampionUpdate,
    *,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": champion_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Champion not found")

    data = body.model_dump(exclude_unset=True)
    patch: Dict[str, Any] = {"updated_at": datetime.utcnow()}
    if current_user and current_user.get("id"):
        patch["updated_by"] = current_user["id"]

    if data.pop("clear_photo", None):
        patch["photo_url"] = None
    if data.pop("clear_student", None):
        patch["student_id"] = None
        patch["student_name"] = None
    if data.pop("clear_branch", None):
        patch["branch_id"] = None

    for key in (
        "name",
        "headline",
        "short_bio",
        "success_story",
        "photo_url",
        "branch_id",
        "display_order",
    ):
        if key in data:
            patch[key] = data[key]

    if "slug" in data and data["slug"]:
        patch["slug"] = await _unique_slug(
            db, data["slug"], exclude_id=champion_id
        )

    if "student_id" in data:
        sid = data["student_id"]
        if sid:
            patch["student_id"] = sid
            patch["student_name"] = await _resolve_student_name(db, sid)
        else:
            patch["student_id"] = None
            patch["student_name"] = None

    if "status" in data and data["status"] is not None:
        st = data["status"]
        st_val = st.value if hasattr(st, "value") else str(st)
        patch["status"] = st_val
        if st_val == ChampionStatus.PUBLISHED.value and not doc.get("published_at"):
            patch["published_at"] = datetime.utcnow()
        if st_val != ChampionStatus.PUBLISHED.value:
            # keep published_at history; do not clear
            pass

    await db[COL].update_one({"id": champion_id}, {"$set": patch})
    updated = await db[COL].find_one({"id": champion_id})
    return {
        "message": "Champion updated",
        "champion": enrich_champion(updated),
    }


async def publish_champion(
    champion_id: str,
    *,
    published: bool = True,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": champion_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Champion not found")
    if doc.get("status") == ChampionStatus.ARCHIVED.value:
        raise HTTPException(status_code=400, detail="Cannot publish an archived champion")

    now = datetime.utcnow()
    if published:
        patch: Dict[str, Any] = {
            "status": ChampionStatus.PUBLISHED.value,
            "updated_at": now,
        }
        if not doc.get("published_at"):
            patch["published_at"] = now
    else:
        patch = {
            "status": ChampionStatus.UNPUBLISHED.value,
            "updated_at": now,
        }
    if current_user and current_user.get("id"):
        patch["updated_by"] = current_user["id"]

    await db[COL].update_one({"id": champion_id}, {"$set": patch})
    updated = await db[COL].find_one({"id": champion_id})
    return {
        "message": "Champion published" if published else "Champion unpublished",
        "champion": enrich_champion(updated),
    }


async def archive_champion(
    champion_id: str,
    *,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": champion_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Champion not found")
    patch = {
        "status": ChampionStatus.ARCHIVED.value,
        "updated_at": datetime.utcnow(),
    }
    if current_user and current_user.get("id"):
        patch["updated_by"] = current_user["id"]
    await db[COL].update_one({"id": champion_id}, {"$set": patch})
    updated = await db[COL].find_one({"id": champion_id})
    return {
        "message": "Champion archived",
        "champion": enrich_champion(updated),
    }


async def search_students_for_link(
    *,
    search: Optional[str] = None,
    limit: int = 30,
) -> Dict[str, Any]:
    """Student options for optional champion ↔ student linkage."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    q: Dict[str, Any] = {"role": "student"}
    if search and search.strip():
        term = search.strip()
        q["$or"] = [
            {"full_name": {"$regex": term, "$options": "i"}},
            {"name": {"$regex": term, "$options": "i"}},
            {"email": {"$regex": term, "$options": "i"}},
            {"phone": {"$regex": term, "$options": "i"}},
            {"id": term},
        ]
    limit = max(1, min(int(limit or 30), 100))
    rows = (
        await db.users.find(
            q,
            {
                "id": 1,
                "full_name": 1,
                "name": 1,
                "email": 1,
                "phone": 1,
                "batch_ref": 1,
            },
        )
        .sort("full_name", 1)
        .limit(limit)
        .to_list(length=limit)
    )
    students = [
        {
            "id": str(r["id"]),
            "name": r.get("full_name") or r.get("name") or str(r["id"]),
            "email": r.get("email"),
            "phone": r.get("phone"),
            "batch_ref": r.get("batch_ref"),
        }
        for r in rows
        if r.get("id")
    ]
    return {"students": students}


_PUBLIC_CHAMPION_KEYS = (
    "id",
    "name",
    "slug",
    "headline",
    "short_bio",
    "success_story",
    "photo_url",
    "display_order",
    "published_at",
)


def _public_champion(doc: Optional[dict], *, include_story: bool = True) -> Optional[dict]:
    enriched = enrich_champion(doc)
    if not enriched:
        return None
    out = {k: enriched.get(k) for k in _PUBLIC_CHAMPION_KEYS}
    if not include_story:
        out.pop("success_story", None)
    return out


async def list_public_champions(
    *,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 48,
) -> Dict[str, Any]:
    """Published champions for the public website."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_champion_indexes(db)

    q: Dict[str, Any] = {"status": ChampionStatus.PUBLISHED.value}
    if search and search.strip():
        term = search.strip()
        q["$or"] = [
            {"name": {"$regex": term, "$options": "i"}},
            {"slug": {"$regex": term, "$options": "i"}},
            {"headline": {"$regex": term, "$options": "i"}},
            {"short_bio": {"$regex": term, "$options": "i"}},
        ]

    skip = max(0, skip)
    limit = max(1, min(limit, 100))
    total = await db[COL].count_documents(q)
    rows = (
        await db[COL]
        .find(q)
        .sort([("display_order", 1), ("published_at", -1), ("name", 1)])
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )

    # Optional achievement counts
    from models.champion_achievement_models import ChampionAchievementStatus
    from utils.champion_achievement_service import COL as ACH_COL

    champions = []
    for row in rows:
        pub = _public_champion(row, include_story=False)
        if not pub:
            continue
        count = await db[ACH_COL].count_documents(
            {
                "champion_id": row.get("id"),
                "status": ChampionAchievementStatus.ACTIVE.value,
            }
        )
        pub["achievement_count"] = count
        champions.append(pub)

    return {
        "champions": champions,
        "total": total,
        "skip": skip,
        "limit": limit,
    }


async def get_public_champion(slug_or_id: str) -> Dict[str, Any]:
    """Published champion detail + active achievements."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    key = str(slug_or_id or "").strip()
    if not key:
        raise HTTPException(status_code=404, detail="Champion not found")

    doc = await db[COL].find_one(
        {"slug": key, "status": ChampionStatus.PUBLISHED.value}
    )
    if not doc:
        doc = await db[COL].find_one(
            {"id": key, "status": ChampionStatus.PUBLISHED.value}
        )
    if not doc:
        raise HTTPException(status_code=404, detail="Champion not found")

    from models.champion_achievement_models import ChampionAchievementStatus
    from utils.champion_achievement_service import (
        COL as ACH_COL,
        enrich_champion_achievement,
    )

    rows = (
        await db[ACH_COL]
        .find(
            {
                "champion_id": doc.get("id"),
                "status": ChampionAchievementStatus.ACTIVE.value,
            }
        )
        .sort([("display_order", 1), ("event_year", -1), ("updated_at", -1)])
        .to_list(length=200)
    )
    achievements = [enrich_champion_achievement(r) for r in rows]
    champion = _public_champion(doc, include_story=True)
    if champion:
        champion["achievement_count"] = len(achievements)

    return {
        "champion": champion,
        "achievements": achievements,
    }
