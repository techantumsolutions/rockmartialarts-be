"""M07-S02 billing cycle schema (Mongo `billing_cycles`)."""
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field
import uuid


class BillingCycleStatus(str, Enum):
    ACTIVE = "active"
    DUE_SOON = "due_soon"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class BillingCycleDocument(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    enrollment_id: str
    student_id: str
    account_user_id: Optional[str] = None
    branch_id: Optional[str] = None
    course_id: Optional[str] = None
    payment_id: Optional[str] = None
    cart_checkout_id: Optional[str] = None
    period_start: datetime
    period_end: datetime
    next_due_date: datetime
    anchor_day: int = 1
    duration_months: int = 1
    status: BillingCycleStatus = BillingCycleStatus.ACTIVE
    currency: str = "INR"
    amount_hint: Optional[float] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class BillingCyclePublic(BaseModel):
    id: str
    enrollment_id: str
    student_id: str
    branch_id: Optional[str] = None
    course_id: Optional[str] = None
    payment_id: Optional[str] = None
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    next_due_date: Optional[datetime] = None
    anchor_day: int = 1
    duration_months: int = 1
    status: str = "active"
    amount_hint: Optional[float] = None
    course_name: Optional[str] = None
    branch_name: Optional[str] = None
    validity_end_date: Optional[datetime] = None
