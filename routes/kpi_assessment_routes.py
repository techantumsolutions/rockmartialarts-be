"""M10-S02 assessment period + student KPI assessment routes."""
from typing import Optional

from fastapi import APIRouter, Depends, Query, status

from models.kpi_assessment_models import (
    AssessmentPeriodCreate,
    AssessmentPeriodUpdate,
    StudentKpiAssessmentUpsert,
)
from models.user_models import UserRole
from utils.kpi_assessment_service import (
    create_period,
    delete_period,
    get_period,
    list_assessments,
    list_eligible_students,
    list_periods,
    update_period,
    upsert_student_assessment,
)
from utils.unified_auth import require_role_unified

periods_router = APIRouter()
assessments_router = APIRouter()

PERIOD_ADMIN = [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN]
ASSESS_ROLES = [
    UserRole.SUPER_ADMIN,
    UserRole.COACH_ADMIN,
    UserRole.BRANCH_MANAGER,
    UserRole.COACH,
]
READ_ROLES = ASSESS_ROLES


# ---- Periods ----


@periods_router.get("")
async def api_list_periods(
    status_filter: Optional[str] = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    current_user: dict = Depends(require_role_unified(READ_ROLES)),
):
    return await list_periods(status=status_filter, skip=skip, limit=limit)


@periods_router.post("", status_code=status.HTTP_201_CREATED)
async def api_create_period(
    body: AssessmentPeriodCreate,
    current_user: dict = Depends(require_role_unified(PERIOD_ADMIN)),
):
    return await create_period(body, current_user=current_user)


@periods_router.get("/{period_id}")
async def api_get_period(
    period_id: str,
    current_user: dict = Depends(require_role_unified(READ_ROLES)),
):
    return await get_period(period_id)


@periods_router.patch("/{period_id}")
async def api_update_period(
    period_id: str,
    body: AssessmentPeriodUpdate,
    current_user: dict = Depends(require_role_unified(PERIOD_ADMIN)),
):
    return await update_period(period_id, body, current_user=current_user)


@periods_router.delete("/{period_id}")
async def api_delete_period(
    period_id: str,
    current_user: dict = Depends(require_role_unified(PERIOD_ADMIN)),
):
    return await delete_period(period_id)


# ---- Assessments ----


@assessments_router.get("/eligible-students")
async def api_eligible_students(
    course_id: str = Query(...),
    branch_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_role_unified(ASSESS_ROLES)),
):
    return await list_eligible_students(
        course_id=course_id,
        branch_id=branch_id,
        current_user=current_user,
    )


@assessments_router.get("")
async def api_list_assessments(
    period_id: Optional[str] = Query(None),
    student_id: Optional[str] = Query(None),
    course_id: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    current_user: dict = Depends(require_role_unified(READ_ROLES)),
):
    return await list_assessments(
        period_id=period_id,
        student_id=student_id,
        course_id=course_id,
        branch_id=branch_id,
        skip=skip,
        limit=limit,
        current_user=current_user,
    )


@assessments_router.post("", status_code=status.HTTP_201_CREATED)
async def api_upsert_assessment(
    body: StudentKpiAssessmentUpsert,
    current_user: dict = Depends(require_role_unified(ASSESS_ROLES)),
):
    return await upsert_student_assessment(body, current_user=current_user)
