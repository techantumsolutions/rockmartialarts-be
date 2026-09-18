"""
M21-S05 Collaboration Partner Gallery models.

Collection: collaboration_partner_gallery (many items per partner branch).
Does not mutate branch.gallery_images — partner-scoped CMS only.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class GalleryMediaType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"


class PartnerGalleryItemCreate(BaseModel):
    media_type: GalleryMediaType = GalleryMediaType.IMAGE
    media_url: Optional[str] = Field(default=None, max_length=1000)
    thumbnail_url: Optional[str] = Field(default=None, max_length=1000)
    video_url: Optional[str] = Field(
        default=None,
        max_length=1000,
        description="External video URL (YouTube/Vimeo) when not using uploaded file",
    )
    caption: Optional[str] = Field(default=None, max_length=500)
    display_order: int = Field(default=100, ge=0, le=100000)
    is_active: bool = True

    @field_validator("media_url", "thumbnail_url", "video_url", "caption", mode="before")
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @model_validator(mode="after")
    def _require_media(self):
        mt = self.media_type
        mt_val = mt.value if isinstance(mt, GalleryMediaType) else str(mt)
        if mt_val == GalleryMediaType.IMAGE.value:
            if not self.media_url:
                raise ValueError("media_url required for image gallery items")
        else:
            if not self.media_url and not self.video_url:
                raise ValueError(
                    "media_url or video_url required for video gallery items"
                )
        return self


class PartnerGalleryItemUpdate(BaseModel):
    media_type: Optional[GalleryMediaType] = None
    media_url: Optional[str] = Field(default=None, max_length=1000)
    clear_media: Optional[bool] = False
    thumbnail_url: Optional[str] = Field(default=None, max_length=1000)
    clear_thumbnail: Optional[bool] = False
    video_url: Optional[str] = Field(default=None, max_length=1000)
    clear_video_url: Optional[bool] = False
    caption: Optional[str] = Field(default=None, max_length=500)
    display_order: Optional[int] = Field(default=None, ge=0, le=100000)
    is_active: Optional[bool] = None

    @field_validator("media_url", "thumbnail_url", "video_url", "caption", mode="before")
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class PartnerGalleryReorderItem(BaseModel):
    id: str
    display_order: int = Field(..., ge=0, le=100000)


class PartnerGalleryReorderRequest(BaseModel):
    items: List[PartnerGalleryReorderItem] = Field(default_factory=list)


class CollaborationPartnerGalleryItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    branch_id: str
    media_type: str = GalleryMediaType.IMAGE.value
    media_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    video_url: Optional[str] = None
    caption: Optional[str] = None
    display_order: int = 100
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
