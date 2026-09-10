from pydantic import BaseModel, Field
from datetime import datetime
from typing import Literal, Optional

LeadStatus = Literal["new", "contacted", "qualified", "converted", "lost"]
LEAD_STATUSES = ("new", "contacted", "qualified", "converted", "lost")


class LeadCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    # Website popup may capture phone + branch only
    email: Optional[str] = Field(default=None, max_length=200)
    phone: str = Field(..., min_length=5, max_length=32)
    course: str = Field(default="", max_length=300)
    source: Optional[str] = Field(default=None, max_length=80)
    branch_id: Optional[str] = Field(default=None, max_length=64)
    branch_name: Optional[str] = Field(default=None, max_length=200)

    class Config:
        extra = "ignore"


class LeadOtpSendBody(BaseModel):
    phone: str = Field(..., min_length=10, max_length=20)


class LeadOtpVerifyBody(BaseModel):
    phone: str = Field(..., min_length=10, max_length=20)
    otp: str = Field(..., min_length=4, max_length=8)


class LeadStatusUpdate(BaseModel):
    status: LeadStatus


class LeadResponse(BaseModel):
    id: str
    name: str
    email: str
    phone: str
    course: str
    source: Optional[str] = None
    branch_id: Optional[str] = None
    branch_name: Optional[str] = None
    status: LeadStatus = "new"
    created_at: datetime
