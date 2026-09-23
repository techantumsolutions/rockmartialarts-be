"""M10-S01 KPI Master Admin routes."""
from typing import Optional

from fastapi import APIRouter, Depends, Query, status

from models.kpi_models import KpiDefinitionCreate, KpiDefinitionUpdate
from models.user_models import UserRole
from utils.kpi_service import (
    create_kpi_definition,
    delete_kpi_definition,
    get_kpi_definition,
    get_weight_summary,
    list_kpi_definitions,
    set_kpi_active,
    update_kpi_definition,
)
from utils.unified_auth import require_role_unified

router = APIRouter()

ADMIN_ROLES = [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN]
READ_ROLES = [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]


@router.get("/weight-summary")
async def kpi_weight_summary(
    current_user: dict = Depends(require_role_unified(READ_ROLES)),
):
    """Active KPI weight totals (percent sum should approach 100)."""
    return await get_weight_summary()


@router.get("")
async def list_kpis(
    active_only: bool = Query(False),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    current_user: dict = Depends(require_role_unified(READ_ROLES)),
):
    return await list_kpi_definitions(active_only=active_only, skip=skip, limit=limit)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_kpi(
    body: KpiDefinitionCreate,
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await create_kpi_definition(body, current_user=current_user)


@router.get("/{kpi_id}")
async def get_kpi(
    kpi_id: str,
    current_user: dict = Depends(require_role_unified(READ_ROLES)),
):
    return await get_kpi_definition(kpi_id)


@router.patch("/{kpi_id}")
async def update_kpi(
    kpi_id: str,
    body: KpiDefinitionUpdate,
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await update_kpi_definition(kpi_id, body, current_user=current_user)


@router.patch("/{kpi_id}/active")
async def set_kpi_active_flag(
    kpi_id: str,
    is_active: bool = Query(...),
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await set_kpi_active(kpi_id, is_active=is_active, current_user=current_user)


@router.delete("/{kpi_id}")
async def delete_kpi(
    kpi_id: str,
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await delete_kpi_definition(kpi_id)
