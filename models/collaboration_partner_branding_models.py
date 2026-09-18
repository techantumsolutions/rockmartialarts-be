"""
M21-S03 Collaboration Partner branding & branch content models.

Collection: collaboration_partner_branding (1:1 with partner branch).
Does not mutate generic branch documents — partner content only.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


class PartnerDayHours(BaseModel):
    day: str
    open_time: Optional[str] = None  # HH:MM
    close_time: Optional[str] = None
    is_closed: bool = False


class PartnerSocialLinks(BaseModel):
    website: Optional[str] = None
    facebook: Optional[str] = None
    instagram: Optional[str] = None
    youtube: Optional[str] = None
    linkedin: Optional[str] = None
    twitter: Optional[str] = None
    whatsapp: Optional[str] = None


class PartnerBrandingMedia(BaseModel):
    logo_url: Optional[str] = None
    logo_alt: Optional[str] = None
    cover_banner_url: Optional[str] = None
    cover_banner_alt: Optional[str] = None


class PartnerBrandingContent(BaseModel):
    short_description: Optional[str] = None
    about_content: Optional[str] = None
    vision: Optional[str] = None
    mission: Optional[str] = None
    our_story: Optional[str] = None
    why_choose_us: Optional[str] = None
    why_choose_us_points: List[str] = Field(default_factory=list)


class PartnerBrandingFacilities(BaseModel):
    facilities: List[str] = Field(default_factory=list)
    parking_info: Optional[str] = None
    location_highlights: List[str] = Field(default_factory=list)
    map_embed_url: Optional[str] = None


def default_operating_hours() -> List[PartnerDayHours]:
    return [
        PartnerDayHours(day=d, open_time="06:00", close_time="21:00", is_closed=False)
        for d in WEEKDAYS
    ]


class CollaborationPartnerBranding(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    branch_id: str
    media: PartnerBrandingMedia = Field(default_factory=PartnerBrandingMedia)
    content: PartnerBrandingContent = Field(default_factory=PartnerBrandingContent)
    operating_hours: List[PartnerDayHours] = Field(default_factory=default_operating_hours)
    hours_notes: Optional[str] = None
    facilities: PartnerBrandingFacilities = Field(default_factory=PartnerBrandingFacilities)
    social_links: PartnerSocialLinks = Field(default_factory=PartnerSocialLinks)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PartnerBrandingUpsert(BaseModel):
    media: Optional[PartnerBrandingMedia] = None
    content: Optional[PartnerBrandingContent] = None
    operating_hours: Optional[List[PartnerDayHours]] = None
    hours_notes: Optional[str] = None
    facilities: Optional[PartnerBrandingFacilities] = None
    social_links: Optional[PartnerSocialLinks] = None


def _model_to_dict(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="python")
    return model.dict()


def empty_branding_response(branch_id: str, branch: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    branch_info = (branch or {}).get("branch") or {}
    return {
        "id": None,
        "branch_id": branch_id,
        "has_branding": False,
        "media": _model_to_dict(PartnerBrandingMedia()),
        "content": _model_to_dict(PartnerBrandingContent()),
        "operating_hours": [_model_to_dict(h) for h in default_operating_hours()],
        "hours_notes": None,
        "facilities": _model_to_dict(PartnerBrandingFacilities()),
        "social_links": _model_to_dict(PartnerSocialLinks()),
        "branch_snapshot": {
            "id": branch_id,
            "name": branch_info.get("name"),
            "code": branch_info.get("code"),
        },
        "created_at": None,
        "updated_at": None,
    }
