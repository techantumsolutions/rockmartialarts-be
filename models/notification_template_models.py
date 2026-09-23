"""
M18-S01 Notification Template Master models.

Collection: notification_templates (shared with legacy seeds, e.g. invoice WhatsApp).
Additive fields — existing docs remain readable via enrich.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class NotificationChannel(str, Enum):
    SMS = "sms"
    WHATSAPP = "whatsapp"
    EMAIL = "email"


class NotificationTemplateCategory(str, Enum):
    """Business type / use-case (distinct from channel)."""

    OTP = "otp"
    WELCOME = "welcome"
    PAYMENT = "payment"
    REMINDER = "reminder"
    INVOICE = "invoice"
    EVENT = "event"
    MARKETING = "marketing"
    TRANSACTIONAL = "transactional"
    SYSTEM = "system"
    CUSTOM = "custom"


class NotificationTemplateStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"


NOTIFICATION_CHANNELS = tuple(c.value for c in NotificationChannel)
NOTIFICATION_TEMPLATE_CATEGORIES = tuple(c.value for c in NotificationTemplateCategory)
NOTIFICATION_TEMPLATE_STATUSES = tuple(s.value for s in NotificationTemplateStatus)

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def extract_placeholders_from_body(body: str) -> List[str]:
    keys: List[str] = []
    for m in _PLACEHOLDER_RE.finditer(body or ""):
        k = m.group(1)
        if k not in keys:
            keys.append(k)
    return keys


def normalize_placeholders(raw: Optional[List[Any]], *, body: Optional[str] = None) -> List[Dict[str, Any]]:
    """Normalize placeholder definitions; merge keys found in body."""
    by_key: Dict[str, Dict[str, Any]] = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                key = item.strip()
                if not key:
                    continue
                by_key[key] = {
                    "key": key,
                    "label": key.replace("_", " ").title(),
                    "required": False,
                    "sample": "",
                }
                continue
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            if not key:
                continue
            by_key[key] = {
                "key": key,
                "label": str(item.get("label") or key.replace("_", " ").title()).strip()
                or key,
                "required": bool(item.get("required", False)),
                "sample": str(item.get("sample") or "").strip(),
            }
    if body:
        for key in extract_placeholders_from_body(body):
            if key not in by_key:
                by_key[key] = {
                    "key": key,
                    "label": key.replace("_", " ").title(),
                    "required": False,
                    "sample": "",
                }
    return list(by_key.values())


def slugify_template_name(value: str) -> str:
    s = (value or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:80] or "template"


class NotificationTemplatePlaceholder(BaseModel):
    key: str = Field(..., min_length=1, max_length=64)
    label: str = Field(default="", max_length=120)
    required: bool = False
    sample: Optional[str] = Field(default=None, max_length=200)

    @field_validator("key", mode="before")
    @classmethod
    def _key(cls, v):
        s = str(v or "").strip()
        if not s or not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", s):
            raise ValueError("placeholder key must be alphanumeric/underscore")
        return s

    @field_validator("label", "sample", mode="before")
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None if v is None else ""
        return str(v).strip()


class NotificationTemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    display_name: Optional[str] = Field(default=None, max_length=160)
    channel: NotificationChannel = NotificationChannel.SMS
    category: NotificationTemplateCategory = NotificationTemplateCategory.CUSTOM
    status: NotificationTemplateStatus = NotificationTemplateStatus.DRAFT
    subject: Optional[str] = Field(default=None, max_length=200)
    body: str = Field(..., min_length=1, max_length=4000)
    placeholders: Optional[List[Dict[str, Any]]] = None
    # Provider references
    dlt_template_id: Optional[str] = Field(default=None, max_length=64)
    provider_template_name: Optional[str] = Field(default=None, max_length=120)
    provider_reference: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Generic provider ref (legacy alias / external id)",
    )
    description: Optional[str] = Field(default=None, max_length=500)
    # Reserved for M18-S03 — optional now
    branch_id: Optional[str] = Field(default=None, max_length=64)
    is_default: bool = False

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v):
        return slugify_template_name(str(v or ""))

    @field_validator(
        "display_name",
        "subject",
        "dlt_template_id",
        "provider_template_name",
        "provider_reference",
        "description",
        "branch_id",
        mode="before",
    )
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @field_validator("body", mode="before")
    @classmethod
    def _body(cls, v):
        s = str(v or "").strip()
        if not s:
            raise ValueError("body is required")
        return s

    @field_validator("placeholders", mode="before")
    @classmethod
    def _ph(cls, v):
        if v is None:
            return None
        return normalize_placeholders(v)

    @model_validator(mode="after")
    def _merge_placeholders(self):
        self.placeholders = normalize_placeholders(self.placeholders, body=self.body)
        if not self.display_name:
            self.display_name = self.name.replace("_", " ").title()
        return self


class NotificationTemplateUpdate(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=160)
    channel: Optional[NotificationChannel] = None
    category: Optional[NotificationTemplateCategory] = None
    status: Optional[NotificationTemplateStatus] = None
    subject: Optional[str] = Field(default=None, max_length=200)
    body: Optional[str] = Field(default=None, min_length=1, max_length=4000)
    placeholders: Optional[List[Dict[str, Any]]] = None
    dlt_template_id: Optional[str] = Field(default=None, max_length=64)
    provider_template_name: Optional[str] = Field(default=None, max_length=120)
    provider_reference: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = Field(default=None, max_length=500)
    branch_id: Optional[str] = Field(default=None, max_length=64)
    clear_branch_id: Optional[bool] = False
    is_default: Optional[bool] = None

    @field_validator(
        "display_name",
        "subject",
        "dlt_template_id",
        "provider_template_name",
        "provider_reference",
        "description",
        "branch_id",
        mode="before",
    )
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @field_validator("body", mode="before")
    @classmethod
    def _body(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            raise ValueError("body cannot be empty")
        return s

    @field_validator("placeholders", mode="before")
    @classmethod
    def _ph(cls, v):
        if v is None:
            return None
        return normalize_placeholders(v)
