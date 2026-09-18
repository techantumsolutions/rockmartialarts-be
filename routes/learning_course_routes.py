"""M16-S01 Online Learning course catalogue routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from models.learning_course_models import LearningCourseCreate, LearningCourseUpdate
from models.user_models import UserRole
from utils.unified_auth import require_role_unified
from utils.learning_course_service import (
    create_learning_course,
    delete_learning_course,
    get_learning_catalogue_detail,
    get_learning_course_admin,
    list_learning_catalogue,
    list_learning_courses_admin,
    update_learning_course,
)

router = APIRouter()

_ADMIN = [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN]


@router.get("/public")
async def public_catalogue(
    search: Optional[str] = Query(None),
    difficulty: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    """Public — published online learning courses only."""
    return await list_learning_catalogue(
        search=search, difficulty=difficulty, skip=skip, limit=limit
    )


@router.get("/public/{slug_or_id}")
async def public_detail(slug_or_id: str):
    """Public — published course detail by slug or id."""
    return await get_learning_catalogue_detail(slug_or_id)


@router.get("")
async def admin_list(
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await list_learning_courses_admin(
        status=status, search=search, skip=skip, limit=limit
    )


@router.post("", status_code=201)
async def admin_create(
    body: LearningCourseCreate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await create_learning_course(body, current_user=current_user)


@router.get("/{course_id}")
async def admin_get(
    course_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await get_learning_course_admin(course_id)


@router.patch("/{course_id}")
async def admin_update(
    course_id: str,
    body: LearningCourseUpdate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await update_learning_course(course_id, body, current_user=current_user)


@router.delete("/{course_id}")
async def admin_delete(
    course_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await delete_learning_course(course_id, current_user=current_user)
