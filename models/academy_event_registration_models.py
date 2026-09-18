"""
M17-S03 Academy Event Registration models.

Collection: academy_event_registrations
Additive — does not touch camp_registrations or demo_bookings.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class AcademyEventRegistrationStatus(str, Enum):
    PENDING_PAYMENT = "pending_payment"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"


HELD_REGISTRATION_STATUSES = (
    AcademyEventRegistrationStatus.PENDING_PAYMENT.value,
    AcademyEventRegistrationStatus.CONFIRMED.value,
)

# Configurable form fields (defaults). Name + phone always required when enabled.
DEFAULT_REGISTRATION_FIELDS: List[Dict[str, Any]] = [
    {
        "key": "participant_name",
        "label": "Full name",
        "field_type": "text",
        "required": True,
        "enabled": True,
    },
    {
        "key": "participant_phone",
        "label": "Mobile number",
        "field_type": "phone",
        "required": True,
        "enabled": True,
    },
    {
        "key": "participant_email",
        "label": "Email",
        "field_type": "email",
        "required": False,
        "enabled": True,
    },
    {
        "key": "participant_age",
        "label": "Age",
        "field_type": "number",
        "required": False,
        "enabled": True,
    },
    {
        "key": "notes",
        "label": "Notes / special requests",
        "field_type": "textarea",
        "required": False,
        "enabled": True,
    },
]

ALLOWED_REGISTRATION_FIELD_KEYS = frozenset(
    f["key"] for f in DEFAULT_REGISTRATION_FIELDS
)
CORE_REQUIRED_KEYS = frozenset({"participant_name", "participant_phone"})


def normalize_registration_fields(
    raw: Optional[List[Any]],
) -> List[Dict[str, Any]]:
    """Merge admin overrides with defaults; always keep name/phone required+enabled."""
    by_key = {f["key"]: dict(f) for f in DEFAULT_REGISTRATION_FIELDS}
    if raw:
        for item in raw:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            if key not in ALLOWED_REGISTRATION_FIELD_KEYS:
                continue
            base = by_key[key]
            if "label" in item and str(item.get("label") or "").strip():
                base["label"] = str(item["label"]).strip()[:120]
            if "enabled" in item:
                base["enabled"] = bool(item["enabled"])
            if "required" in item:
                base["required"] = bool(item["required"])
            by_key[key] = base
    # Core fields cannot be disabled or made optional
    for key in CORE_REQUIRED_KEYS:
        by_key[key]["enabled"] = True
        by_key[key]["required"] = True
    return [by_key[f["key"]] for f in DEFAULT_REGISTRATION_FIELDS]


class AcademyEventRegistrationCreate(BaseModel):
    event_id: Optional[str] = Field(default=None, max_length=64)
    event_slug: Optional[str] = Field(default=None, max_length=120)
    participant_name: str = Field(..., min_length=1, max_length=200)
    participant_phone: str = Field(..., min_length=5, max_length=32)
    participant_email: Optional[str] = Field(default=None, max_length=200)
    participant_age: Optional[int] = Field(default=None, ge=1, le=120)
    notes: Optional[str] = Field(default=None, max_length=2000)
    source: Optional[str] = Field(default="website", max_length=80)
    # Extra answers keyed by field key (for future custom fields)
    field_values: Optional[Dict[str, Any]] = None

    @field_validator("participant_name", "participant_phone", mode="before")
    @classmethod
    def _req(cls, v):
        s = str(v or "").strip()
        if not s:
            raise ValueError("required")
        return s

    @field_validator("participant_email", "event_id", "event_slug", "notes", mode="before")
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class AcademyEventRegistrationPaymentVerify(BaseModel):
    razorpay_order_id: str = Field(..., min_length=1, max_length=128)
    razorpay_payment_id: str = Field(..., min_length=1, max_length=128)
    razorpay_signature: str = Field(..., min_length=1, max_length=256)


ALLOWED_ADMIN_REGISTRATION_STATUS_TRANSITIONS = {
    AcademyEventRegistrationStatus.PENDING_PAYMENT.value: {
        AcademyEventRegistrationStatus.CANCELLED.value,
        AcademyEventRegistrationStatus.EXPIRED.value,
    },
    AcademyEventRegistrationStatus.CONFIRMED.value: {
        AcademyEventRegistrationStatus.CANCELLED.value,
    },
    AcademyEventRegistrationStatus.FAILED.value: {
        AcademyEventRegistrationStatus.CANCELLED.value,
    },
    AcademyEventRegistrationStatus.EXPIRED.value: set(),
    AcademyEventRegistrationStatus.CANCELLED.value: set(),
}


class AcademyEventRegistrationStatusUpdate(BaseModel):
    status: AcademyEventRegistrationStatus
    note: Optional[str] = Field(default=None, max_length=2000)
