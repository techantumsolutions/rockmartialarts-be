"""White-label partner organization branding (1:1 with partner_organizations)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class PartnerOrgBrandingMedia(BaseModel):
    logo_url: Optional[str] = None
    logo_alt: Optional[str] = None
    cover_banner_url: Optional[str] = None
    cover_banner_alt: Optional[str] = None


class PartnerOrgBrandingContent(BaseModel):
    short_description: Optional[str] = None
    about_content: Optional[str] = None
    vision: Optional[str] = None
    mission: Optional[str] = None
    our_story: Optional[str] = None
    why_choose_us: Optional[str] = None
    why_choose_us_points: List[str] = Field(default_factory=list)
    tagline: Optional[str] = None


class PartnerOrgSocialLinks(BaseModel):
    website: Optional[str] = None
    facebook: Optional[str] = None
    instagram: Optional[str] = None
    youtube: Optional[str] = None
    linkedin: Optional[str] = None
    twitter: Optional[str] = None
    whatsapp: Optional[str] = None


class PartnerOrgBrandingFacilities(BaseModel):
    facilities: List[str] = Field(default_factory=list)
    parking_info: Optional[str] = None
    location_highlights: List[str] = Field(default_factory=list)
    map_embed_url: Optional[str] = None


class PartnerOrgBranding(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    partner_org_id: str
    media: PartnerOrgBrandingMedia = Field(default_factory=PartnerOrgBrandingMedia)
    content: PartnerOrgBrandingContent = Field(default_factory=PartnerOrgBrandingContent)
    facilities: PartnerOrgBrandingFacilities = Field(default_factory=PartnerOrgBrandingFacilities)
    social_links: PartnerOrgSocialLinks = Field(default_factory=PartnerOrgSocialLinks)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PartnerOrgBrandingUpsert(BaseModel):
    media: Optional[PartnerOrgBrandingMedia] = None
    content: Optional[PartnerOrgBrandingContent] = None
    facilities: Optional[PartnerOrgBrandingFacilities] = None
    social_links: Optional[PartnerOrgSocialLinks] = None


def _dump(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="python")
    return model.dict()


def empty_branding_response(partner_org_id: str, org: Optional[dict] = None) -> Dict[str, Any]:
    return {
        "id": None,
        "partner_org_id": partner_org_id,
        "has_branding": False,
        "media": _dump(PartnerOrgBrandingMedia()),
        "content": _dump(PartnerOrgBrandingContent()),
        "facilities": _dump(PartnerOrgBrandingFacilities()),
        "social_links": _dump(PartnerOrgSocialLinks()),
        "org_snapshot": {
            "id": partner_org_id,
            "name": (org or {}).get("name"),
            "slug": (org or {}).get("slug"),
            "email": (org or {}).get("email"),
            "phone": (org or {}).get("phone"),
            "address": (org or {}).get("address"),
        },
        "created_at": None,
        "updated_at": None,
    }
