"""M13 Demo session public routes — availability (S02) + paid booking (S03)."""
from typing import Optional

from fastapi import APIRouter, Depends, Query, status

from models.demo_booking_models import DemoBookingCreate, DemoBookingPaymentVerify
from utils.demo_availability_service import (
    list_demo_availability,
    list_demo_availability_options,
)
from utils.demo_booking_service import (
    create_demo_booking,
    create_demo_booking_payment_order,
    get_demo_booking,
    mark_demo_booking_payment_failed,
    verify_demo_booking_payment,
)
from utils.unified_auth import get_optional_current_user_or_superadmin

router = APIRouter()


@router.get("/options")
async def api_demo_availability_options():
    """Branches/courses with active demo schedules."""
    return await list_demo_availability_options()


@router.get("/availability")
async def api_demo_availability(
    branch_id: Optional[str] = Query(None),
    course_id: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    include_full: bool = Query(False),
):
    """
    Public bookable demo slots generated from active schedules.
    Excludes past slots and (by default) fully booked slots.
    """
    return await list_demo_availability(
        branch_id=branch_id,
        course_id=course_id,
        date_from=date_from,
        date_to=date_to,
        include_full=include_full,
    )


@router.post("/bookings", status_code=status.HTTP_201_CREATED)
async def api_create_demo_booking(
    body: DemoBookingCreate,
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """Public — reserve a demo slot (fee from schedule; payment may follow)."""
    return await create_demo_booking(body, current_user=current_user)


@router.get("/bookings/{booking_id}")
async def api_get_demo_booking(booking_id: str):
    """Public — fetch booking by id (confirmation / resume payment)."""
    return await get_demo_booking(booking_id)


@router.post("/bookings/{booking_id}/create-order")
async def api_demo_booking_create_order(
    booking_id: str,
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """Public — create Razorpay order for pending demo booking (M06)."""
    return await create_demo_booking_payment_order(
        booking_id, current_user=current_user
    )


@router.post("/bookings/{booking_id}/verify-payment")
async def api_demo_booking_verify_payment(
    booking_id: str,
    body: DemoBookingPaymentVerify,
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """Public — verify Razorpay payment and confirm booking."""
    return await verify_demo_booking_payment(
        booking_id, body, current_user=current_user
    )


@router.post("/bookings/{booking_id}/payment-failed")
async def api_demo_booking_payment_failed(booking_id: str):
    """Public — mark checkout dismissed/failed (seat held until TTL)."""
    return await mark_demo_booking_payment_failed(booking_id)
