"""M13-S01 Demo schedule admin routes — CRUD (availability is under /api/demo-sessions)."""
from typing import Optional

from fastapi import APIRouter, Depends, Query, status

from models.demo_schedule_models import DemoScheduleCreate, DemoScheduleUpdate
from models.user_models import UserRole
from utils.demo_schedule_service import (
    create_demo_schedule,
    delete_demo_schedule,
    get_demo_schedule,
    list_demo_schedules,
    update_demo_schedule,
)
from utils.unified_auth import require_role_unified

router = APIRouter()

ADMIN_ROLES = [
    UserRole.SUPER_ADMIN,
    UserRole.COACH_ADMIN,
    UserRole.BRANCH_MANAGER,
]


@router.get("")
async def api_list_demo_schedules(
    branch_id: Optional[str] = Query(None),
    course_id: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await list_demo_schedules(
        current_user=current_user,
        branch_id=branch_id,
        course_id=course_id,
        is_active=is_active,
        search=search,
        skip=skip,
        limit=limit,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def api_create_demo_schedule(
    body: DemoScheduleCreate,
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await create_demo_schedule(body, current_user=current_user)


@router.get("/{schedule_id}")
async def api_get_demo_schedule(
    schedule_id: str,
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await get_demo_schedule(schedule_id, current_user=current_user)


@router.patch("/{schedule_id}")
async def api_update_demo_schedule(
    schedule_id: str,
    body: DemoScheduleUpdate,
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await update_demo_schedule(schedule_id, body, current_user=current_user)


@router.delete("/{schedule_id}")
async def api_delete_demo_schedule(
    schedule_id: str,
    hard: bool = Query(False),
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await delete_demo_schedule(
        schedule_id, current_user=current_user, hard=hard
    )
