from fastapi import APIRouter, Depends, Query
from typing import Optional

from controllers.lead_controller import LeadController
from models.lead_models import (
    LeadCreate,
    LeadFollowUpCreate,
    LeadOtpSendBody,
    LeadOtpVerifyBody,
    LeadResponse,
    LeadStatusUpdate,
)
from models.user_models import UserRole
from utils.unified_auth import require_role_unified

router = APIRouter()

_LEAD_ADMIN_ROLES = [
    UserRole.SUPER_ADMIN,
    UserRole.COACH_ADMIN,
    UserRole.BRANCH_MANAGER,
]


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
    """Public: capture registration / interest leads (no auth). M15 dedupe applied."""
    return await LeadController.create_lead(payload)


@router.get("/sources")
async def lead_sources(
    current_user: dict = Depends(require_role_unified(_LEAD_ADMIN_ROLES)),
):
    """M15-S01: source_type options + counts for admin filters."""
    return await LeadController.list_sources(current_user=current_user)


@router.get("/summary")
async def lead_pipeline_summary(
    current_user: dict = Depends(require_role_unified(_LEAD_ADMIN_ROLES)),
):
    """M15-S03: pipeline counts (by status, overdue, due today)."""
    return await LeadController.pipeline_summary(current_user=current_user)


@router.get("")
async def list_leads(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = None,
    status: Optional[str] = Query(
        None, description="Filter by status: new, contacted, qualified, converted, lost"
    ),
    source: Optional[str] = Query(None, description="Legacy source label filter"),
    source_type: Optional[str] = Query(
        None, description="M15 source_type e.g. website_popup, demo_booking"
    ),
    branch_id: Optional[str] = Query(None, description="Filter by branch"),
    follow_up_due: Optional[str] = Query(
        None,
        description="M15-S03: overdue | today | upcoming | unscheduled",
    ),
    sort: Optional[str] = Query(
        None, description="created_at (default) | next_follow_up_at"
    ),
    coach_assignment_status: Optional[str] = Query(
        None, description="M15-S04: pending | accepted | declined | …"
    ),
    unassigned_coach: bool = Query(
        False, description="M15-S04: only leads without pending/accepted coach"
    ),
    current_user: dict = Depends(require_role_unified(_LEAD_ADMIN_ROLES)),
):
    """Admin/BM: list leads with search, status, source, branch, and follow-up filters."""
    return await LeadController.list_leads(
        skip=skip,
        limit=limit,
        search=search,
        status=status,
        source=source,
        source_type=source_type,
        branch_id=branch_id,
        follow_up_due=follow_up_due,
        sort=sort,
        coach_assignment_status=coach_assignment_status,
        unassigned_coach=unassigned_coach,
        current_user=current_user,
    )


@router.get("/{lead_id}")
async def get_lead(
    lead_id: str,
    current_user: dict = Depends(require_role_unified(_LEAD_ADMIN_ROLES)),
):
    """M15-S03: lead detail + recent follow-up history."""
    return await LeadController.get_lead_detail(lead_id, current_user=current_user)


@router.get("/{lead_id}/follow-ups")
async def list_lead_follow_ups(
    lead_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(require_role_unified(_LEAD_ADMIN_ROLES)),
):
    """M15-S03: paginated follow-up history for a lead."""
    return await LeadController.list_follow_ups(
        lead_id, skip=skip, limit=limit, current_user=current_user
    )


@router.post("/{lead_id}/follow-ups", status_code=201)
async def create_lead_follow_up(
    lead_id: str,
    body: LeadFollowUpCreate,
    current_user: dict = Depends(require_role_unified(_LEAD_ADMIN_ROLES)),
):
    """M15-S03: log note/call, set next follow-up, optionally change status."""
    return await LeadController.create_follow_up(
        lead_id, body, current_user=current_user
    )


@router.patch("/{lead_id}", response_model=LeadResponse)
async def update_lead_status(
    lead_id: str,
    body: LeadStatusUpdate,
    current_user: dict = Depends(require_role_unified(_LEAD_ADMIN_ROLES)),
):
    """Admin/BM: update lead pipeline status (also records follow-up history)."""
    return await LeadController.update_status(lead_id, body, current_user=current_user)
