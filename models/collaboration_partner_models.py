"""
M21-S02 Collaboration Partner business & contact profile models.

Collection: collaboration_partner_profiles (1:1 with partner branch).
Partner CMS only applies when branch is_collaboration_partner (S01 gate).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class PartnerRegistrationTax(BaseModel):
    """T03 — business registration / tax identifiers."""

    legal_business_name: Optional[str] = None
    trade_name: Optional[str] = None
    registration_number: Optional[str] = None
    gstin: Optional[str] = None
    pan: Optional[str] = None
    tan: Optional[str] = None
    tax_notes: Optional[str] = None


class PartnerContactInfo(BaseModel):
    """T04 — partner-facing contact (does not replace branch master contact)."""

    primary_contact_name: Optional[str] = None
    primary_contact_designation: Optional[str] = None
    primary_contact_phone: Optional[str] = None
    primary_contact_email: Optional[str] = None
    secondary_contact_name: Optional[str] = None
    secondary_contact_phone: Optional[str] = None
    secondary_contact_email: Optional[str] = None
    billing_email: Optional[str] = None
    support_phone: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    area: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    country: Optional[str] = "India"


class PartnerAgreementInfo(BaseModel):
    """T05 — collaboration / agreement metadata."""

    agreement_type: Optional[str] = None  # franchise | affiliation | co_branding | other
    agreement_reference: Optional[str] = None
    agreement_status: Optional[str] = "draft"  # draft | active | expired | terminated
    start_date: Optional[str] = None  # YYYY-MM-DD
    end_date: Optional[str] = None
    signed_on: Optional[str] = None
    signed_by_name: Optional[str] = None
    signed_by_designation: Optional[str] = None
    revenue_share_percent: Optional[float] = None
    notes: Optional[str] = None


class CollaborationPartnerProfile(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    branch_id: str
    partner_id: str  # auto-generated, immutable (T02)
    registration: PartnerRegistrationTax = Field(default_factory=PartnerRegistrationTax)
    contact: PartnerContactInfo = Field(default_factory=PartnerContactInfo)
    agreement: PartnerAgreementInfo = Field(default_factory=PartnerAgreementInfo)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PartnerProfileUpsert(BaseModel):
    """Admin write body — partner_id is server-owned and ignored if sent."""

    registration: Optional[PartnerRegistrationTax] = None
    contact: Optional[PartnerContactInfo] = None
    agreement: Optional[PartnerAgreementInfo] = None


def _model_to_dict(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="python")
    return model.dict()


def empty_profile_response(branch_id: str, branch: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    branch_info = (branch or {}).get("branch") or {}
    addr = branch_info.get("address") or {}
    return {
        "id": None,
        "branch_id": branch_id,
        "partner_id": None,
        "has_profile": False,
        "registration": _model_to_dict(
            PartnerRegistrationTax(
                legal_business_name=branch_info.get("name"),
                trade_name=branch_info.get("name"),
            )
        ),
        "contact": _model_to_dict(
            PartnerContactInfo(
                primary_contact_email=branch_info.get("email"),
                primary_contact_phone=branch_info.get("phone"),
                address_line1=addr.get("line1"),
                address_line2=addr.get("line2"),
                area=addr.get("area"),
                city=addr.get("city"),
                state=addr.get("state"),
                pincode=addr.get("pincode"),
                country=addr.get("country") or "India",
            )
        ),
        "agreement": _model_to_dict(PartnerAgreementInfo()),
        "branch_snapshot": {
            "id": branch_id,
            "name": branch_info.get("name"),
            "code": branch_info.get("code"),
            "email": branch_info.get("email"),
            "phone": branch_info.get("phone"),
        },
        "created_at": None,
        "updated_at": None,
    }
