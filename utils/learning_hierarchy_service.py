"""
M16-S02 Learning hierarchy service: Course → Level → Lesson → Video.

Collections: learning_levels, learning_lessons
Does not modify physical courses or S01 catalogue fields beyond count denorm.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.learning_hierarchy_models import (
    LEARNING_ITEM_STATUSES,
    LearningItemStatus,
    LearningLessonCreate,
    LearningLessonUpdate,
    LearningLevelCreate,
    LearningLevelUpdate,
)
from utils.database import get_db
from utils.helpers import serialize_doc

logger = logging.getLogger(__name__)

COL_COURSES = "learning_courses"
COL_LEVELS = "learning_levels"
COL_LESSONS = "learning_lessons"


def _public(doc: Optional[dict]) -> Dict[str, Any]:
    return serialize_doc(doc) or {}


def _lesson_public_outline(doc: dict) -> Dict[str, Any]:
    """Public curriculum — no video URLs (S04 protects stream)."""
    return {
        "id": doc.get("id"),
        "level_id": doc.get("level_id"),
        "title": doc.get("title"),
        "description": doc.get("description"),
        "sort_order": doc.get("sort_order", 0),
        "duration_seconds": doc.get("duration_seconds"),
        "is_preview": bool(doc.get("is_preview")),
        "has_video": bool(doc.get("video_url") or doc.get("video_storage_key")),
    }


def _lesson_admin(doc: dict) -> Dict[str, Any]:
    return _public(doc)


async def ensure_learning_hierarchy_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL_LEVELS].create_index("id", unique=True)
        await database[COL_LEVELS].create_index(
            [("course_id", 1), ("sort_order", 1)]
        )
        await database[COL_LESSONS].create_index("id", unique=True)
        await database[COL_LESSONS].create_index(
            [("level_id", 1), ("sort_order", 1)]
        )
        await database[COL_LESSONS].create_index(
            [("course_id", 1), ("sort_order", 1)]
        )
    except Exception:
        logger.exception("Failed ensuring learning hierarchy indexes")


async def _get_course(db, course_id: str) -> dict:
    doc = await db[COL_COURSES].find_one({"id": course_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Learning course not found")
    return doc


async def _refresh_course_counts(db, course_id: str) -> None:
    levels_n = await db[COL_LEVELS].count_documents(
        {
            "course_id": course_id,
            "status": {"$ne": LearningItemStatus.ARCHIVED.value},
        }
    )
    lessons_n = await db[COL_LESSONS].count_documents(
        {
            "course_id": course_id,
            "status": {"$ne": LearningItemStatus.ARCHIVED.value},
        }
    )
    await db[COL_COURSES].update_one(
        {"id": course_id},
        {
            "$set": {
                "levels_count": levels_n,
                "lessons_count": lessons_n,
                "updated_at": datetime.utcnow(),
            }
        },
    )


async def _next_sort_order(db, col: str, filter_q: dict) -> int:
    rows = (
        await db[col]
        .find(filter_q)
        .sort([("sort_order", -1)])
        .limit(1)
        .to_list(length=1)
    )
    if not rows:
        return 10
    return int(rows[0].get("sort_order") or 0) + 10


# ----- Levels -----


async def list_levels_admin(course_id: str) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await _get_course(db, course_id)
    levels = (
        await db[COL_LEVELS]
        .find({"course_id": course_id})
        .sort([("sort_order", 1), ("created_at", 1)])
        .to_list(length=200)
    )
    lessons = (
        await db[COL_LESSONS]
        .find({"course_id": course_id})
        .sort([("sort_order", 1), ("created_at", 1)])
        .to_list(length=2000)
    )
    by_level: Dict[str, List[dict]] = {}
    for les in lessons:
        by_level.setdefault(str(les.get("level_id")), []).append(_lesson_admin(les))

    out = []
    for lv in levels:
        item = _public(lv)
        item["lessons"] = by_level.get(str(lv.get("id")), [])
        item["lessons_count"] = len(item["lessons"])
        out.append(item)
    return {"course_id": course_id, "levels": out, "total_levels": len(out)}


async def create_level(
    course_id: str, body: LearningLevelCreate, *, current_user: dict
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_learning_hierarchy_indexes(db)
    await _get_course(db, course_id)

    now = datetime.utcnow()
    sort_order = body.sort_order
    if sort_order is None:
        sort_order = await _next_sort_order(db, COL_LEVELS, {"course_id": course_id})
    status = body.status.value if isinstance(body.status, LearningItemStatus) else str(body.status)

    doc = {
        "id": str(uuid.uuid4()),
        "course_id": course_id,
        "title": body.title.strip(),
        "description": body.description,
        "sort_order": sort_order,
        "status": status,
        "created_at": now,
        "updated_at": now,
        "created_by": current_user.get("id"),
        "updated_by": current_user.get("id"),
    }
    await db[COL_LEVELS].insert_one(doc)
    await _refresh_course_counts(db, course_id)
    return {"message": "Level created", "level": _public(doc)}


async def update_level(
    course_id: str,
    level_id: str,
    body: LearningLevelUpdate,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL_LEVELS].find_one({"id": level_id, "course_id": course_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Level not found")

    data = body.model_dump(exclude_unset=True)
    patch: Dict[str, Any] = {
        "updated_at": datetime.utcnow(),
        "updated_by": current_user.get("id"),
    }
    if "title" in data and data["title"] is not None:
        patch["title"] = data["title"]
    if "description" in data:
        patch["description"] = data["description"]
    if "sort_order" in data and data["sort_order"] is not None:
        patch["sort_order"] = data["sort_order"]
    if "status" in data and data["status"] is not None:
        st = data["status"].value if hasattr(data["status"], "value") else str(data["status"])
        if st not in LEARNING_ITEM_STATUSES:
            raise HTTPException(status_code=400, detail=f"Invalid status: {st}")
        patch["status"] = st

    await db[COL_LEVELS].update_one({"id": level_id}, {"$set": patch})
    updated = await db[COL_LEVELS].find_one({"id": level_id})
    await _refresh_course_counts(db, course_id)
    return {"message": "Level updated", "level": _public(updated)}


async def delete_level(
    course_id: str, level_id: str, *, current_user: dict
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL_LEVELS].find_one({"id": level_id, "course_id": course_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Level not found")
    now = datetime.utcnow()
    await db[COL_LEVELS].update_one(
        {"id": level_id},
        {
            "$set": {
                "status": LearningItemStatus.ARCHIVED.value,
                "updated_at": now,
                "updated_by": current_user.get("id"),
            }
        },
    )
    await db[COL_LESSONS].update_many(
        {"level_id": level_id, "course_id": course_id},
        {
            "$set": {
                "status": LearningItemStatus.ARCHIVED.value,
                "updated_at": now,
            }
        },
    )
    await _refresh_course_counts(db, course_id)
    updated = await db[COL_LEVELS].find_one({"id": level_id})
    return {"message": "Level archived", "level": _public(updated)}


async def reorder_levels(
    course_id: str, ordered_ids: List[str], *, current_user: dict
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await _get_course(db, course_id)
    now = datetime.utcnow()
    for idx, lid in enumerate(ordered_ids):
        await db[COL_LEVELS].update_one(
            {"id": lid, "course_id": course_id},
            {
                "$set": {
                    "sort_order": (idx + 1) * 10,
                    "updated_at": now,
                    "updated_by": current_user.get("id"),
                }
            },
        )
    return await list_levels_admin(course_id)


# ----- Lessons -----


async def create_lesson(
    course_id: str,
    level_id: str,
    body: LearningLessonCreate,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_learning_hierarchy_indexes(db)
    await _get_course(db, course_id)
    level = await db[COL_LEVELS].find_one({"id": level_id, "course_id": course_id})
    if not level:
        raise HTTPException(status_code=404, detail="Level not found")

    now = datetime.utcnow()
    sort_order = body.sort_order
    if sort_order is None:
        sort_order = await _next_sort_order(
            db, COL_LESSONS, {"level_id": level_id, "course_id": course_id}
        )
    status = body.status.value if isinstance(body.status, LearningItemStatus) else str(body.status)

    doc = {
        "id": str(uuid.uuid4()),
        "course_id": course_id,
        "level_id": level_id,
        "title": body.title.strip(),
        "description": body.description,
        "sort_order": sort_order,
        "status": status,
        "video_url": body.video_url,
        "video_storage_key": body.video_storage_key,
        "duration_seconds": body.duration_seconds,
        "is_preview": bool(body.is_preview),
        "created_at": now,
        "updated_at": now,
        "created_by": current_user.get("id"),
        "updated_by": current_user.get("id"),
    }
    await db[COL_LESSONS].insert_one(doc)
    await _refresh_course_counts(db, course_id)
    return {"message": "Lesson created", "lesson": _lesson_admin(doc)}


async def update_lesson(
    course_id: str,
    level_id: str,
    lesson_id: str,
    body: LearningLessonUpdate,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL_LESSONS].find_one(
        {"id": lesson_id, "level_id": level_id, "course_id": course_id}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Lesson not found")

    data = body.model_dump(exclude_unset=True)
    patch: Dict[str, Any] = {
        "updated_at": datetime.utcnow(),
        "updated_by": current_user.get("id"),
    }
    for key in (
        "title",
        "description",
        "sort_order",
        "video_url",
        "video_storage_key",
        "duration_seconds",
        "is_preview",
    ):
        if key in data:
            patch[key] = data[key]
    if "status" in data and data["status"] is not None:
        st = data["status"].value if hasattr(data["status"], "value") else str(data["status"])
        if st not in LEARNING_ITEM_STATUSES:
            raise HTTPException(status_code=400, detail=f"Invalid status: {st}")
        patch["status"] = st

    await db[COL_LESSONS].update_one({"id": lesson_id}, {"$set": patch})
    updated = await db[COL_LESSONS].find_one({"id": lesson_id})
    await _refresh_course_counts(db, course_id)
    return {"message": "Lesson updated", "lesson": _lesson_admin(updated)}


async def delete_lesson(
    course_id: str,
    level_id: str,
    lesson_id: str,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL_LESSONS].find_one(
        {"id": lesson_id, "level_id": level_id, "course_id": course_id}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Lesson not found")
    await db[COL_LESSONS].update_one(
        {"id": lesson_id},
        {
            "$set": {
                "status": LearningItemStatus.ARCHIVED.value,
                "updated_at": datetime.utcnow(),
                "updated_by": current_user.get("id"),
            }
        },
    )
    await _refresh_course_counts(db, course_id)
    updated = await db[COL_LESSONS].find_one({"id": lesson_id})
    return {"message": "Lesson archived", "lesson": _lesson_admin(updated)}


async def reorder_lessons(
    course_id: str,
    level_id: str,
    ordered_ids: List[str],
    *,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    level = await db[COL_LEVELS].find_one({"id": level_id, "course_id": course_id})
    if not level:
        raise HTTPException(status_code=404, detail="Level not found")
    now = datetime.utcnow()
    for idx, lid in enumerate(ordered_ids):
        await db[COL_LESSONS].update_one(
            {"id": lid, "level_id": level_id, "course_id": course_id},
            {
                "$set": {
                    "sort_order": (idx + 1) * 10,
                    "updated_at": now,
                    "updated_by": current_user.get("id"),
                }
            },
        )
    lessons = (
        await db[COL_LESSONS]
        .find({"level_id": level_id, "course_id": course_id})
        .sort([("sort_order", 1)])
        .to_list(length=500)
    )
    return {
        "message": "Lessons reordered",
        "lessons": [_lesson_admin(x) for x in lessons],
    }


async def get_public_curriculum(course_id: str) -> Dict[str, Any]:
    """Published levels/lessons only; no video URLs."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    levels = (
        await db[COL_LEVELS]
        .find(
            {
                "course_id": course_id,
                "status": LearningItemStatus.PUBLISHED.value,
            }
        )
        .sort([("sort_order", 1)])
        .to_list(length=200)
    )
    lessons = (
        await db[COL_LESSONS]
        .find(
            {
                "course_id": course_id,
                "status": LearningItemStatus.PUBLISHED.value,
            }
        )
        .sort([("sort_order", 1)])
        .to_list(length=2000)
    )
    by_level: Dict[str, List[dict]] = {}
    for les in lessons:
        by_level.setdefault(str(les.get("level_id")), []).append(
            _lesson_public_outline(les)
        )

    curriculum = []
    for lv in levels:
        curriculum.append(
            {
                "id": lv.get("id"),
                "title": lv.get("title"),
                "description": lv.get("description"),
                "sort_order": lv.get("sort_order", 0),
                "lessons": by_level.get(str(lv.get("id")), []),
            }
        )
    return {
        "course_id": course_id,
        "curriculum": curriculum,
        "levels_count": len(curriculum),
        "lessons_count": sum(len(x["lessons"]) for x in curriculum),
    }
