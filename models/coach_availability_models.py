"""
M14-S03 Coach weekly availability models.

Collection: coach_availability (one document per coach).
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from enum import Enum
import uuid

from pydantic import BaseModel, Field, field_validator, model_validator


WEEKDAY_VALUES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def normalize_weekday(value: str) -> str:
    raw = (value or "").strip().lower()
    aliases = {
        "mon": "monday",
        "tue": "tuesday",
        "tues": "tuesday",
        "wed": "wednesday",
        "thu": "thursday",
        "thur": "thursday",
        "thurs": "thursday",
        "fri": "friday",
        "sat": "saturday",
        "sun": "sunday",
    }
    normalized = aliases.get(raw, raw)
    if normalized not in WEEKDAY_VALUES:
        raise ValueError(f"Invalid weekday: {value}")
    return normalized


def normalize_time_hhmm(value: str) -> str:
    raw = (value or "").strip()
    parts = raw.split(":")
    if len(parts) not in (2, 3):
        raise ValueError("Time must be HH:MM")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise ValueError("Time must be HH:MM") from exc
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("Time must be a valid HH:MM")
    return f"{hour:02d}:{minute:02d}"


def time_to_minutes(value: str) -> int:
    hhmm = normalize_time_hhmm(value)
    hour, minute = hhmm.split(":")
    return int(hour) * 60 + int(minute)


class CoachWeeklySlot(BaseModel):
    id: Optional[str] = Field(default=None, max_length=64)
    weekday: str = Field(..., description="monday..sunday")
    start_time: str = Field(..., description="HH:MM")
    end_time: str = Field(..., description="HH:MM")
    service_location_id: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Branch/location for this slot; null = any of coach locations",
    )
    notes: Optional[str] = Field(default=None, max_length=500)

    @field_validator("weekday", mode="before")
    @classmethod
    def _weekday(cls, value):
        return normalize_weekday(str(value or ""))

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def _times(cls, value):
        return normalize_time_hhmm(str(value or ""))

    @field_validator("service_location_id", mode="before")
    @classmethod
    def _empty_loc(cls, value):
        if value is None:
            return None
        s = str(value).strip()
        return s or None

    @model_validator(mode="after")
    def _end_after_start(self):
        if time_to_minutes(self.end_time) <= time_to_minutes(self.start_time):
            raise ValueError("end_time must be after start_time")
        return self


class CoachAvailabilityUpdate(BaseModel):
    """Replace weekly availability + service locations for a coach."""

    timezone: str = Field(default="Asia/Kolkata", max_length=64)
    service_location_ids: List[str] = Field(default_factory=list)
    weekly_slots: List[CoachWeeklySlot] = Field(default_factory=list)
    notes: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("service_location_ids", mode="before")
    @classmethod
    def _locs(cls, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("service_location_ids must be a list")
        out = []
        seen = set()
        for v in value:
            s = str(v or "").strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out


class CoachAvailabilityResponse(BaseModel):
    id: str
    coach_id: str
    timezone: str = "Asia/Kolkata"
    service_location_ids: List[str] = Field(default_factory=list)
    service_location_names: List[str] = Field(default_factory=list)
    weekly_slots: List[dict] = Field(default_factory=list)
    notes: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None
    created_at: Optional[datetime] = None
