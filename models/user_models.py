from pydantic import BaseModel, Field, EmailStr
from datetime import datetime, date
from typing import Optional, Literal
from enum import Enum
import uuid

class UserRole(str, Enum):
    SUPER_ADMIN = "super_admin"
    COACH_ADMIN = "coach_admin"
    COACH = "coach"
    BRANCH_MANAGER = "branch_manager"
    STUDENT = "student"

# DEPRECATED: These classes are being phased out in favor of proper enrollment records
# They remain here temporarily for backward compatibility during migration
class CourseInfo(BaseModel):
    category_id: str
    course_id: str
    duration: str
    batch_ref: Optional[str] = None

class BranchInfo(BaseModel):
    location_id: str
    branch_id: str

class BaseUser(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    email: EmailStr
    phone: str
    first_name: str
    last_name: str
    full_name: str  # Auto-generated from first_name + last_name
    role: UserRole
    biometric_id: Optional[str] = None
    # ESSL biometric mapping (employee code / device user_id)
    essl_user_id: Optional[str] = None
    is_active: bool = True
    date_of_birth: Optional[date] = None
    gender: Optional[str] = None
    # Branch assignment for staff members (coaches, admins)
    branch_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class UserCreate(BaseModel):
    email: EmailStr
    phone: str
    first_name: str
    last_name: str
    role: UserRole
    password: Optional[str] = None
    date_of_birth: Optional[date] = None
    gender: Optional[str] = None
    biometric_id: Optional[str] = None
    essl_user_id: Optional[str] = None
    address: Optional[dict] = None  # { line1, area, city, state, pincode, country }
    emergency_contact: Optional[dict] = None  # { name, phone, relationship }
    branch_id: Optional[str] = None
    course: Optional[CourseInfo] = None
    branch: Optional[BranchInfo] = None
    # Master Data key student_levels (e.g. Beginner, Intermediate, Expert)
    student_level: Optional[str] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class ForgotPassword(BaseModel):
    email: EmailStr

class ResetPassword(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8)


class PasswordResetSendOtpBody(BaseModel):
    """Student password reset: Indian mobile in E.164 (+91…) or national variants (normalized server-side)."""
    phone: str = Field(..., min_length=10, max_length=22)


class PasswordResetVerifyOtpBody(BaseModel):
    phone: str
    otp: str = Field(..., min_length=6, max_length=6)

class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    branch_id: Optional[str] = None
    biometric_id: Optional[str] = None
    essl_user_id: Optional[str] = None
    is_active: Optional[bool] = None
    date_of_birth: Optional[date] = None
    gender: Optional[str] = None
    address: Optional[dict] = None  # { line1, area, city, state, pincode, country }
    emergency_contact: Optional[dict] = None  # { name, phone, relationship }
    # DEPRECATED: Course enrollment should be handled via enrollments collection
    course: Optional[CourseInfo] = None
    branch: Optional[BranchInfo] = None
    course_category_id: Optional[str] = None
    course_id: Optional[str] = None
    course_duration: Optional[str] = None
    location_id: Optional[str] = None
    student_level: Optional[str] = None
    # Primary active enrollment date overrides (persisted on enrollments collection)
    enrollment_start_date: Optional[date] = None
    enrollment_end_date: Optional[date] = None


class StudentNotifyBody(BaseModel):
    """Super-admin triggered transactional messages (SMS + WhatsApp helpers)."""
    kind: Literal["welcome", "payment_reminder"]


class StudentStatusUpdateBody(BaseModel):
    """M08-S01: update student account lifecycle status (users.is_active)."""
    is_active: bool
    reason: Optional[str] = Field(
        None,
        description="Required when deactivating; optional when reactivating.",
        max_length=500,
    )


# Student Profile Models
class StudentAddress(BaseModel):
    street: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = "India"

class StudentEmergencyContact(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    relationship: Optional[str] = None

class StudentMedicalInfo(BaseModel):
    allergies: Optional[str] = None
    medications: Optional[str] = None
    conditions: Optional[str] = None
    blood_type: Optional[str] = None

class StudentProfileResponse(BaseModel):
    id: str
    email: EmailStr
    phone: str
    first_name: str
    last_name: str
    full_name: str
    essl_user_id: Optional[str] = None
    date_of_birth: Optional[date] = None
    gender: Optional[str] = None
    address: Optional[StudentAddress] = None
    emergency_contact: Optional[StudentEmergencyContact] = None
    medical_info: Optional[StudentMedicalInfo] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    # Enrollment information
    enrollments: Optional[list] = []

class StudentProfileUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    date_of_birth: Optional[date] = None
    gender: Optional[str] = None
    address: Optional[StudentAddress] = None
    emergency_contact: Optional[StudentEmergencyContact] = None
    medical_info: Optional[StudentMedicalInfo] = None
