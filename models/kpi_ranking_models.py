"""
M10-S04 KPI ranking models.

Collection: student_kpi_rankings
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional
import uuid

from pydantic import BaseModel, Field


RANKING_VERSION = "m10-s04-v1"


class RankingScopeType(str, Enum):
    OVERALL = "overall"
    BRANCH = "branch"
    COURSE = "course"
    CATEGORY = "category"


class StudentKpiRankingDocument(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    period_id: str
    scope_type: RankingScopeType
    scope_id: str  # "*" for overall
    student_id: str
    course_id: str  # rating source course
    branch_id: str
    category_id: Optional[str] = None
    rating: float
    band: Optional[str] = None
    rank: int = Field(..., ge=1)
    population_size: int = Field(..., ge=0)
    tied: bool = False
    is_complete: bool = True
    formula_version: str = RANKING_VERSION
    calculated_by: Optional[str] = None
    calculated_by_name: Optional[str] = None
    calculated_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RecalculateRankingRequest(BaseModel):
    period_id: str
    scope_types: Optional[List[RankingScopeType]] = Field(
        default=None,
        description="Defaults to all four: overall, branch, course, category",
    )
    branch_id: Optional[str] = None
    course_id: Optional[str] = None
    category_id: Optional[str] = None
    force: bool = False
