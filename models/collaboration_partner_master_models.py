"""
M21-S04 Collaboration Partner Masters / Experts models.

Collection: collaboration_partner_masters (many per partner branch).
Separate from global M20 champions — partner-scoped only.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class PartnerMasterCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    designation: Optional[str] = Field(default=None, max_length=200)
    photo_url: Optional[str] = Field(default=None, max_length=1000)
    biography: Optional[str] = Field(default=None, max_length=20000)
    experience_years: Optional[float] = Field(default=None, ge=0, le=80)
    experience_summary: Optional[str] = Field(default=None, max_length=2000)
    specializations: List[str] = Field(default_factory=list)
    achievements: List[str] = Field(default_factory=list)
    display_order: int = Field(default=100, ge=0, le=100000)
    is_active: bool = True

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v):
        s = str(v or "").strip()
        if not s:
            raise ValueError("name required")
        return s

    @field_validator(
        "designation",
        "photo_url",
        "biography",
        "experience_summary",
        mode="before",
    )
    @classmethod
    def _empty_str(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @field_validator("specializations", "achievements", mode="before")
    @classmethod
    def _list_clean(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            parts = [p.strip() for p in v.replace(",", "\n").split("\n")]
            return [p for p in parts if p]
        return [str(x).strip() for x in v if str(x).strip()]


class PartnerMasterUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    designation: Optional[str] = Field(default=None, max_length=200)
    photo_url: Optional[str] = Field(default=None, max_length=1000)
    clear_photo: Optional[bool] = False
    biography: Optional[str] = Field(default=None, max_length=20000)
    experience_years: Optional[float] = Field(default=None, ge=0, le=80)
    experience_summary: Optional[str] = Field(default=None, max_length=2000)
    specializations: Optional[List[str]] = None
    achievements: Optional[List[str]] = None
    display_order: Optional[int] = Field(default=None, ge=0, le=100000)
    is_active: Optional[bool] = None

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            raise ValueError("name required")
        return s

    @field_validator(
        "designation",
        "photo_url",
        "biography",
        "experience_summary",
        mode="before",
    )
    @classmethod
    def _empty_str(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @field_validator("specializations", "achievements", mode="before")
    @classmethod
    def _list_clean(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            parts = [p.strip() for p in v.replace(",", "\n").split("\n")]
            return [p for p in parts if p]
        return [str(x).strip() for x in v if str(x).strip()]


class CollaborationPartnerMaster(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    branch_id: str
    name: str
    designation: Optional[str] = None
    photo_url: Optional[str] = None
    biography: Optional[str] = None
    experience_years: Optional[float] = None
    experience_summary: Optional[str] = None
    specializations: List[str] = Field(default_factory=list)
    achievements: List[str] = Field(default_factory=list)
    display_order: int = 100
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
