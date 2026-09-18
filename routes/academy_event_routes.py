"""M17 Academy Event routes — S01 admin CMS + S02 public landing."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from models.academy_event_models import AcademyEventCreate, AcademyEventUpdate
from models.user_models import UserRole
from utils.unified_auth import require_role_unified
from utils.academy_event_service import (
    archive_academy_event,
    create_academy_event,
    get_academy_event,
    get_academy_event_public,
    list_academy_events,
    list_academy_events_public,
    update_academy_event,
)

router = APIRouter()

_ADMIN = [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]


# --- Public (must be registered before /{event_id}) ---


@router.get("/public")
async def public_list_events(
    event_type: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    """Public — published academy events only."""
    return await list_academy_events_public(
        event_type=event_type,
        branch_id=branch_id,
        search=search,
        skip=skip,
        limit=limit,
    )


@router.get("/public/{slug_or_id}")
async def public_get_event(slug_or_id: str):
    """Public — published event detail by slug or id."""
    return await get_academy_event_public(slug_or_id)


# --- Admin CMS ---


@router.get("")
async def admin_list_events(
    status: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await list_academy_events(
        status=status,
        event_type=event_type,
        branch_id=branch_id,
        search=search,
        skip=skip,
        limit=limit,
    )


@router.post("", status_code=201)
async def admin_create_event(
    body: AcademyEventCreate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await create_academy_event(body, current_user=current_user)


@router.get("/{event_id}")
async def admin_get_event(
    event_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await get_academy_event(event_id)


@router.patch("/{event_id}")
async def admin_update_event(
    event_id: str,
    body: AcademyEventUpdate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await update_academy_event(event_id, body, current_user=current_user)


@router.delete("/{event_id}")
async def admin_archive_event(
    event_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await archive_academy_event(event_id, current_user=current_user)
