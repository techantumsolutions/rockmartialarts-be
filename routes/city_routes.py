from fastapi import APIRouter, Depends, Query
from typing import Optional

from controllers.city_controller import CityController
from models.location_models import CityCreate, CityUpdate
from models.user_models import UserRole
from utils.unified_auth import require_role_unified, get_current_user_or_superadmin

router = APIRouter()


@router.get("/public")
async def get_public_cities(
    state_id: Optional[str] = Query(None),
    active_only: bool = Query(True),
    skip: int = 0,
    limit: int = 200,
):
    """Active cities for dropdowns. Filter with state_id. No authentication."""
    return await CityController.get_public_cities(
        state_id=state_id,
        active_only=active_only,
        skip=skip,
        limit=limit,
    )


@router.post("")
async def create_city(
    city_data: CityCreate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    return await CityController.create_city(city_data, current_user)


@router.get("")
async def get_cities(
    state_id: Optional[str] = Query(None),
    active_only: bool = Query(False),
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    current_user: dict = Depends(get_current_user_or_superadmin),
):
    return await CityController.get_cities(
        state_id=state_id,
        active_only=active_only,
        search=search,
        skip=skip,
        limit=limit,
        current_user=current_user,
    )


@router.put("/{city_id}")
async def update_city(
    city_id: str,
    city_update: CityUpdate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    return await CityController.update_city(city_id, city_update, current_user)
