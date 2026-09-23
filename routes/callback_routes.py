"""M15-S02 Callback request routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from models.callback_models import CallbackCreate, CallbackStatusUpdate, CallbackUpdate
from models.user_models import UserRole
from utils.unified_auth import require_role_unified
from utils.callback_service import (
    callback_summary,
    create_callback,
    get_callback,
    list_callbacks,
    update_callback,
    update_callback_status,
)

router = APIRouter()

_ADMIN = [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]


@router.post("", status_code=201)
async def api_create_callback(body: CallbackCreate):
    """Public — submit a callback request."""
    return await create_callback(body)


@router.get("")
async def api_list_callbacks(
    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    highlight_only: bool = Query(False),
    search: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await list_callbacks(
        current_user=current_user,
        status=status,
        priority=priority,
        highlight_only=highlight_only,
        search=search,
        branch_id=branch_id,
        skip=skip,
        limit=limit,
    )


@router.get("/summary")
async def api_callback_summary(
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await callback_summary(current_user=current_user)


@router.get("/{callback_id}")
async def api_get_callback(
    callback_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await get_callback(callback_id, current_user=current_user)


@router.patch("/{callback_id}/status")
async def api_callback_status(
    callback_id: str,
    body: CallbackStatusUpdate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await update_callback_status(
        callback_id, body, current_user=current_user
    )


@router.patch("/{callback_id}")
async def api_update_callback(
    callback_id: str,
    body: CallbackUpdate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await update_callback(callback_id, body, current_user=current_user)
