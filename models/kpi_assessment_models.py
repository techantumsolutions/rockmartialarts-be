"""
M10-S02 KPI assessment period + student assessment models.

Collections:
  - kpi_assessment_periods
  - student_kpi_assessments
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional
import uuid

from pydantic import BaseModel, Field


class AssessmentPeriodStatus(str, Enum):
    DRAFT = "draft"
    OPEN = "open"
    CLOSED = "closed"


class AssessmentPeriodDocument(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    code: str
    name: str
    description: Optional[str] = None
    start_date: datetime
    end_date: datetime
    status: AssessmentPeriodStatus = AssessmentPeriodStatus.DRAFT
    branch_ids: List[str] = Field(default_factory=list)
    course_ids: List[str] = Field(default_factory=list)
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AssessmentPeriodCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    start_date: datetime
    end_date: datetime
    status: AssessmentPeriodStatus = AssessmentPeriodStatus.DRAFT
    branch_ids: List[str] = Field(default_factory=list)
    course_ids: List[str] = Field(default_factory=list)


class AssessmentPeriodUpdate(BaseModel):
    code: Optional[str] = Field(default=None, min_length=1, max_length=64)
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    status: Optional[AssessmentPeriodStatus] = None
    branch_ids: Optional[List[str]] = None
    course_ids: Optional[List[str]] = None


class KpiScoreInput(BaseModel):
    kpi_id: str
    raw_score: float
    notes: Optional[str] = Field(default=None, max_length=2000)


class StudentKpiAssessmentUpsert(BaseModel):
    """Upsert scores for one student/course/period (multiple KPIs)."""

    period_id: str
    student_id: str
    course_id: str
    branch_id: Optional[str] = None
    scores: List[KpiScoreInput] = Field(default_factory=list)
    notes: Optional[str] = Field(default=None, max_length=2000)
