"""M16-S01 Online Learning course catalogue & enrollment routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, status

from models.learning_course_models import (
    CreateLearningEnrollmentOrderBody,
    LearningCourseCreate,
    LearningCourseEnrollmentCreate,
    LearningCourseUpdate,
)
from models.user_models import UserRole
from utils.unified_auth import get_optional_current_user_or_superadmin, require_role_unified
from utils.learning_course_service import (
    create_learning_course,
    create_learning_enrollment,
    create_learning_enrollment_order,
    delete_learning_course,
    get_learning_catalogue_detail,
    get_learning_course_admin,
    get_learning_enrollment_stats,
    list_learning_catalogue,
    list_learning_courses_admin,
    list_learning_enrollments_admin,
    update_learning_course,
)

router = APIRouter()

_ADMIN = [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN]


# Public catalogue routes
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


# Public enrollment routes (Razorpay checkout & submission)
@router.post("/enrollment/create-order")
async def api_create_enrollment_order(
    body: CreateLearningEnrollmentOrderBody,
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """Public — create Razorpay payment order for course enrollment."""
    return await create_learning_enrollment_order(body, current_user=current_user)


@router.post("/enrollment", status_code=status.HTTP_201_CREATED)
async def api_submit_enrollment(
    body: LearningCourseEnrollmentCreate,
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """Public — submit course enrollment form with verified payment."""
    return await create_learning_enrollment(body, current_user=current_user)


# Admin enrollment routes (must be defined BEFORE /{course_id} to avoid slug collision)
@router.get("/enrollments/stats")
async def admin_enrollment_stats(
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await get_learning_enrollment_stats()


@router.get("/enrollments")
async def admin_list_all_enrollments(
    course_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await list_learning_enrollments_admin(
        course_id=course_id, search=search, skip=skip, limit=limit
    )


# Admin course management routes
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


@router.get("/{course_id}/enrollments")
async def admin_list_course_enrollments(
    course_id: str,
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """Get all enrollment submissions specifically for this course."""
    return await list_learning_enrollments_admin(
        course_id=course_id, search=search, skip=skip, limit=limit
    )


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
