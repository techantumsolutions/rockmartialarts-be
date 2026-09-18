"""M16-S02 Learning hierarchy routes (nested under /api/learning-courses)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from models.learning_hierarchy_models import (
    LearningLessonCreate,
    LearningLessonUpdate,
    LearningLevelCreate,
    LearningLevelUpdate,
    ReorderBody,
)
from models.user_models import UserRole
from utils.unified_auth import require_role_unified
from utils.learning_hierarchy_service import (
    create_lesson,
    create_level,
    delete_lesson,
    delete_level,
    list_levels_admin,
    reorder_lessons,
    reorder_levels,
    update_lesson,
    update_level,
)

hierarchy_router = APIRouter()

_ADMIN = [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN]


@hierarchy_router.get("/{course_id}/levels")
async def admin_list_levels(
    course_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await list_levels_admin(course_id)


@hierarchy_router.post("/{course_id}/levels", status_code=201)
async def admin_create_level(
    course_id: str,
    body: LearningLevelCreate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await create_level(course_id, body, current_user=current_user)


# Static path segments before {level_id} to avoid shadowing
@hierarchy_router.post("/{course_id}/levels/reorder")
async def admin_reorder_levels(
    course_id: str,
    body: ReorderBody,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await reorder_levels(
        course_id, body.ordered_ids, current_user=current_user
    )


@hierarchy_router.patch("/{course_id}/levels/{level_id}")
async def admin_update_level(
    course_id: str,
    level_id: str,
    body: LearningLevelUpdate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await update_level(course_id, level_id, body, current_user=current_user)


@hierarchy_router.delete("/{course_id}/levels/{level_id}")
async def admin_delete_level(
    course_id: str,
    level_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await delete_level(course_id, level_id, current_user=current_user)


@hierarchy_router.post("/{course_id}/levels/{level_id}/lessons", status_code=201)
async def admin_create_lesson(
    course_id: str,
    level_id: str,
    body: LearningLessonCreate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await create_lesson(
        course_id, level_id, body, current_user=current_user
    )


@hierarchy_router.post("/{course_id}/levels/{level_id}/lessons/reorder")
async def admin_reorder_lessons(
    course_id: str,
    level_id: str,
    body: ReorderBody,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await reorder_lessons(
        course_id, level_id, body.ordered_ids, current_user=current_user
    )


@hierarchy_router.patch("/{course_id}/levels/{level_id}/lessons/{lesson_id}")
async def admin_update_lesson(
    course_id: str,
    level_id: str,
    lesson_id: str,
    body: LearningLessonUpdate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await update_lesson(
        course_id, level_id, lesson_id, body, current_user=current_user
    )


@hierarchy_router.delete("/{course_id}/levels/{level_id}/lessons/{lesson_id}")
async def admin_delete_lesson(
    course_id: str,
    level_id: str,
    lesson_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await delete_lesson(
        course_id, level_id, lesson_id, current_user=current_user
    )
