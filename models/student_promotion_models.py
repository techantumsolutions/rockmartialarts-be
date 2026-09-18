"""
M19-S01 Student Promotion CMS models.

Collection: student_promotions
Separate from M05 discount_rules (cart discounts).
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class PromotionStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"


class PromotionCtaType(str, Enum):
    NONE = "none"
    URL = "url"
    PATH = "path"


class PromotionMediaType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    NONE = "none"


class PromotionTargetMode(str, Enum):
    ALL = "all"
    TARGETED = "targeted"


PROMOTION_STATUSES = tuple(s.value for s in PromotionStatus)
PROMOTION_CTA_TYPES = tuple(c.value for c in PromotionCtaType)
PROMOTION_MEDIA_TYPES = tuple(m.value for m in PromotionMediaType)
PROMOTION_TARGET_MODES = tuple(m.value for m in PromotionTargetMode)


def slugify_promotion(value: str) -> str:
    s = (value or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:120] or "promotion"


def _clean_id_list(raw: Optional[List[Any]], *, limit: int = 500) -> List[str]:
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


def normalize_promotion_target(raw: Optional[Any]) -> Dict[str, Any]:
    """Normalize targeting block (M19-S02)."""
    if raw is None:
        return {
            "mode": PromotionTargetMode.ALL.value,
            "student_ids": [],
            "branch_ids": [],
            "course_ids": [],
            "group_ids": [],
        }
    if isinstance(raw, BaseModel):
        raw = raw.model_dump(mode="json")
    if not isinstance(raw, dict):
        return normalize_promotion_target(None)
    raw_mode = raw.get("mode")
    if hasattr(raw_mode, "value"):
        mode = str(raw_mode.value).strip().lower()
    else:
        mode = str(raw_mode or PromotionTargetMode.ALL.value).strip().lower()
    if mode not in PROMOTION_TARGET_MODES:
        mode = PromotionTargetMode.ALL.value
    student_ids = _clean_id_list(raw.get("student_ids"))
    branch_ids = _clean_id_list(raw.get("branch_ids"))
    course_ids = _clean_id_list(raw.get("course_ids"))
    group_ids = _clean_id_list(raw.get("group_ids"))
    if mode == PromotionTargetMode.ALL.value:
        # Keep lists empty for clarity when broadcasting
        return {
            "mode": mode,
            "student_ids": [],
            "branch_ids": [],
            "course_ids": [],
            "group_ids": [],
        }
    return {
        "mode": mode,
        "student_ids": student_ids,
        "branch_ids": branch_ids,
        "course_ids": course_ids,
        "group_ids": group_ids,
    }


class PromotionTarget(BaseModel):
    """Audience rules — AND across non-empty dimensions when mode=targeted."""

    mode: PromotionTargetMode = PromotionTargetMode.ALL
    student_ids: List[str] = Field(default_factory=list)
    branch_ids: List[str] = Field(default_factory=list)
    course_ids: List[str] = Field(default_factory=list)
    group_ids: List[str] = Field(
        default_factory=list,
        description="Approved groups = student batch_ref values",
    )

    @field_validator("student_ids", "branch_ids", "course_ids", "group_ids", mode="before")
    @classmethod
    def _ids(cls, v):
        return _clean_id_list(v if isinstance(v, list) else [])


class StudentPromotionCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    slug: Optional[str] = Field(default=None, max_length=120)
    short_description: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = Field(default=None, max_length=10000)
    banner_url: Optional[str] = Field(default=None, max_length=1000)
    banner_media_type: PromotionMediaType = PromotionMediaType.NONE
    cta_label: Optional[str] = Field(default=None, max_length=80)
    cta_type: PromotionCtaType = PromotionCtaType.NONE
    cta_url: Optional[str] = Field(default=None, max_length=1000)
    start_at: Optional[str] = Field(
        default=None,
        max_length=40,
        description="ISO datetime — campaign start (inclusive)",
    )
    end_at: Optional[str] = Field(
        default=None,
        max_length=40,
        description="ISO datetime — campaign end (inclusive)",
    )
    status: PromotionStatus = PromotionStatus.DRAFT
    priority: int = Field(default=100, ge=0, le=100000)
    is_active: Optional[bool] = Field(
        default=None,
        description="Convenience; when set overrides status to active/inactive",
    )
    target: Optional[PromotionTarget] = None

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, v):
        s = str(v or "").strip()
        if not s:
            raise ValueError("title required")
        return s

    @field_validator(
        "slug",
        "short_description",
        "description",
        "banner_url",
        "cta_label",
        "cta_url",
        "start_at",
        "end_at",
        mode="before",
    )
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @model_validator(mode="after")
    def _cta_and_dates(self):
        if self.cta_type in (PromotionCtaType.URL, PromotionCtaType.PATH):
            if not self.cta_url:
                raise ValueError("cta_url required when cta_type is url or path")
            if not self.cta_label:
                self.cta_label = "Learn more"
        if self.start_at and self.end_at and self.end_at < self.start_at:
            raise ValueError("end_at must be on or after start_at")
        if self.banner_url and self.banner_media_type == PromotionMediaType.NONE:
            # Infer image by default when URL provided
            low = self.banner_url.lower()
            if any(low.endswith(ext) for ext in (".mp4", ".webm", ".mov")):
                self.banner_media_type = PromotionMediaType.VIDEO
            else:
                self.banner_media_type = PromotionMediaType.IMAGE
        if self.is_active is True:
            self.status = PromotionStatus.ACTIVE
        elif self.is_active is False and self.status == PromotionStatus.ACTIVE:
            self.status = PromotionStatus.INACTIVE
        return self


class StudentPromotionUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    slug: Optional[str] = Field(default=None, max_length=120)
    short_description: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = Field(default=None, max_length=10000)
    banner_url: Optional[str] = Field(default=None, max_length=1000)
    banner_media_type: Optional[PromotionMediaType] = None
    clear_banner: Optional[bool] = False
    cta_label: Optional[str] = Field(default=None, max_length=80)
    cta_type: Optional[PromotionCtaType] = None
    cta_url: Optional[str] = Field(default=None, max_length=1000)
    clear_cta: Optional[bool] = False
    start_at: Optional[str] = Field(default=None, max_length=40)
    end_at: Optional[str] = Field(default=None, max_length=40)
    clear_dates: Optional[bool] = False
    status: Optional[PromotionStatus] = None
    priority: Optional[int] = Field(default=None, ge=0, le=100000)
    is_active: Optional[bool] = None
    target: Optional[PromotionTarget] = None

    @field_validator(
        "title",
        "slug",
        "short_description",
        "description",
        "banner_url",
        "cta_label",
        "cta_url",
        "start_at",
        "end_at",
        mode="before",
    )
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class PromotionEligibilityPreviewRequest(BaseModel):
    """Preview audience size for a target (saved or ad-hoc)."""

    target: Optional[PromotionTarget] = None
    promotion_id: Optional[str] = Field(default=None, max_length=80)
    sample_limit: int = Field(default=20, ge=0, le=50)


class PromotionEligibilityCheckRequest(BaseModel):
    """Check whether one student matches a promotion / target."""

    student_id: str = Field(..., min_length=1, max_length=80)
    promotion_id: Optional[str] = Field(default=None, max_length=80)
    target: Optional[PromotionTarget] = None


class PromotionEventType(str, Enum):
    VIEW = "view"
    CTA = "cta"
    DISMISS = "dismiss"


PROMOTION_EVENT_TYPES = tuple(e.value for e in PromotionEventType)


class PromotionEventCreate(BaseModel):
    """Student engagement event for a live promotion (M19-S03)."""

    promotion_id: str = Field(..., min_length=1, max_length=80)
    event_type: PromotionEventType
    meta: Optional[Dict[str, Any]] = None

    @field_validator("promotion_id", mode="before")
    @classmethod
    def _pid(cls, v):
        s = str(v or "").strip()
        if not s:
            raise ValueError("promotion_id required")
        return s
