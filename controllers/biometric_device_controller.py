"""M09-S01 biometric device CRUD controller."""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException

from models.biometric_device_models import BiometricDeviceCreate, BiometricDeviceUpdate
from utils import biometric_device_service as svc


class BiometricDeviceController:
    @staticmethod
    async def create(body: BiometricDeviceCreate, current_user: dict):
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        return await svc.create_device(body, current_user)

    @staticmethod
    async def list_devices(
        current_user: dict,
        branch_id: Optional[str] = None,
        status: Optional[str] = None,
        vendor: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ):
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        return await svc.list_devices(
            current_user,
            branch_id=branch_id,
            status=status,
            vendor=vendor,
            skip=skip,
            limit=limit,
        )

    @staticmethod
    async def get(device_id: str, current_user: dict):
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        return await svc.get_device(device_id, current_user)

    @staticmethod
    async def update(device_id: str, body: BiometricDeviceUpdate, current_user: dict):
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        return await svc.update_device(device_id, body, current_user)

    @staticmethod
    async def deactivate(device_id: str, current_user: dict):
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        return await svc.deactivate_device(device_id, current_user)
