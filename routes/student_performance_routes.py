from fastapi import APIRouter, Depends, Query
from typing import Optional

from controllers.student_performance_controller import StudentPerformanceController
from models.student_performance_models import (
    CoachFeedbackUpsert,
    GoalsUpsert,
    MedalStatsUpsert,
    ProfilePerformanceUpsert,
    SkillMetricsUpsert,
    WarriorStatsUpsert,
)
from models.user_models import UserRole
from utils.kpi_performance_metrics_service import get_performance_metrics
from utils.course_syllabus_student_service import (
    assert_student_can_stream_syllabus,
    list_accessible_student_profiles,
    list_student_syllabi,
)
from utils.course_syllabus_service import stream_syllabus_file
from utils.unified_auth import require_role_unified

router = APIRouter()

DASHBOARD_ROLES = [
    UserRole.STUDENT,
    UserRole.SUPER_ADMIN,
    UserRole.COACH_ADMIN,
    UserRole.COACH,
    UserRole.BRANCH_MANAGER,
]


@router.get("/dashboard/{student_id}")
async def get_student_performance_dashboard(
    student_id: str,
    current_user: dict = Depends(require_role_unified(DASHBOARD_ROLES)),
):
    return await StudentPerformanceController.get_dashboard(student_id, current_user)


@router.get("/performance-metrics/{student_id}")
async def get_student_kpi_performance_metrics(
    student_id: str,
    period_id: Optional[str] = Query(None),
    course_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_role_unified(DASHBOARD_ROLES)),
):
    """
    M10-S05 additive KPI / rating / ranking summary for the performance dashboard.
    Does not alter existing /dashboard/{id} payload.
    """
    return await get_performance_metrics(
        student_id,
        current_user=current_user,
        period_id=period_id,
        course_id=course_id,
    )


@router.get("/syllabus-profiles")
async def get_syllabus_student_profiles(
    current_user: dict = Depends(require_role_unified(DASHBOARD_ROLES)),
):
    """M11-S02-T04 — profiles available for student syllabus switcher."""
    return await list_accessible_student_profiles(current_user=current_user)


@router.get("/syllabi/{student_id}")
async def get_student_course_syllabi(
    student_id: str,
    current_user: dict = Depends(require_role_unified(DASHBOARD_ROLES)),
):
    """
    M11-S02 — list active syllabi for actively enrolled courses of the selected student.
    """
    return await list_student_syllabi(student_id, current_user=current_user)


@router.get("/syllabi/{student_id}/file/{syllabus_id}")
async def get_student_syllabus_file(
    student_id: str,
    syllabus_id: str,
    current_user: dict = Depends(require_role_unified(DASHBOARD_ROLES)),
):
    """
    Authenticated PDF stream for an enrolled student (enrollment-gated).
    Prep for M11-S03 viewer; no public URL.
    """
    await assert_student_can_stream_syllabus(
        syllabus_id, current_user=current_user, student_id=student_id
    )
    return await stream_syllabus_file(syllabus_id)


@router.put("/achievements/{student_id}")
async def put_performance_achievements(
    student_id: str,
    body: MedalStatsUpsert,
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.COACH, UserRole.BRANCH_MANAGER]
        )
    ),
):
    return await StudentPerformanceController.put_medals(student_id, body, current_user)


@router.put("/skills/{student_id}")
async def put_performance_skills(
    student_id: str,
    body: SkillMetricsUpsert,
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.COACH, UserRole.BRANCH_MANAGER]
        )
    ),
):
    return await StudentPerformanceController.put_skills(student_id, body, current_user)


@router.put("/goals/{student_id}")
async def put_performance_goals(
    student_id: str,
    body: GoalsUpsert,
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.COACH, UserRole.BRANCH_MANAGER]
        )
    ),
):
    return await StudentPerformanceController.put_goals(student_id, body, current_user)


@router.put("/feedback/{student_id}")
async def put_performance_feedback(
    student_id: str,
    body: CoachFeedbackUpsert,
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.COACH, UserRole.BRANCH_MANAGER]
        )
    ),
):
    return await StudentPerformanceController.put_feedback(student_id, body, current_user)


@router.put("/profile/{student_id}")
async def put_performance_profile(
    student_id: str,
    body: ProfilePerformanceUpsert,
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.COACH, UserRole.BRANCH_MANAGER]
        )
    ),
):
    return await StudentPerformanceController.put_profile(student_id, body, current_user)


@router.put("/warrior-stats/{student_id}")
async def put_performance_warrior_stats(
    student_id: str,
    body: WarriorStatsUpsert,
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.COACH, UserRole.BRANCH_MANAGER]
        )
    ),
):
    return await StudentPerformanceController.put_warrior(student_id, body, current_user)
