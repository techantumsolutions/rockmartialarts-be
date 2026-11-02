from typing import Optional

from fastapi import APIRouter, Depends, Query

from models.student_content_models import StudentShowcaseAchievementCreate, StudentShowcaseAchievementUpdate
from models.user_models import UserRole
from utils.unified_auth import require_role_unified
from controllers import student_showcase_achievement_controller as ctrl

router = APIRouter()


@router.get("")
async def get_showcase_achievements_public(
    branch_id: Optional[str] = Query(None),
    course_id: Optional[str] = Query(None),
    is_global: Optional[bool] = Query(None),
    limit: int = Query(10, ge=1, le=50),
    skip: int = Query(0, ge=0, le=500),
):
    """Public showcase achievements (marketing). Not the enrollment student_achievements API."""
    items = await ctrl.list_public(
        branch_id=branch_id,
        course_id=course_id,
        is_global=is_global,
        limit=limit,
        skip=skip,
    )
    return {"achievements": items}


_SHOWCASE_MANAGE_ROLES = [
    UserRole.SUPER_ADMIN,
    UserRole.BRANCH_MANAGER,
    UserRole.PARTNER_SUPER_ADMIN,
]


@router.get("/manage")
async def list_showcase_manage(
    status: Optional[str] = Query(None),
    current_user: dict = Depends(require_role_unified(_SHOWCASE_MANAGE_ROLES)),
):
    items = await ctrl.list_manage(current_user, status=status)
    return {"achievements": items}


@router.post("")
async def create_showcase_achievement(
    data: StudentShowcaseAchievementCreate,
    current_user: dict = Depends(require_role_unified(_SHOWCASE_MANAGE_ROLES)),
):
    return await ctrl.create(data, current_user)


@router.put("/{achievement_id}")
async def update_showcase_achievement(
    achievement_id: str,
    data: StudentShowcaseAchievementUpdate,
    current_user: dict = Depends(require_role_unified(_SHOWCASE_MANAGE_ROLES)),
):
    return await ctrl.update(achievement_id, data, current_user)


@router.delete("/{achievement_id}")
async def delete_showcase_achievement(
    achievement_id: str,
    current_user: dict = Depends(require_role_unified(_SHOWCASE_MANAGE_ROLES)),
):
    return await ctrl.delete_showcase(achievement_id, current_user)
