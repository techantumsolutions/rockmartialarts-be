"""M09-S01 biometric device configuration routes."""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from controllers.biometric_device_controller import BiometricDeviceController
from models.biometric_device_models import BiometricDeviceCreate, BiometricDeviceUpdate
from models.user_models import UserRole
from utils.unified_auth import require_role_unified

router = APIRouter()

_ROLES = [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]


@router.post("")
async def create_biometric_device(
    body: BiometricDeviceCreate,
    current_user: dict = Depends(require_role_unified(_ROLES)),
):
    """Create a biometric device bound to an approved branch."""
    return await BiometricDeviceController.create(body, current_user)


@router.get("")
async def list_biometric_devices(
    branch_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="active|inactive|maintenance|all"),
    vendor: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    current_user: dict = Depends(require_role_unified(_ROLES)),
):
    """List devices (Branch Managers see only their assigned branches)."""
    return await BiometricDeviceController.list_devices(
        current_user,
        branch_id=branch_id,
        status=status,
        vendor=vendor,
        skip=skip,
        limit=limit,
    )


@router.get("/{device_id}")
async def get_biometric_device(
    device_id: str,
    current_user: dict = Depends(require_role_unified(_ROLES)),
):
    return await BiometricDeviceController.get(device_id, current_user)


@router.patch("/{device_id}")
async def update_biometric_device(
    device_id: str,
    body: BiometricDeviceUpdate,
    current_user: dict = Depends(require_role_unified(_ROLES)),
):
    return await BiometricDeviceController.update(device_id, body, current_user)


@router.delete("/{device_id}")
async def deactivate_biometric_device(
    device_id: str,
    current_user: dict = Depends(require_role_unified(_ROLES)),
):
    """Soft-deactivate device (keeps branch mapping for audit)."""
    return await BiometricDeviceController.deactivate(device_id, current_user)
