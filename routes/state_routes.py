from fastapi import APIRouter, Depends, Query
from typing import Optional

from controllers.state_controller import StateController
from models.state_models import StateCreate, StateUpdate
from models.user_models import UserRole
from utils.unified_auth import require_role_unified, get_current_user_or_superadmin

router = APIRouter()


@router.get("/public")
async def get_public_states(
    active_only: bool = Query(True),
    skip: int = 0,
    limit: int = 100,
):
    """Active states for registration and discovery dropdowns. No authentication."""
    return await StateController.get_public_states(active_only=active_only, skip=skip, limit=limit)


@router.post("")
async def create_state(
    state_data: StateCreate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    return await StateController.create_state(state_data, current_user)


@router.get("")
async def get_states(
    active_only: bool = Query(False),
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    current_user: dict = Depends(get_current_user_or_superadmin),
):
    return await StateController.get_states(
        active_only=active_only,
        search=search,
        skip=skip,
        limit=limit,
        current_user=current_user,
    )


@router.put("/{state_id}")
async def update_state(
    state_id: str,
    state_update: StateUpdate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    return await StateController.update_state(state_id, state_update, current_user)
