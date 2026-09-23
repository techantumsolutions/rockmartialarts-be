"""M17 Academy Event Registration routes — S03 book/pay + S04 OTP / mine."""
from typing import Optional

from fastapi import APIRouter, Depends, Header, status
from pydantic import BaseModel, Field

from models.academy_event_registration_models import (
    AcademyEventRegistrationCreate,
    AcademyEventRegistrationPaymentVerify,
)
from utils.academy_event_registration_otp_service import (
    get_academy_event_registration_owned,
    list_my_academy_event_registrations,
    send_event_registration_otp,
    verify_event_registration_otp,
)
from utils.academy_event_registration_service import (
    create_academy_event_registration,
    create_academy_event_registration_payment_order,
    mark_academy_event_registration_payment_failed,
    verify_academy_event_registration_payment,
)
from utils.unified_auth import get_optional_current_user_or_superadmin

router = APIRouter()


class SendOtpBody(BaseModel):
    phone: str = Field(..., min_length=10, max_length=20)


class VerifyOtpBody(BaseModel):
    phone: str = Field(..., min_length=10, max_length=20)
    otp: str = Field(..., min_length=4, max_length=8)


# --- S04 OTP + mine (must be before /{registration_id}) ---


@router.post("/send-otp")
async def api_send_otp(body: SendOtpBody):
    """Public — send OTP to look up event registrations by phone."""
    return await send_event_registration_otp(body.phone)


@router.post("/verify-otp")
async def api_verify_otp(body: VerifyOtpBody):
    """Public — verify OTP; returns verification_token (scope academy_event_registration)."""
    return await verify_event_registration_otp(body.phone, body.otp)


@router.get("/mine")
async def api_list_mine(
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
    x_event_registration_token: Optional[str] = Header(
        None, alias="X-Event-Registration-Token"
    ),
):
    """
    List registrations for verified phone (OTP token) or signed-in account phone.
    Pass X-Event-Registration-Token from verify-otp.
    """
    token = (
        x_event_registration_token.strip()
        if x_event_registration_token and x_event_registration_token.strip()
        else None
    )
    return await list_my_academy_event_registrations(
        verification_token=token,
        current_user=current_user,
    )


# --- S03 create / pay ---


@router.post("", status_code=status.HTTP_201_CREATED)
async def api_create_registration(
    body: AcademyEventRegistrationCreate,
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """Public — register for a published event (fee from event; payment may follow)."""
    return await create_academy_event_registration(body, current_user=current_user)


@router.get("/{registration_id}")
async def api_get_registration(
    registration_id: str,
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
    x_event_registration_token: Optional[str] = Header(
        None, alias="X-Event-Registration-Token"
    ),
):
    """Owned fetch — OTP token or account phone must match participant_phone."""
    token = (
        x_event_registration_token.strip()
        if x_event_registration_token and x_event_registration_token.strip()
        else None
    )
    return await get_academy_event_registration_owned(
        registration_id,
        verification_token=token,
        current_user=current_user,
    )


@router.post("/{registration_id}/create-order")
async def api_create_order(
    registration_id: str,
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """Public — create Razorpay order for pending registration."""
    return await create_academy_event_registration_payment_order(
        registration_id, current_user=current_user
    )


@router.post("/{registration_id}/verify-payment")
async def api_verify_payment(
    registration_id: str,
    body: AcademyEventRegistrationPaymentVerify,
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """Public — verify Razorpay payment and confirm registration."""
    return await verify_academy_event_registration_payment(
        registration_id, body, current_user=current_user
    )


@router.post("/{registration_id}/payment-failed")
async def api_payment_failed(registration_id: str):
    """Public — mark checkout dismissed/failed (seat held until TTL)."""
    return await mark_academy_event_registration_payment_failed(registration_id)
