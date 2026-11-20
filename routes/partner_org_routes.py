"""Routes for white-label partner organizations."""

from typing import Optional

from fastapi import APIRouter, Body, Depends, Query

from controllers.partner_org_controller import PartnerOrgController
from controllers.partner_org_branding_controller import (
    PartnerOrgBrandingController,
    PartnerOrgLandingLeadCreate,
)
from models.partner_org_branding_models import PartnerOrgBrandingUpsert
from models.partner_branding_approval_models import (
    BrandingEditRequestCreate,
    BrandingEditReviewBody,
)
from models.partner_org_models import (
    PartnerAdminResetPassword,
    PartnerCoursePermissionsUpdate,
    PartnerFeaturePermissionsUpdate,
    PartnerLogin,
    PartnerMessagingQuotaUpdate,
    PartnerOrgCreate,
    PartnerOrgUpdateStatus,
    PartnerOrgVerifyPayment,
    PartnerProfileUpdate,
    PartnerPublicSiteSettingsUpdate,
    PartnerSetPassword,
    PartnerValidityUpdate,
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
    """Partner Super Admin: create/update own org branding (requires SA approval)."""
    return await PartnerOrgBrandingController.upsert_my_branding(body, current_user)


@router.get("/me/branding-edit-permission")
async def get_my_branding_edit_permission(
    branch_id: Optional[str] = Query(None, description="Academy id for per-academy branding scope"),
    current_user: dict = Depends(require_role_unified([UserRole.PARTNER_SUPER_ADMIN])),
):
    """Partner Super Admin: branding edit permission (org or academy scope)."""
    return await PartnerOrgBrandingController.get_my_branding_edit_permission(
        current_user, branch_id=branch_id
    )


@router.post("/me/branding-edit-request")
async def request_branding_edit_access(
    body: BrandingEditRequestCreate = Body(default=BrandingEditRequestCreate()),
    current_user: dict = Depends(require_role_unified([UserRole.PARTNER_SUPER_ADMIN])),
):
    """Partner Super Admin: request permission to edit org or academy branding."""
    return await PartnerOrgBrandingController.request_branding_edit(
        current_user,
        note=(body.note if body else None),
        branch_id=(body.branch_id if body else None),
    )


@router.get("/branding-edit-requests")
async def list_branding_edit_requests(
    status: Optional[str] = Query("pending"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Rock Super Admin: list partner branding edit requests."""
    return await PartnerOrgBrandingController.list_branding_edit_requests(
        current_user, status=status, skip=skip, limit=limit
    )


@router.post("/branding-edit-requests/{request_id}/approve")
async def approve_branding_edit_request(
    request_id: str,
    body: BrandingEditReviewBody = Body(default=BrandingEditReviewBody()),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    return await PartnerOrgBrandingController.approve_branding_edit_request(
        request_id, current_user, reason=(body.reason if body else None)
    )


@router.post("/branding-edit-requests/{request_id}/reject")
async def reject_branding_edit_request(
    request_id: str,
    body: BrandingEditReviewBody = Body(default=BrandingEditReviewBody()),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    return await PartnerOrgBrandingController.reject_branding_edit_request(
        request_id, current_user, reason=(body.reason if body else None)
    )


@router.post("/{partner_org_id}/branding-edit-revoke")
async def revoke_branding_edit_access(
    partner_org_id: str,
    body: BrandingEditReviewBody = Body(default=BrandingEditReviewBody()),
    branch_id: Optional[str] = Query(None, description="Revoke academy-scoped branding only"),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    return await PartnerOrgBrandingController.revoke_branding_edit_access(
        partner_org_id,
        current_user,
        reason=(body.reason if body else None),
        branch_id=branch_id,
    )


@router.get("/{partner_org_id}/branding")
async def get_partner_org_branding_admin(
    partner_org_id: str,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Rock Super Admin: read partner Page CMS / org branding."""
    return await PartnerOrgBrandingController.get_admin_branding(partner_org_id, current_user)


@router.put("/{partner_org_id}/branding")
async def upsert_partner_org_branding_admin(
    partner_org_id: str,
    body: PartnerOrgBrandingUpsert,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Rock Super Admin: create/update partner Page CMS / org branding."""
    return await PartnerOrgBrandingController.upsert_admin_branding(
        partner_org_id, body, current_user
    )


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


@router.post("/{partner_org_id}/reset-password")
async def admin_reset_partner_password(
    partner_org_id: str,
    body: PartnerAdminResetPassword,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Rock Super Admin sets/resets partner admin password in Partner Profiles."""
    return await PartnerOrgController.admin_reset_password(
        partner_org_id, body, current_user
    )


@router.patch("/{partner_org_id}/status")
async def set_partner_status(
    partner_org_id: str,
    body: PartnerOrgUpdateStatus,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    return await PartnerOrgController.set_active(partner_org_id, body, current_user)


# Static "/me/..." paths MUST be registered before "/{partner_org_id}/..."
# or FastAPI will treat "me" as a partner_org_id.
@router.get("/me/feature-permissions")
async def get_my_partner_feature_permissions(
    current_user: dict = Depends(require_role_unified([UserRole.PARTNER_SUPER_ADMIN])),
):
    """Partner admin read-only feature flags for dashboard nav (from DB)."""
    return await PartnerOrgController.get_my_feature_permissions(current_user)


@router.get("/me/messaging-quota")
async def get_my_messaging_quota(
    current_user: dict = Depends(require_role_unified([UserRole.PARTNER_SUPER_ADMIN])),
):
    """Partner admin: SMS/WhatsApp allocated, utilized, remaining."""
    return await PartnerOrgController.get_my_messaging_quota(current_user)


@router.patch("/{partner_org_id}/validity")
async def set_partner_validity(
    partner_org_id: str,
    body: PartnerValidityUpdate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    return await PartnerOrgController.update_validity(partner_org_id, body, current_user)


@router.get("/{partner_org_id}/feature-permissions")
async def get_partner_feature_permissions(
    partner_org_id: str,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    return await PartnerOrgController.get_feature_permissions(partner_org_id, current_user)


@router.patch("/{partner_org_id}/feature-permissions")
async def patch_partner_feature_permissions(
    partner_org_id: str,
    body: PartnerFeaturePermissionsUpdate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    return await PartnerOrgController.patch_feature_permissions(partner_org_id, body, current_user)


@router.get("/{partner_org_id}/course-permissions")
async def get_partner_course_permissions(
    partner_org_id: str,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Super Admin: read Partner course create/edit permission flags."""
    return await PartnerOrgController.get_course_permissions(partner_org_id, current_user)


@router.patch("/{partner_org_id}/course-permissions")
async def patch_partner_course_permissions(
    partner_org_id: str,
    body: PartnerCoursePermissionsUpdate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Super Admin: grant/revoke Partner course create/edit permissions."""
    return await PartnerOrgController.patch_course_permissions(
        partner_org_id, body, current_user
    )


@router.patch("/{partner_org_id}/public-site-settings")
async def patch_partner_public_site_settings(
    partner_org_id: str,
    body: PartnerPublicSiteSettingsUpdate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Super Admin: enable/disable partner Page CMS and public website footer."""
    return await PartnerOrgController.patch_public_site_settings(
        partner_org_id, body, current_user
    )


@router.get("/{partner_org_id}/messaging-quota")
async def get_partner_messaging_quota(
    partner_org_id: str,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Super Admin: read partner SMS/WhatsApp quota utilization."""
    return await PartnerOrgController.get_messaging_quota(partner_org_id, current_user)


@router.patch("/{partner_org_id}/messaging-quota")
async def patch_partner_messaging_quota(
    partner_org_id: str,
    body: PartnerMessagingQuotaUpdate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Super Admin: set partner SMS/WhatsApp allocated limits."""
    return await PartnerOrgController.patch_messaging_quota(partner_org_id, body, current_user)
