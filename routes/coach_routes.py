from fastapi import APIRouter, Depends, Request, Query, File, UploadFile
from typing import Optional

from controllers.coach_controller import CoachController
from models.coach_models import (
    CoachCreate,
    CoachUpdate,
    CoachLogin,
    CoachForgotPassword,
    CoachResetPassword,
    CoachRegistrationCreate,
    CoachApprovalActionBody,
    CoachRejectBody,
    CoachActiveStatusBody,
)
from models.user_models import UserRole
from utils.unified_auth import (
    get_optional_current_user_or_superadmin,
    require_role_unified,
)
from utils.coach_registration_service import (
    get_coach_registration_options,
    register_coach,
    upload_registration_photo,
)
from utils.coach_approval_service import (
    approve_coach,
    approval_summary,
    get_approval_history,
    get_coach_for_approval,
    list_approval_coaches,
    reject_coach,
    set_coach_active_status,
)
from utils.coach_availability_service import (
    get_availability,
    get_availability_options,
    update_availability,
)
from models.coach_availability_models import CoachAvailabilityUpdate

router = APIRouter()

@router.post("/login")
async def coach_login(login_data: CoachLogin):
    """Coach login endpoint"""
    return await CoachController.login_coach(login_data)

@router.post("/forgot-password")
async def forgot_password(forgot_password_data: CoachForgotPassword):
    """Initiate password reset process for coach"""
    return await CoachController.forgot_password(forgot_password_data.email)

@router.post("/reset-password")
async def reset_password(reset_password_data: CoachResetPassword):
    """Reset coach password using a token"""
    return await CoachController.reset_password(reset_password_data.token, reset_password_data.new_password)


@router.get("/register/options")
async def coach_registration_options():
    """M14-S01 public — branches, specializations, experience options for registration form."""
    return await get_coach_registration_options()


@router.post("/register/photo")
async def coach_registration_photo(file: UploadFile = File(...)):
    """M14-S01 public — image-only photo upload for coach registration."""
    return await upload_registration_photo(file)


@router.post("/register", status_code=201)
async def coach_self_register(
    body: CoachRegistrationCreate,
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """M14-S01 public self-registration (pending approval). Does not replace admin create."""
    return await register_coach(body, current_user=current_user)


# ---------- M14-S02 Coach Approval & Status (admin) ----------

_APPROVAL_ROLES = [
    UserRole.SUPER_ADMIN,
    UserRole.COACH_ADMIN,
    UserRole.BRANCH_MANAGER,
]


@router.get("/approvals")
async def list_coach_approvals(
    approval_status: Optional[str] = Query(
        None, description="pending | approved | rejected | all"
    ),
    is_active: Optional[bool] = Query(None, description="Filter by active flag"),
    registration_source: Optional[str] = Query(
        None, description="self | admin | all"
    ),
    search: Optional[str] = Query(None, description="Name, email, phone, or id"),
    skip: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    current_user: dict = Depends(require_role_unified(_APPROVAL_ROLES)),
):
    """M14-S02 admin coach list with approval filters."""
    return await list_approval_coaches(
        current_user=current_user,
        approval_status=approval_status,
        is_active=is_active,
        registration_source=registration_source,
        search=search,
        skip=skip,
        limit=limit,
    )


@router.get("/approvals/summary")
async def coach_approvals_summary(
    current_user: dict = Depends(require_role_unified(_APPROVAL_ROLES)),
):
    """M14-S02 approval counts for admin dashboard."""
    return await approval_summary(current_user=current_user)


@router.get("/approvals/{coach_id}")
async def coach_approval_detail(
    coach_id: str,
    current_user: dict = Depends(require_role_unified(_APPROVAL_ROLES)),
):
    """M14-S02 coach detail + approval history."""
    return await get_coach_for_approval(coach_id, current_user=current_user)


@router.post("/{coach_id}/approve")
async def coach_approve(
    coach_id: str,
    body: Optional[CoachApprovalActionBody] = None,
    current_user: dict = Depends(require_role_unified(_APPROVAL_ROLES)),
):
    """M14-S02 approve pending/rejected coach (sets active)."""
    return await approve_coach(
        coach_id, body or CoachApprovalActionBody(), current_user=current_user
    )


@router.post("/{coach_id}/reject")
async def coach_reject(
    coach_id: str,
    body: Optional[CoachRejectBody] = None,
    current_user: dict = Depends(require_role_unified(_APPROVAL_ROLES)),
):
    """M14-S02 reject coach registration (sets inactive)."""
    return await reject_coach(
        coach_id, body or CoachRejectBody(), current_user=current_user
    )


@router.patch("/{coach_id}/active-status")
async def coach_active_status(
    coach_id: str,
    body: CoachActiveStatusBody,
    current_user: dict = Depends(require_role_unified(_APPROVAL_ROLES)),
):
    """M14-S02 set active/inactive for approved coaches (records history)."""
    return await set_coach_active_status(coach_id, body, current_user=current_user)


@router.get("/{coach_id}/approval-history")
async def coach_approval_history(
    coach_id: str,
    current_user: dict = Depends(require_role_unified(_APPROVAL_ROLES)),
):
    """M14-S02 approval / status history trail."""
    return await get_approval_history(coach_id, current_user=current_user)


@router.post("")
async def create_coach(
    coach_data: CoachCreate,
    request: Request,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]))
):
    """Create new coach with nested structure"""
    return await CoachController.create_coach(coach_data, request, current_user)

@router.get("")
async def get_coaches(
    skip: int = Query(0, ge=0, description="Number of coaches to skip"),
    limit: int = Query(50, ge=1, le=100, description="Number of coaches to return"),
    active_only: bool = Query(True, description="Filter only active coaches"),
    area_of_expertise: Optional[str] = Query(None, description="Filter by area of expertise"),
    approval_status: Optional[str] = Query(
        None, description="M14 additive: pending | approved | rejected"
    ),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.COACH, UserRole.BRANCH_MANAGER]))
):
    """Get coaches with filtering options"""
    return await CoachController.get_coaches(
        skip, limit, active_only, area_of_expertise, current_user, approval_status
    )

@router.get("/me")
async def get_my_profile(
    current_user: dict = Depends(require_role_unified([UserRole.COACH]))
):
    """Get current coach's profile"""
    return await CoachController.get_coach_by_id(current_user["id"], current_user)


# ---------- M14-S03 Coach Availability ----------

_AVAIL_ROLES = [
    UserRole.SUPER_ADMIN,
    UserRole.COACH_ADMIN,
    UserRole.BRANCH_MANAGER,
    UserRole.COACH,
]


@router.get("/me/availability/options")
async def my_availability_options(
    current_user: dict = Depends(require_role_unified([UserRole.COACH])),
):
    """M14-S03 — locations/weekdays for current coach."""
    return await get_availability_options(
        coach_id=current_user["id"], current_user=current_user
    )


@router.get("/me/availability")
async def my_availability(
    current_user: dict = Depends(require_role_unified([UserRole.COACH])),
):
    """M14-S03 — get current coach weekly availability."""
    return await get_availability(
        coach_id=current_user["id"], current_user=current_user
    )


@router.put("/me/availability")
async def my_availability_update(
    body: CoachAvailabilityUpdate,
    current_user: dict = Depends(require_role_unified([UserRole.COACH])),
):
    """M14-S03 — update current coach weekly availability."""
    return await update_availability(
        coach_id=current_user["id"], body=body, current_user=current_user
    )


@router.get("/{coach_id}/availability/options")
async def coach_availability_options(
    coach_id: str,
    current_user: dict = Depends(require_role_unified(_AVAIL_ROLES)),
):
    """M14-S03 — admin/coach options for a coach's availability form."""
    return await get_availability_options(coach_id=coach_id, current_user=current_user)


@router.get("/{coach_id}/availability")
async def coach_availability_get(
    coach_id: str,
    current_user: dict = Depends(require_role_unified(_AVAIL_ROLES)),
):
    """M14-S03 — get coach weekly availability."""
    return await get_availability(coach_id=coach_id, current_user=current_user)


@router.put("/{coach_id}/availability")
async def coach_availability_put(
    coach_id: str,
    body: CoachAvailabilityUpdate,
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER, UserRole.COACH]
        )
    ),
):
    """M14-S03 — update coach weekly availability (coach self or admin)."""
    return await update_availability(
        coach_id=coach_id, body=body, current_user=current_user
    )


@router.get("/public/homepage")
async def get_public_homepage_coaches(
    limit: int = Query(8, ge=1, le=8, description="Max coaches for homepage (ordered by display_order)"),
):
    """Public homepage coaches (display_order, then created_at). No authentication."""
    return await CoachController.get_public_homepage_coaches(limit)

@router.put("/me")
async def update_my_profile(
    coach_update: CoachUpdate,
    request: Request,
    current_user: dict = Depends(require_role_unified([UserRole.COACH]))
):
    """Update current coach's profile"""
    return await CoachController.update_coach_profile(current_user["id"], coach_update, request, current_user)

@router.get("/{coach_id}")
async def get_coach_by_id(
    coach_id: str,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.COACH, UserRole.BRANCH_MANAGER]))
):
    """Get coach by ID - accessible by Super Admin, Coach Admin, Coach, and Branch Manager"""
    return await CoachController.get_coach_by_id(coach_id, current_user)

@router.put("/{coach_id}")
async def update_coach(
    coach_id: str,
    coach_update: CoachUpdate,
    request: Request,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN]))
):
    """Update coach information"""
    return await CoachController.update_coach(coach_id, coach_update, request, current_user)

@router.delete("/{coach_id}")
async def deactivate_coach(
    coach_id: str,
    request: Request,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.BRANCH_MANAGER]))
):
    """Deactivate coach (Super Admin and Branch Manager)"""
    return await CoachController.deactivate_coach(coach_id, request, current_user)


@router.patch("/{coach_id}/activate")
async def activate_coach(
    coach_id: str,
    request: Request,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.BRANCH_MANAGER]))
):
    """Activate coach (Super Admin and Branch Manager)"""
    return await CoachController.activate_coach(coach_id, request, current_user)


# @router.get("/public/by-course/{course_id}")
# async def get_coaches_by_course_public(
#     course_id: str
# ):
#     """Get coaches assigned to a specific course - Public endpoint (no authentication required)"""
#     return await CoachController.get_coaches_by_course_public(course_id)

@router.get("/by-course/{course_id}")
async def get_coaches_by_course(
    course_id: str,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.COACH, UserRole.BRANCH_MANAGER]))
):
    """Get coaches assigned to a specific course"""
    return await CoachController.get_coaches_by_course(course_id, current_user)

@router.get("/{coach_id}/courses")
async def get_coach_courses(
    coach_id: str,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.COACH, UserRole.BRANCH_MANAGER]))
):
    """Get courses assigned to a specific coach"""
    return await CoachController.get_coach_courses(coach_id, current_user)

@router.get("/{coach_id}/students")
async def get_coach_students(
    coach_id: str,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.COACH, UserRole.BRANCH_MANAGER]))
):
    """Get students enrolled in courses taught by a specific coach"""
    return await CoachController.get_coach_students(coach_id, current_user)

@router.post("/{coach_id}/send-credentials")
async def send_coach_credentials(
    coach_id: str,
    request: Request,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN]))
):
    """Send login credentials to coach via email"""
    return await CoachController.send_credentials_email(coach_id, request, current_user)

@router.get("/stats/overview")
async def get_coach_stats(
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN]))
):
    """Get coach statistics and analytics"""
    return await CoachController.get_coach_stats()
