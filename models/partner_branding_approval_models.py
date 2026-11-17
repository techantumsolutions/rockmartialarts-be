"""Partner branding page edit approval models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class BrandingEditRequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"


class BrandingEditPermission(str, Enum):
    NONE = "none"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"


class BrandingEditRequestCreate(BaseModel):
    """Partner-initiated request body (no fields required)."""

    note: Optional[str] = Field(None, max_length=500)


class BrandingEditReviewBody(BaseModel):
    reason: Optional[str] = Field(None, max_length=1000)


def new_branding_request_id() -> str:
    return str(uuid4())


def branding_request_public(doc: Optional[dict]) -> Optional[Dict[str, Any]]:
    if not doc:
        return None
    return {
        "id": doc.get("id"),
        "partner_org_id": doc.get("partner_org_id"),
        "partner_name": doc.get("partner_name"),
        "partner_slug": doc.get("partner_slug"),
        "requested_by": doc.get("requested_by"),
        "requested_by_name": doc.get("requested_by_name"),
        "requested_by_email": doc.get("requested_by_email"),
        "requested_at": doc.get("requested_at"),
        "status": doc.get("status"),
        "reviewed_by": doc.get("reviewed_by"),
        "reviewed_by_name": doc.get("reviewed_by_name"),
        "reviewed_at": doc.get("reviewed_at"),
        "review_reason": doc.get("review_reason"),
        "access_revoked_at": doc.get("access_revoked_at"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }
