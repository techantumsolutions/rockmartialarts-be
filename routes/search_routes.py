from fastapi import APIRouter, Depends, Query
from typing import Optional
from controllers.search_controller import SearchController
from models.user_models import UserRole
from utils.unified_auth import require_role_unified, get_current_user_or_superadmin

router = APIRouter()

@router.get("/global")
async def global_search(
    q: str = Query(..., min_length=2, description="Search query (minimum 2 characters)"),
    type: Optional[str] = Query(None, description="Filter by entity type: users, coaches, courses, branches"),
    limit: int = Query(50, ge=1, le=100, description="Maximum results per category"),
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """
    Global search across all entities (users, coaches, courses, branches)
    Accessible by Super Admin, Coach Admin, and Coach with appropriate filtering
    """
    return await SearchController.global_search(q, type, limit, current_user)

@router.get("/users")
async def search_users(
    q: str = Query(..., min_length=2, description="Search query (minimum 2 characters)"),
    role: Optional[UserRole] = Query(None, description="Filter by user role"),
    branch_id: Optional[str] = Query(None, description="Filter by branch ID"),
    limit: int = Query(50, ge=1, le=100, description="Maximum results"),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.COACH]))
):
    """
    Search specifically in users
    Accessible by Super Admin, Coach Admin, and Coach with role-based filtering
    """
    return await SearchController.search_users(q, role, branch_id, limit, current_user)

@router.get("/students")
async def search_students(
    q: Optional[str] = Query(None, description="Search query (optional)"),
    branch_id: Optional[str] = Query(None, description="Filter by branch ID"),
    course_id: Optional[str] = Query(None, description="Filter by course ID"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    start_date: Optional[str] = Query(None, description="Filter by start date (ISO format)"),
    end_date: Optional[str] = Query(None, description="Filter by end date (ISO format)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=1000, description="Maximum results"),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.COACH, UserRole.BRANCH_MANAGER]))
):
    """
    Comprehensive student search with enrollment data
    Supports filtering by branch, course, activity status, and date range
    Accessible by Super Admin, Coach Admin, Coach, and Branch Manager with role-based filtering
    """
    return await SearchController.search_students(q, branch_id, course_id, is_active, start_date, end_date, skip, limit, current_user)

@router.get("/coaches")
async def search_coaches(
    q: str = Query(..., min_length=2, description="Search query (minimum 2 characters)"),
    area_of_expertise: Optional[str] = Query(None, description="Filter by area of expertise"),
    active_only: bool = Query(True, description="Filter only active coaches"),
    limit: int = Query(50, ge=1, le=100, description="Maximum results"),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.COACH]))
):
    """
    Search specifically in coaches
    Accessible by Super Admin, Coach Admin, and Coach
    """
    return await SearchController.search_coaches(q, area_of_expertise, active_only, limit, current_user)

@router.get("/courses")
async def search_courses(
    q: str = Query(..., min_length=2, description="Search query (minimum 2 characters)"),
    category_id: Optional[str] = Query(None, description="Filter by category ID"),
    difficulty_level: Optional[str] = Query(None, description="Filter by difficulty level"),
    active_only: bool = Query(True, description="Filter only active courses"),
    limit: int = Query(50, ge=1, le=100, description="Maximum results"),
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """
    Search specifically in courses
    Accessible by Super Admin, Coach Admin, and Coach
    """
    return await SearchController.search_courses(q, category_id, difficulty_level, active_only, limit, current_user)

@router.get("/branches")
async def search_branches(
    q: str = Query(..., min_length=2, description="Search query (minimum 2 characters)"),
    active_only: bool = Query(True, description="Filter only active branches"),
    limit: int = Query(50, ge=1, le=100, description="Maximum results"),
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """
    Search specifically in branches
    Accessible by Super Admin, Coach Admin, and Coach
    """
    return await SearchController.search_branches(q, active_only, limit, current_user)

# Superadmin-specific search endpoints
@router.get("/superadmin/global")
async def superadmin_global_search(
    q: str = Query(..., min_length=2, description="Search query (minimum 2 characters)"),
    type: Optional[str] = Query(None, description="Filter by entity type: users, coaches, courses, branches"),
    limit: int = Query(50, ge=1, le=100, description="Maximum results per category"),
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """
    Global search for superadmin with full access to all data
    """
    # For superadmin, we can bypass some role restrictions
    if current_user.get("role") == "superadmin":
        # Create a mock super admin user for the search controller
        superadmin_user = {
            "id": current_user.get("id"),
            "role": "super_admin",  # Convert to expected format
            "branch_id": None  # Superadmin has access to all branches
        }
        return await SearchController.global_search(q, type, limit, superadmin_user)
    else:
        return await SearchController.global_search(q, type, limit, current_user)
