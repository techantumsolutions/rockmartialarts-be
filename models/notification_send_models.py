"""
M18-S02 Notification Sending — request/log models.

Normalized delivery logs live in `notification_logs` (additive fields;
legacy rows still listable via enrich).
Scheduled work uses `notification_outbox`.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator


class NotificationDeliveryStatus(str, Enum):
    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    STUBBED = "stubbed"
    FAILED = "failed"
    DELIVERED = "delivered"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class NotificationSendRequest(BaseModel):
    """Send immediately using a template (or raw body)."""

    recipient: str = Field(..., min_length=5, max_length=32)
    channel: Optional[str] = Field(
        default=None,
        description="sms|whatsapp|email — defaults from template",
    )
    template_id: Optional[str] = Field(default=None, max_length=64)
    template_name: Optional[str] = Field(default=None, max_length=80)
    body: Optional[str] = Field(
        default=None,
        max_length=4000,
        description="Raw body when no template (admin/test only)",
    )
    subject: Optional[str] = Field(default=None, max_length=200)
    context: Optional[Dict[str, Any]] = None
    source: Optional[str] = Field(default="admin_send", max_length=80)
    user_id: Optional[str] = Field(default=None, max_length=64)
    branch_id: Optional[str] = Field(default=None, max_length=64)
    metadata: Optional[Dict[str, Any]] = None
    dry_run: bool = False

    @field_validator("recipient", mode="before")
    @classmethod
    def _phone(cls, v):
        s = "".join(c for c in str(v or "") if c.isdigit() or c == "+")
        if len("".join(c for c in s if c.isdigit())) < 10:
            raise ValueError("recipient phone required")
        return s

    @field_validator("channel", "template_id", "template_name", "subject", "source", "user_id", "branch_id", mode="before")
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class NotificationScheduleRequest(BaseModel):
    """Enqueue a notification for later delivery."""

    recipient: str = Field(..., min_length=5, max_length=32)
    scheduled_at: str = Field(
        ...,
        min_length=8,
        max_length=40,
        description="ISO datetime when send should run (UTC preferred)",
    )
    channel: Optional[str] = None
    template_id: Optional[str] = None
    template_name: Optional[str] = None
    body: Optional[str] = Field(default=None, max_length=4000)
    subject: Optional[str] = None
    context: Optional[Dict[str, Any]] = None
    source: Optional[str] = Field(default="scheduled", max_length=80)
    user_id: Optional[str] = None
    branch_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    max_attempts: int = Field(default=3, ge=1, le=10)

    @field_validator("recipient", mode="before")
    @classmethod
    def _phone(cls, v):
        s = "".join(c for c in str(v or "") if c.isdigit() or c == "+")
        if len("".join(c for c in s if c.isdigit())) < 10:
            raise ValueError("recipient phone required")
        return s

    @field_validator(
        "channel",
        "template_id",
        "template_name",
        "subject",
        "source",
        "user_id",
        "branch_id",
        mode="before",
    )
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class NotificationCronBody(BaseModel):
    secret: str
    dry_run: bool = False
    limit: int = Field(default=50, ge=1, le=200)


class NotificationRetryBody(BaseModel):
    force: bool = False
