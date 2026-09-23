from pydantic import BaseModel, Field, EmailStr, AliasChoices, ConfigDict
from datetime import datetime, date
from typing import Optional, Dict, List
import uuid

class Address(BaseModel):
    line1: str
    area: str
    city: str
    state: str
    pincode: str
    country: str

class BranchInfo(BaseModel):
    name: str
    code: str
    email: EmailStr
    phone: str
    address: Address

class OperationalTiming(BaseModel):
    day: str
    open: str  # Format: "HH:MM"
    close: str  # Format: "HH:MM"

class OperationalDetails(BaseModel):
    courses_offered: List[str]  # Course names for display purposes
    timings: List[OperationalTiming]
    holidays: List[str]  # List of date strings in YYYY-MM-DD format


class AssignmentBatch(BaseModel):
    """Per-batch schedule when a course is offered at a branch (optional; persisted with branch)."""
    model_config = ConfigDict(extra="ignore")

    start_time: str = Field(
        default="",
        validation_alias=AliasChoices("start_time", "startTime"),
    )
    end_time: str = Field(
        default="",
        validation_alias=AliasChoices("end_time", "endTime"),
    )
    coach_id: str = Field(
        default="",
        validation_alias=AliasChoices("coach_id", "coachId"),
    )
    days: List[str] = Field(default_factory=list)
    batch_name: str = Field(
        default="",
        validation_alias=AliasChoices("batch_name", "name"),
        description="Optional display name in admin / registration (e.g. Morning batch).",
    )
    batch_id: str = Field(
        default="",
        validation_alias=AliasChoices("batch_id", "id"),
        description="Stable id for registration / pricing (persisted).",
    )
    batch_fee: Optional[float] = Field(
        default=None,
        validation_alias=AliasChoices("batch_fee", "batchFee", "fee", "price"),
        description="Per-batch course fee at this branch (overrides tenure-based fee when set).",
    )
    fee_per_duration: Optional[Dict[str, float]] = Field(
        default=None,
        validation_alias=AliasChoices("fee_per_duration", "feePerDuration"),
        description="Per-duration pricing: { duration_id: fee_amount }.",
    )
    pricing_type_per_duration: Optional[Dict[str, str]] = Field(
        default=None,
        validation_alias=AliasChoices("pricing_type_per_duration", "pricingTypePerDuration"),
        description="Per-duration pricing type: { duration_id: 'monthly' | 'flat' }.",
    )
    enabled_per_duration: Optional[Dict[str, bool]] = Field(
        default=None,
        validation_alias=AliasChoices("enabled_per_duration", "enabledPerDuration"),
        description="Per-duration enable/disable map: { duration_id: true|false }.",
    )


class CourseAssignmentDetail(BaseModel):
    model_config = ConfigDict(extra="ignore")

    course_id: str = Field(validation_alias=AliasChoices("course_id", "courseId"))
    is_available: bool = True
    batches: List[AssignmentBatch] = Field(default_factory=list)


class Assignments(BaseModel):
    model_config = ConfigDict(extra="ignore")

    accessories_available: bool
    courses: List[str]  # List of course IDs (UUIDs)
    branch_admins: List[str]  # List of user IDs (UUIDs) for coaches
    course_schedule: Optional[List[CourseAssignmentDetail]] = Field(
        default=None,
        validation_alias=AliasChoices("course_schedule", "courseSchedule"),
    )

class BankDetails(BaseModel):
    bank_name: str
    account_number: str
    upi_id: str

class Branch(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    branch: BranchInfo
    location_id: str  # City UUID (locations.id); legacy docs may still store a city name
    manager_id: str
    operational_details: OperationalDetails
    assignments: Assignments
    bank_details: BankDetails
    admission_fee: float = 500.0
    slug: Optional[str] = None
    allows_collaboration: bool = False  # M21 — collaboration partner branch
    is_collaboration_partner: bool = False  # Alias of allows_collaboration (API/UI)
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class BranchCreate(BaseModel):
    branch: BranchInfo
    location_id: str  # City UUID (locations.id)
    manager_id: str
    operational_details: OperationalDetails
    assignments: Assignments
    bank_details: BankDetails
    admission_fee: float = 500.0
    slug: Optional[str] = None
    allows_collaboration: bool = False
    is_collaboration_partner: Optional[bool] = None

class BranchUpdate(BaseModel):
    branch: Optional[BranchInfo] = None
    location_id: Optional[str] = None
    manager_id: Optional[str] = None
    operational_details: Optional[OperationalDetails] = None
    assignments: Optional[Assignments] = None
    bank_details: Optional[BankDetails] = None
    admission_fee: Optional[float] = None
    slug: Optional[str] = None
    allows_collaboration: Optional[bool] = None
    is_collaboration_partner: Optional[bool] = None
    is_active: Optional[bool] = None
