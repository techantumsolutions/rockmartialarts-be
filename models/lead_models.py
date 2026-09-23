from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from typing import Literal, Optional

LeadStatus = Literal["new", "contacted", "qualified", "converted", "lost"]
LEAD_STATUSES = ("new", "contacted", "qualified", "converted", "lost")

LeadFollowUpAction = Literal[
    "note", "call", "email", "meeting", "status_change", "scheduled", "other"
]
LEAD_FOLLOW_UP_ACTIONS = (
    "note",
    "call",
    "email",
    "meeting",
    "status_change",
    "scheduled",
    "other",
)


class LeadCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    # Website popup may capture phone + branch only
    email: Optional[str] = Field(default=None, max_length=200)
    phone: str = Field(..., min_length=5, max_length=32)
    course: str = Field(default="", max_length=300)
    source: Optional[str] = Field(default=None, max_length=80)
    # M15-S01 additive traceability (optional; inferred from source when omitted)
    source_type: Optional[str] = Field(default=None, max_length=80)
    source_ref_id: Optional[str] = Field(default=None, max_length=64)
    source_ref_type: Optional[str] = Field(default=None, max_length=64)
    branch_id: Optional[str] = Field(default=None, max_length=64)
    branch_name: Optional[str] = Field(default=None, max_length=200)
    # M21-S11 partner landing optional message
    message: Optional[str] = Field(default=None, max_length=2000)

    class Config:
        extra = "ignore"


class LeadOtpSendBody(BaseModel):
    phone: str = Field(..., min_length=10, max_length=20)


class LeadOtpVerifyBody(BaseModel):
    phone: str = Field(..., min_length=10, max_length=20)
    otp: str = Field(..., min_length=4, max_length=8)


class LeadStatusUpdate(BaseModel):
    """Backward-compatible status PATCH. Optional note is logged to follow-up history."""
    status: LeadStatus
    note: Optional[str] = Field(default=None, max_length=2000)


class LeadFollowUpCreate(BaseModel):
    """M15-S03: log a follow-up (note/call/etc.), optionally change status and schedule next."""
    note: Optional[str] = Field(default=None, max_length=2000)
    action: LeadFollowUpAction = "note"
    status: Optional[LeadStatus] = None
    next_follow_up_at: Optional[datetime] = None
    clear_next_follow_up: bool = False

    @field_validator("note", mode="before")
    @classmethod
    def _empty_note(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class LeadFollowUpEventResponse(BaseModel):
    id: str
    lead_id: str
    action: str
    note: Optional[str] = None
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    next_follow_up_at: Optional[datetime] = None
    actor_id: Optional[str] = None
    actor_name: Optional[str] = None
    actor_role: Optional[str] = None
    created_at: datetime


class LeadResponse(BaseModel):
    id: str
    name: str
    email: str
    phone: str
    course: str
    source: Optional[str] = None
    source_type: Optional[str] = None
    source_ref_id: Optional[str] = None
    source_ref_type: Optional[str] = None
    branch_id: Optional[str] = None
    branch_name: Optional[str] = None
    message: Optional[str] = None
    status: LeadStatus = "new"
    phone_canonical: Optional[str] = None
    capture_count: int = 1
    last_captured_at: Optional[datetime] = None
    duplicate_merged: bool = False
    # M15-S03 pipeline fields (additive)
    next_follow_up_at: Optional[datetime] = None
    last_follow_up_at: Optional[datetime] = None
    last_follow_up_note: Optional[str] = None
    # M15-S04 coach assignment summary (additive)
    coach_assignment_id: Optional[str] = None
    assigned_coach_id: Optional[str] = None
    assigned_coach_name: Optional[str] = None
    coach_assignment_status: Optional[str] = None
    # M15-S05 session booking summary
    next_session_booking_id: Optional[str] = None
    next_session_at: Optional[str] = None
    session_booking_status: Optional[str] = None
    created_at: datetime
