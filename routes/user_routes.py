from fastapi import APIRouter, Depends, Request, Path
from typing import Optional
from controllers.user_controller import UserController
from models.user_models import UserCreate, UserUpdate, UserRole, StudentNotifyBody, StudentStatusUpdateBody
from utils.auth import require_role
from utils.unified_auth import require_role_unified, get_current_user_or_superadmin

router = APIRouter()

@router.post("")
async def create_user(
    user_data: UserCreate,
    request: Request,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER, UserRole.COACH]))
):
    return await UserController.create_user(user_data, request, current_user)

@router.get("")
async def get_users(
    role: Optional[UserRole] = None,
    branch_id: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.COACH, UserRole.BRANCH_MANAGER]))
):
    """Get users with filtering - accessible by Super Admin, Coach Admin, Coach, and Branch Manager"""
    return await UserController.get_users(role, branch_id, skip, limit, current_user)

@router.get("/students/details")
async def get_student_details(
    unassigned_only: Optional[bool] = False,
    branch_id: Optional[str] = None,
    is_active: Optional[bool] = None,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.COACH, UserRole.BRANCH_MANAGER]))
):
    """Get detailed student information. Optional is_active filters account status (active/inactive)."""
    return await UserController.get_student_details(
        current_user,
        unassigned_only=unassigned_only,
        branch_id=branch_id,
        is_active=is_active,
    )


@router.get("/students/biometric-mappings")
async def list_student_biometric_mappings(
    q: Optional[str] = None,
    branch_id: Optional[str] = None,
    mapped: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]
        )
    ),
):
    """M09-S02: list students with biometric mapping status (Admin/BM scoped)."""
    return await UserController.list_student_biometric_mappings(
        current_user,
        q=q,
        branch_id=branch_id,
        mapped=mapped,
        skip=skip,
        limit=limit,
    )

@router.get("/{user_id}/enrollments")
async def get_user_enrollments(
    user_id: str,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.COACH, UserRole.BRANCH_MANAGER]))
):
    """Get enrollment history for a specific student"""
    return await UserController.get_user_enrollments(user_id, current_user)

@router.get("/{user_id}/payments")
async def get_user_payments(
    user_id: str,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.COACH, UserRole.BRANCH_MANAGER]))
):
    """Get payment history for a specific student"""
    return await UserController.get_user_payments(user_id, current_user)


@router.post("/{user_id}/notify-student")
async def notify_student(
    user_id: str,
    body: StudentNotifyBody,
    request: Request,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Send welcome or payment reminder via SMS + WhatsApp (super admin)."""
    return await UserController.send_student_notification(user_id, body.kind, request, current_user)


@router.get("/{user_id}")
async def get_user_by_id(
    user_id: str = Path(..., description="User ID"),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.COACH, UserRole.BRANCH_MANAGER]))
):
    """Get single user by ID - accessible by Super Admin, Coach Admin, Coach, and Branch Manager"""
    return await UserController.get_user(user_id, current_user)

@router.put("/{user_id}")
async def update_user(
    user_id: str,
    user_update: UserUpdate,
    request: Request,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]))
):
    return await UserController.update_user(user_id, user_update, request, current_user)

@router.post("/{user_id}/force-password-reset")
async def force_password_reset(
    user_id: str,
    request: Request,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN]))
):
    return await UserController.force_password_reset(user_id, request, current_user)

@router.get("/debug/current-user")
async def debug_current_user(
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]))
):
    """Debug endpoint to show current user data structure"""
    # Remove sensitive information
    debug_user = {k: v for k, v in current_user.items() if k not in ["password_hash", "password"]}
    return {
        "current_user": debug_user,
        "user_keys": list(current_user.keys()),
        "role": current_user.get("role"),
        "branch_assignment": current_user.get("branch_assignment"),
        "branch_id": current_user.get("branch_id")
    }

@router.get("/debug/delete-permissions/{user_id}")
async def debug_delete_permissions(
    user_id: str,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]))
):
    """Debug endpoint to check delete permissions for a specific user"""
    from utils.database import get_db

    # Get the target user
    user = await get_db().users.find_one({"id": user_id})
    if not user:
        return {"error": "User not found"}

    # Get current user info
    current_role = current_user.get("role")

    result = {
        "current_user_role": current_role,
        "target_user_id": user_id,
        "target_user_role": user.get("role"),
        "target_user_branch_id": user.get("branch_id"),
        "can_delete": False,
        "reason": ""
    }

    if current_role == "branch_manager":
        # Get branch manager's assigned branch ID
        branch_assignment = current_user.get("branch_assignment")
        direct_branch_id = current_user.get("branch_id")

        manager_branch_id = None
        if branch_assignment and branch_assignment.get("branch_id"):
            manager_branch_id = branch_assignment["branch_id"]
        elif direct_branch_id:
            manager_branch_id = direct_branch_id

        result["manager_branch_id"] = manager_branch_id
        result["branch_assignment"] = branch_assignment
        result["direct_branch_id"] = direct_branch_id

        if not manager_branch_id:
            result["reason"] = "No branch assigned to this manager"
            return result

        # Check if student belongs to branch manager's branch
        user_branch_id = user.get("branch_id")
        belongs_to_branch = False

        # First check if user has direct branch_id assignment
        if user_branch_id == manager_branch_id:
            belongs_to_branch = True
            result["match_method"] = "direct_branch_id"
        else:
            # Query enrollments collection for this student
            student_enrollments = await get_db().enrollments.find({
                "student_id": user_id,
                "is_active": True
            }).to_list(length=100)

            result["student_enrollments"] = len(student_enrollments)
            result["enrollment_branches"] = [e.get("branch_id") for e in student_enrollments]

            # Check if any enrollment is for the branch manager's branch
            for enrollment in student_enrollments:
                if enrollment.get("branch_id") == manager_branch_id:
                    belongs_to_branch = True
                    result["match_method"] = "enrollment"
                    break

        result["can_delete"] = belongs_to_branch
        if not belongs_to_branch:
            if user_branch_id:
                result["reason"] = f"Student is assigned to branch {user_branch_id}, but you manage branch {manager_branch_id}"
            else:
                result["reason"] = f"Student has no branch assignment or enrollments in your branch ({manager_branch_id})"
    else:
        result["can_delete"] = True
        result["reason"] = "Super admin or coach admin can delete any user"

    return result

@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    request: Request,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]))
):
    """Permanently delete user - accessible by Super Admin, Coach Admin, and Branch Manager"""
    return await UserController.delete_user(user_id, request, current_user)

@router.patch("/{user_id}/status")
async def update_student_status(
    user_id: str,
    body: StudentStatusUpdateBody,
    request: Request,
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]
        )
    ),
):
    """M08-S01: update student account active/inactive status with reason + history."""
    return await UserController.update_student_status(user_id, body, request, current_user)


@router.get("/{user_id}/status-history")
async def get_student_status_history(
    user_id: str,
    limit: int = 50,
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]
        )
    ),
):
    """M08-S01: student account status history."""
    return await UserController.get_student_status_history(user_id, current_user, limit=limit)


@router.get("/{user_id}/biometric-mapping")
async def get_student_biometric_mapping(
    user_id: str,
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]
        )
    ),
):
    """M09-S02: get biometric mapping for a student."""
    return await UserController.get_student_biometric_mapping(user_id, current_user)


@router.put("/{user_id}/biometric-mapping")
async def set_student_biometric_mapping(
    user_id: str,
    request: Request,
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]
        )
    ),
):
    """M09-S02: create/update biometric mapping (duplicate → 409)."""
    from utils.student_biometric_mapping_service import StudentBiometricMappingBody

    try:
        raw = await request.json()
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    body = StudentBiometricMappingBody(**raw)
    return await UserController.set_student_biometric_mapping(
        user_id, body, request, current_user
    )


@router.delete("/{user_id}/biometric-mapping")
async def clear_student_biometric_mapping(
    user_id: str,
    request: Request,
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]
        )
    ),
):
    """M09-S02: clear biometric mapping."""
    return await UserController.clear_student_biometric_mapping(
        user_id, request, current_user
    )


@router.get("/{user_id}/id-card")
async def get_student_id_card(
    user_id: str,
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]
        )
    ),
):
    """M08-S03: view current student ID card."""
    return await UserController.get_student_id_card(user_id, current_user)


@router.post("/{user_id}/id-card")
async def generate_student_id_card(
    user_id: str,
    request: Request,
    regenerate: bool = False,
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]
        )
    ),
):
    """M08-S03: generate (or regenerate) student ID card + secure QR token."""
    return await UserController.generate_student_id_card(
        user_id, request, current_user, regenerate=regenerate
    )


@router.post("/{user_id}/id-card/revoke")
async def revoke_student_id_card(
    user_id: str,
    request: Request,
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]
        )
    ),
):
    """M08-S03: revoke active student ID card."""
    return await UserController.revoke_student_id_card(user_id, request, current_user)


@router.patch("/{user_id}/deactivate")
async def deactivate_user(
    user_id: str,
    request: Request,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN]))
):
    """Deactivate user (soft delete) - accessible by Super Admin only"""
    return await UserController.deactivate_user(user_id, request, current_user)
