"""
M16-S01 Online Learning course catalogue models.

Collection: learning_courses
Separate from physical academy `courses` (branch enrollment).
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class LearningCourseStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


LEARNING_COURSE_STATUSES = tuple(s.value for s in LearningCourseStatus)


def slugify(value: str) -> str:
    import re

    s = (value or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:120] or "course"


class LearningCourseCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    slug: Optional[str] = Field(default=None, max_length=120)
    short_description: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = Field(default=None, max_length=10000)
    thumbnail_url: Optional[str] = Field(default=None, max_length=1000)
    trailer_url: Optional[str] = Field(
        default=None,
        max_length=1000,
        description="Optional public marketing trailer (not a protected lesson)",
    )
    difficulty: Optional[str] = Field(
        default=None, max_length=80, description="e.g. Beginner, Intermediate"
    )
    language: Optional[str] = Field(default="English", max_length=60)
    estimated_hours: Optional[float] = Field(default=None, ge=0, le=10000)
    status: LearningCourseStatus = LearningCourseStatus.DRAFT
    sort_order: int = Field(default=100, ge=0, le=100000)
    seo_title: Optional[str] = Field(default=None, max_length=200)
    seo_description: Optional[str] = Field(default=None, max_length=500)

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, v):
        s = str(v or "").strip()
        if not s:
            raise ValueError("title is required")
        return s

    @field_validator(
        "slug",
        "short_description",
        "description",
        "thumbnail_url",
        "trailer_url",
        "difficulty",
        "language",
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


class LearningCourseUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    slug: Optional[str] = Field(default=None, max_length=120)
    short_description: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = Field(default=None, max_length=10000)
    thumbnail_url: Optional[str] = Field(default=None, max_length=1000)
    trailer_url: Optional[str] = Field(default=None, max_length=1000)
    difficulty: Optional[str] = Field(default=None, max_length=80)
    language: Optional[str] = Field(default=None, max_length=60)
    estimated_hours: Optional[float] = Field(default=None, ge=0, le=10000)
    status: Optional[LearningCourseStatus] = None
    sort_order: Optional[int] = Field(default=None, ge=0, le=100000)
    seo_title: Optional[str] = Field(default=None, max_length=200)
    seo_description: Optional[str] = Field(default=None, max_length=500)

    @field_validator(
        "title",
        "slug",
        "short_description",
        "description",
        "thumbnail_url",
        "trailer_url",
        "difficulty",
        "language",
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
