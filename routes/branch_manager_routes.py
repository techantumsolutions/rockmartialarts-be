from fastapi import APIRouter, Depends, Request, Query
from typing import Optional

from controllers.branch_manager_controller import BranchManagerController
from models.branch_manager_models import (
    BranchManagerCreate, BranchManagerUpdate, BranchManagerLogin,
    BranchManagerForgotPassword, BranchManagerResetPassword, BranchManagerProfileUpdate
)
from models.user_models import UserRole
from utils.unified_auth import require_role_unified

router = APIRouter()

@router.post("/login")
async def branch_manager_login(login_data: BranchManagerLogin):
    """Branch manager login endpoint"""
    return await BranchManagerController.login_branch_manager(login_data)

@router.post("/forgot-password")
async def forgot_password(forgot_password_data: BranchManagerForgotPassword):
    """Initiate password reset process for branch manager"""
    return await BranchManagerController.forgot_password(forgot_password_data.email)

@router.post("/reset-password")
async def reset_password(reset_password_data: BranchManagerResetPassword):
    """Reset branch manager password using a token"""
    return await BranchManagerController.reset_password(
        reset_password_data.token,
        reset_password_data.new_password,
    )

@router.get("/me")
async def get_branch_manager_profile(
    current_user: dict = Depends(require_role_unified([UserRole.BRANCH_MANAGER]))
):
    """Get current branch manager's profile"""
    # Remove sensitive information
    manager_profile = {k: v for k, v in current_user.items() if k not in ["password_hash", "password"]}

    return {
        "branch_manager": manager_profile
    }

@router.put("/me")
async def update_branch_manager_profile(
    profile_data: BranchManagerProfileUpdate,
    current_user: dict = Depends(require_role_unified([UserRole.BRANCH_MANAGER]))
):
    """Update current branch manager's own profile - using same pattern as working routes"""
    try:
        from utils.database import get_db
        from datetime import datetime

        db = get_db()
        manager_id = current_user["id"]

        # Build update data similar to other working controllers
        update_data = {}

        if profile_data.full_name:
            update_data["full_name"] = profile_data.full_name
            # Also update nested personal_info structure if it exists
            name_parts = profile_data.full_name.split(' ', 1)
            update_data["personal_info.first_name"] = name_parts[0]
            update_data["personal_info.last_name"] = name_parts[1] if len(name_parts) > 1 else ""

        if profile_data.email:
            update_data["email"] = profile_data.email
            # Also update nested contact_info structure if it exists
            update_data["contact_info.email"] = profile_data.email

        if profile_data.phone:
            update_data["phone"] = profile_data.phone
            # Also update nested contact_info structure if it exists
            update_data["contact_info.phone"] = profile_data.phone

        # Add timestamp like other controllers
        update_data["updated_at"] = datetime.utcnow()

        # Perform database update
        result = await db.branch_managers.update_one(
            {"id": manager_id},
            {"$set": update_data}
        )

        if result.matched_count == 0:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Branch manager not found")

        # Return success response
        return {
            "status": "success",
            "message": "Profile updated successfully",
            "data": {
                "id": manager_id,
                "full_name": profile_data.full_name,
                "email": profile_data.email,
                "phone": profile_data.phone
            }
        }

    except Exception as e:
        from fastapi import HTTPException
        if isinstance(e, HTTPException):
            raise
        print(f"DEBUG: Error in profile update: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Profile update failed: {str(e)}")

@router.post("")
async def create_branch_manager(
    manager_data: BranchManagerCreate,
    request: Request,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN]))
):
    """Create new branch manager with nested structure"""
    return await BranchManagerController.create_branch_manager(manager_data, request, current_user)

@router.get("")
async def get_branch_managers(
    skip: int = Query(0, ge=0, description="Number of branch managers to skip"),
    limit: int = Query(50, ge=1, le=100, description="Number of branch managers to return"),
    active_only: bool = Query(True, description="Filter only active branch managers"),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN]))
):
    """Get list of branch managers with pagination"""
    return await BranchManagerController.get_branch_managers(skip, limit, active_only, current_user)

@router.get("/{manager_id}")
async def get_branch_manager(
    manager_id: str,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.BRANCH_MANAGER]))
):
    """Get specific branch manager by ID"""
    return await BranchManagerController.get_branch_manager(manager_id, current_user)

@router.put("/{manager_id}")
async def update_branch_manager(
    manager_id: str,
    manager_data: BranchManagerUpdate,
    request: Request,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.BRANCH_MANAGER]))
):
    """Update existing branch manager - now supports branch managers updating themselves"""
    print(f"DEBUG: Update route called with manager_id: {manager_id}")
    print(f"DEBUG: Current user ID: {current_user.get('id')}")
    print(f"DEBUG: Current user role: {current_user.get('role')}")

    # Allow branch managers to update their own profile
    if current_user.get("role") == "branch_manager" and current_user.get("id") != manager_id:
        from fastapi import HTTPException
        print(f"DEBUG: Authorization failed - user {current_user.get('id')} trying to update {manager_id}")
        raise HTTPException(status_code=403, detail="Branch managers can only update their own profile")

    print(f"DEBUG: Authorization passed, calling controller")
    return await BranchManagerController.update_branch_manager(manager_id, manager_data, request, current_user)

@router.delete("/{manager_id}")
async def delete_branch_manager(
    manager_id: str,
    request: Request,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN]))
):
    """Delete branch manager"""
    return await BranchManagerController.delete_branch_manager(manager_id, request, current_user)

@router.post("/{manager_id}/send-credentials")
async def send_branch_manager_credentials(
    manager_id: str,
    request: Request,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN]))
):
    """Send login credentials to branch manager via email"""
    return await BranchManagerController.send_credentials_email(manager_id, request, current_user)
