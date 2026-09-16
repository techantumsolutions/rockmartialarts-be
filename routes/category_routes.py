from fastapi import APIRouter, Depends, status
from typing import Optional
from controllers.category_controller import CategoryController
from models.category_models import CategoryCreate, CategoryUpdate
from models.user_models import UserRole
from utils.auth import require_role, get_current_active_user
from utils.unified_auth import require_role_unified, get_current_user_or_superadmin

router = APIRouter()

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_category(
    category_data: CategoryCreate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN]))
):
    """Create a new category - accessible by Super Admin only"""
    return await CategoryController.create_category(category_data, current_user)

@router.get("")
async def get_categories(
    parent_id: Optional[str] = None,
    active_only: bool = True,
    include_subcategories: bool = False,
    skip: int = 0,
    limit: int = 50,
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get categories with optional filtering - authenticated endpoint"""
    return await CategoryController.get_categories(parent_id, active_only, include_subcategories, skip, limit, current_user)

@router.get("/public/all")
async def get_public_categories(
    active_only: bool = True,
    include_subcategories: bool = True,
    skip: int = 0,
    limit: int = 100
):
    """Get all categories - Public endpoint (no authentication required)"""
    return await CategoryController.get_public_categories(active_only, include_subcategories, skip, limit)

@router.get("/public/nav")
async def get_public_category_nav():
    """Top-level active categories for website navigation — id, name, slug only."""
    return await CategoryController.get_public_category_nav()

@router.get("/public/by-slug/{slug}")
async def get_public_category_by_slug(slug: str):
    """Public category landing: category, subcategories, and active courses for this category only."""
    return await CategoryController.get_public_category_by_slug(slug)

@router.get("/{category_id}/subcategories")
async def get_subcategories(
    category_id: str,
    active_only: bool = True,
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """List subcategories for a category - authenticated endpoint"""
    return await CategoryController.get_subcategories(category_id, active_only, current_user)

@router.get("/{category_id}")
async def get_category(
    category_id: str,
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get single category by ID - authenticated endpoint"""
    return await CategoryController.get_category(category_id, current_user)

@router.put("/{category_id}")
async def update_category(
    category_id: str,
    category_update: CategoryUpdate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN]))
):
    """Update category - accessible by Super Admin only"""
    return await CategoryController.update_category(category_id, category_update, current_user)

@router.delete("/{category_id}")
async def delete_category(
    category_id: str,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN]))
):
    """Delete category - accessible by Super Admin only"""
    return await CategoryController.delete_category(category_id, current_user)

@router.get("/public/details")
async def get_categories_with_details(
    category_id: Optional[str] = None,
    include_courses: bool = True,
    active_only: bool = True,
    skip: int = 0,
    limit: int = 50
):
    """Get category details with associated courses - Public endpoint (no authentication required)"""
    return await CategoryController.get_categories_with_details(category_id, include_courses, active_only, skip, limit)

@router.get("/public/with-courses-and-durations")
async def get_categories_with_courses_and_durations(
    category_id: Optional[str] = None,
    active_only: bool = True,
    include_locations: bool = False,
    skip: int = 0,
    limit: int = 20
):
    """Get categories with their courses and available durations in nested structure - Public endpoint (no authentication required)"""
    return await CategoryController.get_categories_with_courses_and_durations(category_id, active_only, include_locations, skip, limit)

@router.get("/public/location-hierarchy")
async def get_category_location_hierarchy(
    category_id: str,
    location_id: Optional[str] = None,
    active_only: bool = True,
    include_pricing: bool = True
):
    """Get complete hierarchy for a category - courses, locations, branches, and durations - Public endpoint (no authentication required)"""
    return await CategoryController.get_category_location_hierarchy(category_id, location_id, active_only, include_pricing)
