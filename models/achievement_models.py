from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List
import uuid


class StudentAchievement(BaseModel):
    """Student achievement record. Stored in student_achievements collection."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    student_id: str = Field(..., description="User ID of the student")
    branch_id: str = Field(..., description="Branch ID (derived from student)")
    title: str = Field(..., min_length=1)
    description: Optional[str] = None
    images: List[str] = Field(default_factory=list, description="List of image URLs")
    documents: List[str] = Field(default_factory=list, description="List of document/certificate URLs")
    is_featured: bool = Field(default=False, description="Show on homepage when true (super admin)")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    is_deleted: bool = Field(default=False, description="Soft delete")


class AchievementCreate(BaseModel):
    student_id: str
    title: str
    description: Optional[str] = None
    images: List[str] = Field(default_factory=list)
    documents: List[str] = Field(default_factory=list)


class AchievementUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    images: Optional[List[str]] = None
    documents: Optional[List[str]] = None
    is_featured: Optional[bool] = None


class AchievementFeaturedUpdate(BaseModel):
    is_featured: bool
