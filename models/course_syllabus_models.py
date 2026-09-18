"""
M11-S01 Course syllabus models.

Collection: course_syllabi
One active syllabus per course (enforced in service + partial unique index).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
import uuid

from pydantic import BaseModel, Field


class CourseSyllabusDocument(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    course_id: str
    title: Optional[str] = None
    notes: Optional[str] = None
    version: int = Field(..., ge=1)
    is_active: bool = False
    original_filename: str
    stored_filename: str
    content_type: str = "application/pdf"
    size_bytes: int = Field(..., ge=0)
    storage_key: str  # relative path under private syllabi root
    sha256: Optional[str] = None
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    superseded_at: Optional[datetime] = None
    superseded_by_id: Optional[str] = None
    uploaded_by: Optional[str] = None
    uploaded_by_name: Optional[str] = None
    activated_by: Optional[str] = None
    activated_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class CourseSyllabusMetaUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    notes: Optional[str] = Field(default=None, max_length=2000)
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
