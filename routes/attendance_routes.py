from fastapi import APIRouter, Depends, Query
from typing import Optional
from datetime import datetime
from controllers.attendance_controller import AttendanceController
from models.attendance_models import (
    AttendanceCreate, BiometricAttendance, CoachAttendanceCreate, BranchManagerAttendanceCreate,
    AttendanceMarkRequest
)
from models.user_models import UserRole
from utils.unified_auth import require_role_unified, get_current_user_or_superadmin

router = APIRouter()

@router.get("/reports")
async def get_attendance_reports(
    student_id: Optional[str] = Query(None),
    coach_id: Optional[str] = Query(None),
    course_id: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: Optional[int] = Query(None, ge=1, le=1000),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.COACH, UserRole.BRANCH_MANAGER]))
):
    """Get attendance reports with filtering"""
    return await AttendanceController.get_attendance_reports(
        student_id, coach_id, course_id, branch_id, start_date, end_date, current_user, limit
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
    attendance_data: BiometricAttendance
):
    """Record attendance from biometric device"""
    return await AttendanceController.biometric_attendance(attendance_data)

@router.get("/export")
async def export_attendance_reports(
    student_id: Optional[str] = Query(None),
    coach_id: Optional[str] = Query(None),
    course_id: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    format: str = Query("csv", regex="^(csv|excel)$"),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]))
):
    """Export attendance reports"""
    return await AttendanceController.export_attendance_reports(
        student_id, coach_id, course_id, branch_id, start_date, end_date, format, current_user
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

@router.get("/student/my-attendance")
async def get_my_attendance(
    start_date: Optional[str] = Query(None, description="Start date in ISO format (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date in ISO format (YYYY-MM-DD)"),
    current_user: dict = Depends(require_role_unified([UserRole.STUDENT]))
):
    """Get attendance data for the authenticated student"""
    return await AttendanceController.get_student_my_attendance(current_user, start_date, end_date)
