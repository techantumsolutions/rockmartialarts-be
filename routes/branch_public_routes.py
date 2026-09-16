from fastapi import APIRouter
from typing import Optional
from controllers.location_controller import LocationController
from controllers.branch_controller import BranchController

router = APIRouter()

@router.get("/public/by-location/{location_id}")
async def get_branches_by_location(
    location_id: str,
    include_courses: bool = True,
    include_timings: bool = True,
    active_only: bool = True,
    skip: int = 0,
    limit: int = 50
):
    """Get branches filtered by location - Public endpoint (no authentication required)"""
    return await LocationController.get_branches_by_location(location_id, include_courses, include_timings, active_only, skip, limit)

@router.get("/public/all")
async def get_all_branches_public(
    active_only: bool = True,
    skip: int = 0,
    limit: int = 100
):
    """Get all branches - Public endpoint (no authentication required)"""
    return await BranchController.get_branches_public(active_only, skip, limit)


@router.get("/public/search")
async def search_branches_public(
    state_id: Optional[str] = None,
    city_id: Optional[str] = None,
    location_id: Optional[str] = None,
    q: Optional[str] = None,
    active_only: bool = True,
    skip: int = 0,
    limit: int = 100,
):
    """Public branch discovery. state_id, city_id, and q work alone or together."""
    return await BranchController.search_branches_public(
        state_id=state_id,
        city_id=city_id or location_id,
        q=q,
        active_only=active_only,
        skip=skip,
        limit=limit,
    )


@router.get("/public/by-slug/{slug}")
async def get_public_branch_by_slug(slug: str):
    """Public branch detail by slug or id, with available courses."""
    return await BranchController.get_branch_by_slug(slug)


@router.get("/public/{branch_id}")
async def get_branch_public(branch_id: str):
    """Get one branch by ID for public detail page (no authentication required)"""
    return await BranchController.get_branch_public(branch_id)
