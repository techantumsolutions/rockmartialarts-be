from fastapi import APIRouter, Depends, Query
from typing import Optional

from controllers.lead_controller import LeadController
from models.lead_models import LeadCreate, LeadOtpSendBody, LeadOtpVerifyBody, LeadResponse, LeadStatusUpdate
from models.user_models import UserRole
from utils.unified_auth import require_role_unified

router = APIRouter()


@router.post("/send-otp")
async def send_lead_otp(body: LeadOtpSendBody):
    """Public: SMS OTP for website lead popup. Must be registered before /{id} routes."""
    return await LeadController.send_otp(body.phone)


@router.post("/verify-otp")
async def verify_lead_otp(body: LeadOtpVerifyBody):
    """Public: verify lead popup OTP. Returns verification_token (scope lead_popup only)."""
    return await LeadController.verify_otp(body)


@router.post("", response_model=LeadResponse, status_code=201)
async def create_lead(payload: LeadCreate):
    """Public: capture registration / interest leads (no auth)."""
    return await LeadController.create_lead(payload)


@router.get("")
async def list_leads(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = None,
    status: Optional[str] = Query(None, description="Filter by status: new, contacted, qualified, converted, lost"),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Super Admin: list leads with optional search, status filter, and pagination."""
    return await LeadController.list_leads(skip=skip, limit=limit, search=search, status=status)


@router.patch("/{lead_id}", response_model=LeadResponse)
async def update_lead_status(
    lead_id: str,
    body: LeadStatusUpdate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Super Admin: update lead pipeline status."""
    return await LeadController.update_status(lead_id, body)
