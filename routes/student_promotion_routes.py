"""M19 Student Promotion CMS + targeting + student popup routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from models.student_promotion_models import (
    PromotionEligibilityCheckRequest,
    PromotionEligibilityPreviewRequest,
    PromotionEventCreate,
    StudentPromotionCreate,
    StudentPromotionUpdate,
)
from models.user_models import UserRole
from utils.promotion_eligibility import (
    get_targeting_options,
    is_student_eligible_for_target,
    preview_eligibility,
)
from utils.student_promotion_service import (
    archive_student_promotion,
    create_student_promotion,
    get_student_promotion,
    list_student_promotions,
    update_student_promotion,
)
from utils.student_promotion_student_service import (
    list_eligible_promotions_for_student,
    record_promotion_event,
)
from utils.unified_auth import require_role_unified

router = APIRouter()

_ADMIN = [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN]
_STUDENT = [UserRole.STUDENT]


@router.get("")
async def api_list_promotions(
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    include_archived: bool = Query(False),
    live_only: bool = Query(False),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await list_student_promotions(
        status=status,
        search=search,
        skip=skip,
        limit=limit,
        include_archived=include_archived,
        live_only=live_only,
    )


@router.post("", status_code=201)
async def api_create_promotion(
    body: StudentPromotionCreate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await create_student_promotion(body, current_user=current_user)


@router.get("/targeting/options")
async def api_targeting_options(
    student_search: Optional[str] = Query(None),
    student_limit: int = Query(40, ge=1, le=100),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """Branch / course / group / student options for the target selector."""
    return await get_targeting_options(
        student_search=student_search,
        student_limit=student_limit,
    )


@router.post("/eligibility/preview")
async def api_eligibility_preview(
    body: PromotionEligibilityPreviewRequest,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """
    Preview eligible audience size.
    Provide either `target` (ad-hoc) or `promotion_id` (saved target).
    """
    target = body.target
    if body.promotion_id and target is None:
        data = await get_student_promotion(body.promotion_id)
        target = (data.get("promotion") or {}).get("target")
    return await preview_eligibility(
        target.model_dump(mode="json") if hasattr(target, "model_dump") else target,
        sample_limit=body.sample_limit,
    )


@router.post("/eligibility/check")
async def api_eligibility_check(
    body: PromotionEligibilityCheckRequest,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """Check whether a single student matches a promotion / target."""
    target = body.target
    if body.promotion_id and target is None:
        data = await get_student_promotion(body.promotion_id)
        target = (data.get("promotion") or {}).get("target")
    elif body.promotion_id and target is not None:
        pass
    elif target is None:
        return {
            "student_id": body.student_id,
            "eligible": False,
            "detail": "target or promotion_id required",
        }
    eligible = await is_student_eligible_for_target(
        body.student_id,
        target.model_dump(mode="json") if hasattr(target, "model_dump") else target,
    )
    return {
        "student_id": body.student_id,
        "eligible": eligible,
        "target": (
            target.model_dump(mode="json")
            if hasattr(target, "model_dump")
            else target
        ),
    }


# --- M19-S03 student dashboard surfaces (must be before /{promotion_id}) ---


@router.get("/me/eligible")
async def api_my_eligible_promotions(
    exclude_dismissed: bool = Query(True),
    limit: int = Query(5, ge=1, le=20),
    current_user: dict = Depends(require_role_unified(_STUDENT)),
):
    """Live promotions the authenticated student is eligible to see."""
    return await list_eligible_promotions_for_student(
        current_user["id"],
        exclude_dismissed=exclude_dismissed,
        limit=limit,
    )


@router.post("/me/events", status_code=201)
async def api_record_my_promotion_event(
    body: PromotionEventCreate,
    current_user: dict = Depends(require_role_unified(_STUDENT)),
):
    """Record view / CTA / dismiss for the authenticated student."""
    return await record_promotion_event(
        student_id=current_user["id"],
        promotion_id=body.promotion_id,
        event_type=body.event_type.value,
        meta=body.meta,
    )


@router.get("/{promotion_id}/eligibility")
async def api_promotion_eligibility(
    promotion_id: str,
    sample_limit: int = Query(20, ge=0, le=50),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    data = await get_student_promotion(promotion_id)
    promo = data.get("promotion") or {}
    preview = await preview_eligibility(
        promo.get("target"),
        sample_limit=sample_limit,
    )
    preview["promotion_id"] = promo.get("id")
    return preview


@router.get("/{promotion_id}")
async def api_get_promotion(
    promotion_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await get_student_promotion(promotion_id)


@router.patch("/{promotion_id}")
async def api_update_promotion(
    promotion_id: str,
    body: StudentPromotionUpdate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await update_student_promotion(
        promotion_id, body, current_user=current_user
    )


@router.delete("/{promotion_id}")
async def api_archive_promotion(
    promotion_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await archive_student_promotion(promotion_id, current_user=current_user)
