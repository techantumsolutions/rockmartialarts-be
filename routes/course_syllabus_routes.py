"""M11-S01 Course syllabus admin routes."""
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status

from models.course_syllabus_models import CourseSyllabusMetaUpdate
from models.user_models import UserRole
from utils.course_syllabus_service import (
    activate_syllabus,
    create_syllabus,
    deactivate_syllabus,
    get_syllabus,
    list_syllabi,
    replace_syllabus,
    stream_syllabus_file,
    update_syllabus_meta,
)
from utils.unified_auth import require_role_unified

router = APIRouter()

ADMIN_ROLES = [
    UserRole.SUPER_ADMIN,
    UserRole.COACH_ADMIN,
    UserRole.BRANCH_MANAGER,
    UserRole.COACH,
]


@router.get("")
async def api_list_syllabi(
    course_id: Optional[str] = Query(None),
    active_only: bool = Query(False),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await list_syllabi(
        course_id=course_id, active_only=active_only, skip=skip, limit=limit
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def api_create_syllabus(
    course_id: str = Form(...),
    title: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    activate: bool = Form(False),
    file: UploadFile = File(...),
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await create_syllabus(
        course_id=course_id,
        file=file,
        current_user=current_user,
        title=title,
        notes=notes,
        activate=activate,
    )


@router.get("/{syllabus_id}")
async def api_get_syllabus(
    syllabus_id: str,
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await get_syllabus(syllabus_id)


@router.patch("/{syllabus_id}")
async def api_update_syllabus_meta(
    syllabus_id: str,
    body: CourseSyllabusMetaUpdate,
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await update_syllabus_meta(syllabus_id, body, current_user=current_user)


@router.post("/{syllabus_id}/replace", status_code=status.HTTP_201_CREATED)
async def api_replace_syllabus(
    syllabus_id: str,
    title: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    activate: bool = Form(True),
    file: UploadFile = File(...),
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await replace_syllabus(
        syllabus_id,
        file=file,
        current_user=current_user,
        title=title,
        notes=notes,
        activate=activate,
    )


@router.post("/{syllabus_id}/activate")
async def api_activate_syllabus(
    syllabus_id: str,
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await activate_syllabus(syllabus_id, current_user=current_user)


@router.post("/{syllabus_id}/deactivate")
async def api_deactivate_syllabus(
    syllabus_id: str,
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await deactivate_syllabus(syllabus_id, current_user=current_user)


@router.get("/{syllabus_id}/file")
async def api_stream_syllabus_file(
    syllabus_id: str,
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    """Authenticated inline PDF stream for admin verification (private storage)."""
    return await stream_syllabus_file(syllabus_id)
