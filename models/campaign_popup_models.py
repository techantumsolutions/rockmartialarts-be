"""Website campaign / promo popups (CMS Popup Creation). Separate from lead popup_form."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def new_campaign_popup_id() -> str:
    return str(uuid.uuid4())


def _normalize_date_str(v: str) -> str:
    """Accept YYYY-MM-DD (preferred) or ISO datetime; store as YYYY-MM-DD."""
    s = (v or "").strip()
    if not s:
        raise ValueError("Date is required")
    # datetime-like → date part
    if "T" in s:
        s = s.split("T", 1)[0]
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    raise ValueError("Date must be YYYY-MM-DD")


class CampaignPopupCreate(BaseModel):
    event_title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    image_url: Optional[str] = None
    start_date: str = Field(..., description="YYYY-MM-DD inclusive")
    end_date: str = Field(..., description="YYYY-MM-DD inclusive")
    location: Optional[str] = Field(None, max_length=300)
    cta_label: Optional[str] = Field(None, max_length=80)
    cta_url: Optional[str] = Field(None, max_length=1000)
    delay_minutes: int = Field(1, ge=0, le=1440)
    priority: int = Field(0, description="Higher wins when multiple are in-window")
    enabled: bool = True

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def validate_dates(cls, v):
        return _normalize_date_str(str(v))

    @field_validator("event_title", mode="before")
    @classmethod
    def strip_title(cls, v):
        s = (str(v) if v is not None else "").strip()
        if not s:
            raise ValueError("Event title is required")
        return s


class CampaignPopupUpdate(BaseModel):
    event_title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    image_url: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    location: Optional[str] = Field(None, max_length=300)
    cta_label: Optional[str] = Field(None, max_length=80)
    cta_url: Optional[str] = Field(None, max_length=1000)
    delay_minutes: Optional[int] = Field(None, ge=0, le=1440)
    priority: Optional[int] = None
    enabled: Optional[bool] = None

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def validate_dates_optional(cls, v):
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return _normalize_date_str(str(v))

    @field_validator("event_title", mode="before")
    @classmethod
    def strip_title_optional(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            raise ValueError("Event title cannot be empty")
        return s


class CampaignPopupEnabledPatch(BaseModel):
    enabled: bool


class CampaignPopupResponse(BaseModel):
    id: str
    event_title: str
    description: Optional[str] = None
    image_url: Optional[str] = None
    start_date: str
    end_date: str
    location: Optional[str] = None
    cta_label: Optional[str] = None
    cta_url: Optional[str] = None
    delay_minutes: int = 1
    priority: int = 0
    enabled: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
