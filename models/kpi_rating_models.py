"""
M10-S03 KPI rating models.

Collection: student_kpi_ratings
Unique: student_id + course_id + period_id
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional
import uuid

from pydantic import BaseModel, Field


FORMULA_VERSION = "m10-s03-v1"


class RatingBand(str, Enum):
    OUTSTANDING = "outstanding"
    EXCELLENT = "excellent"
    GOOD = "good"
    FAIR = "fair"
    NEEDS_IMPROVEMENT = "needs_improvement"


class KpiRatingBreakdownLine(BaseModel):
    kpi_id: str
    kpi_code: Optional[str] = None
    kpi_name: Optional[str] = None
    weight: float
    weight_unit: str = "percent"
    raw_score: float
    min_score: float
    max_score: float
    normalized_score: float
    contribution: float  # normalized * weight (before / weight_sum)


class StudentKpiRatingDocument(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    student_id: str
    course_id: str
    branch_id: str
    period_id: str
    rating: float = Field(..., ge=0, le=100)
    band: RatingBand
    is_complete: bool = False
    weight_sum: float = 0.0
    formula_version: str = FORMULA_VERSION
    missing_kpi_ids: List[str] = Field(default_factory=list)
    breakdown: List[KpiRatingBreakdownLine] = Field(default_factory=list)
    calculated_by: Optional[str] = None
    calculated_by_name: Optional[str] = None
    calculated_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RecalculateRatingRequest(BaseModel):
    """Recalculate one student or all students in a period."""

    period_id: str
    student_id: Optional[str] = None
    course_id: Optional[str] = None
    branch_id: Optional[str] = None
    force: bool = Field(
        default=False,
        description="Required to recalculate when period status is closed",
    )


class PreviewRatingRequest(BaseModel):
    """Preview without persisting (optional dry-run)."""

    period_id: str
    student_id: str
    course_id: str
    branch_id: Optional[str] = None
