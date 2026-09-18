"""
M15-S02 Callback request models.

Collection: lead_callbacks
Also creates/links a lead with source_type=callback (via lead_service).
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class CallbackStatus(str, Enum):
    NEW = "new"
    CONTACTED = "contacted"
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class CallbackPriority(str, Enum):
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


CALLBACK_STATUSES = tuple(s.value for s in CallbackStatus)
CALLBACK_PRIORITIES = tuple(p.value for p in CallbackPriority)

# Statuses that still need admin attention (highlighted in list when high/urgent)
OPEN_CALLBACK_STATUSES = {
    CallbackStatus.NEW.value,
    CallbackStatus.CONTACTED.value,
    CallbackStatus.SCHEDULED.value,
}


class CallbackCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    phone: str = Field(..., min_length=5, max_length=32)
    email: Optional[str] = Field(default=None, max_length=200)
    branch_id: Optional[str] = Field(default=None, max_length=64)
    branch_name: Optional[str] = Field(default=None, max_length=200)
    preferred_time: Optional[str] = Field(
        default=None,
        max_length=120,
        description="Preferred callback window e.g. Morning, 6–8 PM",
    )
    preferred_date: Optional[str] = Field(
        default=None, max_length=32, description="Optional YYYY-MM-DD"
    )
    message: Optional[str] = Field(default=None, max_length=2000)
    priority: CallbackPriority = CallbackPriority.NORMAL
    course_interest: Optional[str] = Field(default=None, max_length=300)

    @field_validator("name", "phone", mode="before")
    @classmethod
    def _strip_required(cls, v):
        s = str(v or "").strip()
        if not s:
            raise ValueError("required")
        return s

    @field_validator("email", "branch_id", "branch_name", "preferred_time", "preferred_date", "message", "course_interest", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class CallbackStatusUpdate(BaseModel):
    status: CallbackStatus
    admin_note: Optional[str] = Field(default=None, max_length=1000)


class CallbackUpdate(BaseModel):
    priority: Optional[CallbackPriority] = None
    admin_note: Optional[str] = Field(default=None, max_length=1000)
    preferred_time: Optional[str] = Field(default=None, max_length=120)
    preferred_date: Optional[str] = Field(default=None, max_length=32)
    highlight: Optional[bool] = Field(
        default=None,
        description="Force highlight override; null = derive from priority",
    )
