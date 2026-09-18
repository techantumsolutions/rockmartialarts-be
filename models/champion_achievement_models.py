"""
M20-S02 Champion Achievements & Media models.

Collection: champion_achievements
Linked to champions; separate from enrollment student_achievements
and marketing showcase achievements.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator


class RecognitionLevel(str, Enum):
    ACADEMY = "academy"
    LOCAL = "local"
    REGIONAL = "regional"
    STATE = "state"
    NATIONAL = "national"
    INTERNATIONAL = "international"
    OTHER = "other"


class ChampionAchievementStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"


RECOGNITION_LEVELS = tuple(r.value for r in RecognitionLevel)
CHAMPION_ACHIEVEMENT_STATUSES = tuple(s.value for s in ChampionAchievementStatus)


def _clean_url_list(raw: Optional[List[Any]], *, limit: int = 30) -> List[str]:
    out: List[str] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        s = str(item or "").strip()
        if s and s not in out:
            out.append(s)
        if len(out) >= limit:
            break
    return out


class ChampionAchievementCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    description: Optional[str] = Field(default=None, max_length=5000)
    recognition_level: RecognitionLevel = RecognitionLevel.OTHER
    competition_name: Optional[str] = Field(default=None, max_length=300)
    award_title: Optional[str] = Field(default=None, max_length=300)
    place: Optional[str] = Field(
        default=None,
        max_length=80,
        description="e.g. 1st, Gold, Runner-up",
    )
    event_year: Optional[int] = Field(default=None, ge=1950, le=2100)
    event_date: Optional[str] = Field(
        default=None,
        max_length=40,
        description="ISO date or free-form date string",
    )
    images: List[str] = Field(default_factory=list)
    videos: List[str] = Field(default_factory=list)
    documents: List[str] = Field(default_factory=list)
    display_order: int = Field(default=100, ge=0, le=100000)
    status: ChampionAchievementStatus = ChampionAchievementStatus.ACTIVE

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, v):
        s = str(v or "").strip()
        if not s:
            raise ValueError("title required")
        return s

    @field_validator(
        "description",
        "competition_name",
        "award_title",
        "place",
        "event_date",
        mode="before",
    )
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @field_validator("images", "videos", "documents", mode="before")
    @classmethod
    def _urls(cls, v):
        return _clean_url_list(v if isinstance(v, list) else [])


class ChampionAchievementUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=300)
    description: Optional[str] = Field(default=None, max_length=5000)
    recognition_level: Optional[RecognitionLevel] = None
    competition_name: Optional[str] = Field(default=None, max_length=300)
    award_title: Optional[str] = Field(default=None, max_length=300)
    place: Optional[str] = Field(default=None, max_length=80)
    event_year: Optional[int] = Field(default=None, ge=1950, le=2100)
    clear_event_year: Optional[bool] = False
    event_date: Optional[str] = Field(default=None, max_length=40)
    images: Optional[List[str]] = None
    videos: Optional[List[str]] = None
    documents: Optional[List[str]] = None
    display_order: Optional[int] = Field(default=None, ge=0, le=100000)
    status: Optional[ChampionAchievementStatus] = None

    @field_validator(
        "title",
        "description",
        "competition_name",
        "award_title",
        "place",
        "event_date",
        mode="before",
    )
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @field_validator("images", "videos", "documents", mode="before")
    @classmethod
    def _urls(cls, v):
        if v is None:
            return None
        return _clean_url_list(v if isinstance(v, list) else [])
