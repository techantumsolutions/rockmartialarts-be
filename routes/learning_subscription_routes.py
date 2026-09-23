"""M16-S03 Learning subscription routes — plans, checkout, grants, admin."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from models.learning_subscription_models import (
    LearningSubscribeCheckoutBody,
    LearningSubscribeGrantBody,
    LearningSubscribePaymentVerify,
    LearningSubscriptionPlanCreate,
    LearningSubscriptionPlanUpdate,
)
from models.user_models import UserRole
from utils.unified_auth import require_role_unified
from utils.learning_subscription_service import (
    checkout,
    create_plan,
    ensure_default_plans_for_course,
    get_my_subscription,
    grant_subscription,
    list_payment_history,
    list_plans,
    list_plans_public,
    list_subscriptions_admin,
    refresh_all_statuses,
    update_plan,
    verify_payment,
)

router = APIRouter()

_ADMIN = [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN]
_STUDENT = [UserRole.STUDENT]
_ADMIN_STUDENT = [
    UserRole.SUPER_ADMIN,
    UserRole.COACH_ADMIN,
    UserRole.STUDENT,
]


# ----- Public -----


@router.get("/plans/public")
async def api_public_plans(course_id: str = Query(..., min_length=1)):
    """Active plans for a published learning course (no auth)."""
    return await list_plans_public(course_id)


# ----- Admin plans -----


@router.get("/plans")
async def api_list_plans(
    course_id: Optional[str] = Query(None),
    active_only: bool = Query(False),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await list_plans(
        course_id=course_id,
        active_only=active_only,
        current_user=current_user,
    )


@router.post("/plans", status_code=201)
async def api_create_plan(
    body: LearningSubscriptionPlanCreate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await create_plan(body, current_user=current_user)


@router.post("/plans/seed-defaults/{course_id}", status_code=201)
async def api_seed_defaults(
    course_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """Create standard 3-month + Lifetime plans when course has none."""
    return await ensure_default_plans_for_course(
        course_id, current_user=current_user
    )


@router.patch("/plans/{plan_id}")
async def api_update_plan(
    plan_id: str,
    body: LearningSubscriptionPlanUpdate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await update_plan(plan_id, body, current_user=current_user)


# ----- Student me -----


@router.get("/me")
async def api_my_subscription(
    course_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_role_unified(_ADMIN_STUDENT)),
):
    return await get_my_subscription(
        user_id=current_user["id"],
        course_id=course_id,
        current_user=current_user,
    )


@router.get("/me/history")
async def api_my_history(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(require_role_unified(_STUDENT)),
):
    return await list_payment_history(
        user_id=current_user["id"],
        current_user=current_user,
        skip=skip,
        limit=limit,
    )


@router.post("/me/checkout")
async def api_my_checkout(
    body: LearningSubscribeCheckoutBody,
    current_user: dict = Depends(require_role_unified(_STUDENT)),
):
    return await checkout(
        user_id=current_user["id"], body=body, current_user=current_user
    )


@router.post("/me/verify-payment")
async def api_my_verify(
    body: LearningSubscribePaymentVerify,
    current_user: dict = Depends(require_role_unified(_STUDENT)),
):
    return await verify_payment(
        user_id=current_user["id"], body=body, current_user=current_user
    )


# ----- Admin subscriptions -----


@router.get("")
async def api_list_subscriptions(
    course_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await list_subscriptions_admin(
        current_user=current_user,
        course_id=course_id,
        status=status,
        search=search,
        skip=skip,
        limit=limit,
    )


@router.post("/grant")
async def api_grant(
    body: LearningSubscribeGrantBody,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await grant_subscription(body, current_user=current_user)


@router.post("/refresh-statuses")
async def api_refresh(
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await refresh_all_statuses(current_user=current_user)
