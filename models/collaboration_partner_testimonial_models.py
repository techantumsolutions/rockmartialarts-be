"""
M21-S06 Collaboration Partner Testimonials models.

Collection: collaboration_partner_testimonials (many per partner branch).
Separate from student_testimonials and CMS homepage testimonials.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class PartnerTestimonialAuthorRole(str, Enum):
    STUDENT = "student"
    PARENT = "parent"
    GUARDIAN = "guardian"
    OTHER = "other"


class PartnerTestimonialStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    UNPUBLISHED = "unpublished"


class PartnerTestimonialCreate(BaseModel):
    person_name: str = Field(..., min_length=1, max_length=200)
    person_role: PartnerTestimonialAuthorRole = PartnerTestimonialAuthorRole.STUDENT
    related_student_name: Optional[str] = Field(default=None, max_length=200)
    photo_url: Optional[str] = Field(default=None, max_length=1000)
    testimonial_text: str = Field(..., min_length=1, max_length=5000)
    rating: Optional[float] = Field(default=None, ge=0, le=5)
    status: PartnerTestimonialStatus = PartnerTestimonialStatus.DRAFT
    display_order: int = Field(default=100, ge=0, le=100000)
    is_active: bool = True
    publish: Optional[bool] = Field(
        default=None,
        description="When true, set status=published on create",
    )

    @field_validator("person_name", "testimonial_text", mode="before")
    @classmethod
    def _required_str(cls, v):
        s = str(v or "").strip()
        if not s:
            raise ValueError("required")
        return s

    @field_validator("related_student_name", "photo_url", mode="before")
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    def resolved_status(self) -> str:
        if self.publish is True:
            return PartnerTestimonialStatus.PUBLISHED.value
        if isinstance(self.status, PartnerTestimonialStatus):
            return self.status.value
        return str(self.status or PartnerTestimonialStatus.DRAFT.value)


class PartnerTestimonialUpdate(BaseModel):
    person_name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    person_role: Optional[PartnerTestimonialAuthorRole] = None
    related_student_name: Optional[str] = Field(default=None, max_length=200)
    photo_url: Optional[str] = Field(default=None, max_length=1000)
    clear_photo: Optional[bool] = False
    testimonial_text: Optional[str] = Field(default=None, min_length=1, max_length=5000)
    rating: Optional[float] = Field(default=None, ge=0, le=5)
    clear_rating: Optional[bool] = False
    status: Optional[PartnerTestimonialStatus] = None
    display_order: Optional[int] = Field(default=None, ge=0, le=100000)
    is_active: Optional[bool] = None
    publish: Optional[bool] = None

    @field_validator("person_name", "testimonial_text", mode="before")
    @classmethod
    def _optional_required(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            raise ValueError("cannot be empty")
        return s

    @field_validator("related_student_name", "photo_url", mode="before")
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class PartnerTestimonialReorderItem(BaseModel):
    id: str
    display_order: int = Field(..., ge=0, le=100000)


class PartnerTestimonialReorderRequest(BaseModel):
    items: List[PartnerTestimonialReorderItem] = Field(default_factory=list)


class CollaborationPartnerTestimonial(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    branch_id: str
    person_name: str
    person_role: str = PartnerTestimonialAuthorRole.STUDENT.value
    related_student_name: Optional[str] = None
    photo_url: Optional[str] = None
    testimonial_text: str
    rating: Optional[float] = None
    status: str = PartnerTestimonialStatus.DRAFT.value
    display_order: int = 100
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
