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
    price: Optional[float] = Field(default=0.0, ge=0)
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

    @field_validator("price", mode="before")
    @classmethod
    def _price(cls, v):
        if v is None or v == "":
            return 0.0
        try:
            return float(v)
        except (ValueError, TypeError):
            raise ValueError("price must be a valid number")

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
    price: Optional[float] = Field(default=None, ge=0)
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

    @field_validator("price", mode="before")
    @classmethod
    def _price_update(cls, v):
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            raise ValueError("price must be a valid number")

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


class CreateLearningEnrollmentOrderBody(BaseModel):
    course_id: str = Field(..., min_length=1, max_length=120)
    student_name: Optional[str] = Field(default=None, max_length=120)
    email: Optional[str] = Field(default=None, max_length=120)
    phone: Optional[str] = Field(default=None, max_length=30)


class LearningCourseEnrollmentCreate(BaseModel):
    course_id: str = Field(..., min_length=1, max_length=120)
    student_name: str = Field(..., min_length=1, max_length=120)
    email: str = Field(..., min_length=3, max_length=120)
    phone: str = Field(..., min_length=5, max_length=30)
    city: Optional[str] = Field(default=None, max_length=100)
    state: Optional[str] = Field(default=None, max_length=100)
    address: Optional[str] = Field(default=None, max_length=300)
    pincode: Optional[str] = Field(default=None, max_length=20)
    skill_level: Optional[str] = Field(default=None, max_length=80)
    learning_goals: Optional[str] = Field(default=None, max_length=1000)
    notes: Optional[str] = Field(default=None, max_length=1000)
    razorpay_order_id: Optional[str] = None
    razorpay_payment_id: Optional[str] = None
    razorpay_signature: Optional[str] = None
