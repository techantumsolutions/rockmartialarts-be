"""Registration form PDF management for student portal downloads."""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
import uuid

from pydantic import BaseModel, Field, model_validator


AvailabilityType = Literal["global", "branch", "course", "branch_course"]
FormStatus = Literal["active", "inactive"]


class BranchCoursePair(BaseModel):
    branch_id: str = Field(..., min_length=1)
    course_id: str = Field(..., min_length=1)


class RegistrationFormCreate(BaseModel):
    name: str = Field(..., min_length=1)
    description: Optional[str] = None
    file_url: Optional[str] = None
    status: FormStatus = "inactive"
    display_order: int = 0
    availability_type: AvailabilityType = "global"
    branch_ids: Optional[List[str]] = None
    course_ids: Optional[List[str]] = None
    branch_course_pairs: Optional[List[BranchCoursePair]] = None

    @model_validator(mode="after")
    def validate_availability(self) -> "RegistrationFormCreate":
        branch_ids = self.branch_ids or []
        course_ids = self.course_ids or []
        pairs = self.branch_course_pairs or []
        file_url = (self.file_url or "").strip()

        if self.status == "active" and not file_url:
            raise ValueError("A PDF must be uploaded before the form can be activated")

        if self.availability_type == "branch" and not branch_ids:
            raise ValueError("At least one branch is required for branch availability")
        if self.availability_type == "course" and not course_ids:
            raise ValueError("At least one course is required for course availability")
        if self.availability_type == "branch_course" and not pairs:
            raise ValueError("At least one branch and course combination is required")

        return self


class RegistrationFormUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    description: Optional[str] = None
    file_url: Optional[str] = None
    status: Optional[FormStatus] = None
    display_order: Optional[int] = None
    availability_type: Optional[AvailabilityType] = None
    branch_ids: Optional[List[str]] = None
    course_ids: Optional[List[str]] = None
    branch_course_pairs: Optional[List[BranchCoursePair]] = None


def new_doc_id() -> str:
    return str(uuid.uuid4())


def stamp_created_by(current_user: dict) -> Dict[str, Any]:
    role = current_user.get("role") or ""
    if role == "super_admin":
        r = "super_admin"
    elif role == "branch_manager":
        r = "branch_manager"
    else:
        r = "branch_manager"
    return {"role": r, "user_id": current_user.get("id") or ""}
