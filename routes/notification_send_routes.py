"""M18-S02 Notification Sending Service routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from models.notification_send_models import (
    NotificationCronBody,
    NotificationRetryBody,
    NotificationScheduleRequest,
    NotificationSendRequest,
)
from models.user_models import UserRole
from utils.unified_auth import require_role_unified
from utils.notification_providers import provider_info
from utils.notification_send_service import (
    get_notification_log,
    list_notification_logs,
    list_notification_outbox,
    mark_notification_delivered,
    retry_notification_log,
    run_notification_cron,
    schedule_notification,
    send_notification,
)

router = APIRouter()

_ADMIN = [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]


@router.get("/provider")
async def api_provider_info(
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return {"provider": provider_info()}


@router.post("/send")
async def api_send_notification(
    body: NotificationSendRequest,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """Send SMS/WhatsApp/Email via unified service + write delivery log."""
    return await send_notification(body, current_user=current_user)


@router.post("/schedule", status_code=201)
async def api_schedule_notification(
    body: NotificationScheduleRequest,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await schedule_notification(body, current_user=current_user)


@router.post("/cron/process")
async def api_notification_cron(body: NotificationCronBody):
    """Process due outbox + retries. Requires NOTIFICATION_CRON_SECRET (or shared cron secrets)."""
    return await run_notification_cron(body)


@router.get("/logs")
async def api_list_logs(
    channel: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    template_id: Optional[str] = Query(None),
    recipient: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await list_notification_logs(
        channel=channel,
        status=status,
        template_id=template_id,
        recipient=recipient,
        source=source,
        search=search,
        skip=skip,
        limit=limit,
    )


@router.get("/logs/{log_id}")
async def api_get_log(
    log_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await get_notification_log(log_id)


@router.post("/logs/{log_id}/retry")
async def api_retry_log(
    log_id: str,
    body: Optional[NotificationRetryBody] = None,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    force = bool(body.force) if body else False
    return await retry_notification_log(
        log_id, force=force, current_user=current_user
    )


@router.post("/logs/{log_id}/delivered")
async def api_mark_delivered(
    log_id: str,
    provider_message_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """Manual/webhook-style delivery status update."""
    return await mark_notification_delivered(
        log_id, provider_message_id=provider_message_id
    )


@router.get("/outbox")
async def api_list_outbox(
    status: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await list_notification_outbox(status=status, skip=skip, limit=limit)
