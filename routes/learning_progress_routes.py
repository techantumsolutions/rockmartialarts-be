"""M16-S05 Learning progress routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from models.learning_progress_models import (
    LearningProgressCompleteBody,
    LearningProgressHeartbeatBody,
)
from models.user_models import UserRole
from utils.unified_auth import require_role_unified
from utils.learning_progress_service import (
    complete_lesson,
    get_course_progress,
    get_resume,
    heartbeat,
    list_my_progress,
)

router = APIRouter()

_STUDENT_ADMIN = [
    UserRole.STUDENT,
    UserRole.SUPER_ADMIN,
    UserRole.COACH_ADMIN,
]


@router.get("/me")
async def api_list_my_progress(
    current_user: dict = Depends(require_role_unified(_STUDENT_ADMIN)),
):
    return await list_my_progress(current_user=current_user)


@router.get("/me/courses/{course_id}")
async def api_course_progress(
    course_id: str,
    current_user: dict = Depends(require_role_unified(_STUDENT_ADMIN)),
):
    return await get_course_progress(course_id, current_user=current_user)


@router.get("/me/courses/{course_id}/resume")
async def api_resume(
    course_id: str,
    current_user: dict = Depends(require_role_unified(_STUDENT_ADMIN)),
):
    return await get_resume(course_id, current_user=current_user)


@router.post("/me/courses/{course_id}/heartbeat")
async def api_heartbeat(
    course_id: str,
    body: LearningProgressHeartbeatBody,
    current_user: dict = Depends(require_role_unified(_STUDENT_ADMIN)),
):
    return await heartbeat(course_id, body, current_user=current_user)


@router.post("/me/courses/{course_id}/complete-lesson")
async def api_complete(
    course_id: str,
    body: LearningProgressCompleteBody,
    current_user: dict = Depends(require_role_unified(_STUDENT_ADMIN)),
):
    return await complete_lesson(course_id, body, current_user=current_user)
