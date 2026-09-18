"""
M14-S04 Coach subscription models.

Business rules (fee, duration, grace, deactivation) live on admin-configurable plans.
Collections: coach_subscription_plans, coach_subscriptions, coach_subscription_payments
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class CoachSubscriptionStatus(str, Enum):
    PENDING_PAYMENT = "pending_payment"
    ACTIVE = "active"
    GRACE = "grace"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class CoachSubscriptionPaymentStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    WAIVED = "waived"


class CoachSubscriptionPlanCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=1000)
    fee_inr: float = Field(..., ge=0, le=1_000_000, description="Subscription fee in INR")
    duration_days: int = Field(
        ..., ge=1, le=3660, description="Paid access duration in days"
    )
    grace_period_days: int = Field(
        default=10,
        ge=0,
        le=365,
        description="Days after ends_at before deactivation (default 10)",
    )
    deactivate_on_grace_expiry: bool = Field(
        default=True,
        description="When grace ends, set coach is_active=False",
    )
    is_active: bool = True
    is_default: bool = False
    sort_order: int = Field(default=100, ge=0, le=10000)

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v):
        s = str(v or "").strip()
        if not s:
            raise ValueError("name is required")
        return s


class CoachSubscriptionPlanUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=1000)
    fee_inr: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    duration_days: Optional[int] = Field(default=None, ge=1, le=3660)
    grace_period_days: Optional[int] = Field(default=None, ge=0, le=365)
    deactivate_on_grace_expiry: Optional[bool] = None
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None
    sort_order: Optional[int] = Field(default=None, ge=0, le=10000)


class CoachSubscriptionCheckoutBody(BaseModel):
    plan_id: str = Field(..., min_length=1, max_length=64)


class CoachSubscriptionPaymentVerify(BaseModel):
    subscription_id: str = Field(..., min_length=1, max_length=64)
    razorpay_order_id: str = Field(..., min_length=1, max_length=128)
    razorpay_payment_id: str = Field(..., min_length=1, max_length=128)
    razorpay_signature: str = Field(..., min_length=1, max_length=256)


class CoachSubscriptionGrantBody(BaseModel):
    """Admin complimentary / manual activation (no Razorpay)."""

    plan_id: str = Field(..., min_length=1, max_length=64)
    note: Optional[str] = Field(default=None, max_length=1000)
    start_immediately: bool = True


DEFAULT_PLAN = {
    "name": "Monthly Coach Access",
    "description": (
        "Standard monthly coach platform access. "
        "Configurable fee, duration, grace period, and deactivation."
    ),
    "fee_inr": 999.0,
    "duration_days": 30,
    "grace_period_days": 10,
    "deactivate_on_grace_expiry": True,
    "is_active": True,
    "is_default": True,
    "sort_order": 10,
}
