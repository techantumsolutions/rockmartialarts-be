"""M10-S03 KPI rating calculation routes."""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from models.kpi_rating_models import PreviewRatingRequest, RecalculateRatingRequest
from models.user_models import UserRole
from utils.kpi_rating_service import (
    get_rating,
    list_ratings,
    preview_rating,
    recalculate_ratings,
)
from utils.unified_auth import require_role_unified

router = APIRouter()

CALC_ROLES = [
    UserRole.SUPER_ADMIN,
    UserRole.COACH_ADMIN,
    UserRole.BRANCH_MANAGER,
    UserRole.COACH,
]
READ_ROLES = CALC_ROLES


@router.get("/formula")
async def api_formula_meta(
    current_user: dict = Depends(require_role_unified(READ_ROLES)),
):
    """Return approved formula version and band thresholds (T01 reference)."""
    return {
        "formula_version": "m10-s03-v1",
        "description": (
            "rating = Σ(normalized_score × weight) / Σ(weight); "
            "normalized = (raw-min)/(max-min)×100"
        ),
        "doc": "docs/M10_S03_RATING_FORMULA.md",
        "bands": {
            "outstanding": ">= 90",
            "excellent": ">= 75 and < 90",
            "good": ">= 60 and < 75",
            "fair": ">= 40 and < 60",
            "needs_improvement": "< 40",
        },
    }


@router.get("")
async def api_list_ratings(
    period_id: Optional[str] = Query(None),
    student_id: Optional[str] = Query(None),
    course_id: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    current_user: dict = Depends(require_role_unified(READ_ROLES)),
):
    return await list_ratings(
        period_id=period_id,
        student_id=student_id,
        course_id=course_id,
        branch_id=branch_id,
        skip=skip,
        limit=limit,
        current_user=current_user,
    )


@router.get("/{rating_id}")
async def api_get_rating(
    rating_id: str,
    current_user: dict = Depends(require_role_unified(READ_ROLES)),
):
    return await get_rating(rating_id, current_user=current_user)


@router.post("/preview")
async def api_preview_rating(
    body: PreviewRatingRequest,
    current_user: dict = Depends(require_role_unified(CALC_ROLES)),
):
    return await preview_rating(body, current_user=current_user)


@router.post("/recalculate")
async def api_recalculate_ratings(
    body: RecalculateRatingRequest,
    current_user: dict = Depends(require_role_unified(CALC_ROLES)),
):
    return await recalculate_ratings(body, current_user=current_user)
