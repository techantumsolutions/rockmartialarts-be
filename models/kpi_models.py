"""
M10-S01 KPI Master models.

Configurable KPI definitions with weightage, max score, and active status.
Stored in Mongo collection `kpi_definitions`.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional
import uuid

from pydantic import BaseModel, Field


class KpiWeightUnit(str, Enum):
    PERCENT = "percent"
    POINTS = "points"


class KpiDefinitionDocument(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    code: str
    name: str
    description: Optional[str] = None
    weight: float = Field(..., gt=0, description="Relative weight (percent or points)")
    weight_unit: KpiWeightUnit = KpiWeightUnit.PERCENT
    max_score: float = Field(default=100.0, gt=0, description="Maximum raw score for normalization")
    min_score: float = Field(default=0.0, ge=0, description="Minimum allowed raw score")
    sort_order: int = Field(default=100, ge=0)
    is_active: bool = True
    # Optional scope: empty = applies globally
    branch_ids: List[str] = Field(default_factory=list)
    course_ids: List[str] = Field(default_factory=list)
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class KpiDefinitionCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    weight: float = Field(..., gt=0)
    weight_unit: KpiWeightUnit = KpiWeightUnit.PERCENT
    max_score: float = Field(default=100.0, gt=0)
    min_score: float = Field(default=0.0, ge=0)
    sort_order: int = Field(default=100, ge=0)
    is_active: bool = True
    branch_ids: List[str] = Field(default_factory=list)
    course_ids: List[str] = Field(default_factory=list)


class KpiDefinitionUpdate(BaseModel):
    code: Optional[str] = Field(default=None, min_length=1, max_length=64)
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    weight: Optional[float] = Field(default=None, gt=0)
    weight_unit: Optional[KpiWeightUnit] = None
    max_score: Optional[float] = Field(default=None, gt=0)
    min_score: Optional[float] = Field(default=None, ge=0)
    sort_order: Optional[int] = Field(default=None, ge=0)
    is_active: Optional[bool] = None
    branch_ids: Optional[List[str]] = None
    course_ids: Optional[List[str]] = None
