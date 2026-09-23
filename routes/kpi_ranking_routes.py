"""M10-S04 KPI ranking routes."""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from models.kpi_ranking_models import RecalculateRankingRequest
from models.user_models import UserRole
from utils.kpi_ranking_service import get_ranking, list_rankings, recalculate_rankings
from utils.unified_auth import require_role_unified

router = APIRouter()

CALC_ROLES = [
    UserRole.SUPER_ADMIN,
    UserRole.COACH_ADMIN,
    UserRole.BRANCH_MANAGER,
    UserRole.COACH,
]
READ_ROLES = CALC_ROLES


@router.get("/policy")
async def api_ranking_policy(
    current_user: dict = Depends(require_role_unified(READ_ROLES)),
):
    return {
        "formula_version": "m10-s04-v1",
        "doc": "docs/M10_S04_RANKING_POLICY.md",
        "tie_policy": "competition",
        "tie_example": "[95,90,90,80] -> ranks [1,2,2,4]",
        "scope_types": ["overall", "branch", "course", "category"],
        "eligibility": {
            "require_complete_rating": True,
            "exclude_inactive_students": True,
            "multi_course_in_population": "highest_rating",
        },
    }


@router.get("")
async def api_list_rankings(
    period_id: Optional[str] = Query(None),
    scope_type: Optional[str] = Query(None),
    scope_id: Optional[str] = Query(None),
    student_id: Optional[str] = Query(None),
    course_id: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    category_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    current_user: dict = Depends(require_role_unified(READ_ROLES)),
):
    return await list_rankings(
        period_id=period_id,
        scope_type=scope_type,
        scope_id=scope_id,
        student_id=student_id,
        course_id=course_id,
        branch_id=branch_id,
        category_id=category_id,
        skip=skip,
        limit=limit,
        current_user=current_user,
    )


@router.get("/{ranking_id}")
async def api_get_ranking(
    ranking_id: str,
    current_user: dict = Depends(require_role_unified(READ_ROLES)),
):
    return await get_ranking(ranking_id, current_user=current_user)


@router.post("/recalculate")
async def api_recalculate_rankings(
    body: RecalculateRankingRequest,
    current_user: dict = Depends(require_role_unified(CALC_ROLES)),
):
    return await recalculate_rankings(body, current_user=current_user)
