"""
M21-S11 Partner landing page lead capture.

Public submissions are tagged partner_landing + branch, then stored in the
shared leads collection so Super Admin and assigned Branch Managers see them
in the existing Leads admin UI.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from controllers.collaboration_partner_landing_controller import (
    CollaborationPartnerLandingController,
)
from utils.collaboration_partner import (
    branch_is_collaboration_partner,
    get_branch_or_404,
)
from utils.database import get_db
from utils.lead_service import create_or_merge_lead, lead_to_response_dict


class PartnerLandingLeadCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    phone: str = Field(..., min_length=5, max_length=32)
    email: Optional[str] = Field(default=None, max_length=200)
    message: Optional[str] = Field(default=None, max_length=2000)
    course: Optional[str] = Field(default=None, max_length=300)
    interest: Optional[str] = Field(default=None, max_length=300)

    @field_validator("name", "phone", mode="before")
    @classmethod
    def _required_str(cls, v):
        s = str(v or "").strip()
        if not s:
            raise ValueError("required")
        return s

    @field_validator("email", "message", "course", "interest", mode="before")
    @classmethod
    def _opt_str(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None


async def _create_for_partner_branch(branch: dict, payload: PartnerLandingLeadCreate) -> dict:
    if not branch_is_collaboration_partner(branch):
        raise HTTPException(status_code=404, detail="Partner not found")
    if not branch.get("is_active", True):
        raise HTTPException(status_code=404, detail="Partner not found")

    bi = branch.get("branch") or {}
    branch_id = str(branch.get("id"))
    branch_name = bi.get("name") or branch.get("name") or "Partner"
    interest = payload.course or payload.interest or ""

    ser, merged = await create_or_merge_lead(
        name=payload.name,
        phone=payload.phone,
        email=payload.email or "",
        course=interest,
        source="partner_landing",
        source_type="partner_landing",
        source_ref_type=None,
        source_ref_id=None,
        branch_id=branch_id,
        branch_name=branch_name,
        message=payload.message,
    )
    out = lead_to_response_dict(ser)
    out["duplicate_merged"] = merged
    out["routed_to_super_admin"] = True
    out["available_to_assigned_branch_manager"] = True
    out["partner"] = {
        "branch_id": branch_id,
        "branch_name": branch_name,
        "slug": branch.get("slug"),
    }
    return {
        "message": "Thank you — we received your enquiry.",
        "lead": out,
    }


class CollaborationPartnerLeadController:
    @staticmethod
    async def submit_by_branch_id(
        branch_id: str, payload: PartnerLandingLeadCreate
    ) -> Dict[str, Any]:
        db = get_db()
        branch = await get_branch_or_404(db, branch_id)
        return await _create_for_partner_branch(branch, payload)

    @staticmethod
    async def submit_by_slug(
        slug: str, payload: PartnerLandingLeadCreate
    ) -> Dict[str, Any]:
        branch = await CollaborationPartnerLandingController._resolve_partner_branch_by_slug(
            slug
        )
        return await _create_for_partner_branch(branch, payload)
