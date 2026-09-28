"""Admin + public routes for website campaign popups."""
from fastapi import APIRouter, Depends

from controllers import campaign_popup_controller as ctrl
from models.campaign_popup_models import (
    CampaignPopupCreate,
    CampaignPopupEnabledPatch,
    CampaignPopupUpdate,
)
from models.user_models import UserRole
from utils.unified_auth import require_role_unified

router = APIRouter()


@router.get("/public/active")
async def get_public_active_campaign_popup():
    """Best enabled, in-date campaign popup (no auth)."""
    return await ctrl.get_public_active()


@router.get("")
async def list_campaign_popups(
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    items = await ctrl.list_all()
    return {"popups": items, "total": len(items)}


@router.post("", status_code=201)
async def create_campaign_popup(
    data: CampaignPopupCreate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    return await ctrl.create(data, current_user)


@router.get("/{popup_id}")
async def get_campaign_popup(
    popup_id: str,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    return await ctrl.get_by_id(popup_id)


@router.put("/{popup_id}")
async def update_campaign_popup(
    popup_id: str,
    data: CampaignPopupUpdate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    return await ctrl.update(popup_id, data, current_user)


@router.patch("/{popup_id}/enabled")
async def patch_campaign_popup_enabled(
    popup_id: str,
    data: CampaignPopupEnabledPatch,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    return await ctrl.set_enabled(popup_id, data, current_user)


@router.delete("/{popup_id}")
async def delete_campaign_popup(
    popup_id: str,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    return await ctrl.delete(popup_id, current_user)
