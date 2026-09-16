"""M05-S05 cart checkout session models (Mongo `cart_checkouts`)."""
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
import uuid


class CartCheckoutStatus(str, Enum):
    PENDING_PAYMENT = "pending_payment"
    PAID = "paid"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"
    FAILED = "failed"


class CartCheckoutEnrollmentLink(BaseModel):
    cart_item_id: str
    student_line_id: str
    student_id: str
    enrollment_id: str
    course_id: str
    branch_id: str
    course_name: str = ""
    branch_name: str = ""
    student_label: str = ""


class CartCheckoutConfirmBody(BaseModel):
    cart_checkout_id: str
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class CartCheckoutDocument(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    cart_id: str
    owner_user_id: str
    status: CartCheckoutStatus = CartCheckoutStatus.PENDING_PAYMENT
    students_snapshot: List[Dict[str, Any]] = Field(default_factory=list)
    items_snapshot: List[Dict[str, Any]] = Field(default_factory=list)
    totals: Dict[str, Any] = Field(default_factory=dict)
    discount_snapshot: Optional[Dict[str, Any]] = None
    enrollment_links: List[CartCheckoutEnrollmentLink] = Field(default_factory=list)
    razorpay_order_id: Optional[str] = None
    razorpay_payment_id: Optional[str] = None
    payment_id: Optional[str] = None
    amount_inr: float = 0.0
    currency: str = "INR"
    fulfillment_key: str = ""
    fulfilled_at: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
