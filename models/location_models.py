from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List
import uuid

class Location(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str  # e.g., "Hyderabad", "Mumbai", "Delhi"
    code: str  # e.g., "HYD", "MUM", "DEL"
    state: str
    state_id: Optional[str] = None  # FK to states; additive, existing docs may omit
    slug: Optional[str] = None
    country: str = "India"
    timezone: str = "Asia/Kolkata"
    is_active: bool = True
    display_order: int = 0  # For sorting locations
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class LocationCreate(BaseModel):
    name: str
    code: str
    state: str
    state_id: Optional[str] = None
    slug: Optional[str] = None
    country: str = "India"
    timezone: str = "Asia/Kolkata"
    is_active: bool = True
    display_order: int = 0
    description: Optional[str] = None

class LocationUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    state: Optional[str] = None
    state_id: Optional[str] = None
    slug: Optional[str] = None
    country: Optional[str] = None
    timezone: Optional[str] = None
    is_active: Optional[bool] = None
    display_order: Optional[int] = None
    description: Optional[str] = None

class CityCreate(BaseModel):
    """Admin city payload; persisted in the existing locations collection."""
    name: str
    state_id: str
    code: Optional[str] = None
    slug: Optional[str] = None
    country: str = "India"
    timezone: str = "Asia/Kolkata"
    is_active: bool = True
    display_order: int = 0
    description: Optional[str] = None

class CityUpdate(BaseModel):
    name: Optional[str] = None
    state_id: Optional[str] = None
    code: Optional[str] = None
    slug: Optional[str] = None
    country: Optional[str] = None
    timezone: Optional[str] = None
    is_active: Optional[bool] = None
    display_order: Optional[int] = None
    description: Optional[str] = None

class LocationWithBranches(BaseModel):
    id: str
    name: str
    code: str
    state: str
    state_id: Optional[str] = None
    slug: Optional[str] = None
    country: str
    timezone: str
    is_active: bool
    display_order: int
    description: Optional[str] = None
    branch_count: int
    branches: List[dict]  # List of branch objects
    created_at: datetime
    updated_at: datetime

class LocationResponse(BaseModel):
    id: str
    name: str
    code: str
    state: str
    state_id: Optional[str] = None
    slug: Optional[str] = None
    country: str
    timezone: str
    is_active: bool
    display_order: int
    description: Optional[str] = None
    branch_count: Optional[int] = None
    created_at: datetime
    updated_at: datetime
