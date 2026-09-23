from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum

class StudentEnrollmentCreate(BaseModel):
    course_id: str
    branch_id: str
    start_date: datetime

class StudentPaymentCreate(BaseModel):
    enrollment_id: str
    amount: float
    payment_method: str
    transaction_id: str = None
    notes: str = None


class ConfirmRazorpayPayment(BaseModel):
    """Payload from frontend after Razorpay success to record payment and update enrollment."""
    enrollment_id: str
    amount: Optional[float] = None  # Ignored; amount is taken from enrollment (fee + admission) server-side.
    razorpay_payment_id: str
    razorpay_order_id: Optional[str] = None
    razorpay_signature: Optional[str] = None
    duration_months: Optional[int] = None  # for renewal: extend end_date by this many months
    course_name: Optional[str] = None
    branch_name: Optional[str] = None


class BeneficiaryInput(BaseModel):
    beneficiary_type: str = "self"  # "self" | "family" | "friend" | "other"
    beneficiary_name: Optional[str] = None
    beneficiary_phone: Optional[str] = None
    beneficiary_relationship: Optional[str] = None

class PrepareStudentCheckoutBody(BaseModel):
    """Create a pending enrollment and return IDs for Razorpay (student dashboard browse → pay)."""
    course_id: str
    branch_id: str
    duration: str
    batch_ref: Optional[str] = Field(
        None,
        description="Branch course_schedule batch_ref or synthetic __index:n__ (matches payment-info).",
    )
    beneficiary: Optional[BeneficiaryInput] = None


class RenewalQuoteBody(BaseModel):
    """M07-S03 renewal quote for an existing enrollment (grace / overdue / arrear breakdown)."""

    enrollment_id: str
    duration: Optional[str] = Field(
        None,
        description="Tenure id/code; defaults to enrollment.duration_id when omitted.",
    )
    batch_ref: Optional[str] = None
    beneficiary: Optional[BeneficiaryInput] = None


class PrepareRenewalCheckoutBody(BaseModel):
    """M07-S04: prepare pending renewal checkout from an existing enrollment + tenure."""

    enrollment_id: str
    duration: Optional[str] = Field(
        None,
        description="Tenure id/code; defaults to enrollment.duration_id when omitted.",
    )
    batch_ref: Optional[str] = None
    beneficiary: Optional[BeneficiaryInput] = None


class CreateStudentRazorpayOrderBody(BaseModel):
    """Create a Razorpay order for an existing pending enrollment (amount is taken from enrollment server-side)."""
    enrollment_id: str

# New models for registration payment flow
class StudentRegistrationPayment(BaseModel):
    student_id: str
    course_id: str
    branch_id: str
    category_id: str
    duration: str
    total_amount: float
    admission_fee: float
    course_fee: float
    payment_method: str
    payment_status: str = "pending"
    transaction_id: Optional[str] = None
    payment_date: Optional[datetime] = None
    created_at: datetime = datetime.utcnow()

class PaymentCalculation(BaseModel):
    course_fee: float
    admission_fee: float = 500.0  # Default admission fee
    total_amount: float
    currency: str = "INR"
    duration_multiplier: float = 1.0
    # Flat price / offer fields (optional)
    original_price: Optional[float] = None  # Price before flat offer
    discount_amount: Optional[float] = None  # Discount = original_price - total_amount
    is_flat_price: bool = False  # True when flat/offer price was applied

class CoursePaymentInfo(BaseModel):
    course_id: str
    course_name: str
    category_name: str
    branch_name: str
    duration: str
    pricing: PaymentCalculation
