from pydantic import BaseModel, Field, EmailStr
from datetime import datetime
from typing import List, Optional, Dict
from enum import Enum
import uuid

# Assignment details for coaches
class AssignmentDetails(BaseModel):
    courses: List[str] = Field(default_factory=list)  # List of course IDs assigned to the coach
    salary: Optional[float] = None  # Coach's salary
    join_date: Optional[str] = None  # Date when coach joined (ISO format)

# Emergency contact information
class EmergencyContact(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    relationship: Optional[str] = None

class PersonalInfo(BaseModel):
    first_name: str
    last_name: str
    gender: str
    date_of_birth: str  # YYYY-MM-DD format

class ContactInfo(BaseModel):
    email: EmailStr
    country_code: str
    phone: str
    password: Optional[str] = None

class AddressInfo(BaseModel):
    address: str
    area: str
    city: str
    state: str
    zip_code: str
    country: str

# Qualification item with name and year
class Qualification(BaseModel):
    name: str
    year: str

class ProfessionalInfo(BaseModel):
    education_qualification: str
    professional_experience: str
    designation_id: str
    certifications: List[str]
    qualifications: Optional[List[Qualification]] = Field(default_factory=list)  # New field for structured qualifications
    category_id: Optional[str] = None  # New field for category
    sub_category_id: Optional[str] = None  # New field for sub-category


class CoachApprovalStatus(str, Enum):
    """M14 coach registration / approval gate (additive)."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class CoachRegistrationCreate(BaseModel):
    """
    M14-S01 public self-registration payload.
    Does not replace admin CoachCreate.
    """

    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    gender: str = Field(..., min_length=1, max_length=40)
    date_of_birth: str = Field(..., description="YYYY-MM-DD")
    email: EmailStr
    country_code: str = Field(default="+91", max_length=8)
    phone: str = Field(..., min_length=5, max_length=20)
    password: str = Field(..., min_length=6, max_length=128)
    address: str = Field(..., min_length=1, max_length=300)
    area: str = Field(default="", max_length=120)
    city: str = Field(..., min_length=1, max_length=120)
    state: str = Field(..., min_length=1, max_length=120)
    zip_code: str = Field(default="", max_length=20)
    country: str = Field(default="India", max_length=80)
    professional_experience: str = Field(
        ..., min_length=1, max_length=500, description="Experience summary or range"
    )
    education_qualification: Optional[str] = Field(default=None, max_length=200)
    designation: Optional[str] = Field(default="Coach", max_length=120)
    specializations: List[str] = Field(..., min_length=1)
    certifications: List[str] = Field(default_factory=list)
    service_location_ids: List[str] = Field(
        ...,
        min_length=1,
        description="Branch IDs where the coach can serve",
    )
    profile_image_url: Optional[str] = Field(default=None, max_length=500)
    about_short: Optional[str] = Field(default=None, max_length=1000)
    user_id: Optional[str] = Field(
        default=None, max_length=64, description="Optional linked user id"
    )


class CoachCreate(BaseModel):
    personal_info: PersonalInfo
    contact_info: ContactInfo
    address_info: AddressInfo
    professional_info: ProfessionalInfo
    areas_of_expertise: List[str]
    branch_id: Optional[str] = None  # Branch assignment for the coach
    assignment_details: Optional[AssignmentDetails] = None  # Course assignments and other details
    emergency_contact: Optional[EmergencyContact] = None  # Emergency contact information
    profile_image_url: Optional[str] = None
    about_short: Optional[str] = None
    featured_on_homepage: bool = False
    homepage_rating: Optional[float] = None
    display_order: Optional[int] = None

class CoachUpdate(BaseModel):
    personal_info: Optional[PersonalInfo] = None
    contact_info: Optional[ContactInfo] = None
    address_info: Optional[AddressInfo] = None
    professional_info: Optional[ProfessionalInfo] = None
    areas_of_expertise: Optional[List[str]] = None
    branch_id: Optional[str] = None  # Branch assignment for the coach
    assignment_details: Optional[AssignmentDetails] = None  # Course assignments and other details
    emergency_contact: Optional[EmergencyContact] = None  # Emergency contact information
    is_active: Optional[bool] = None  # Coach active/inactive status
    profile_image_url: Optional[str] = None
    about_short: Optional[str] = None
    featured_on_homepage: Optional[bool] = None
    homepage_rating: Optional[float] = None
    display_order: Optional[int] = None

class CoachLogin(BaseModel):
    email: EmailStr
    password: str

class CoachLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    coach: dict
    expires_in: int

class Coach(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    personal_info: PersonalInfo
    contact_info: ContactInfo
    address_info: AddressInfo
    professional_info: ProfessionalInfo
    areas_of_expertise: List[str]
    branch_id: Optional[str] = None  # Branch assignment for the coach
    assignment_details: Optional[AssignmentDetails] = None  # Course assignments and other details
    emergency_contact: Optional[EmergencyContact] = None  # Emergency contact information
    # Basic user fields for authentication and identification
    email: EmailStr  # Duplicate from contact_info for easy access
    phone: str  # Full phone with country code
    first_name: str  # Duplicate from personal_info for easy access
    last_name: str  # Duplicate from personal_info for easy access
    full_name: str  # Auto-generated from first_name + last_name
    role: str = "coach"  # Fixed as coach
    is_active: bool = True
    password_hash: str  # Hashed password
    profile_image_url: Optional[str] = None
    about_short: Optional[str] = None
    featured_on_homepage: bool = False
    homepage_rating: Optional[float] = None
    display_order: Optional[int] = None
    # M14 additive fields (optional on legacy records)
    approval_status: str = CoachApprovalStatus.APPROVED.value
    user_id: Optional[str] = None
    service_location_ids: List[str] = Field(default_factory=list)
    registration_source: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class CoachResponse(BaseModel):
    id: str
    personal_info: PersonalInfo
    contact_info: dict  # ContactInfo without password
    address_info: AddressInfo
    professional_info: ProfessionalInfo
    areas_of_expertise: List[str]
    branch_id: Optional[str] = None  # Branch assignment for the coach
    assignment_details: Optional[AssignmentDetails] = None  # Course assignments and other details
    emergency_contact: Optional[EmergencyContact] = None  # Emergency contact information
    full_name: str
    is_active: bool
    profile_image_url: Optional[str] = None
    about_short: Optional[str] = None
    featured_on_homepage: bool = False
    homepage_rating: Optional[float] = None
    display_order: Optional[int] = None
    approval_status: Optional[str] = None
    user_id: Optional[str] = None
    service_location_ids: Optional[List[str]] = None
    registration_source: Optional[str] = None
    created_at: datetime
    updated_at: datetime

class CoachForgotPassword(BaseModel):
    email: EmailStr

class CoachResetPassword(BaseModel):
    token: str
    new_password: str


class CoachApprovalActionBody(BaseModel):
    """M14-S02 approve action (optional admin note)."""

    note: Optional[str] = Field(default=None, max_length=1000)


class CoachRejectBody(BaseModel):
    """M14-S02 reject action — note recommended for audit."""

    note: Optional[str] = Field(default=None, max_length=1000)


class CoachActiveStatusBody(BaseModel):
    """M14-S02 set active/inactive (approved coaches only)."""

    is_active: bool
    note: Optional[str] = Field(default=None, max_length=1000)


class CoachApprovalHistoryEntry(BaseModel):
    id: str
    coach_id: str
    from_status: Optional[str] = None
    to_status: str
    action: str
    note: Optional[str] = None
    is_active_before: Optional[bool] = None
    is_active_after: Optional[bool] = None
    actor_id: Optional[str] = None
    actor_name: Optional[str] = None
    actor_role: Optional[str] = None
    created_at: datetime
