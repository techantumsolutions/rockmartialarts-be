from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, Dict
import uuid


class BranchCourse(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    branch_id: str
    course_id: str
    is_available: bool = True
    fee_per_duration: Optional[Dict[str, float]] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class BranchCourseUpsert(BaseModel):
    branch_id: str
    course_id: str
    is_available: bool = True
    fee_per_duration: Optional[Dict[str, float]] = None


class BranchCourseUpdate(BaseModel):
    is_available: Optional[bool] = None
    fee_per_duration: Optional[Dict[str, float]] = None
