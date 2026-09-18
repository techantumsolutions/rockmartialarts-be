"""
M16-S02 Online Learning hierarchy models.

Collections: learning_levels, learning_lessons
Structure: Course → Level → Lesson → Video metadata
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class LearningItemStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


LEARNING_ITEM_STATUSES = tuple(s.value for s in LearningItemStatus)


class LearningLevelCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=5000)
    sort_order: Optional[int] = Field(default=None, ge=0, le=100000)
    status: LearningItemStatus = LearningItemStatus.PUBLISHED

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, v):
        s = str(v or "").strip()
        if not s:
            raise ValueError("title is required")
        return s

    @field_validator("description", mode="before")
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class LearningLevelUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=5000)
    sort_order: Optional[int] = Field(default=None, ge=0, le=100000)
    status: Optional[LearningItemStatus] = None

    @field_validator("title", "description", mode="before")
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class LearningLessonCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=5000)
    sort_order: Optional[int] = Field(default=None, ge=0, le=100000)
    status: LearningItemStatus = LearningItemStatus.PUBLISHED
    # Video metadata (S04 will protect playback; S02 stores admin-managed source)
    video_url: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Admin-managed video source URL (not exposed publicly until entitled)",
    )
    video_storage_key: Optional[str] = Field(default=None, max_length=500)
    duration_seconds: Optional[int] = Field(default=None, ge=0, le=86400)
    is_preview: bool = Field(
        default=False,
        description="Optional free preview lesson (S04 may allow without sub)",
    )

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, v):
        s = str(v or "").strip()
        if not s:
            raise ValueError("title is required")
        return s

    @field_validator("description", "video_url", "video_storage_key", mode="before")
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class LearningLessonUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=5000)
    sort_order: Optional[int] = Field(default=None, ge=0, le=100000)
    status: Optional[LearningItemStatus] = None
    video_url: Optional[str] = Field(default=None, max_length=2000)
    video_storage_key: Optional[str] = Field(default=None, max_length=500)
    duration_seconds: Optional[int] = Field(default=None, ge=0, le=86400)
    is_preview: Optional[bool] = None

    @field_validator("title", "description", "video_url", "video_storage_key", mode="before")
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class ReorderBody(BaseModel):
    ordered_ids: List[str] = Field(..., min_length=1)

    @field_validator("ordered_ids", mode="before")
    @classmethod
    def _ids(cls, v):
        if not isinstance(v, list):
            raise ValueError("ordered_ids must be a list")
        out = []
        for x in v:
            s = str(x or "").strip()
            if s:
                out.append(s)
        if not out:
            raise ValueError("ordered_ids required")
        return out
