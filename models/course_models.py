from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, Dict, Any, List
import uuid

class StudentRequirements(BaseModel):
    max_students: int
    min_age: int
    max_age: int
    prerequisites: List[str]

class CourseContent(BaseModel):
    syllabus: str
    equipment_required: List[str]

class MediaResources(BaseModel):
    course_image_url: Optional[str] = None
    promo_video_url: Optional[str] = None


class CourseSEO(BaseModel):
    meta_title: Optional[str] = None
    meta_description: Optional[str] = None
    meta_keywords: Optional[str] = None
    og_image: Optional[str] = None

class BranchPriceEntry(BaseModel):
    branch_id: str
    amount: Optional[float] = None
    currency: Optional[str] = None
    fee_1_month: Optional[float] = None
    fee_3_months: Optional[float] = None
    fee_6_months: Optional[float] = None
    fee_1_year: Optional[float] = None
    # New: flexible per-duration fees keyed by duration id/code (used by tenure UI)
    fee_per_duration: Optional[Dict[str, float]] = None
    # Flat/offer price per duration (overrides calculated fee when present)
    flat_price_per_duration: Optional[Dict[str, float]] = None


class Pricing(BaseModel):
    currency: str
    amount: float
    branch_specific_pricing: bool
    fee_1_month: Optional[float] = None
    fee_3_months: Optional[float] = None
    fee_6_months: Optional[float] = None
    fee_1_year: Optional[float] = None
    branch_prices: Optional[List[BranchPriceEntry]] = None
    # New: flexible per-duration fees keyed by duration id/code (used by tenure UI)
    fee_per_duration: Optional[Dict[str, float]] = None
    # Flat/offer price per duration (overrides calculated fee when present)
    flat_price_per_duration: Optional[Dict[str, float]] = None

class Settings(BaseModel):
    offers_certification: bool
    active: bool

class Course(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    code: str
    description: str
    martial_art_style_id: str
    difficulty_level: str
    category_id: str
    # New: explicit sub-category id (child category) stored separately from parent category
    sub_category: Optional[str] = None
    slug: Optional[str] = None
    instructor_id: str
    student_requirements: StudentRequirements
    course_content: CourseContent
    media_resources: MediaResources
    pricing: Pricing
    settings: Settings
    # Public course page: hero_section (bullet_points[]), about_section (content_blocks: [{ title, description, bullet_points[], image? }]), etc.
    page_content: Optional[Dict[str, Any]] = None
    seo: Optional[CourseSEO] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class CourseCreate(BaseModel):
    title: str
    code: str
    description: str
    martial_art_style_id: str
    difficulty_level: str
    category_id: str
    # New: sub-category id on create
    sub_category: Optional[str] = None
    slug: Optional[str] = None
    instructor_id: str
    student_requirements: StudentRequirements
    course_content: CourseContent
    media_resources: MediaResources
    pricing: Pricing
    settings: Settings
    page_content: Optional[Dict[str, Any]] = None
    seo: Optional[CourseSEO] = None

class CourseUpdate(BaseModel):
    title: Optional[str] = None
    code: Optional[str] = None
    description: Optional[str] = None
    duration: Optional[str] = None  # duration id from master data
    martial_art_style_id: Optional[str] = None
    difficulty_level: Optional[str] = None
    category_id: Optional[str] = None
    # New: allow updating stored sub-category id
    sub_category: Optional[str] = None
    slug: Optional[str] = None
    instructor_id: Optional[str] = None
    student_requirements: Optional[StudentRequirements] = None
    course_content: Optional[CourseContent] = None
    media_resources: Optional[MediaResources] = None
    pricing: Optional[Pricing] = None
    settings: Optional[Settings] = None
    page_content: Optional[Dict[str, Any]] = None
    seo: Optional[CourseSEO] = None
