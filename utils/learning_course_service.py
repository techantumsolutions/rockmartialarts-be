"""
M16-S01 Online Learning course catalogue service.

Collection: learning_courses — additive; does not touch physical `courses`.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.learning_course_models import (
    LEARNING_COURSE_STATUSES,
    LearningCourseCreate,
    LearningCourseStatus,
    LearningCourseUpdate,
    slugify,
)
from utils.database import get_db
from utils.helpers import serialize_doc

logger = logging.getLogger(__name__)

COL = "learning_courses"


def _public(doc: Optional[dict]) -> Dict[str, Any]:
    return serialize_doc(doc) or {}


def _catalogue_view(doc: dict) -> Dict[str, Any]:
    """Public-safe fields only."""
    return {
        "id": doc.get("id"),
        "title": doc.get("title"),
        "slug": doc.get("slug"),
        "short_description": doc.get("short_description"),
        "description": doc.get("description"),
        "thumbnail_url": doc.get("thumbnail_url"),
        "trailer_url": doc.get("trailer_url"),
        "difficulty": doc.get("difficulty"),
        "language": doc.get("language"),
        "estimated_hours": doc.get("estimated_hours"),
        "sort_order": doc.get("sort_order", 100),
        "seo_title": doc.get("seo_title"),
        "seo_description": doc.get("seo_description"),
        "published_at": doc.get("published_at"),
        # Placeholders for S02 hierarchy counts (always 0 until levels exist)
        "levels_count": int(doc.get("levels_count") or 0),
        "lessons_count": int(doc.get("lessons_count") or 0),
    }


async def ensure_learning_course_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL].create_index("id", unique=True)
        await database[COL].create_index("slug", unique=True)
        await database[COL].create_index([("status", 1), ("sort_order", 1)])
        await database[COL].create_index([("created_at", -1)])
    except Exception:
        logger.exception("Failed ensuring learning course indexes")


async def _unique_slug(db, base: str, exclude_id: Optional[str] = None) -> str:
    slug = slugify(base)
    candidate = slug
    n = 2
    while True:
        q: Dict[str, Any] = {"slug": candidate}
        if exclude_id:
            q["id"] = {"$ne": exclude_id}
        exists = await db[COL].find_one(q, {"id": 1})
        if not exists:
            return candidate
        candidate = f"{slug}-{n}"[:120]
        n += 1


async def create_learning_course(
    body: LearningCourseCreate, *, current_user: dict
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_learning_course_indexes(db)

    now = datetime.utcnow()
    status = body.status.value if isinstance(body.status, LearningCourseStatus) else str(body.status)
    slug = await _unique_slug(db, body.slug or body.title)
    doc = {
        "id": str(uuid.uuid4()),
        "title": body.title.strip(),
        "slug": slug,
        "short_description": body.short_description,
        "description": body.description,
        "thumbnail_url": body.thumbnail_url,
        "trailer_url": body.trailer_url,
        "difficulty": body.difficulty,
        "language": body.language or "English",
        "estimated_hours": body.estimated_hours,
        "status": status,
        "sort_order": body.sort_order,
        "seo_title": body.seo_title,
        "seo_description": body.seo_description,
        "levels_count": 0,
        "lessons_count": 0,
        "published_at": now if status == LearningCourseStatus.PUBLISHED.value else None,
        "created_at": now,
        "updated_at": now,
        "created_by": current_user.get("id"),
        "updated_by": current_user.get("id"),
    }
    await db[COL].insert_one(doc)
    return {"message": "Learning course created", "course": _public(doc)}


async def update_learning_course(
    course_id: str, body: LearningCourseUpdate, *, current_user: dict
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": course_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Learning course not found")

    data = body.model_dump(exclude_unset=True)
    patch: Dict[str, Any] = {
        "updated_at": datetime.utcnow(),
        "updated_by": current_user.get("id"),
    }
    if "title" in data and data["title"] is not None:
        patch["title"] = data["title"]
    if "slug" in data and data["slug"] is not None:
        patch["slug"] = await _unique_slug(db, data["slug"], exclude_id=course_id)
    for key in (
        "short_description",
        "description",
        "thumbnail_url",
        "trailer_url",
        "difficulty",
        "language",
        "estimated_hours",
        "sort_order",
        "seo_title",
        "seo_description",
    ):
        if key in data:
            patch[key] = data[key]

    if "status" in data and data["status"] is not None:
        st = data["status"].value if hasattr(data["status"], "value") else str(data["status"])
        if st not in LEARNING_COURSE_STATUSES:
            raise HTTPException(status_code=400, detail=f"Invalid status: {st}")
        patch["status"] = st
        if st == LearningCourseStatus.PUBLISHED.value and not doc.get("published_at"):
            patch["published_at"] = datetime.utcnow()

    await db[COL].update_one({"id": course_id}, {"$set": patch})
    updated = await db[COL].find_one({"id": course_id})
    return {"message": "Learning course updated", "course": _public(updated)}


async def delete_learning_course(course_id: str, *, current_user: dict) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": course_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Learning course not found")
    # Soft-archive preferred over hard delete for catalogue integrity
    await db[COL].update_one(
        {"id": course_id},
        {
            "$set": {
                "status": LearningCourseStatus.ARCHIVED.value,
                "updated_at": datetime.utcnow(),
                "updated_by": current_user.get("id"),
            }
        },
    )
    updated = await db[COL].find_one({"id": course_id})
    return {"message": "Learning course archived", "course": _public(updated)}


async def get_learning_course_admin(course_id: str) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": course_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Learning course not found")
    return {"course": _public(doc)}


async def list_learning_courses_admin(
    *,
    status: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    clauses: List[Dict[str, Any]] = []
    st = (status or "").strip().lower()
    if st and st != "all":
        if st not in LEARNING_COURSE_STATUSES:
            raise HTTPException(status_code=400, detail=f"Invalid status: {st}")
        clauses.append({"status": st})

    q_search = (search or "").strip()
    if q_search:
        rx = {"$regex": re.escape(q_search), "$options": "i"}
        clauses.append(
            {
                "$or": [
                    {"title": rx},
                    {"slug": rx},
                    {"short_description": rx},
                    {"difficulty": rx},
                ]
            }
        )

    q: Dict[str, Any] = {"$and": clauses} if clauses else {}
    limit = min(max(limit, 1), 200)
    skip = max(skip, 0)
    total = await db[COL].count_documents(q)
    rows = (
        await db[COL]
        .find(q)
        .sort([("sort_order", 1), ("created_at", -1)])
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {
        "courses": [_public(r) for r in rows],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


async def list_learning_catalogue(
    *,
    search: Optional[str] = None,
    difficulty: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    clauses: List[Dict[str, Any]] = [
        {"status": LearningCourseStatus.PUBLISHED.value}
    ]
    q_search = (search or "").strip()
    if q_search:
        rx = {"$regex": re.escape(q_search), "$options": "i"}
        clauses.append(
            {
                "$or": [
                    {"title": rx},
                    {"short_description": rx},
                    {"description": rx},
                    {"difficulty": rx},
                ]
            }
        )
    diff = (difficulty or "").strip()
    if diff and diff.lower() != "all":
        clauses.append({"difficulty": {"$regex": f"^{re.escape(diff)}$", "$options": "i"}})

    q = {"$and": clauses}
    limit = min(max(limit, 1), 100)
    skip = max(skip, 0)
    total = await db[COL].count_documents(q)
    rows = (
        await db[COL]
        .find(q)
        .sort([("sort_order", 1), ("published_at", -1)])
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {
        "courses": [_catalogue_view(r) for r in rows],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


async def get_learning_catalogue_detail(slug_or_id: str) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    key = (slug_or_id or "").strip()
    if not key:
        raise HTTPException(status_code=404, detail="Course not found")
    doc = await db[COL].find_one(
        {
            "status": LearningCourseStatus.PUBLISHED.value,
            "$or": [{"slug": key}, {"id": key}],
        }
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Course not found")
    course = _catalogue_view(doc)
    try:
        from utils.learning_hierarchy_service import get_public_curriculum

        curr = await get_public_curriculum(doc["id"])
        course["curriculum"] = curr.get("curriculum") or []
        course["levels_count"] = curr.get("levels_count", course.get("levels_count") or 0)
        course["lessons_count"] = curr.get(
            "lessons_count", course.get("lessons_count") or 0
        )
    except Exception:
        course["curriculum"] = []
    return {"course": course}
