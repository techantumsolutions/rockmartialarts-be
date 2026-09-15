from fastapi import APIRouter, Depends, Query, status
from controllers.cms_controller import CMSController
from controllers.cms_residential_camp_controller import CMSResidentialCampController
from models.cms_models import CMSContentUpdate, CMSContentResponse
from models.cms_residential_camp_models import CampEventFields, ResidentialCampContent, ResidentialCampResponse
from models.user_models import UserRole
from utils.unified_auth import require_role_unified

router = APIRouter()



@router.get("/public/branch-testimonials/{branch_id}")
async def get_branch_testimonials_public(
    branch_id: str,
    limit: int = Query(4, ge=1, le=20),
):
    """Branch-scoped testimonials for public website (no auth required)."""
    return await CMSController.get_branch_testimonials_public(branch_id, limit)


@router.get("/public/residential-camp", response_model=ResidentialCampResponse)
async def get_residential_camp_public():
    """Residential camp landing page content (no auth required)."""
    return await CMSResidentialCampController.get_content()


@router.get("/public")
async def get_cms_content_public():
    """Get CMS content for public website (no auth required)"""
    return await CMSController.get_cms_content_public()


@router.put("/camp-events/{event_id}")
async def update_camp_event(
    event_id: str,
    data: CampEventFields,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Update a camp event document (super-admin). Does not require Save All."""
    return await CMSResidentialCampController.update_event(event_id, data)


@router.get("/residential-camp", response_model=ResidentialCampResponse)
async def get_residential_camp(
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN]))
):
    """Get residential camp CMS content (super-admin)."""
    return await CMSResidentialCampController.get_content()


@router.put("/residential-camp", response_model=ResidentialCampResponse)
async def update_residential_camp(
    data: ResidentialCampContent,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN]))
):
    """Update residential camp CMS content (super-admin)."""
    return await CMSResidentialCampController.update_content(data)


@router.post("/residential-camp/new-event", response_model=ResidentialCampResponse)
async def start_residential_camp_event(
    data: CampEventFields,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Archive the current event and create a new current camp_events document immediately."""
    return await CMSResidentialCampController.start_new_event(data)


@router.get("", response_model=CMSContentResponse)
async def get_cms_content(
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN]))
):
    """Get CMS content (homepage sections, footer, branding, page SEO)"""
    return await CMSController.get_cms_content(current_user)


@router.put("", response_model=CMSContentResponse)
async def update_cms_content(
    data: CMSContentUpdate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN]))
):
    """Update CMS content"""
    return await CMSController.update_cms_content(data, current_user)


@router.put("/branding/{field}")
async def update_branding_image(
    field: str,
    image_url: str,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN]))
):
    """Update a branding image (navbar_logo, footer_logo, favicon, site_loader_image)"""
    return await CMSController.upload_branding_image(field, image_url, current_user)
