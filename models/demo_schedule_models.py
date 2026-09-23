"""
M13-S01 Demo schedule configuration models.

Collection: demo_schedules
Stores branch, course, recurring rules, slot timing, optional capacity, and fee.
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


DEFAULT_DEMO_FEE_INR = 300.0

WEEKDAY_VALUES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


class DemoRecurrenceType(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"


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


class DemoScheduleCreate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    branch_id: str = Field(..., min_length=1, max_length=64)
    course_id: str = Field(..., min_length=1, max_length=64)
    recurrence: DemoRecurrenceType = DemoRecurrenceType.WEEKLY
    weekdays: List[str] = Field(default_factory=list)
    start_time: str = Field(..., description="HH:MM")
    end_time: str = Field(..., description="HH:MM")
    capacity: Optional[int] = Field(
        default=None,
        ge=1,
        le=500,
        description="Optional max bookings per generated slot; null = unlimited",
    )
    fee_inr: float = Field(
        default=DEFAULT_DEMO_FEE_INR,
        ge=0,
        le=100000,
        description="Demo fee in INR; default reference ₹300",
    )
    effective_from: Optional[str] = Field(
        default=None, max_length=32, description="YYYY-MM-DD"
    )
    effective_until: Optional[str] = Field(
        default=None, max_length=32, description="YYYY-MM-DD"
    )
    is_active: bool = True
    notes: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("weekdays", mode="before")
    @classmethod
    def _normalize_weekdays(cls, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("weekdays must be a list")
        return [normalize_weekday(v) for v in value]

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def _normalize_times(cls, value):
        return normalize_time_hhmm(str(value or ""))

    @field_validator("effective_from", "effective_until", mode="before")
    @classmethod
    def _empty_date_to_none(cls, value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @model_validator(mode="after")
    def _validate_rules(self):
        if time_to_minutes(self.start_time) >= time_to_minutes(self.end_time):
            raise ValueError("end_time must be after start_time")

        if self.recurrence == DemoRecurrenceType.WEEKLY:
            if not self.weekdays:
                raise ValueError("weekdays required for weekly recurrence")
            # de-dupe preserve order
            seen = set()
            unique = []
            for day in self.weekdays:
                if day not in seen:
                    seen.add(day)
                    unique.append(day)
            self.weekdays = unique
        else:
            # daily = every day; store all weekdays for consistent slot generation later
            self.weekdays = list(WEEKDAY_VALUES)

        if self.effective_from and self.effective_until:
            if self.effective_until < self.effective_from:
                raise ValueError("effective_until must be on or after effective_from")
        return self


class DemoScheduleUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    branch_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    course_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    recurrence: Optional[DemoRecurrenceType] = None
    weekdays: Optional[List[str]] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    capacity: Optional[int] = Field(default=None, ge=1, le=500)
    clear_capacity: bool = False
    fee_inr: Optional[float] = Field(default=None, ge=0, le=100000)
    effective_from: Optional[str] = Field(default=None, max_length=32)
    effective_until: Optional[str] = Field(default=None, max_length=32)
    clear_effective_until: bool = False
    is_active: Optional[bool] = None
    notes: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("weekdays", mode="before")
    @classmethod
    def _normalize_weekdays(cls, value):
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValueError("weekdays must be a list")
        return [normalize_weekday(v) for v in value]

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def _normalize_times(cls, value):
        if value is None:
            return None
        return normalize_time_hhmm(str(value or ""))

    @field_validator("effective_from", "effective_until", mode="before")
    @classmethod
    def _empty_date_to_none(cls, value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None
