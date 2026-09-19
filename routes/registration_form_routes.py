from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from controllers import registration_form_controller as ctrl
from models.registration_form_models import RegistrationFormCreate, RegistrationFormUpdate
from models.user_models import UserRole
from utils.auth import get_current_user
from utils.unified_auth import require_role_unified

router = APIRouter()


@router.get("/student")
async def list_registration_forms_for_student(
    current_user: dict = Depends(get_current_user),
):
    """Active registration forms applicable to the logged-in student."""
    if current_user.get("role") != UserRole.STUDENT.value:
        raise HTTPException(status_code=403, detail="Students only")
    items = await ctrl.list_for_student(current_user["id"])
    return {"registration_forms": items}


@router.get("/manage")
async def list_registration_forms_manage(
    status: Optional[str] = Query(None, description="active or inactive"),
    current_user: dict = Depends(
        require_role_unified([UserRole.SUPER_ADMIN, UserRole.BRANCH_MANAGER])
    ),
):
    items = await ctrl.list_manage(current_user, status=status)
    return {"registration_forms": items}


@router.post("")
async def create_registration_form(
    data: RegistrationFormCreate,
    current_user: dict = Depends(
        require_role_unified([UserRole.SUPER_ADMIN, UserRole.BRANCH_MANAGER])
    ),
):
    return await ctrl.create(data, current_user)


@router.put("/{form_id}")
async def update_registration_form(
    form_id: str,
    data: RegistrationFormUpdate,
    current_user: dict = Depends(
        require_role_unified([UserRole.SUPER_ADMIN, UserRole.BRANCH_MANAGER])
    ),
):
    return await ctrl.update(form_id, data, current_user)


@router.delete("/{form_id}")
async def delete_registration_form(
    form_id: str,
    current_user: dict = Depends(
        require_role_unified([UserRole.SUPER_ADMIN, UserRole.BRANCH_MANAGER])
    ),
):
    return await ctrl.delete_form(form_id, current_user)
