"""Routes for white-label partner organizations."""

from typing import Optional

from fastapi import APIRouter, Body, Depends, Query

from controllers.partner_org_controller import PartnerOrgController
from controllers.partner_org_branding_controller import (
    PartnerOrgBrandingController,
    PartnerOrgLandingLeadCreate,
)
from models.partner_org_branding_models import PartnerOrgBrandingUpsert
from models.partner_org_models import (
    PartnerLogin,
    PartnerOrgCreate,
    PartnerOrgUpdateStatus,
    PartnerOrgVerifyPayment,
    PartnerProfileUpdate,
    PartnerSetPassword,
)
from models.user_models import UserRole
from utils.unified_auth import require_role_unified

router = APIRouter()


@router.get("/public/directory")
async def public_partner_directory(
    q: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    """Public listing of active white-label partner organizations."""
    return await PartnerOrgBrandingController.public_directory(q=q, skip=skip, limit=limit)


@router.get("/public/{slug}/landing")
async def public_partner_landing(slug: str):
    """Public landing payload for a white-label partner org."""
    return await PartnerOrgBrandingController.public_landing_by_slug(slug)


@router.post("/public/{slug}/leads")
async def public_partner_landing_lead(slug: str, body: PartnerOrgLandingLeadCreate):
    """Capture a lead from the white-label partner landing page."""
    return await PartnerOrgBrandingController.submit_landing_lead(slug, body)


@router.get("/me")
async def get_my_partner_profile(
    current_user: dict = Depends(require_role_unified([UserRole.PARTNER_SUPER_ADMIN])),
):
    """Partner Super Admin: get own account profile."""
    return await PartnerOrgController.get_me(current_user)


@router.put("/me")
async def update_my_partner_profile(
    body: PartnerProfileUpdate,
    current_user: dict = Depends(require_role_unified([UserRole.PARTNER_SUPER_ADMIN])),
):
    """Partner Super Admin: update own account profile."""
    return await PartnerOrgController.update_me(body, current_user)


@router.get("/me/profile")
async def get_my_partner_profile(
    current_user: dict = Depends(require_role_unified([UserRole.PARTNER_SUPER_ADMIN])),
):
    """Partner Super Admin: get own account profile for the dashboard Profile page."""
    return await PartnerOrgController.get_my_profile(current_user)


@router.put("/me/profile")
async def update_my_partner_profile(
    body: dict = Body(default_factory=dict),
    current_user: dict = Depends(require_role_unified([UserRole.PARTNER_SUPER_ADMIN])),
):
    """Partner Super Admin: update own name / email / phone."""
    return await PartnerOrgController.update_my_profile(current_user, body or {})


@router.get("/me/branding")
async def get_my_partner_branding(
    current_user: dict = Depends(require_role_unified([UserRole.PARTNER_SUPER_ADMIN])),
):
    """Partner Super Admin: get own org branding (Rock SA cannot manage this)."""
    return await PartnerOrgBrandingController.get_my_branding(current_user)


@router.put("/me/branding")
async def upsert_my_partner_branding(
    body: PartnerOrgBrandingUpsert,
    current_user: dict = Depends(require_role_unified([UserRole.PARTNER_SUPER_ADMIN])),
):
    """Partner Super Admin: create/update own org branding."""
    return await PartnerOrgBrandingController.upsert_my_branding(body, current_user)


@router.get("/public/{slug}")
async def public_partner(slug: str):
    return await PartnerOrgController.public_by_slug(slug)


@router.post("/public/{slug}/login")
async def partner_login(slug: str, body: PartnerLogin):
    return await PartnerOrgController.login(slug, body)


@router.post("/public/set-password")
async def partner_set_password(body: PartnerSetPassword):
    return await PartnerOrgController.set_password(body)


@router.get("")
async def list_partner_orgs(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    include_inactive: bool = Query(True),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    return await PartnerOrgController.list_orgs(
        skip=skip, limit=limit, include_inactive=include_inactive
    )


@router.post("/create-offline")
async def create_partner_offline(
    body: PartnerOrgCreate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    return await PartnerOrgController.create_offline(body, current_user)


@router.post("/create-online-order")
async def create_partner_online_order(
    body: PartnerOrgCreate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    return await PartnerOrgController.create_online_order(body, current_user)


@router.post("/verify-online-payment")
async def verify_partner_online_payment(
    body: PartnerOrgVerifyPayment,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    return await PartnerOrgController.verify_online(body, current_user)


@router.post("/{partner_org_id}/resend-invite")
async def resend_partner_invite(
    partner_org_id: str,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Re-send partner set-password invitation email."""
    return await PartnerOrgController.resend_invite(partner_org_id, current_user)


@router.patch("/{partner_org_id}/status")
async def set_partner_status(
    partner_org_id: str,
    body: PartnerOrgUpdateStatus,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    return await PartnerOrgController.set_active(partner_org_id, body, current_user)
