"""M09-S01 biometric device models (Mongo `biometric_devices` collection)."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
import uuid

from pydantic import BaseModel, Field


class BiometricDeviceStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    MAINTENANCE = "maintenance"


class BiometricDeviceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    vendor: str = Field(default="essl", min_length=1, max_length=64)
    vendor_device_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Vendor serial / terminal id (unique per vendor)",
    )
    branch_id: str = Field(..., min_length=1, description="Approved branch this device belongs to")
    status: BiometricDeviceStatus = BiometricDeviceStatus.ACTIVE
    location_note: Optional[str] = Field(default=None, max_length=255)
    ip_address: Optional[str] = Field(default=None, max_length=64)


class BiometricDeviceUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    vendor: Optional[str] = Field(default=None, min_length=1, max_length=64)
    vendor_device_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    branch_id: Optional[str] = Field(default=None, min_length=1)
    status: Optional[BiometricDeviceStatus] = None
    location_note: Optional[str] = Field(default=None, max_length=255)
    ip_address: Optional[str] = Field(default=None, max_length=64)


class BiometricDeviceDocument(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    vendor: str = "essl"
    vendor_device_id: str
    branch_id: str
    status: BiometricDeviceStatus = BiometricDeviceStatus.ACTIVE
    location_note: Optional[str] = None
    ip_address: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
