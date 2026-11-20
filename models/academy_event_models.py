"""
M17 Academy Event CMS models (S01 + S03 registration field config).

Collection: academy_events
Separate from legacy branch-calendar `events` and camp_events.
"""
from __future__ import annotations

import re
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from models.academy_event_registration_models import normalize_registration_fields

DEFAULT_REMINDER_OFFSETS_HOURS = [24]
ALLOWED_REMINDER_OFFSETS = frozenset({1, 2, 6, 12, 24, 48, 72})


def normalize_reminder_offsets(raw: Optional[List[Any]]) -> List[int]:
    out: List[int] = []
    if raw is None:
        return list(DEFAULT_REMINDER_OFFSETS_HOURS)
    for item in raw:
        try:
            h = int(item)
        except (TypeError, ValueError):
            continue
        if h in ALLOWED_REMINDER_OFFSETS and h not in out:
            out.append(h)
    out.sort(reverse=True)
    return out or list(DEFAULT_REMINDER_OFFSETS_HOURS)


class AcademyEventType(str, Enum):
    EVENT = "event"
    SEMINAR = "seminar"
    WORKSHOP = "workshop"


class AcademyEventStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


ACADEMY_EVENT_TYPES = tuple(t.value for t in AcademyEventType)
ACADEMY_EVENT_STATUSES = tuple(s.value for s in AcademyEventStatus)


def slugify(value: str) -> str:
    s = (value or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:120] or "event"


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}(:\d{2})?$")


def _norm_time(t: str) -> str:
    s = (t or "").strip()
    if not s:
        raise ValueError("time required")
    if len(s) == 5 and _TIME_RE.match(s):
        return s
    if len(s) >= 8 and _TIME_RE.match(s[:8]):
        return s[:8]
    raise ValueError(f"invalid time: {t}")


def batch_start_iso(batch: Dict[str, Any]) -> str:
    st = _norm_time(str(batch.get("start_time") or ""))[:5]
    return f"{batch['date']}T{st}:00"


def batch_end_iso(batch: Dict[str, Any]) -> str:
    et = _norm_time(str(batch.get("end_time") or ""))[:5]
    return f"{batch['date']}T{et}:00"


def event_bounds_from_batches(batches: List[Dict[str, Any]]) -> tuple[str, Optional[str]]:
    """Earliest start and latest end across batches (ISO-like local datetimes)."""
    if not batches:
        raise ValueError("batches required")
    starts = [batch_start_iso(b) for b in batches]
    ends = [batch_end_iso(b) for b in batches]
    return min(starts), max(ends)


def normalize_event_batches(
    raw: Optional[List[Any]],
    *,
    start_at: Optional[str] = None,
    end_at: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Persistable batch rows with stable ids and HH:MM times."""
    out: List[Dict[str, Any]] = []
    if raw:
        for item in raw:
            if isinstance(item, AcademyEventBatch):
                item = item.model_dump()
            if not isinstance(item, dict):
                continue
            date = str(item.get("date") or "").strip()
            if not _DATE_RE.match(date):
                raise ValueError(f"invalid batch date: {date}")
            start_time = _norm_time(str(item.get("start_time") or ""))
            end_time = _norm_time(str(item.get("end_time") or ""))
            if end_time <= start_time:
                raise ValueError("batch end_time must be after start_time")
            bid = str(item.get("id") or "").strip() or str(uuid.uuid4())
            label = str(item.get("label") or "").strip() or None
            out.append(
                {
                    "id": bid,
                    "label": label,
                    "date": date,
                    "start_time": start_time[:5],
                    "end_time": end_time[:5],
                }
            )
    return out


class AcademyEventBatch(BaseModel):
    id: Optional[str] = Field(default=None, max_length=64)
    label: Optional[str] = Field(default=None, max_length=120)
    date: str = Field(..., min_length=10, max_length=10, description="YYYY-MM-DD")
    start_time: str = Field(..., min_length=4, max_length=12)
    end_time: str = Field(..., min_length=4, max_length=12)

    @field_validator("label", mode="before")
    @classmethod
    def _label(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @field_validator("date", mode="before")
    @classmethod
    def _date(cls, v):
        s = str(v or "").strip()
        if not _DATE_RE.match(s):
            raise ValueError("date must be YYYY-MM-DD")
        return s

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def _time(cls, v):
        return _norm_time(str(v or ""))

    @model_validator(mode="after")
    def _order(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class AcademyEventCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    slug: Optional[str] = Field(default=None, max_length=120)
    event_type: AcademyEventType = AcademyEventType.EVENT
    short_description: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = Field(default=None, max_length=20000)
    venue: Optional[str] = Field(default=None, max_length=300)
    venue_address: Optional[str] = Field(default=None, max_length=500)
    start_at: Optional[str] = Field(
        default=None,
        min_length=8,
        max_length=40,
        description="ISO datetime (local or UTC) for event start",
    )
    end_at: Optional[str] = Field(default=None, max_length=40)
    batches: Optional[List[AcademyEventBatch]] = Field(
        default=None,
        description="Optional time slots; start_at/end_at derived from earliest/latest when set",
    )
    branch_id: Optional[str] = Field(default=None, max_length=64)
    fee_inr: float = Field(default=0, ge=0, le=1_000_000)
    capacity: Optional[int] = Field(
        default=None,
        ge=1,
        le=100000,
        description="Max seats; null = unlimited",
    )
    thumbnail_url: Optional[str] = Field(default=None, max_length=1000)
    status: AcademyEventStatus = AcademyEventStatus.DRAFT
    sort_order: int = Field(default=100, ge=0, le=100000)
    seo_title: Optional[str] = Field(default=None, max_length=200)
    seo_description: Optional[str] = Field(default=None, max_length=500)
    registration_enabled: bool = True
    registration_fields: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Configurable registration form fields (defaults applied if omitted)",
    )
    reminders_enabled: bool = True
    reminder_offsets_hours: Optional[List[int]] = Field(
        default=None,
        description="Hours before event_start to SMS confirmed registrants (e.g. [48, 24])",
    )

    @field_validator("title", mode="before")
    @classmethod
    def _req_title(cls, v):
        s = str(v or "").strip()
        if not s:
            raise ValueError("required")
        return s

    @field_validator("start_at", mode="before")
    @classmethod
    def _start(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @field_validator("batches", mode="before")
    @classmethod
    def _batches(cls, v):
        if v is None:
            return None
        if not isinstance(v, list):
            raise ValueError("batches must be a list")
        return v

    @field_validator(
        "slug",
        "short_description",
        "description",
        "venue",
        "venue_address",
        "end_at",
        "branch_id",
        "thumbnail_url",
        "seo_title",
        "seo_description",
        mode="before",
    )
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @field_validator("registration_fields", mode="before")
    @classmethod
    def _fields(cls, v):
        if v is None:
            return None
        return normalize_registration_fields(v)

    @field_validator("reminder_offsets_hours", mode="before")
    @classmethod
    def _offsets(cls, v):
        if v is None:
            return None
        return normalize_reminder_offsets(v)

    @model_validator(mode="after")
    def _dates(self):
        batch_models = self.batches or []
        if batch_models:
            return self
        if not self.start_at:
            raise ValueError("start_at or batches is required")
        if self.end_at and self.start_at and self.end_at < self.start_at:
            raise ValueError("end_at must be on or after start_at")
        return self


class AcademyEventUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    slug: Optional[str] = Field(default=None, max_length=120)
    event_type: Optional[AcademyEventType] = None
    short_description: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = Field(default=None, max_length=20000)
    venue: Optional[str] = Field(default=None, max_length=300)
    venue_address: Optional[str] = Field(default=None, max_length=500)
    start_at: Optional[str] = Field(default=None, max_length=40)
    end_at: Optional[str] = Field(default=None, max_length=40)
    batches: Optional[List[AcademyEventBatch]] = Field(
        default=None,
        description="Replace all time slots when provided",
    )
    branch_id: Optional[str] = Field(default=None, max_length=64)
    fee_inr: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    capacity: Optional[int] = Field(default=None, ge=1, le=100000)
    clear_capacity: Optional[bool] = Field(
        default=None, description="When true, sets capacity to unlimited (null)"
    )
    thumbnail_url: Optional[str] = Field(default=None, max_length=1000)
    status: Optional[AcademyEventStatus] = None
    sort_order: Optional[int] = Field(default=None, ge=0, le=100000)
    seo_title: Optional[str] = Field(default=None, max_length=200)
    seo_description: Optional[str] = Field(default=None, max_length=500)
    registration_enabled: Optional[bool] = None
    registration_fields: Optional[List[Dict[str, Any]]] = None
    reminders_enabled: Optional[bool] = None
    reminder_offsets_hours: Optional[List[int]] = None

    @field_validator(
        "title",
        "slug",
        "short_description",
        "description",
        "venue",
        "venue_address",
        "start_at",
        "end_at",
        "branch_id",
        "thumbnail_url",
        "seo_title",
        "seo_description",
        mode="before",
    )
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @field_validator("registration_fields", mode="before")
    @classmethod
    def _fields(cls, v):
        if v is None:
            return None
        return normalize_registration_fields(v)

    @field_validator("reminder_offsets_hours", mode="before")
    @classmethod
    def _offsets(cls, v):
        if v is None:
            return None
        return normalize_reminder_offsets(v)

    @field_validator("batches", mode="before")
    @classmethod
    def _batches_upd(cls, v):
        if v is None:
            return None
        if not isinstance(v, list):
            raise ValueError("batches must be a list")
        return v