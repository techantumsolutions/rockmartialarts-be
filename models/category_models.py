from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List
import uuid

class Category(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    code: str  # Unique category code
    description: Optional[str] = None
    parent_category_id: Optional[str] = None  # For hierarchical categories
    slug: Optional[str] = None
    is_active: bool = True
    display_order: int = 0  # For sorting categories
    icon_url: Optional[str] = None
    color_code: Optional[str] = None  # For UI theming
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class CategoryCreate(BaseModel):
    name: str
    code: str
    description: Optional[str] = None
    parent_category_id: Optional[str] = None
    slug: Optional[str] = None
    is_active: bool = True
    display_order: int = 0
    icon_url: Optional[str] = None
    color_code: Optional[str] = None

class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    description: Optional[str] = None
    parent_category_id: Optional[str] = None
    slug: Optional[str] = None
    is_active: Optional[bool] = None
    display_order: Optional[int] = None
    icon_url: Optional[str] = None
    color_code: Optional[str] = None

class CategoryResponse(BaseModel):
    id: str
    name: str
    code: str
    description: Optional[str] = None
    parent_category_id: Optional[str] = None
    slug: Optional[str] = None
    is_active: bool
    display_order: int
    icon_url: Optional[str] = None
    color_code: Optional[str] = None
    subcategories: Optional[List['CategoryResponse']] = None  # For hierarchical display
    course_count: Optional[int] = None  # Number of courses in this category
    created_at: datetime
    updated_at: datetime

# Enable forward references for recursive model
CategoryResponse.model_rebuild()
