from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum
import uuid

class NotificationType(str, Enum):
    SMS = "sms"
    WHATSAPP = "whatsapp"
    EMAIL = "email"

class NotificationTemplate(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    type: NotificationType
    subject: Optional[str] = None
    body: str
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class NotificationTemplateCreate(BaseModel):
    name: str
    type: NotificationType
    subject: Optional[str] = None
    body: str

class TriggerNotification(BaseModel):
    user_id: str
    template_id: str
    context: Optional[Dict[str, Any]] = {}

class NotificationLog(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    template_id: str
    type: NotificationType
    status: str
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

class BroadcastAnnouncement(BaseModel):
    branch_id: Optional[str] = None
    template_id: str
    context: Optional[Dict[str, Any]] = {}

class ClassReminder(BaseModel):
    course_id: str
    branch_id: str

# New models for payment and registration notifications
class PaymentNotification(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    payment_id: str
    student_id: str
    notification_type: str  # "payment_received", "registration_complete", etc.
    title: str
    message: str
    amount: Optional[float] = None
    course_name: Optional[str] = None
    branch_name: Optional[str] = None
    is_read: bool = False
    priority: str = "normal"  # "low", "normal", "high", "urgent"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    read_at: Optional[datetime] = None

class PaymentNotificationCreate(BaseModel):
    payment_id: str
    student_id: str
    notification_type: str
    title: str
    message: str
    amount: Optional[float] = None
    course_name: Optional[str] = None
    branch_name: Optional[str] = None
    priority: str = "normal"

# Message notification models
class MessageNotification(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    message_id: str
    thread_id: str
    recipient_id: str
    recipient_type: str  # "student", "coach", "branch_manager", "superadmin"
    sender_id: str
    sender_name: str
    sender_type: str
    notification_type: str = "new_message"  # "new_message", "message_reply"
    title: str
    message: str
    subject: str
    is_read: bool = False
    priority: str = "normal"  # "low", "normal", "high", "urgent"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    read_at: Optional[datetime] = None

class MessageNotificationCreate(BaseModel):
    message_id: str
    thread_id: str
    recipient_id: str
    recipient_type: str
    sender_id: str
    sender_name: str
    sender_type: str
    notification_type: str = "new_message"
    title: str
    message: str
    subject: str
    priority: str = "normal"


class LeadNotification(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    notification_type: str = "new_lead"
    lead_id: str
    lead_name: Optional[str] = None
    recipient_id: str
    recipient_role: str
    title: str
    message: str
    link: Optional[str] = None
    dedupe_key: str
    is_read: bool = False
    priority: str = "normal"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    read_at: Optional[datetime] = None
