"""
M16-S05 Learning progress models.

Collection: learning_progress (one document per user + course)
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class LearningProgressHeartbeatBody(BaseModel):
    """Upsert last-viewed lesson + optional playback position / completion."""

    lesson_id: str = Field(..., min_length=1, max_length=64)
    position_seconds: Optional[float] = Field(
        default=None, ge=0, le=86400, description="Current playback position"
    )
    duration_seconds: Optional[float] = Field(
        default=None, ge=0, le=86400, description="Known media duration"
    )
    completed: Optional[bool] = Field(
        default=None,
        description="Force mark lesson complete (e.g. video ended)",
    )

    @field_validator("lesson_id", mode="before")
    @classmethod
    def _lid(cls, v):
        s = str(v or "").strip()
        if not s:
            raise ValueError("lesson_id required")
        return s


class LearningProgressCompleteBody(BaseModel):
    lesson_id: str = Field(..., min_length=1, max_length=64)

    @field_validator("lesson_id", mode="before")
    @classmethod
    def _lid(cls, v):
        s = str(v or "").strip()
        if not s:
            raise ValueError("lesson_id required")
        return s
