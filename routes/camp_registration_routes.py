from typing import Optional

from fastapi import APIRouter, Depends, File, Query, UploadFile

from controllers.camp_registration_controller import CampRegistrationController
from models.camp_registration_models import CampRegistrationCreate, CampRegistrationResponse, CampRegVerifyPayment
from models.user_models import UserRole
from utils.unified_auth import require_role_unified

router = APIRouter()


@router.post("/screenshot")
async def upload_camp_registration_screenshot(file: UploadFile = File(...)):
    """Public: payment screenshot for camp registration."""
    return await CampRegistrationController.upload_screenshot(file)


@router.post("", response_model=CampRegistrationResponse, status_code=201)
async def create_camp_registration(payload: CampRegistrationCreate):
    """Public: submit residential camp registration draft (pending payment)."""
    return await CampRegistrationController.create(payload)


@router.post("/{reg_id}/create-order")
async def create_camp_payment_order(reg_id: str):
    """Public: Razorpay order for 50% camp fee."""
    return await CampRegistrationController.create_order(reg_id)


@router.post("/{reg_id}/verify-payment")
async def verify_camp_payment(reg_id: str, body: CampRegVerifyPayment):
    """Public: verify Razorpay payment and mark registration paid."""
    return await CampRegistrationController.verify_payment(reg_id, body)


@router.get("")
async def list_camp_registrations(
    skip: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=200),
    search: Optional[str] = None,
    event_id: Optional[str] = None,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Super-admin: list camp registrations."""
    return await CampRegistrationController.list_registrations(
        skip=skip, limit=limit, search=search, event_id=event_id
    )


@router.get("/{reg_id}", response_model=CampRegistrationResponse)
async def get_camp_registration(
    reg_id: str,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Super-admin: full camp registration."""
    return await CampRegistrationController.get_one(reg_id)
