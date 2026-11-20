"""White-label partner organization (tenant) models — M22."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, Union
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
    # Actual cash amount received when payment_method is offline (required for offline).
    cash_amount: Optional[float] = Field(None, gt=0)
    # Optional Super Admin messaging quotas (0 = channel disabled)
    sms_allocated: Optional[int] = Field(None, ge=0)
    whatsapp_allocated: Optional[int] = Field(None, ge=0)


class PartnerMessagingQuotaUpdate(BaseModel):
    sms_allocated: Optional[int] = Field(None, ge=0)
    whatsapp_allocated: Optional[int] = Field(None, ge=0)


class PartnerOrgVerifyPayment(BaseModel):
    partner_org_id: str
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class PartnerSetPassword(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8)


class PartnerAdminResetPassword(BaseModel):
    """Rock Super Admin sets partner admin password in-app (Partner Profiles)."""

    new_password: str = Field(..., min_length=8, max_length=128)


class PartnerLogin(BaseModel):
    email: EmailStr
    password: str


class PartnerProfileUpdate(BaseModel):
    full_name: Optional[str] = Field(None, min_length=1, max_length=200)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, min_length=7, max_length=20)


class PartnerOrgUpdateStatus(BaseModel):
    is_active: bool


class PartnerValidityUpdate(BaseModel):
    valid_from: Optional[Union[str, datetime]] = None
    valid_until: Optional[Union[str, datetime]] = None


class PartnerFeaturePermissionsUpdate(BaseModel):
    feature_permissions: Dict[str, bool] = Field(default_factory=dict)


class PartnerCoursePermissionsUpdate(BaseModel):
    """Super Admin grants Partner SA course create/edit (default both false)."""

    can_create_courses: bool = False
    can_edit_courses: bool = False


class PartnerPublicSiteSettingsUpdate(BaseModel):
    """Super Admin: partner public website CMS + footer visibility."""

    page_cms_enabled: Optional[bool] = None
    public_footer_enabled: Optional[bool] = None


def new_partner_org_id() -> str:
    return str(uuid4())


def partner_org_public(doc: Optional[dict]) -> Optional[Dict[str, Any]]:
    if not doc:
        return None
    from utils.partner_org_access import partner_org_public_extras
    from utils.partner_messaging_quota import quota_summary_from_doc

    payload = {
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
        "sms_allocated": doc.get("sms_allocated"),
        "whatsapp_allocated": doc.get("whatsapp_allocated"),
        "sms_utilized": doc.get("sms_utilized"),
        "whatsapp_utilized": doc.get("whatsapp_utilized"),
        "sms_reserved": doc.get("sms_reserved"),
        "whatsapp_reserved": doc.get("whatsapp_reserved"),
        "messaging_quota": quota_summary_from_doc(doc),
        "cash_amount": doc.get("cash_amount") or doc.get("amount_received"),
        "amount_received": doc.get("amount_received") or doc.get("cash_amount"),
        "registration_fee": doc.get("registration_fee"),
        "invoice_id": doc.get("invoice_id"),
        "invoice_number": doc.get("invoice_number"),
        "branding_edit_permission": doc.get("branding_edit_permission") or "none",
    }
    payload.update(partner_org_public_extras(doc))
    return payload
