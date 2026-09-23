"""
M15-S04 Lead ↔ Coach assignment models.

Collection: lead_coach_assignments
Denormalized summary fields also stored on leads for list/filter UX.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class LeadCoachAssignmentStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


ASSIGNMENT_STATUSES = tuple(s.value for s in LeadCoachAssignmentStatus)
ACTIVE_ASSIGNMENT_STATUSES = {
    LeadCoachAssignmentStatus.PENDING.value,
    LeadCoachAssignmentStatus.ACCEPTED.value,
}


class LeadCoachAssignBody(BaseModel):
    coach_id: str = Field(..., min_length=1, max_length=64)
    note: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("coach_id", mode="before")
    @classmethod
    def _strip_coach(cls, v):
        s = str(v or "").strip()
        if not s:
            raise ValueError("coach_id required")
        return s

    @field_validator("note", mode="before")
    @classmethod
    def _empty_note(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class LeadCoachDeclineBody(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=500)
    note: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("reason", "note", mode="before")
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class LeadCoachAcceptBody(BaseModel):
    note: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("note", mode="before")
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None
