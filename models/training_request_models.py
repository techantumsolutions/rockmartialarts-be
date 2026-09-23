"""
M12 Training request models.

Collection: training_requests (discriminated by type).
M12-S01 home … M12-S04 corporate; M12-S05 residential (packages/fees/payment).
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional
import uuid

from pydantic import BaseModel, Field


class TrainingRequestType(str, Enum):
    HOME = "home"
    SCHOOL = "school"
    COLLEGE = "college"
    CORPORATE = "corporate"
    RESIDENTIAL = "residential"


class TrainingRequestStatus(str, Enum):
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    COACH_ASSIGNED = "coach_assigned"
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


ALLOWED_STATUS_TRANSITIONS = {
    TrainingRequestStatus.SUBMITTED.value: {
        TrainingRequestStatus.UNDER_REVIEW.value,
        TrainingRequestStatus.COACH_ASSIGNED.value,
        TrainingRequestStatus.REJECTED.value,
        TrainingRequestStatus.CANCELLED.value,
    },
    TrainingRequestStatus.UNDER_REVIEW.value: {
        TrainingRequestStatus.COACH_ASSIGNED.value,
        TrainingRequestStatus.SCHEDULED.value,
        TrainingRequestStatus.REJECTED.value,
        TrainingRequestStatus.CANCELLED.value,
    },
    TrainingRequestStatus.COACH_ASSIGNED.value: {
        TrainingRequestStatus.SCHEDULED.value,
        TrainingRequestStatus.UNDER_REVIEW.value,
        TrainingRequestStatus.COMPLETED.value,
        TrainingRequestStatus.CANCELLED.value,
    },
    TrainingRequestStatus.SCHEDULED.value: {
        TrainingRequestStatus.COMPLETED.value,
        TrainingRequestStatus.CANCELLED.value,
        TrainingRequestStatus.COACH_ASSIGNED.value,
    },
    TrainingRequestStatus.COMPLETED.value: set(),
    TrainingRequestStatus.REJECTED.value: set(),
    TrainingRequestStatus.CANCELLED.value: set(),
}


class HomeTrainingDetails(BaseModel):
    """M12-S01 Home Training fields."""

    participant_name: str = Field(..., min_length=1, max_length=200)
    participant_age: Optional[int] = Field(default=None, ge=1, le=120)
    participant_phone: str = Field(..., min_length=5, max_length=32)
    participant_email: Optional[str] = Field(default=None, max_length=200)
    number_of_participants: int = Field(default=1, ge=1, le=50)
    address_line1: str = Field(..., min_length=1, max_length=300)
    address_line2: Optional[str] = Field(default=None, max_length=300)
    city: str = Field(..., min_length=1, max_length=120)
    state: str = Field(..., min_length=1, max_length=120)
    pincode: Optional[str] = Field(default=None, max_length=20)
    preferred_date: Optional[str] = Field(
        default=None, max_length=32, description="YYYY-MM-DD"
    )
    preferred_time: Optional[str] = Field(
        default=None, max_length=64, description="e.g. Morning / 10:00 AM"
    )
    training_type: Optional[str] = Field(
        default=None, max_length=200, description="e.g. Karate, self-defense"
    )
    training_goals: Optional[str] = Field(default=None, max_length=2000)
    special_requirements: Optional[str] = Field(default=None, max_length=2000)


class HomeTrainingRequestCreate(BaseModel):
    contact_name: str = Field(..., min_length=1, max_length=200)
    contact_phone: str = Field(..., min_length=5, max_length=32)
    contact_email: Optional[str] = Field(default=None, max_length=200)
    branch_id: Optional[str] = Field(default=None, max_length=64)
    branch_name: Optional[str] = Field(default=None, max_length=200)
    source: Optional[str] = Field(default="website", max_length=80)
    notes: Optional[str] = Field(default=None, max_length=2000)
    details: HomeTrainingDetails


class SchoolTrainingDetails(BaseModel):
    """M12-S02 School Training fields — school, contact, participants, location, schedule."""

    school_name: str = Field(..., min_length=1, max_length=200)
    school_type: Optional[str] = Field(
        default=None, max_length=120, description="e.g. Primary, Secondary, CBSE"
    )
    contact_designation: Optional[str] = Field(
        default=None, max_length=120, description="e.g. Principal, PE Teacher"
    )
    number_of_students: int = Field(default=1, ge=1, le=5000)
    age_group: Optional[str] = Field(
        default=None, max_length=120, description="e.g. 8–12 years / Grade 5–8"
    )
    grade_levels: Optional[str] = Field(default=None, max_length=200)
    address_line1: str = Field(..., min_length=1, max_length=300)
    address_line2: Optional[str] = Field(default=None, max_length=300)
    city: str = Field(..., min_length=1, max_length=120)
    state: str = Field(..., min_length=1, max_length=120)
    pincode: Optional[str] = Field(default=None, max_length=20)
    preferred_date: Optional[str] = Field(
        default=None, max_length=32, description="YYYY-MM-DD"
    )
    preferred_time: Optional[str] = Field(
        default=None, max_length=64, description="e.g. After school / 3:00 PM"
    )
    schedule_notes: Optional[str] = Field(
        default=None, max_length=2000, description="Weekly schedule preference"
    )
    training_type: Optional[str] = Field(
        default=None, max_length=200, description="e.g. Self-defense, fitness"
    )
    training_goals: Optional[str] = Field(default=None, max_length=2000)
    special_requirements: Optional[str] = Field(default=None, max_length=2000)


class SchoolTrainingRequestCreate(BaseModel):
    contact_name: str = Field(..., min_length=1, max_length=200)
    contact_phone: str = Field(..., min_length=5, max_length=32)
    contact_email: Optional[str] = Field(default=None, max_length=200)
    branch_id: Optional[str] = Field(default=None, max_length=64)
    branch_name: Optional[str] = Field(default=None, max_length=200)
    source: Optional[str] = Field(default="website", max_length=80)
    notes: Optional[str] = Field(default=None, max_length=2000)
    details: SchoolTrainingDetails


class CollegeTrainingDetails(BaseModel):
    """M12-S03 College Training — college name, contact, participants, location, schedule."""

    college_name: str = Field(..., min_length=1, max_length=200)
    college_type: Optional[str] = Field(
        default=None,
        max_length=120,
        description="e.g. Engineering, Arts, University campus",
    )
    department: Optional[str] = Field(
        default=None, max_length=200, description="e.g. Sports, NSS, Student Affairs"
    )
    contact_designation: Optional[str] = Field(
        default=None, max_length=120, description="e.g. Dean, Coordinator, Faculty"
    )
    number_of_participants: int = Field(default=1, ge=1, le=5000)
    year_of_study: Optional[str] = Field(
        default=None, max_length=120, description="e.g. 1st–3rd year"
    )
    participant_group: Optional[str] = Field(
        default=None, max_length=200, description="e.g. Mixed batch / Girls only"
    )
    address_line1: str = Field(..., min_length=1, max_length=300)
    address_line2: Optional[str] = Field(default=None, max_length=300)
    city: str = Field(..., min_length=1, max_length=120)
    state: str = Field(..., min_length=1, max_length=120)
    pincode: Optional[str] = Field(default=None, max_length=20)
    preferred_date: Optional[str] = Field(
        default=None, max_length=32, description="YYYY-MM-DD"
    )
    preferred_time: Optional[str] = Field(
        default=None, max_length=64, description="e.g. Evening / 5:00 PM"
    )
    schedule_notes: Optional[str] = Field(
        default=None, max_length=2000, description="Weekly schedule preference"
    )
    training_type: Optional[str] = Field(
        default=None, max_length=200, description="e.g. Self-defense, fitness"
    )
    training_goals: Optional[str] = Field(default=None, max_length=2000)
    special_requirements: Optional[str] = Field(default=None, max_length=2000)


class CollegeTrainingRequestCreate(BaseModel):
    contact_name: str = Field(..., min_length=1, max_length=200)
    contact_phone: str = Field(..., min_length=5, max_length=32)
    contact_email: Optional[str] = Field(default=None, max_length=200)
    branch_id: Optional[str] = Field(default=None, max_length=64)
    branch_name: Optional[str] = Field(default=None, max_length=200)
    source: Optional[str] = Field(default="website", max_length=80)
    notes: Optional[str] = Field(default=None, max_length=2000)
    details: CollegeTrainingDetails


class CorporateTrainingDetails(BaseModel):
    """M12-S04 Corporate — organization, contact, employee count, location, requirement, schedule."""

    organization_name: str = Field(..., min_length=1, max_length=200)
    organization_type: Optional[str] = Field(
        default=None,
        max_length=120,
        description="e.g. IT, Manufacturing, Startup, MNC",
    )
    industry: Optional[str] = Field(default=None, max_length=120)
    contact_designation: Optional[str] = Field(
        default=None, max_length=120, description="e.g. HR Manager, Admin"
    )
    employee_count: int = Field(default=1, ge=1, le=10000)
    department_or_team: Optional[str] = Field(
        default=None, max_length=200, description="e.g. All staff / Security team"
    )
    training_requirement: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Primary training requirement / brief",
    )
    address_line1: str = Field(..., min_length=1, max_length=300)
    address_line2: Optional[str] = Field(default=None, max_length=300)
    city: str = Field(..., min_length=1, max_length=120)
    state: str = Field(..., min_length=1, max_length=120)
    pincode: Optional[str] = Field(default=None, max_length=20)
    preferred_date: Optional[str] = Field(
        default=None, max_length=32, description="YYYY-MM-DD"
    )
    preferred_time: Optional[str] = Field(
        default=None, max_length=64, description="e.g. Lunch break / 6:00 PM"
    )
    schedule_notes: Optional[str] = Field(
        default=None, max_length=2000, description="Weekly / session schedule"
    )
    training_type: Optional[str] = Field(
        default=None, max_length=200, description="e.g. Self-defense, wellness"
    )
    training_goals: Optional[str] = Field(default=None, max_length=2000)
    special_requirements: Optional[str] = Field(default=None, max_length=2000)


class CorporateTrainingRequestCreate(BaseModel):
    contact_name: str = Field(..., min_length=1, max_length=200)
    contact_phone: str = Field(..., min_length=5, max_length=32)
    contact_email: Optional[str] = Field(default=None, max_length=200)
    branch_id: Optional[str] = Field(default=None, max_length=64)
    branch_name: Optional[str] = Field(default=None, max_length=200)
    source: Optional[str] = Field(default="website", max_length=80)
    notes: Optional[str] = Field(default=None, max_length=2000)
    details: CorporateTrainingDetails


class ResidentialPackage(BaseModel):
    """Configurable residential package (catalog)."""

    id: str
    name: str
    duration_days: Optional[int] = None
    duration_label: str = ""
    fee_total_inr: int = Field(default=0, ge=0)
    fee_pay_now_inr: int = Field(default=0, ge=0)
    includes_accommodation: bool = True
    includes_food: bool = True
    description: Optional[str] = None
    payment_required: bool = True
    is_active: bool = True


# Default catalog — PO/client can refine later without changing camp CMS.
DEFAULT_RESIDENTIAL_PACKAGES: List[dict] = [
    {
        "id": "weekend-3d",
        "name": "Weekend Immersion (3 days)",
        "duration_days": 3,
        "duration_label": "3 days / 2 nights",
        "fee_total_inr": 15000,
        "fee_pay_now_inr": 7500,
        "includes_accommodation": True,
        "includes_food": True,
        "description": "Short residential immersion with lodging and meals.",
        "payment_required": True,
        "is_active": True,
    },
    {
        "id": "week-7d",
        "name": "Week Intensive (7 days)",
        "duration_days": 7,
        "duration_label": "7 days / 6 nights",
        "fee_total_inr": 35000,
        "fee_pay_now_inr": 15000,
        "includes_accommodation": True,
        "includes_food": True,
        "description": "Full-week residential program with lodging and meals.",
        "payment_required": True,
        "is_active": True,
    },
    {
        "id": "fortnight-14d",
        "name": "Fortnight Residential (14 days)",
        "duration_days": 14,
        "duration_label": "14 days / 13 nights",
        "fee_total_inr": 65000,
        "fee_pay_now_inr": 25000,
        "includes_accommodation": True,
        "includes_food": True,
        "description": "Extended residential training block.",
        "payment_required": True,
        "is_active": True,
    },
    {
        "id": "enquiry-custom",
        "name": "Custom / Enquiry only",
        "duration_days": None,
        "duration_label": "Custom duration",
        "fee_total_inr": 0,
        "fee_pay_now_inr": 0,
        "includes_accommodation": True,
        "includes_food": True,
        "description": "Tell us your preferred dates — we will confirm package and fees.",
        "payment_required": False,
        "is_active": True,
    },
]


class ResidentialTrainingDetails(BaseModel):
    """M12-S05 Residential — participant, package, accommodation, food, fees, admission."""

    participant_name: str = Field(..., min_length=1, max_length=200)
    participant_age: Optional[int] = Field(default=None, ge=1, le=120)
    participant_phone: str = Field(..., min_length=5, max_length=32)
    participant_email: Optional[str] = Field(default=None, max_length=200)
    gender: Optional[str] = Field(default=None, max_length=40)
    emergency_contact_name: Optional[str] = Field(default=None, max_length=200)
    emergency_contact_phone: Optional[str] = Field(default=None, max_length=32)
    package_id: str = Field(..., min_length=1, max_length=64)
    package_name: Optional[str] = Field(default=None, max_length=200)
    duration_days: Optional[int] = Field(default=None, ge=1, le=365)
    duration_label: Optional[str] = Field(default=None, max_length=120)
    preferred_start_date: Optional[str] = Field(
        default=None, max_length=32, description="YYYY-MM-DD"
    )
    preferred_end_date: Optional[str] = Field(
        default=None, max_length=32, description="YYYY-MM-DD"
    )
    accommodation: str = Field(
        default="shared_dorm",
        max_length=80,
        description="shared_dorm | twin_sharing | private_room | none",
    )
    food_preference: str = Field(
        default="veg",
        max_length=80,
        description="veg | non_veg | both | none",
    )
    medical_notes: Optional[str] = Field(default=None, max_length=2000)
    training_goals: Optional[str] = Field(default=None, max_length=2000)
    special_requirements: Optional[str] = Field(default=None, max_length=2000)
    # Fee snapshot (filled server-side from package; client may omit)
    fee_total_inr: Optional[int] = Field(default=None, ge=0)
    fee_pay_now_inr: Optional[int] = Field(default=None, ge=0)
    city: Optional[str] = Field(default=None, max_length=120)
    state: Optional[str] = Field(default=None, max_length=120)


class ResidentialTrainingRequestCreate(BaseModel):
    contact_name: str = Field(..., min_length=1, max_length=200)
    contact_phone: str = Field(..., min_length=5, max_length=32)
    contact_email: Optional[str] = Field(default=None, max_length=200)
    branch_id: Optional[str] = Field(default=None, max_length=64)
    branch_name: Optional[str] = Field(default=None, max_length=200)
    source: Optional[str] = Field(default="website", max_length=80)
    notes: Optional[str] = Field(default=None, max_length=2000)
    pay_now: bool = Field(
        default=False,
        description="If true and package requires payment, payment_status starts as pending",
    )
    details: ResidentialTrainingDetails


class TrainingRequestPaymentVerify(BaseModel):
    """M06 Razorpay verify payload for residential training requests."""

    razorpay_order_id: str = Field(..., min_length=1, max_length=128)
    razorpay_payment_id: str = Field(..., min_length=1, max_length=128)
    razorpay_signature: str = Field(..., min_length=1, max_length=256)


class TrainingRequestStatusUpdate(BaseModel):
    status: TrainingRequestStatus
    note: Optional[str] = Field(default=None, max_length=2000)


class TrainingRequestCoachAssign(BaseModel):
    coach_id: str = Field(..., min_length=1, max_length=64)
    note: Optional[str] = Field(default=None, max_length=2000)


class TrainingRequestDocument(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: TrainingRequestType = TrainingRequestType.HOME
    status: TrainingRequestStatus = TrainingRequestStatus.SUBMITTED
    contact_name: str
    contact_phone: str
    contact_email: Optional[str] = None
    branch_id: Optional[str] = None
    branch_name: Optional[str] = None
    source: Optional[str] = None
    notes: Optional[str] = None
    details: dict = Field(default_factory=dict)
    assigned_coach_id: Optional[str] = None
    assigned_coach_name: Optional[str] = None
    assigned_at: Optional[datetime] = None
    assigned_by: Optional[str] = None
    created_by: Optional[str] = None  # logged-in student id if any
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
