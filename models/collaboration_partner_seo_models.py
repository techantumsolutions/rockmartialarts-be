"""
M21-S08 Collaboration Partner SEO models.

Collection: collaboration_partner_seo (1:1 with partner branch).
Does not mutate generic branch documents — partner SEO only.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class CollaborationPartnerSeo(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    branch_id: str
    meta_title: Optional[str] = Field(default=None, max_length=200)
    meta_description: Optional[str] = Field(default=None, max_length=500)
    keywords: Optional[str] = Field(default=None, max_length=500)
    og_image: Optional[str] = Field(default=None, max_length=1000)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PartnerSeoUpsert(BaseModel):
    meta_title: Optional[str] = Field(default=None, max_length=200)
    meta_description: Optional[str] = Field(default=None, max_length=500)
    keywords: Optional[str] = Field(default=None, max_length=500)
    og_image: Optional[str] = Field(default=None, max_length=1000)


def empty_seo_response(
    branch_id: str, branch: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    branch_info = (branch or {}).get("branch") or {}
    return {
        "id": None,
        "branch_id": branch_id,
        "has_seo": False,
        "meta_title": None,
        "meta_description": None,
        "keywords": None,
        "og_image": None,
        "branch_snapshot": {
            "id": branch_id,
            "name": branch_info.get("name"),
            "code": branch_info.get("code"),
        },
        "created_at": None,
        "updated_at": None,
    }
