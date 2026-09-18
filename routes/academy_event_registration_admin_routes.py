"""M17-S05 Academy Event Registration administration routes."""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from models.academy_event_registration_models import (
    AcademyEventRegistrationStatusUpdate,
)
from models.user_models import UserRole
from utils.academy_event_registration_admin_service import (
    export_academy_event_registrations_admin,
    get_academy_event_registration_admin,
    list_academy_event_registrations_admin,
    summarize_academy_event_registrations_admin,
    update_academy_event_registration_status_admin,
)
from utils.unified_auth import require_role_unified

router = APIRouter()

ADMIN_ROLES = [
    UserRole.SUPER_ADMIN,
    UserRole.COACH_ADMIN,
    UserRole.BRANCH_MANAGER,
]


@router.get("/summary")
async def api_registrations_summary(
    branch_id: Optional[str] = Query(None),
    event_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await summarize_academy_event_registrations_admin(
        current_user=current_user, branch_id=branch_id, event_id=event_id
    )


@router.get("/export")
async def api_export_registrations(
    event_id: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    payment_status: Optional[str] = Query(None),
    created_from: Optional[str] = Query(None, alias="from"),
    created_to: Optional[str] = Query(None, alias="to"),
    search: Optional[str] = Query(None),
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await export_academy_event_registrations_admin(
        current_user=current_user,
        event_id=event_id,
        branch_id=branch_id,
        status=status,
        payment_status=payment_status,
        created_from=created_from,
        created_to=created_to,
        search=search,
    )


@router.get("")
async def api_list_registrations(
    event_id: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    payment_status: Optional[str] = Query(None),
    created_from: Optional[str] = Query(None, alias="from"),
    created_to: Optional[str] = Query(None, alias="to"),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await list_academy_event_registrations_admin(
        current_user=current_user,
        event_id=event_id,
        branch_id=branch_id,
        status=status,
        payment_status=payment_status,
        created_from=created_from,
        created_to=created_to,
        search=search,
        skip=skip,
        limit=limit,
    )


@router.get("/{registration_id}")
async def api_get_registration_admin(
    registration_id: str,
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await get_academy_event_registration_admin(
        registration_id, current_user=current_user
    )


@router.patch("/{registration_id}/status")
async def api_update_registration_status(
    registration_id: str,
    body: AcademyEventRegistrationStatusUpdate,
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await update_academy_event_registration_status_admin(
        registration_id, body, current_user=current_user
    )
