from fastapi import APIRouter, Depends, Query
from controllers.branch_course_controller import BranchCourseController
from models.branch_course_models import BranchCourseUpsert, BranchCourseUpdate
from models.user_models import UserRole
from utils.unified_auth import require_role_unified, get_current_user_or_superadmin

router = APIRouter()


@router.post("")
async def upsert_branch_course(
    body: BranchCourseUpsert,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.BRANCH_MANAGER])),
):
    """Assign or update a course mapping for a branch."""
    return await BranchCourseController.upsert_mapping(body, current_user)


@router.put("/{branch_id}/{course_id}")
async def update_branch_course(
    branch_id: str,
    course_id: str,
    body: BranchCourseUpdate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.BRANCH_MANAGER])),
):
    """Update availability or fees for an existing branch-course pair."""
    return await BranchCourseController.update_mapping(branch_id, course_id, body, current_user)


@router.get("/by-branch/{branch_id}")
async def get_branch_courses(
    branch_id: str,
    available_only: bool = Query(False),
    current_user: dict = Depends(get_current_user_or_superadmin),
):
    """Retrieve courses mapped to a branch."""
    return await BranchCourseController.get_by_branch(branch_id, available_only, current_user)


@router.get("/by-course/{course_id}")
async def get_course_branches(
    course_id: str,
    available_only: bool = Query(False),
    current_user: dict = Depends(get_current_user_or_superadmin),
):
    """Retrieve branches that offer a course."""
    return await BranchCourseController.get_by_course(course_id, available_only, current_user)
