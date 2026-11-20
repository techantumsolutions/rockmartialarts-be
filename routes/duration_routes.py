from fastapi import APIRouter, Depends, status
from typing import Optional
from controllers.duration_controller import DurationController
from models.duration_models import DurationCreate, DurationUpdate
from models.user_models import UserRole
from utils.auth import require_role, get_current_active_user
from utils.unified_auth import require_role_unified, get_current_user_or_superadmin

router = APIRouter()

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_duration(
    duration_data: DurationCreate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN]))
):
    """Create a new duration - accessible by Super Admin only"""
    return await DurationController.create_duration(duration_data, current_user)

@router.get("")
async def get_durations(
    active_only: bool = True,
    skip: int = 0,
    limit: int = 50,
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get durations with optional filtering - authenticated endpoint"""
    return await DurationController.get_durations(active_only, skip, limit, current_user)

@router.get("/public/all")
async def get_public_durations(
    active_only: bool = True,
    skip: int = 0,
    limit: int = 100
):
    """Get all durations - Public endpoint (no authentication required)"""
    return await DurationController.get_public_durations(active_only, skip, limit)

@router.get("/{duration_id}")
async def get_duration(
    duration_id: str,
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get single duration by ID - authenticated endpoint"""
    return await DurationController.get_duration(duration_id, current_user)

@router.put("/{duration_id}")
async def update_duration(
    duration_id: str,
    duration_update: DurationUpdate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN]))
):
    """Update duration - accessible by Super Admin only"""
    return await DurationController.update_duration(duration_id, duration_update, current_user)

@router.delete("/{duration_id}")
async def delete_duration(
    duration_id: str,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN]))
):
    """Delete duration - accessible by Super Admin only"""
    return await DurationController.delete_duration(duration_id, current_user)

@router.get("/public/by-course/{course_id}")
async def get_durations_by_course(
    course_id: str,
    active_only: bool = True,
    include_pricing: bool = True,
    branch_id: Optional[str] = None,
    batch_ref: Optional[str] = None,
):
    """Get available durations for a specific course - Public endpoint (no authentication required).

    When ``branch_id`` + ``batch_ref`` are provided, tenures follow that batch's
    enabled/fee_per_duration setup from Academy → Course Assignments.
    """
    return await DurationController.get_durations_by_course(
        course_id,
        active_only,
        include_pricing,
        branch_id=branch_id,
        batch_ref=batch_ref,
    )

@router.get("/public/by-location-course")
async def get_durations_by_location_course(
    location_id: str,
    course_id: str,
    include_pricing: bool = True,
    include_branches: bool = False
):
    """Get durations based on location and course combination - Public endpoint (no authentication required)"""
    return await DurationController.get_durations_by_location_course(location_id, course_id, include_pricing, include_branches)
