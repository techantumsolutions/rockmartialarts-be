from typing import Optional

from fastapi import APIRouter, Depends, Query

from controllers.billing_controller import BillingController
from models.user_models import UserRole
from utils.unified_auth import require_role_unified

router = APIRouter()


@router.get("")
async def list_billing_cycles(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    student_id: Optional[str] = None,
    enrollment_id: Optional[str] = None,
    status: Optional[str] = None,
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.BRANCH_MANAGER, UserRole.STUDENT]
        )
    ),
):
    return await BillingController.list_cycles(
        current_user,
        skip=skip,
        limit=limit,
        student_id=student_id,
        enrollment_id=enrollment_id,
        status=status,
    )


@router.get("/enrollment/{enrollment_id}")
async def get_billing_for_enrollment(
    enrollment_id: str,
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.BRANCH_MANAGER, UserRole.STUDENT]
        )
    ),
):
    return await BillingController.get_for_enrollment(enrollment_id, current_user)


@router.get("/{cycle_id}")
async def get_billing_cycle(
    cycle_id: str,
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.BRANCH_MANAGER, UserRole.STUDENT]
        )
    ),
):
    return await BillingController.get_cycle(cycle_id, current_user)
