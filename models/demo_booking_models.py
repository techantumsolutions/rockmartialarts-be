"""
M13-S03 Paid demo booking models.

Collection: demo_bookings
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class DemoBookingStatus(str, Enum):
    PENDING_PAYMENT = "pending_payment"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"


class DemoBookingCreate(BaseModel):
    schedule_id: str = Field(..., min_length=1, max_length=64)
    slot_date: str = Field(..., description="YYYY-MM-DD")
    start_time: str = Field(..., description="HH:MM (must match schedule)")
    end_time: Optional[str] = Field(
        default=None, description="Optional; validated against schedule"
    )
    participant_name: str = Field(..., min_length=1, max_length=200)
    participant_phone: str = Field(..., min_length=5, max_length=32)
    participant_email: Optional[str] = Field(default=None, max_length=200)
    participant_age: Optional[int] = Field(default=None, ge=1, le=120)
    notes: Optional[str] = Field(default=None, max_length=2000)
    source: Optional[str] = Field(default="website", max_length=80)

    @field_validator("slot_date", mode="before")
    @classmethod
    def _date(cls, value):
        text = str(value or "").strip()
        if len(text) < 10:
            raise ValueError("slot_date must be YYYY-MM-DD")
        return text[:10]

    @field_validator("participant_email", mode="before")
    @classmethod
    def _email(cls, value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class DemoBookingPaymentVerify(BaseModel):
    razorpay_order_id: str = Field(..., min_length=1, max_length=128)
    razorpay_payment_id: str = Field(..., min_length=1, max_length=128)
    razorpay_signature: str = Field(..., min_length=1, max_length=256)


ALLOWED_ADMIN_STATUS_TRANSITIONS = {
    DemoBookingStatus.PENDING_PAYMENT.value: {
        DemoBookingStatus.CANCELLED.value,
        DemoBookingStatus.EXPIRED.value,
    },
    DemoBookingStatus.CONFIRMED.value: {
        DemoBookingStatus.CANCELLED.value,
    },
    DemoBookingStatus.FAILED.value: {
        DemoBookingStatus.CANCELLED.value,
    },
    DemoBookingStatus.EXPIRED.value: set(),
    DemoBookingStatus.CANCELLED.value: set(),
}


class DemoBookingStatusUpdate(BaseModel):
    status: DemoBookingStatus
    note: Optional[str] = Field(default=None, max_length=2000)
