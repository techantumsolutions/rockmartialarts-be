"""M17-S06 Academy Event Reminder routes (cron + admin)."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from models.user_models import UserRole
from utils.unified_auth import require_role_unified
from utils.academy_event_reminder_service import (
    admin_trigger_event_reminders,
    list_academy_event_reminder_logs,
    run_academy_event_reminders,
)

router = APIRouter()

_ADMIN = [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]


class EventReminderCronBody(BaseModel):
    secret: str
    dry_run: bool = False
    event_id: Optional[str] = None
    force_offsets: Optional[List[int]] = None


class EventReminderTriggerBody(BaseModel):
    offset_hours: Optional[List[int]] = Field(
        default=None,
        description="Force these offsets; defaults to event configured offsets",
    )
    dry_run: bool = False


@router.post("/cron/reminders")
async def cron_academy_event_reminders(body: EventReminderCronBody):
    """Scheduled job — requires ACADEMY_EVENT_CRON_SECRET or REG_CHECKOUT_CRON_SECRET."""
    return await run_academy_event_reminders(
        secret=body.secret,
        dry_run=body.dry_run,
        event_id=body.event_id,
        force_offsets=body.force_offsets,
    )


@router.get("/reminders/logs")
async def admin_list_reminder_logs(
    event_id: Optional[str] = Query(None),
    registration_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """Admin — SMS reminder delivery logs."""
    return await list_academy_event_reminder_logs(
        event_id=event_id,
        registration_id=registration_id,
        status=status,
        skip=skip,
        limit=limit,
    )


@router.post("/{event_id}/reminders/trigger")
async def admin_trigger_reminders(
    event_id: str,
    body: EventReminderTriggerBody,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """Admin — send reminders now for confirmed registrants (force offsets)."""
    return await admin_trigger_event_reminders(
        event_id,
        offset_hours=body.offset_hours,
        dry_run=body.dry_run,
    )
