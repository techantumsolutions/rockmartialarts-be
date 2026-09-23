"""
M16-S05 Learning progress service.

Tracks last-viewed lesson, per-lesson state, and course % complete.
Additive — does not touch dojo attendance/KPI progress.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from fastapi import HTTPException

from models.learning_progress_models import (
    LearningProgressCompleteBody,
    LearningProgressHeartbeatBody,
)
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.learning_access_service import evaluate_access
from utils.learning_subscription_service import has_active_learning_access

logger = logging.getLogger(__name__)

COL = "learning_progress"
COL_COURSES = "learning_courses"
COL_LESSONS = "learning_lessons"
COL_LEVELS = "learning_levels"

# Auto-complete when viewer reaches this fraction of duration
COMPLETE_THRESHOLD = 0.9


def _role(user: Optional[dict]) -> str:
    return str((user or {}).get("role") or "").lower().replace(" ", "_")


def _is_admin(user: Optional[dict]) -> bool:
    return _role(user) in {
        "superadmin",
        "super_admin",
        "coach_admin",
        "coachadmin",
    }


async def ensure_learning_progress_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL].create_index("id", unique=True)
        await database[COL].create_index(
            [("user_id", 1), ("course_id", 1)], unique=True
        )
        await database[COL].create_index([("user_id", 1), ("updated_at", -1)])
    except Exception:
        logger.exception("Failed ensuring learning progress indexes")


async def _published_lesson_ids(db, course_id: str) -> List[str]:
    rows = (
        await db[COL_LESSONS]
        .find({"course_id": course_id, "status": "published"})
        .sort([("sort_order", 1)])
        .to_list(length=2000)
    )
    return [str(r["id"]) for r in rows if r.get("id")]


def _compute_percent(completed: Set[str], total_ids: List[str]) -> float:
    if not total_ids:
        return 0.0
    done = sum(1 for lid in total_ids if lid in completed)
    return round(100.0 * done / len(total_ids), 1)


def _public_progress(doc: dict, *, total_lessons: int = 0) -> Dict[str, Any]:
    out = serialize_doc(doc) or {}
    # Drop bulky nested noise for list views if needed — keep lesson_states for detail
    out["total_lessons"] = total_lessons or out.get("total_lessons") or 0
    out["completed_count"] = len(out.get("completed_lesson_ids") or [])
    return out


async def _get_or_create(db, user_id: str, course_id: str) -> dict:
    doc = await db[COL].find_one({"user_id": user_id, "course_id": course_id})
    if doc:
        return doc
    import uuid

    now = datetime.utcnow()
    doc = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "course_id": course_id,
        "last_lesson_id": None,
        "last_position_seconds": 0.0,
        "completed_lesson_ids": [],
        "lesson_states": {},
        "progress_percent": 0.0,
        "total_lessons": 0,
        "created_at": now,
        "updated_at": now,
    }
    try:
        await db[COL].insert_one(doc)
    except Exception:
        # Race on unique index
        existing = await db[COL].find_one(
            {"user_id": user_id, "course_id": course_id}
        )
        if existing:
            return existing
        raise
    return doc


async def _assert_can_track(
    db, *, user_id: str, course_id: str, lesson: dict, current_user: dict
) -> None:
    if _is_admin(current_user):
        return
    if current_user.get("id") != user_id:
        raise HTTPException(status_code=403, detail="Not your progress")
    access = await evaluate_access(lesson=lesson, current_user=current_user)
    if not access.get("allowed"):
        raise HTTPException(
            status_code=int(access.get("status_code") or 403),
            detail=access.get("detail") or "Not allowed to track this lesson",
        )


async def list_my_progress(*, current_user: dict) -> Dict[str, Any]:
    if _role(current_user) not in {"student"} and not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Students only")
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_learning_progress_indexes(db)
    user_id = current_user["id"]
    rows = (
        await db[COL]
        .find({"user_id": user_id})
        .sort([("updated_at", -1)])
        .to_list(length=100)
    )
    out = []
    for row in rows:
        course = await db[COL_COURSES].find_one({"id": row.get("course_id")})
        total_ids = await _published_lesson_ids(db, row["course_id"])
        completed = set(row.get("completed_lesson_ids") or [])
        percent = _compute_percent(completed, total_ids)
        item = _public_progress(row, total_lessons=len(total_ids))
        item["progress_percent"] = percent
        item["course"] = {
            "id": (course or {}).get("id"),
            "title": (course or {}).get("title"),
            "slug": (course or {}).get("slug"),
            "thumbnail_url": (course or {}).get("thumbnail_url"),
            "status": (course or {}).get("status"),
        }
        # Resume hint
        last_id = row.get("last_lesson_id")
        last_lesson = None
        if last_id:
            les = await db[COL_LESSONS].find_one({"id": last_id})
            if les:
                last_lesson = {
                    "id": les.get("id"),
                    "title": les.get("title"),
                    "is_preview": bool(les.get("is_preview")),
                }
        item["last_lesson"] = last_lesson
        item["resume"] = {
            "lesson_id": last_id,
            "position_seconds": float(row.get("last_position_seconds") or 0),
            "available": bool(last_id),
        }
        # entitlement snapshot
        access = await has_active_learning_access(user_id, row["course_id"])
        item["entitled"] = bool(access.get("entitled")) or _is_admin(current_user)
        out.append(item)

    return {"progress": out, "total": len(out)}


async def get_course_progress(
    course_id: str, *, current_user: dict
) -> Dict[str, Any]:
    if _role(current_user) not in {"student"} and not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Students only")
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    course = await db[COL_COURSES].find_one({"id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    user_id = current_user["id"]
    doc = await db[COL].find_one({"user_id": user_id, "course_id": course_id})
    total_ids = await _published_lesson_ids(db, course_id)
    if not doc:
        return {
            "course_id": course_id,
            "course": {
                "id": course.get("id"),
                "title": course.get("title"),
                "slug": course.get("slug"),
                "thumbnail_url": course.get("thumbnail_url"),
            },
            "progress_percent": 0.0,
            "completed_lesson_ids": [],
            "completed_count": 0,
            "total_lessons": len(total_ids),
            "last_lesson_id": None,
            "last_position_seconds": 0,
            "lesson_states": {},
            "resume": {"lesson_id": None, "position_seconds": 0, "available": False},
            "entitled": bool(
                (await has_active_learning_access(user_id, course_id)).get("entitled")
            )
            or _is_admin(current_user),
        }

    completed = set(doc.get("completed_lesson_ids") or [])
    percent = _compute_percent(completed, total_ids)
    # Keep denorm in sync when lesson catalogue changes
    if abs(float(doc.get("progress_percent") or 0) - percent) > 0.05 or int(
        doc.get("total_lessons") or 0
    ) != len(total_ids):
        await db[COL].update_one(
            {"id": doc["id"]},
            {
                "$set": {
                    "progress_percent": percent,
                    "total_lessons": len(total_ids),
                    "updated_at": datetime.utcnow(),
                }
            },
        )
        doc["progress_percent"] = percent
        doc["total_lessons"] = len(total_ids)

    out = _public_progress(doc, total_lessons=len(total_ids))
    out["course"] = {
        "id": course.get("id"),
        "title": course.get("title"),
        "slug": course.get("slug"),
        "thumbnail_url": course.get("thumbnail_url"),
    }
    out["resume"] = {
        "lesson_id": doc.get("last_lesson_id"),
        "position_seconds": float(doc.get("last_position_seconds") or 0),
        "available": bool(doc.get("last_lesson_id")),
    }
    access = await has_active_learning_access(user_id, course_id)
    out["entitled"] = bool(access.get("entitled")) or _is_admin(current_user)
    return out


async def get_resume(course_id: str, *, current_user: dict) -> Dict[str, Any]:
    detail = await get_course_progress(course_id, current_user=current_user)
    resume = detail.get("resume") or {}
    lesson = None
    if resume.get("lesson_id"):
        db = get_db()
        les = await db[COL_LESSONS].find_one({"id": resume["lesson_id"]})
        if les:
            lesson = {
                "id": les.get("id"),
                "title": les.get("title"),
                "level_id": les.get("level_id"),
                "is_preview": bool(les.get("is_preview")),
            }
    return {
        "course_id": course_id,
        "resume": resume,
        "lesson": lesson,
        "progress_percent": detail.get("progress_percent"),
    }


async def heartbeat(
    course_id: str,
    body: LearningProgressHeartbeatBody,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    if _role(current_user) not in {"student"} and not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Students only")
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_learning_progress_indexes(db)

    lesson = await db[COL_LESSONS].find_one(
        {"id": body.lesson_id, "course_id": course_id, "status": "published"}
    )
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")

    user_id = current_user["id"]
    await _assert_can_track(
        db, user_id=user_id, course_id=course_id, lesson=lesson, current_user=current_user
    )

    doc = await _get_or_create(db, user_id, course_id)
    now = datetime.utcnow()
    states: Dict[str, Any] = dict(doc.get("lesson_states") or {})
    prev = dict(states.get(body.lesson_id) or {})

    position = body.position_seconds
    if position is None:
        position = float(prev.get("position_seconds") or 0)
    position = float(max(0, position))

    duration = body.duration_seconds
    if duration is None:
        duration = lesson.get("duration_seconds")
    if duration is not None:
        duration = float(duration)

    completed_ids: List[str] = list(doc.get("completed_lesson_ids") or [])
    completed_set = set(completed_ids)
    was_complete = body.lesson_id in completed_set or bool(prev.get("completed"))

    mark_complete = bool(body.completed) or was_complete
    if (
        not mark_complete
        and duration
        and duration > 0
        and position >= COMPLETE_THRESHOLD * duration
    ):
        mark_complete = True

    if mark_complete and body.lesson_id not in completed_set:
        completed_ids.append(body.lesson_id)
        completed_set.add(body.lesson_id)

    lesson_percent = 0.0
    if mark_complete:
        lesson_percent = 100.0
    elif duration and duration > 0:
        lesson_percent = round(min(100.0, 100.0 * position / duration), 1)

    states[body.lesson_id] = {
        "lesson_id": body.lesson_id,
        "position_seconds": position,
        "duration_seconds": duration,
        "percent": lesson_percent,
        "completed": mark_complete,
        "updated_at": now.isoformat(),
    }

    total_ids = await _published_lesson_ids(db, course_id)
    course_percent = _compute_percent(completed_set, total_ids)

    course_doc = await db[COL_COURSES].find_one({"id": course_id}) or {}
    patch = {
        "last_lesson_id": body.lesson_id,
        "last_position_seconds": position,
        "completed_lesson_ids": completed_ids,
        "lesson_states": states,
        "progress_percent": course_percent,
        "total_lessons": len(total_ids),
        "updated_at": now,
        "course_title": course_doc.get("title"),
        "course_slug": course_doc.get("slug"),
    }
    await db[COL].update_one({"id": doc["id"]}, {"$set": patch})
    updated = await db[COL].find_one({"id": doc["id"]})
    return {
        "message": "Progress saved",
        "progress": _public_progress(updated, total_lessons=len(total_ids)),
        "lesson_state": states[body.lesson_id],
    }


async def complete_lesson(
    course_id: str,
    body: LearningProgressCompleteBody,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    return await heartbeat(
        course_id,
        LearningProgressHeartbeatBody(
            lesson_id=body.lesson_id,
            completed=True,
            position_seconds=None,
            duration_seconds=None,
        ),
        current_user=current_user,
    )
