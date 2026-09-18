from fastapi import APIRouter, Depends, Query, Request, Header, HTTPException
from typing import Optional
from datetime import datetime
import os
from controllers.attendance_controller import AttendanceController
from models.attendance_models import (
    AttendanceCreate, BiometricAttendance, CoachAttendanceCreate, BranchManagerAttendanceCreate,
    AttendanceMarkRequest
)
from models.user_models import UserRole
from utils.unified_auth import (
    require_role_unified,
    get_current_user_or_superadmin,
    get_optional_current_user_or_superadmin,
)
from utils.biometric_attendance_ingest import (
    BiometricIngestRequest,
    ingest_events,
    list_unmatched,
    ingest_stats,
)
from utils.attendance_correction_service import (
    AttendanceCorrectionRequest,
    correct_attendance,
    list_correction_history,
)

router = APIRouter()


async def require_biometric_ingest_auth(
    request: Request,
    x_biometric_ingest_key: Optional[str] = Header(None, alias="X-Biometric-Ingest-Key"),
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """
    Allow device middleware via shared API key, or Admin JWT for manual/testing ingest.
    """
    expected = (os.getenv("BIOMETRIC_INGEST_API_KEY") or "").strip()
    provided = (x_biometric_ingest_key or "").strip()
    if expected and provided and provided == expected:
        return {"auth": "api_key", "role": "ingest"}
    if current_user:
        role = str(current_user.get("role") or "").lower()
        if role in {"super_admin", "superadmin", "coach_admin", "branch_manager"}:
            return current_user
    if not expected:
        raise HTTPException(
            status_code=401,
            detail="Set BIOMETRIC_INGEST_API_KEY or authenticate as Admin to ingest punches.",
        )
    raise HTTPException(
        status_code=401,
        detail="Invalid ingest credentials. Provide X-Biometric-Ingest-Key or Admin Bearer token.",
    )

@router.get("/reports")
async def get_attendance_reports(
    student_id: Optional[str] = Query(None),
    coach_id: Optional[str] = Query(None),
    course_id: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="present | absent | late"),
    method: Optional[str] = Query(None, description="manual | biometric | ..."),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.COACH, UserRole.BRANCH_MANAGER]))
):
    """Get attendance reports with filtering"""
    return await AttendanceController.get_attendance_reports(
        student_id,
        coach_id,
        course_id,
        branch_id,
        start_date,
        end_date,
        status,
        method,
        current_user,
    )

@router.get("/students")
async def get_student_attendance(
    branch_id: Optional[str] = Query(None),
    course_id: Optional[str] = Query(None),
    date: Optional[str] = Query(None, description="Single date in ISO format (YYYY-MM-DD)"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.COACH, UserRole.BRANCH_MANAGER]))
):
    """Get student attendance data"""
    return await AttendanceController.get_student_attendance(
        branch_id, course_id, date, start_date, end_date, current_user
    )

@router.get("/coaches")
async def get_coach_attendance(
    branch_id: Optional[str] = Query(None),
    date: Optional[str] = Query(None, description="Single date in ISO format (YYYY-MM-DD)"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]))
):
    """Get coach attendance data"""
    return await AttendanceController.get_coach_attendance(
        branch_id, date, start_date, end_date, current_user
    )

@router.get("/stats")
async def get_attendance_stats(
    branch_id: Optional[str] = Query(None),
    date: Optional[str] = Query(None, description="Single date in ISO format (YYYY-MM-DD)"),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]))
):
    """Get attendance statistics with optional date filtering"""
    return await AttendanceController.get_attendance_stats(branch_id, date, current_user)

@router.post("/manual")
async def mark_manual_attendance(
    attendance_data: AttendanceCreate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.COACH]))
):
    """Manually mark attendance"""
    return await AttendanceController.mark_manual_attendance(attendance_data, current_user)

@router.post("/biometric")
async def biometric_attendance(
    attendance_data: BiometricAttendance,
    _auth: dict = Depends(require_biometric_ingest_auth),
):
    """
    Legacy single-punch biometric endpoint (now authenticated).
    Prefer POST /api/attendance/ingest/biometric for batch ingest.
    """
    return await AttendanceController.biometric_attendance(attendance_data)


@router.post("/ingest/biometric")
async def ingest_biometric_events(
    body: BiometricIngestRequest,
    _auth: dict = Depends(require_biometric_ingest_auth),
):
    """M09-S03: ingest normalized biometric punch events (deduped, device→branch)."""
    return await ingest_events(body)


@router.get("/ingest/unmatched")
async def get_unmatched_biometric_punches(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]
        )
    ),
):
    """M09-S03: list unmatched biometric punches for Admin review."""
    return await list_unmatched(skip=skip, limit=limit)


@router.get("/ingest/stats")
async def get_biometric_ingest_stats(
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]
        )
    ),
):
    """M09-S03: ingest summary stats."""
    return await ingest_stats()


@router.get("/export")
async def export_attendance_reports(
    student_id: Optional[str] = Query(None),
    coach_id: Optional[str] = Query(None),
    course_id: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="present | absent | late"),
    method: Optional[str] = Query(None, description="manual | biometric | ..."),
    format: str = Query("csv", regex="^(csv|excel)$"),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]))
):
    """Export attendance reports"""
    return await AttendanceController.export_attendance_reports(
        student_id,
        coach_id,
        course_id,
        branch_id,
        start_date,
        end_date,
        format,
        status,
        method,
        current_user,
    )

@router.get("/coach/{coach_id}/students")
async def get_coach_students(
    coach_id: str,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.COACH, UserRole.BRANCH_MANAGER]))
):
    """Get students assigned to a specific coach"""
    return await AttendanceController.get_coach_students(coach_id, current_user)

@router.get("/coach/{coach_id}/students/attendance")
async def get_coach_students_attendance(
    coach_id: str,
    date: str = Query(..., description="Date in ISO format (YYYY-MM-DD)"),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.COACH, UserRole.BRANCH_MANAGER]))
):
    """Get students assigned to a specific coach with their attendance for a specific date"""
    return await AttendanceController.get_coach_students_attendance(coach_id, date, current_user)

@router.post("/coach/mark")
async def mark_coach_attendance(
    attendance_data: CoachAttendanceCreate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.BRANCH_MANAGER]))
):
    """Mark attendance for a coach"""
    return await AttendanceController.mark_coach_attendance(attendance_data, current_user)

@router.post("/branch-manager/mark")
async def mark_branch_manager_attendance(
    attendance_data: BranchManagerAttendanceCreate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN]))
):
    """Mark attendance for a branch manager"""
    return await AttendanceController.mark_branch_manager_attendance(attendance_data, current_user)

@router.get("/branch-manager/{branch_manager_id}/students/attendance")
async def get_branch_manager_students_attendance(
    branch_manager_id: str,
    date: str = Query(..., description="Date in ISO format (YYYY-MM-DD)"),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.BRANCH_MANAGER]))
):
    """Get students in branch manager's branches with their attendance for a specific date"""
    return await AttendanceController.get_branch_manager_students_attendance(branch_manager_id, date, current_user)

@router.post("/mark")
async def mark_comprehensive_attendance(
    attendance_request: AttendanceMarkRequest,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.COACH, UserRole.BRANCH_MANAGER]))
):
    """Mark attendance for any user type (student, coach, branch_manager)"""
    return await AttendanceController.mark_comprehensive_attendance(attendance_request, current_user)


@router.post("/corrections")
async def create_attendance_correction(
    body: AttendanceCorrectionRequest,
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]
        )
    ),
):
    """
    M09-S05: correct an existing attendance record.
    Requires a non-empty reason; writes full before/after audit.
    First-time marking should continue to use POST /mark.
    """
    return await correct_attendance(body, current_user)


@router.get("/corrections")
async def get_attendance_corrections(
    attendance_id: Optional[str] = Query(None),
    student_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]
        )
    ),
):
    """M09-S05: list correction / adjustment audit history."""
    return await list_correction_history(
        attendance_id=attendance_id,
        student_id=student_id,
        skip=skip,
        limit=limit,
        current_user=current_user,
    )


@router.get("/student/my-attendance")
async def get_my_attendance(
    start_date: Optional[str] = Query(None, description="Start date in ISO format (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date in ISO format (YYYY-MM-DD)"),
    current_user: dict = Depends(require_role_unified([UserRole.STUDENT]))
):
    """Get attendance data for the authenticated student"""
    return await AttendanceController.get_student_my_attendance(current_user, start_date, end_date)
