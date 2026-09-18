"""
M21 collaboration partner routes
(S01–S11: flag … landing, BM permissions, partner leads).
Mounted under /api/collaboration-partners.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from controllers.collaboration_partner_branding_controller import (
    CollaborationPartnerBrandingController,
)
from controllers.collaboration_partner_controller import CollaborationPartnerController
from controllers.collaboration_partner_gallery_controller import (
    CollaborationPartnerGalleryController,
)
from controllers.collaboration_partner_landing_controller import (
    CollaborationPartnerLandingController,
)
from controllers.collaboration_partner_lead_controller import (
    CollaborationPartnerLeadController,
    PartnerLandingLeadCreate,
)
from controllers.collaboration_partner_master_controller import (
    CollaborationPartnerMasterController,
)
from controllers.collaboration_partner_seo_controller import (
    CollaborationPartnerSeoController,
)
from controllers.collaboration_partner_team_controller import (
    CollaborationPartnerTeamController,
)
from controllers.collaboration_partner_testimonial_controller import (
    CollaborationPartnerTestimonialController,
)
from models.collaboration_partner_branding_models import PartnerBrandingUpsert
from models.collaboration_partner_gallery_models import (
    PartnerGalleryItemCreate,
    PartnerGalleryItemUpdate,
    PartnerGalleryReorderRequest,
)
from models.collaboration_partner_master_models import (
    PartnerMasterCreate,
    PartnerMasterUpdate,
)
from models.collaboration_partner_models import PartnerProfileUpsert
from models.collaboration_partner_seo_models import PartnerSeoUpsert
from models.collaboration_partner_team_models import (
    PartnerTeamMemberCreate,
    PartnerTeamMemberUpdate,
)
from models.collaboration_partner_testimonial_models import (
    PartnerTestimonialCreate,
    PartnerTestimonialReorderRequest,
    PartnerTestimonialUpdate,
)
from models.user_models import UserRole
from utils.collaboration_partner import (
    get_partner_branch_for_cms,
    partner_cms_permissions,
)
from utils.database import get_db
from utils.unified_auth import require_role_unified

router = APIRouter()

_ADMIN = [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]


@router.get("/public/partners")
async def api_public_list_partners(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    q: Optional[str] = Query(None),
):
    """Public directory of collaboration partner branches (no auth)."""
    return await CollaborationPartnerLandingController.list_public_partners(
        skip=skip, limit=limit, q=q
    )


@router.get("/public/by-slug/{slug}")
async def api_public_partner_landing_by_slug(slug: str):
    """
    Full partner landing payload by slug (no auth).
    404 when slug is missing or branch is not a collaboration partner.
    """
    return await CollaborationPartnerLandingController.get_landing_by_slug(slug)


@router.post("/public/by-slug/{slug}/leads", status_code=201)
async def api_public_partner_lead_by_slug(
    slug: str,
    body: PartnerLandingLeadCreate,
):
    """
    M21-S11: Public partner contact form submit (no auth).
    Tags source as partner_landing + branch; visible to Super Admin and assigned BM.
    """
    return await CollaborationPartnerLeadController.submit_by_slug(slug, body)


@router.get("/public/branches/{branch_id}/landing")
async def api_public_partner_landing_by_id(branch_id: str):
    """Full partner landing payload by branch id (no auth). 404 if not partner."""
    return await CollaborationPartnerLandingController.get_landing_by_branch_id(
        branch_id
    )


@router.post("/public/branches/{branch_id}/leads", status_code=201)
async def api_public_partner_lead_by_id(
    branch_id: str,
    body: PartnerLandingLeadCreate,
):
    """M21-S11: Public partner lead submit by branch id (no auth)."""
    return await CollaborationPartnerLeadController.submit_by_branch_id(
        branch_id, body
    )


@router.get("/me/permissions")
async def api_partner_cms_permissions(
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """
    M21-S10: Partner CMS permission summary for the current user.
    Reuses Branch Manager role; BM is scoped to assigned partner branches.
    """
    db = get_db()
    return await partner_cms_permissions(db, current_user)


@router.get("/branches/{branch_id}/status")
async def api_collaboration_partner_status(
    branch_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """
    Whether partner CMS features are enabled for this branch.
    Includes S02/S03 summary (has_profile, partner_id, has_branding).
    Branch Managers: assigned branch only.
    """
    return await CollaborationPartnerController.status_with_profile(
        branch_id, current_user=current_user
    )


@router.get("/branches/{branch_id}/require-enabled")
async def api_require_partner_enabled(
    branch_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """403 when branch is not a collaboration partner — used by partner CMS."""
    db = get_db()
    branch = await get_partner_branch_for_cms(db, branch_id, current_user)
    return {
        "branch_id": branch_id,
        "partner_features_enabled": True,
        "branch": {
            "id": branch.get("id"),
            "name": (branch.get("branch") or {}).get("name"),
            "is_collaboration_partner": True,
        },
    }


@router.get("/partner-branches")
async def api_list_partner_branches(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """List branches flagged as collaboration partners (with profile/branding summary)."""
    return await CollaborationPartnerController.list_partner_branches(
        skip=skip, limit=limit, current_user=current_user
    )


@router.get("/branches/{branch_id}/profile")
async def api_get_partner_profile(
    branch_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """Get partner business/contact/agreement profile (403 if not partner)."""
    return await CollaborationPartnerController.get_profile(
        branch_id, current_user=current_user
    )


@router.put("/branches/{branch_id}/profile")
async def api_upsert_partner_profile(
    branch_id: str,
    body: PartnerProfileUpsert,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """Create or update partner profile. Auto-assigns Partner ID on first save."""
    return await CollaborationPartnerController.upsert_profile(
        branch_id, body, current_user=current_user
    )


@router.get("/branches/{branch_id}/branding")
async def api_get_partner_branding(
    branch_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """Get partner branding & content (403 if not partner). Scoped to this branch only."""
    return await CollaborationPartnerBrandingController.get_branding(
        branch_id, current_user=current_user
    )


@router.put("/branches/{branch_id}/branding")
async def api_upsert_partner_branding(
    branch_id: str,
    body: PartnerBrandingUpsert,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """Create or update partner branding for the selected collaboration branch only."""
    return await CollaborationPartnerBrandingController.upsert_branding(
        branch_id, body, current_user=current_user
    )


@router.get("/public/branches/{branch_id}/seo")
async def api_public_partner_seo(branch_id: str):
    """Partner SEO for public branch/landing metadata (no auth)."""
    return await CollaborationPartnerSeoController.get_public(branch_id)


@router.get("/branches/{branch_id}/seo")
async def api_get_partner_seo(
    branch_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """Get partner SEO (403 if not partner). Scoped to this branch only."""
    return await CollaborationPartnerSeoController.get_seo(
        branch_id, current_user=current_user
    )


@router.put("/branches/{branch_id}/seo")
async def api_upsert_partner_seo(
    branch_id: str,
    body: PartnerSeoUpsert,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """Create or update partner SEO for the selected collaboration branch only."""
    return await CollaborationPartnerSeoController.upsert_seo(
        branch_id, body, current_user=current_user
    )


@router.get("/branches/{branch_id}/masters")
async def api_list_partner_masters(
    branch_id: str,
    include_inactive: bool = Query(False),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """List masters/experts for a collaboration partner branch."""
    return await CollaborationPartnerMasterController.list_masters(
        branch_id,
        include_inactive=include_inactive,
        skip=skip,
        limit=limit,
        current_user=current_user,
    )


@router.post("/branches/{branch_id}/masters")
async def api_create_partner_master(
    branch_id: str,
    body: PartnerMasterCreate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """Create a master/expert for the selected partner branch only."""
    return await CollaborationPartnerMasterController.create_master(
        branch_id, body, current_user=current_user
    )


@router.get("/branches/{branch_id}/masters/{master_id}")
async def api_get_partner_master(
    branch_id: str,
    master_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await CollaborationPartnerMasterController.get_master(
        branch_id, master_id, current_user=current_user
    )


@router.put("/branches/{branch_id}/masters/{master_id}")
async def api_update_partner_master(
    branch_id: str,
    master_id: str,
    body: PartnerMasterUpdate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await CollaborationPartnerMasterController.update_master(
        branch_id, master_id, body, current_user=current_user
    )


@router.delete("/branches/{branch_id}/masters/{master_id}")
async def api_delete_partner_master(
    branch_id: str,
    master_id: str,
    hard: bool = Query(False),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """Soft-deactivate by default; pass hard=true to permanently delete."""
    return await CollaborationPartnerMasterController.delete_master(
        branch_id, master_id, hard=hard, current_user=current_user
    )


@router.get("/branches/{branch_id}/gallery")
async def api_list_partner_gallery(
    branch_id: str,
    include_inactive: bool = Query(False),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """List gallery items for a collaboration partner branch."""
    return await CollaborationPartnerGalleryController.list_items(
        branch_id,
        include_inactive=include_inactive,
        skip=skip,
        limit=limit,
        current_user=current_user,
    )


@router.post("/branches/{branch_id}/gallery")
async def api_create_partner_gallery_item(
    branch_id: str,
    body: PartnerGalleryItemCreate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await CollaborationPartnerGalleryController.create_item(
        branch_id, body, current_user=current_user
    )


@router.put("/branches/{branch_id}/gallery/reorder")
async def api_reorder_partner_gallery(
    branch_id: str,
    body: PartnerGalleryReorderRequest,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """Update display_order for multiple gallery items (same branch only)."""
    return await CollaborationPartnerGalleryController.reorder_items(
        branch_id, body, current_user=current_user
    )


@router.get("/branches/{branch_id}/gallery/{item_id}")
async def api_get_partner_gallery_item(
    branch_id: str,
    item_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await CollaborationPartnerGalleryController.get_item(
        branch_id, item_id, current_user=current_user
    )


@router.put("/branches/{branch_id}/gallery/{item_id}")
async def api_update_partner_gallery_item(
    branch_id: str,
    item_id: str,
    body: PartnerGalleryItemUpdate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await CollaborationPartnerGalleryController.update_item(
        branch_id, item_id, body, current_user=current_user
    )


@router.delete("/branches/{branch_id}/gallery/{item_id}")
async def api_delete_partner_gallery_item(
    branch_id: str,
    item_id: str,
    hard: bool = Query(False),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """Soft-deactivate by default; pass hard=true to permanently delete."""
    return await CollaborationPartnerGalleryController.delete_item(
        branch_id, item_id, hard=hard, current_user=current_user
    )


@router.get("/public/branches/{branch_id}/testimonials")
async def api_public_partner_testimonials(
    branch_id: str,
    limit: int = Query(24, ge=1, le=50),
):
    """
    Published partner testimonials for public branch/landing pages (no auth).
    Non-partner branches return an empty list.
    """
    return await CollaborationPartnerTestimonialController.list_public(
        branch_id, limit=limit
    )


@router.get("/branches/{branch_id}/testimonials")
async def api_list_partner_testimonials(
    branch_id: str,
    include_inactive: bool = Query(False),
    status: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await CollaborationPartnerTestimonialController.list_items(
        branch_id,
        include_inactive=include_inactive,
        status=status,
        skip=skip,
        limit=limit,
        current_user=current_user,
    )


@router.post("/branches/{branch_id}/testimonials")
async def api_create_partner_testimonial(
    branch_id: str,
    body: PartnerTestimonialCreate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await CollaborationPartnerTestimonialController.create_item(
        branch_id, body, current_user=current_user
    )


@router.put("/branches/{branch_id}/testimonials/reorder")
async def api_reorder_partner_testimonials(
    branch_id: str,
    body: PartnerTestimonialReorderRequest,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await CollaborationPartnerTestimonialController.reorder_items(
        branch_id, body, current_user=current_user
    )


@router.get("/branches/{branch_id}/testimonials/{item_id}")
async def api_get_partner_testimonial(
    branch_id: str,
    item_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await CollaborationPartnerTestimonialController.get_item(
        branch_id, item_id, current_user=current_user
    )


@router.put("/branches/{branch_id}/testimonials/{item_id}")
async def api_update_partner_testimonial(
    branch_id: str,
    item_id: str,
    body: PartnerTestimonialUpdate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await CollaborationPartnerTestimonialController.update_item(
        branch_id, item_id, body, current_user=current_user
    )


@router.delete("/branches/{branch_id}/testimonials/{item_id}")
async def api_delete_partner_testimonial(
    branch_id: str,
    item_id: str,
    hard: bool = Query(False),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await CollaborationPartnerTestimonialController.delete_item(
        branch_id, item_id, hard=hard, current_user=current_user
    )


@router.get("/public/branches/{branch_id}/team")
async def api_public_partner_team(
    branch_id: str,
    limit: int = Query(48, ge=1, le=100),
):
    """
    Active partner team members for public surfaces (no auth).
    Contact fields only when contact_approved=true. Non-partners get [].
    """
    return await CollaborationPartnerTeamController.list_public(
        branch_id, limit=limit
    )


@router.get("/branches/{branch_id}/team")
async def api_list_partner_team(
    branch_id: str,
    include_inactive: bool = Query(False),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await CollaborationPartnerTeamController.list_members(
        branch_id,
        include_inactive=include_inactive,
        skip=skip,
        limit=limit,
        current_user=current_user,
    )


@router.post("/branches/{branch_id}/team")
async def api_create_partner_team_member(
    branch_id: str,
    body: PartnerTeamMemberCreate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await CollaborationPartnerTeamController.create_member(
        branch_id, body, current_user=current_user
    )


@router.get("/branches/{branch_id}/team/{member_id}")
async def api_get_partner_team_member(
    branch_id: str,
    member_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await CollaborationPartnerTeamController.get_member(
        branch_id, member_id, current_user=current_user
    )


@router.put("/branches/{branch_id}/team/{member_id}")
async def api_update_partner_team_member(
    branch_id: str,
    member_id: str,
    body: PartnerTeamMemberUpdate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await CollaborationPartnerTeamController.update_member(
        branch_id, member_id, body, current_user=current_user
    )


@router.delete("/branches/{branch_id}/team/{member_id}")
async def api_delete_partner_team_member(
    branch_id: str,
    member_id: str,
    hard: bool = Query(False),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await CollaborationPartnerTeamController.delete_member(
        branch_id, member_id, hard=hard, current_user=current_user
    )
