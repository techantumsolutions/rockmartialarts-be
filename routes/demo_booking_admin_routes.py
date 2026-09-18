"""M13-S04 Demo booking administration routes (auth required)."""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from models.demo_booking_models import DemoBookingStatusUpdate
from models.user_models import UserRole
from utils.demo_booking_service import (
    export_demo_bookings_admin,
    get_demo_booking_admin,
    list_demo_bookings_admin,
    summarize_demo_bookings_admin,
    update_demo_booking_status_admin,
)
from utils.unified_auth import require_role_unified

router = APIRouter()

ADMIN_ROLES = [
    UserRole.SUPER_ADMIN,
    UserRole.COACH_ADMIN,
    UserRole.BRANCH_MANAGER,
]


@router.get("/summary")
async def api_demo_bookings_summary(
    branch_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await summarize_demo_bookings_admin(
        current_user=current_user, branch_id=branch_id
    )


@router.get("/export")
async def api_export_demo_bookings(
    branch_id: Optional[str] = Query(None),
    course_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    payment_status: Optional[str] = Query(None),
    slot_date_from: Optional[str] = Query(None, alias="from"),
    slot_date_to: Optional[str] = Query(None, alias="to"),
    search: Optional[str] = Query(None),
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await export_demo_bookings_admin(
        current_user=current_user,
        branch_id=branch_id,
        course_id=course_id,
        status=status,
        payment_status=payment_status,
        slot_date_from=slot_date_from,
        slot_date_to=slot_date_to,
        search=search,
    )


@router.get("")
async def api_list_demo_bookings(
    branch_id: Optional[str] = Query(None),
    course_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    payment_status: Optional[str] = Query(None),
    slot_date_from: Optional[str] = Query(None, alias="from"),
    slot_date_to: Optional[str] = Query(None, alias="to"),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await list_demo_bookings_admin(
        current_user=current_user,
        branch_id=branch_id,
        course_id=course_id,
        status=status,
        payment_status=payment_status,
        slot_date_from=slot_date_from,
        slot_date_to=slot_date_to,
        search=search,
        skip=skip,
        limit=limit,
    )


@router.get("/{booking_id}")
async def api_get_demo_booking_admin(
    booking_id: str,
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await get_demo_booking_admin(booking_id, current_user=current_user)


@router.patch("/{booking_id}/status")
async def api_update_demo_booking_status(
    booking_id: str,
    body: DemoBookingStatusUpdate,
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await update_demo_booking_status_admin(
        booking_id, body, current_user=current_user
    )
