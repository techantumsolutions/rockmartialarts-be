"""M05-S04 enrollment discount rule models (Mongo `discount_rules` collection)."""
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field
import uuid


class DiscountKind(str, Enum):
    PERCENTAGE = "percentage"
    FIXED = "fixed"


class DiscountTrigger(str, Enum):
    MULTI_STUDENT = "multi_student"
    MULTI_COURSE_CART = "multi_course_cart"
    MULTI_COURSE_STUDENT = "multi_course_student"
    COMBINATION = "combination"
    CART_MIN_AMOUNT = "cart_min_amount"


class DiscountApplyScope(str, Enum):
    CART = "cart"
    PER_STUDENT_LINE = "per_student_line"


class DiscountRuleDocument(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    code: str
    name: str
    description: Optional[str] = None
    discount_kind: DiscountKind = DiscountKind.PERCENTAGE
    discount_value: float = Field(..., gt=0)
    trigger: DiscountTrigger
    apply_scope: DiscountApplyScope = DiscountApplyScope.CART
    min_students: int = Field(default=2, ge=1)
    min_cart_items: int = Field(default=2, ge=1)
    min_courses_per_student: int = Field(default=2, ge=1)
    min_cart_amount: float = Field(default=0, ge=0)
    max_discount_amount: Optional[float] = Field(default=None, ge=0)
    stackable: bool = False
    priority: int = Field(default=100, ge=0)
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    is_active: bool = True
    branch_ids: List[str] = Field(default_factory=list)
    course_ids: List[str] = Field(default_factory=list)
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class DiscountRuleCreate(BaseModel):
    code: str
    name: str
    description: Optional[str] = None
    discount_kind: DiscountKind = DiscountKind.PERCENTAGE
    discount_value: float = Field(..., gt=0)
    trigger: DiscountTrigger
    apply_scope: DiscountApplyScope = DiscountApplyScope.CART
    min_students: int = Field(default=2, ge=1)
    min_cart_items: int = Field(default=2, ge=1)
    min_courses_per_student: int = Field(default=2, ge=1)
    min_cart_amount: float = Field(default=0, ge=0)
    max_discount_amount: Optional[float] = Field(default=None, ge=0)
    stackable: bool = False
    priority: int = Field(default=100, ge=0)
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    is_active: bool = True
    branch_ids: List[str] = Field(default_factory=list)
    course_ids: List[str] = Field(default_factory=list)


class DiscountRuleUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    discount_kind: Optional[DiscountKind] = None
    discount_value: Optional[float] = Field(default=None, gt=0)
    trigger: Optional[DiscountTrigger] = None
    apply_scope: Optional[DiscountApplyScope] = None
    min_students: Optional[int] = Field(default=None, ge=1)
    min_cart_items: Optional[int] = Field(default=None, ge=1)
    min_courses_per_student: Optional[int] = Field(default=None, ge=1)
    min_cart_amount: Optional[float] = Field(default=None, ge=0)
    max_discount_amount: Optional[float] = Field(default=None, ge=0)
    stackable: Optional[bool] = None
    priority: Optional[int] = Field(default=None, ge=0)
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    is_active: Optional[bool] = None
    branch_ids: Optional[List[str]] = None
    course_ids: Optional[List[str]] = None
