"""M15-S04 Lead coach assignment routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, Query

from models.lead_coach_assignment_models import (
    LeadCoachAcceptBody,
    LeadCoachAssignBody,
    LeadCoachDeclineBody,
)
from models.user_models import UserRole
from utils.unified_auth import require_role_unified
from utils.lead_coach_assignment_service import (
    accept_assignment,
    assign_coach_to_lead,
    cancel_lead_assignment,
    decline_assignment,
    get_lead_assignment,
    list_eligible_coaches,
    list_my_assignments,
)

# Nested under /api/leads
lead_assignment_router = APIRouter()

# Coach-facing under /api/lead-coach-assignments
coach_assignment_router = APIRouter()

_ADMIN = [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]
_COACH = [UserRole.COACH]


@lead_assignment_router.get("/{lead_id}/eligible-coaches")
async def api_eligible_coaches(
    lead_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await list_eligible_coaches(lead_id, current_user=current_user)


@lead_assignment_router.get("/{lead_id}/coach-assignment")
async def api_get_assignment(
    lead_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await get_lead_assignment(lead_id, current_user=current_user)


@lead_assignment_router.post("/{lead_id}/coach-assignment", status_code=201)
async def api_assign_coach(
    lead_id: str,
    body: LeadCoachAssignBody,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await assign_coach_to_lead(lead_id, body, current_user=current_user)


@lead_assignment_router.post("/{lead_id}/coach-assignment/cancel")
async def api_cancel_assignment(
    lead_id: str,
    assignment_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await cancel_lead_assignment(
        lead_id, current_user=current_user, assignment_id=assignment_id
    )


@coach_assignment_router.get("/me")
async def api_my_assignments(
    status: Optional[str] = Query("pending"),
    skip: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    current_user: dict = Depends(require_role_unified(_COACH)),
):
    return await list_my_assignments(
        current_user=current_user, status=status, skip=skip, limit=limit
    )


@coach_assignment_router.post("/{assignment_id}/accept")
async def api_accept(
    assignment_id: str,
    body: Optional[LeadCoachAcceptBody] = Body(default=None),
    current_user: dict = Depends(require_role_unified(_COACH)),
):
    return await accept_assignment(
        assignment_id, body or LeadCoachAcceptBody(), current_user=current_user
    )


@coach_assignment_router.post("/{assignment_id}/decline")
async def api_decline(
    assignment_id: str,
    body: Optional[LeadCoachDeclineBody] = Body(default=None),
    current_user: dict = Depends(require_role_unified(_COACH)),
):
    return await decline_assignment(
        assignment_id, body or LeadCoachDeclineBody(), current_user=current_user
    )
