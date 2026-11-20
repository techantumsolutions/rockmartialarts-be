from typing import Optional

from fastapi import APIRouter, Depends, Query

from models.student_content_models import StudentTestimonialCreate, StudentTestimonialUpdate
from models.user_models import UserRole
from utils.unified_auth import require_role_unified
from controllers import student_testimonial_controller as ctrl

router = APIRouter()


@router.get("")
async def get_testimonials_public(
    branch_id: Optional[str] = Query(None),
    is_global: Optional[bool] = Query(None),
    limit: int = Query(10, ge=1, le=50),
):
    """Public active testimonials.

    Site-wide (no branch_id): all academy testimonials with academy_name.
    Academy page: pass branch_id for that academy only.
    Legacy: is_global=true still returns global-only rows.
    """
    items = await ctrl.list_public(branch_id=branch_id, is_global=is_global, limit=limit)
    return {"testimonials": items}


@router.get("/manage")
async def list_testimonials_manage(
    status: Optional[str] = Query(None, description="active or inactive"),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.BRANCH_MANAGER])),
):
    items = await ctrl.list_manage(current_user, status=status)
    return {"testimonials": items}


@router.post("")
async def create_testimonial(
    data: StudentTestimonialCreate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.BRANCH_MANAGER])),
):
    return await ctrl.create(data, current_user)


@router.put("/{testimonial_id}")
async def update_testimonial(
    testimonial_id: str,
    data: StudentTestimonialUpdate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.BRANCH_MANAGER])),
):
    return await ctrl.update(testimonial_id, data, current_user)


@router.delete("/{testimonial_id}")
async def delete_testimonial(
    testimonial_id: str,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.BRANCH_MANAGER])),
):
    return await ctrl.delete_testimonial(testimonial_id, current_user)
