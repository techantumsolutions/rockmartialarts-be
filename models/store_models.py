from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List
import uuid


class StoreCategory(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    slug: str
    description: Optional[str] = None
    display_order: int = 0
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class StoreCategoryCreate(BaseModel):
    name: str
    slug: Optional[str] = None
    description: Optional[str] = None
    display_order: Optional[int] = 0
    is_active: Optional[bool] = True


class StoreCategoryUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    display_order: Optional[int] = None
    is_active: Optional[bool] = None


class StoreProduct(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    description: str
    category_id: str
    category_slug: Optional[str] = None
    category_name: Optional[str] = None
    price: float
    offer_percentage: float = 0.0  # Percentage discount, e.g. 10.0 for 10%
    final_price: float = 0.0        # Computed price after discount
    stock: int = 0
    image_url: Optional[str] = None
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class StoreProductCreate(BaseModel):
    title: str
    description: Optional[str] = ""
    category_id: str
    price: float
    offer_percentage: Optional[float] = 0.0
    stock: Optional[int] = 0
    image_url: Optional[str] = None
    is_active: Optional[bool] = True


class StoreProductUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category_id: Optional[str] = None
    price: Optional[float] = None
    offer_percentage: Optional[float] = None
    stock: Optional[int] = None
    image_url: Optional[str] = None
    is_active: Optional[bool] = None


class StoreProductStatusUpdate(BaseModel):
    is_active: bool


class StoreOrderItem(BaseModel):
    product_id: str
    title: str
    category_name: Optional[str] = None
    price: float
    offer_percentage: float = 0.0
    final_price: float
    quantity: int = 1
    image_url: Optional[str] = None


class StoreOrder(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    order_number: str
    student_id: str
    student_name: str
    student_email: Optional[str] = None
    student_phone: Optional[str] = None
    branch_id: Optional[str] = None
    branch_name: Optional[str] = None
    items: List[StoreOrderItem] = []
    total_amount: float
    payment_method: str = "razorpay"  # "razorpay" | "cash"
    payment_status: str = "paid"      # "paid" | "pending"
    payment_id: Optional[str] = None
    razorpay_order_id: Optional[str] = None
    razorpay_signature: Optional[str] = None
    fulfillment_status: str = "pending"  # "pending" | "given_to_student"
    given_to_student: bool = False
    given_at: Optional[datetime] = None
    given_by_admin_name: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class StorePaymentOrderRequest(BaseModel):
    product_id: str
    quantity: int = 1


class StoreOrderCreate(BaseModel):
    product_id: str
    quantity: int = 1
    payment_method: Optional[str] = "razorpay"
    payment_id: Optional[str] = None
    razorpay_order_id: Optional[str] = None
    razorpay_signature: Optional[str] = None
    payment_status: Optional[str] = "paid"


class OrderFulfillmentUpdate(BaseModel):
    given_to_student: bool

