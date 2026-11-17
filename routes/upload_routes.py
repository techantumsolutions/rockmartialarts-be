from fastapi import APIRouter, Depends, File, UploadFile
from controllers.upload_controller import UploadController
from models.user_models import UserRole
from utils.unified_auth import require_role_unified

router = APIRouter()


@router.post("")
async def upload_file(
    file: UploadFile = File(...),
    current_user: dict = Depends(
        require_role_unified(
            [
                UserRole.SUPER_ADMIN,
                UserRole.BRANCH_MANAGER,
                UserRole.COACH_ADMIN,
                UserRole.COACH,
                UserRole.PARTNER_SUPER_ADMIN,
            ]
        )
    ),
):
    """Upload a file (image, video, or PDF). Returns the public URL."""
    role = str((current_user or {}).get("role") or "").lower()
    if role == UserRole.PARTNER_SUPER_ADMIN.value or role == "partner_super_admin":
        org_id = (current_user or {}).get("partner_org_id")
        if not org_id:
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="Partner organization context required")
        from utils.partner_branding_approval import assert_partner_can_edit_branding

        await assert_partner_can_edit_branding(org_id)
    return await UploadController.upload_file(file, current_user)
