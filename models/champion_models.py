"""
M20-S01 Champion Profile CMS models.

Collection: champions
Separate from showcase achievements and enrollment student_achievements.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class ChampionStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    UNPUBLISHED = "unpublished"
    ARCHIVED = "archived"


CHAMPION_STATUSES = tuple(s.value for s in ChampionStatus)


def slugify_champion(value: str) -> str:
    s = (value or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:120] or "champion"


class ChampionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    slug: Optional[str] = Field(default=None, max_length=120)
    headline: Optional[str] = Field(default=None, max_length=300)
    short_bio: Optional[str] = Field(default=None, max_length=1000)
    success_story: Optional[str] = Field(
        default=None,
        max_length=20000,
        description="Long-form success story / content",
    )
    photo_url: Optional[str] = Field(default=None, max_length=1000)
    student_id: Optional[str] = Field(
        default=None,
        max_length=80,
        description="Optional link to an enrolled student user",
    )
    branch_id: Optional[str] = Field(default=None, max_length=80)
    display_order: int = Field(default=100, ge=0, le=100000)
    status: ChampionStatus = ChampionStatus.DRAFT
    publish: Optional[bool] = Field(
        default=None,
        description="When true, set status=published on create",
    )

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v):
        s = str(v or "").strip()
        if not s:
            raise ValueError("name required")
        return s

    @field_validator(
        "slug",
        "headline",
        "short_bio",
        "success_story",
        "photo_url",
        "student_id",
        "branch_id",
        mode="before",
    )
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @model_validator(mode="after")
    def _publish_flag(self):
        if self.publish is True:
            self.status = ChampionStatus.PUBLISHED
        return self


class ChampionUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    slug: Optional[str] = Field(default=None, max_length=120)
    headline: Optional[str] = Field(default=None, max_length=300)
    short_bio: Optional[str] = Field(default=None, max_length=1000)
    success_story: Optional[str] = Field(default=None, max_length=20000)
    photo_url: Optional[str] = Field(default=None, max_length=1000)
    clear_photo: Optional[bool] = False
    student_id: Optional[str] = Field(default=None, max_length=80)
    clear_student: Optional[bool] = False
    branch_id: Optional[str] = Field(default=None, max_length=80)
    clear_branch: Optional[bool] = False
    display_order: Optional[int] = Field(default=None, ge=0, le=100000)
    status: Optional[ChampionStatus] = None

    @field_validator(
        "name",
        "slug",
        "headline",
        "short_bio",
        "success_story",
        "photo_url",
        "student_id",
        "branch_id",
        mode="before",
    )
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class ChampionPublishRequest(BaseModel):
    published: bool = True
