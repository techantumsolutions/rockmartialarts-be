"""
M16-S03 Learning (LMS) subscription plan models.

Collections: learning_subscription_plans, learning_subscriptions,
             learning_subscription_payments

Separate from coach_subscription_* and student dojo billing.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class LearningPlanKind(str, Enum):
    THREE_MONTH = "3_month"
    LIFETIME = "lifetime"
    CUSTOM = "custom"


class LearningSubscriptionStatus(str, Enum):
    PENDING_PAYMENT = "pending_payment"
    ACTIVE = "active"
    GRACE = "grace"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class LearningSubscriptionPaymentStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    WAIVED = "waived"


# Canonical durations for seeded kinds
THREE_MONTH_DAYS = 90
LIFETIME_DAYS = 36500  # ~100 years; is_lifetime flag drives expiry logic


class LearningSubscriptionPlanCreate(BaseModel):
    course_id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    plan_kind: LearningPlanKind = LearningPlanKind.CUSTOM
    fee_inr: float = Field(..., ge=0, le=1_000_000)
    duration_days: Optional[int] = Field(
        default=None,
        ge=1,
        le=LIFETIME_DAYS,
        description="Paid access days; ignored when plan_kind=lifetime (uses LIFETIME_DAYS)",
    )
    grace_period_days: int = Field(default=7, ge=0, le=365)
    is_lifetime: Optional[bool] = Field(
        default=None,
        description="If unset, derived from plan_kind=lifetime",
    )
    is_active: bool = True
    sort_order: int = Field(default=100, ge=0, le=10000)

    @field_validator("course_id", "name", mode="before")
    @classmethod
    def _req(cls, v):
        s = str(v or "").strip()
        if not s:
            raise ValueError("required")
        return s

    @field_validator("description", mode="before")
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @model_validator(mode="after")
    def _apply_kind(self):
        kind = self.plan_kind
        if kind == LearningPlanKind.THREE_MONTH:
            object.__setattr__(self, "duration_days", THREE_MONTH_DAYS)
            object.__setattr__(self, "is_lifetime", False)
            if not self.name:
                object.__setattr__(self, "name", "3-Month Access")
        elif kind == LearningPlanKind.LIFETIME:
            object.__setattr__(self, "duration_days", LIFETIME_DAYS)
            object.__setattr__(self, "is_lifetime", True)
            object.__setattr__(self, "grace_period_days", 0)
        else:
            # custom
            if self.is_lifetime:
                object.__setattr__(self, "duration_days", self.duration_days or LIFETIME_DAYS)
                object.__setattr__(self, "grace_period_days", 0)
            elif self.duration_days is None:
                object.__setattr__(self, "duration_days", THREE_MONTH_DAYS)
            if self.is_lifetime is None:
                object.__setattr__(self, "is_lifetime", False)
        return self


class LearningSubscriptionPlanUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    plan_kind: Optional[LearningPlanKind] = None
    fee_inr: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    duration_days: Optional[int] = Field(default=None, ge=1, le=LIFETIME_DAYS)
    grace_period_days: Optional[int] = Field(default=None, ge=0, le=365)
    is_lifetime: Optional[bool] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = Field(default=None, ge=0, le=10000)

    @field_validator("name", "description", mode="before")
    @classmethod
    def _empty(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class LearningSubscribeCheckoutBody(BaseModel):
    plan_id: str = Field(..., min_length=1, max_length=64)


class LearningSubscribePaymentVerify(BaseModel):
    subscription_id: str = Field(..., min_length=1, max_length=64)
    razorpay_order_id: str = Field(..., min_length=1, max_length=128)
    razorpay_payment_id: str = Field(..., min_length=1, max_length=128)
    razorpay_signature: str = Field(..., min_length=1, max_length=256)


class LearningSubscribeGrantBody(BaseModel):
    """Admin complimentary activation (no Razorpay)."""

    user_id: str = Field(..., min_length=1, max_length=64)
    plan_id: str = Field(..., min_length=1, max_length=64)
    note: Optional[str] = Field(default=None, max_length=1000)
    start_immediately: bool = True


# Suggested defaults for admin UX / seed helpers
DEFAULT_THREE_MONTH = {
    "name": "3-Month Access",
    "description": "Full course access for 90 days from activation.",
    "plan_kind": LearningPlanKind.THREE_MONTH.value,
    "fee_inr": 2999.0,
    "duration_days": THREE_MONTH_DAYS,
    "grace_period_days": 7,
    "is_lifetime": False,
    "is_active": True,
    "sort_order": 10,
}

DEFAULT_LIFETIME = {
    "name": "Lifetime Access",
    "description": "One-time purchase — keep access for life.",
    "plan_kind": LearningPlanKind.LIFETIME.value,
    "fee_inr": 9999.0,
    "duration_days": LIFETIME_DAYS,
    "grace_period_days": 0,
    "is_lifetime": True,
    "is_active": True,
    "sort_order": 20,
}
