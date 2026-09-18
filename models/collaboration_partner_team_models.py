"""
M21-S07 Collaboration Partner Team models.

Collection: collaboration_partner_team (many members per partner branch).
Separate from operational coaches and S04 masters/experts.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class PartnerTeamMemberCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    designation: Optional[str] = Field(default=None, max_length=200)
    role: Optional[str] = Field(
        default=None,
        max_length=120,
        description="Team role label e.g. Manager, Reception, Coach Admin",
    )
    photo_url: Optional[str] = Field(default=None, max_length=1000)
    contact_email: Optional[str] = Field(default=None, max_length=200)
    contact_phone: Optional[str] = Field(default=None, max_length=40)
    contact_approved: bool = Field(
        default=False,
        description="When true, contact may be shown on public partner surfaces",
    )
    bio: Optional[str] = Field(default=None, max_length=2000)
    display_order: int = Field(default=100, ge=0, le=100000)
    is_active: bool = True

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v):
        s = str(v or "").strip()
        if not s:
            raise ValueError("name required")
        return s

    @field_validator(
        "designation",
        "role",
        "photo_url",
        "contact_email",
        "contact_phone",
        "bio",
        mode="before",
    )
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class PartnerTeamMemberUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    designation: Optional[str] = Field(default=None, max_length=200)
    role: Optional[str] = Field(default=None, max_length=120)
    photo_url: Optional[str] = Field(default=None, max_length=1000)
    clear_photo: Optional[bool] = False
    contact_email: Optional[str] = Field(default=None, max_length=200)
    clear_contact_email: Optional[bool] = False
    contact_phone: Optional[str] = Field(default=None, max_length=40)
    clear_contact_phone: Optional[bool] = False
    contact_approved: Optional[bool] = None
    bio: Optional[str] = Field(default=None, max_length=2000)
    display_order: Optional[int] = Field(default=None, ge=0, le=100000)
    is_active: Optional[bool] = None

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            raise ValueError("name required")
        return s

    @field_validator(
        "designation",
        "role",
        "photo_url",
        "contact_email",
        "contact_phone",
        "bio",
        mode="before",
    )
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class CollaborationPartnerTeamMember(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    branch_id: str
    name: str
    designation: Optional[str] = None
    role: Optional[str] = None
    photo_url: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_approved: bool = False
    bio: Optional[str] = None
    display_order: int = 100
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
