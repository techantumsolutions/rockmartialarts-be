"""M18-S01/S03 Notification Template Master routes."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from models.notification_template_models import (
    NotificationTemplateCreate,
    NotificationTemplateUpdate,
)
from models.user_models import UserRole
from utils.unified_auth import require_role_unified
from utils.notification_template_service import (
    archive_notification_template,
    clone_rock_template_to_branch,
    create_notification_template,
    get_notification_template,
    list_notification_templates,
    list_placeholder_catalog,
    preview_notification_template,
    resolve_notification_template,
    update_notification_template,
)

router = APIRouter()

_ADMIN = [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]


class TemplatePreviewBody(BaseModel):
    context: Optional[Dict[str, Any]] = Field(default=None)


class CloneToBranchBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    branch_id: str = Field(..., min_length=1, max_length=64)
    channel: str = Field(default="sms", max_length=20)


@router.get("/meta")
async def api_template_meta(
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """Channels, categories, statuses, and common placeholders."""
    return await list_placeholder_catalog()


@router.get("/resolve")
async def api_resolve_template(
    name: str = Query(..., min_length=1),
    channel: str = Query("sms"),
    branch_id: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """M18-S03 — branch template first, then Rock Martial Arts global fallback."""
    return await resolve_notification_template(
        name=name,
        channel=channel,
        branch_id=branch_id,
        category=category,
    )


@router.post("/clone-to-branch", status_code=201)
async def api_clone_to_branch(
    body: CloneToBranchBody,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """Copy a Rock default template into a branch override (draft)."""
    return await clone_rock_template_to_branch(
        name=body.name,
        branch_id=body.branch_id,
        channel=body.channel,
        current_user=current_user,
    )


@router.get("")
async def api_list_templates(
    channel: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    scope: Optional[str] = Query(
        None, description="global|branch|all — BM defaults to own+global"
    ),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    include_archived: bool = Query(False),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await list_notification_templates(
        channel=channel,
        category=category,
        status=status,
        branch_id=branch_id,
        scope=scope,
        search=search,
        skip=skip,
        limit=limit,
        include_archived=include_archived,
        current_user=current_user,
    )


@router.post("", status_code=201)
async def api_create_template(
    body: NotificationTemplateCreate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await create_notification_template(body, current_user=current_user)


@router.get("/{template_id}")
async def api_get_template(
    template_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await get_notification_template(template_id, current_user=current_user)


@router.patch("/{template_id}")
async def api_update_template(
    template_id: str,
    body: NotificationTemplateUpdate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await update_notification_template(
        template_id, body, current_user=current_user
    )


@router.delete("/{template_id}")
async def api_archive_template(
    template_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await archive_notification_template(template_id, current_user=current_user)


@router.post("/{template_id}/preview")
async def api_preview_template(
    template_id: str,
    body: TemplatePreviewBody,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await preview_notification_template(
        template_id, context=body.context or {}
    )
