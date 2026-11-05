"""White-label partner organization (tenant) models — M22."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, EmailStr, Field


class PartnerPaymentMethod(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"


class PartnerOrgStatus(str, Enum):
    PENDING_PAYMENT = "pending_payment"
    INVITED = "invited"
    ACTIVE = "active"
    DISABLED = "disabled"


class PartnerOrgCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    email: EmailStr
    phone: str = Field(..., min_length=7, max_length=20)
    address: str = Field(..., min_length=3, max_length=500)
    payment_method: PartnerPaymentMethod


class PartnerOrgVerifyPayment(BaseModel):
    partner_org_id: str
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class PartnerSetPassword(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8)


class PartnerLogin(BaseModel):
    email: EmailStr
    password: str


class PartnerProfileUpdate(BaseModel):
    full_name: Optional[str] = Field(None, min_length=1, max_length=200)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, min_length=7, max_length=20)


class PartnerOrgUpdateStatus(BaseModel):
    is_active: bool


def new_partner_org_id() -> str:
    return str(uuid4())


def partner_org_public(doc: Optional[dict]) -> Optional[Dict[str, Any]]:
    if not doc:
        return None
    return {
        "id": doc.get("id"),
        "name": doc.get("name"),
        "slug": doc.get("slug"),
        "email": doc.get("email"),
        "phone": doc.get("phone"),
        "address": doc.get("address"),
        "payment_method": doc.get("payment_method"),
        "payment_status": doc.get("payment_status"),
        "status": doc.get("status"),
        "is_active": doc.get("is_active", True),
        "admin_user_id": doc.get("admin_user_id"),
        "login_path": f"/partners/{doc.get('slug')}/login" if doc.get("slug") else None,
        "invite_email_sent": doc.get("invite_email_sent"),
        "invite_email_sent_at": doc.get("invite_email_sent_at"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "activated_at": doc.get("activated_at"),
    }
