"""M05-S01 enrollment cart models (Mongo `carts` collection)."""
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field
import uuid


class CartStatus(str, Enum):
    ACTIVE = "active"
    CHECKED_OUT = "checked_out"
    ABANDONED = "abandoned"


class CartStudentLine(BaseModel):
    student_line_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    student_id: Optional[str] = None
    label: str
    email: Optional[str] = None
    phone: Optional[str] = None


class CartItemPricing(BaseModel):
    course_fee: float
    admission_fee: float = 0.0
    total_amount: float
    currency: str = "INR"
    duration_multiplier: float = 1.0
    original_price: Optional[float] = None
    discount_amount: Optional[float] = None
    is_flat_price: bool = False


class CartItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    student_line_id: str
    course_id: str
    branch_id: str
    duration_id: str
    batch_ref: Optional[str] = None
    course_name: str = ""
    branch_name: str = ""
    category_name: str = ""
    duration_name: str = ""
    pricing: CartItemPricing


class CartTotals(BaseModel):
    currency: str = "INR"
    course_fee_subtotal: float = 0.0
    admission_fee_subtotal: float = 0.0
    subtotal_amount: float = 0.0
    promo_discount_total: float = 0.0
    total_amount: float = 0.0
    item_count: int = 0
    student_count: int = 0


class CartValidationIssue(BaseModel):
    code: str
    message: str
    item_id: Optional[str] = None
    student_line_id: Optional[str] = None


class CartStudentCreate(BaseModel):
    label: str
    student_id: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None


class CartStudentsBulkCreate(BaseModel):
    """Add several student lines to one cart (M05-S03)."""
    students: List[CartStudentCreate] = Field(..., min_length=1, max_length=15)


class CartStudentUpdate(BaseModel):
    label: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None


class CartItemCreate(BaseModel):
    student_line_id: str
    course_id: str
    branch_id: str
    duration_id: str
    batch_ref: Optional[str] = None


class CartMultiCourseSelection(BaseModel):
    course_id: str
    duration_id: str
    batch_ref: Optional[str] = None


class CartMultiCourseAddBody(BaseModel):
    """Add several courses for one student at one branch (M05-S02)."""
    student_line_id: str
    branch_id: str
    selections: List[CartMultiCourseSelection]


class CartItemUpdate(BaseModel):
    duration_id: Optional[str] = None
    batch_ref: Optional[str] = None


class CartEnsureBody(BaseModel):
    guest_token: Optional[str] = None


class CartDocument(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    owner_user_id: Optional[str] = None
    guest_token: Optional[str] = None
    status: CartStatus = CartStatus.ACTIVE
    students: List[CartStudentLine] = Field(default_factory=list)
    items: List[CartItem] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
