"""
M15-S05 Coach session booking models.

Collection: coach_session_bookings
Additive — does not replace demo_bookings or training_requests.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class CoachSessionStatus(str, Enum):
    SCHEDULED = "scheduled"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


SESSION_STATUSES = tuple(s.value for s in CoachSessionStatus)
ACTIVE_SESSION_STATUSES = {
    CoachSessionStatus.SCHEDULED.value,
    CoachSessionStatus.CONFIRMED.value,
}


def _normalize_hhmm(value: str) -> str:
    raw = (value or "").strip()
    parts = raw.split(":")
    if len(parts) not in (2, 3):
        raise ValueError("Time must be HH:MM")
    hour = int(parts[0])
    minute = int(parts[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("Invalid time")
    return f"{hour:02d}:{minute:02d}"


class CoachSessionBookingCreate(BaseModel):
    coach_id: str = Field(..., min_length=1, max_length=64)
    session_date: str = Field(..., description="YYYY-MM-DD")
    start_time: str = Field(..., description="HH:MM")
    end_time: str = Field(..., description="HH:MM")
    branch_id: Optional[str] = Field(default=None, max_length=64)
    branch_name: Optional[str] = Field(default=None, max_length=200)
    lead_id: Optional[str] = Field(default=None, max_length=64)
    lead_coach_assignment_id: Optional[str] = Field(default=None, max_length=64)
    participant_name: Optional[str] = Field(default=None, max_length=200)
    participant_phone: Optional[str] = Field(default=None, max_length=32)
    participant_email: Optional[str] = Field(default=None, max_length=200)
    notes: Optional[str] = Field(default=None, max_length=2000)
    source_type: str = Field(default="lead", max_length=40)

    @field_validator("coach_id", "session_date", mode="before")
    @classmethod
    def _strip_req(cls, v):
        s = str(v or "").strip()
        if not s:
            raise ValueError("required")
        return s

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def _time(cls, v):
        return _normalize_hhmm(str(v or ""))

    @field_validator(
        "branch_id",
        "branch_name",
        "lead_id",
        "lead_coach_assignment_id",
        "participant_name",
        "participant_phone",
        "participant_email",
        "notes",
        mode="before",
    )
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @model_validator(mode="after")
    def _range(self):
        from models.coach_availability_models import time_to_minutes

        if time_to_minutes(self.start_time) >= time_to_minutes(self.end_time):
            raise ValueError("end_time must be after start_time")
        return self


class CoachSessionStatusUpdate(BaseModel):
    status: CoachSessionStatus
    notes: Optional[str] = Field(default=None, max_length=2000)
    cancellation_reason: Optional[str] = Field(default=None, max_length=500)

    @field_validator("notes", "cancellation_reason", mode="before")
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class CoachSessionReschedule(BaseModel):
    session_date: str = Field(..., description="YYYY-MM-DD")
    start_time: str = Field(..., description="HH:MM")
    end_time: str = Field(..., description="HH:MM")
    branch_id: Optional[str] = Field(default=None, max_length=64)
    notes: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("session_date", mode="before")
    @classmethod
    def _strip_date(cls, v):
        s = str(v or "").strip()
        if not s:
            raise ValueError("required")
        return s

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def _time(cls, v):
        return _normalize_hhmm(str(v or ""))
