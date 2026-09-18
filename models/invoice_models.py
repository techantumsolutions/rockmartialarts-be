"""M07-S01 invoice + invoice line-item schemas (Mongo `invoices`)."""
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
import uuid


class InvoiceStatus(str, Enum):
    ISSUED = "issued"
    VOID = "void"


class InvoiceLineItem(BaseModel):
    """One billable line (student × course), supporting multi-student carts."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    description: str = ""
    student_id: Optional[str] = None
    student_label: str = ""
    enrollment_id: Optional[str] = None
    course_id: Optional[str] = None
    course_name: str = ""
    branch_id: Optional[str] = None
    branch_name: str = ""
    quantity: int = 1
    course_fee: float = 0.0
    admission_fee: float = 0.0
    discount_amount: float = 0.0
    line_total: float = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class InvoiceParty(BaseModel):
    name: str = ""
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None


class InvoiceDocument(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    invoice_number: str
    status: InvoiceStatus = InvoiceStatus.ISSUED
    payment_id: Optional[str] = None
    cart_checkout_id: Optional[str] = None
    student_id: Optional[str] = None
    account_user_id: Optional[str] = None
    branch_id: Optional[str] = None
    currency: str = "INR"
    company: InvoiceParty = Field(default_factory=InvoiceParty)
    customer: InvoiceParty = Field(default_factory=InvoiceParty)
    line_items: List[InvoiceLineItem] = Field(default_factory=list)
    subtotal_amount: float = 0.0
    admission_total: float = 0.0
    discount_total: float = 0.0
    tax_total: float = 0.0
    total_amount: float = 0.0
    payment_reference: Optional[str] = None
    payment_method: Optional[str] = None
    payment_gateway_label: Optional[str] = None
    paid_at: Optional[datetime] = None
    notes: Optional[str] = None
    document_html: Optional[str] = None
    source: str = "payment"  # payment | cart | registration
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class InvoiceListItem(BaseModel):
    id: str
    invoice_number: str
    status: str
    total_amount: float
    currency: str = "INR"
    paid_at: Optional[datetime] = None
    customer_name: str = ""
    payment_id: Optional[str] = None
    cart_checkout_id: Optional[str] = None
    payment_reference: Optional[str] = None
    line_count: int = 0
    created_at: Optional[datetime] = None
