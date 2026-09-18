"""M14-S04 Coach subscription routes — plans, checkout, history, admin."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from models.coach_subscription_models import (
    CoachSubscriptionCheckoutBody,
    CoachSubscriptionGrantBody,
    CoachSubscriptionPaymentVerify,
    CoachSubscriptionPlanCreate,
    CoachSubscriptionPlanUpdate,
)
from models.user_models import UserRole
from utils.unified_auth import require_role_unified
from utils.coach_subscription_service import (
    checkout,
    create_plan,
    get_current_subscription,
    grant_subscription,
    list_payment_history,
    list_plans,
    list_subscriptions_admin,
    refresh_all_statuses,
    update_plan,
    verify_payment,
)

router = APIRouter()

_ADMIN = [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN]
_ADMIN_BM = [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]
_COACH = [UserRole.COACH]
_ALL = [
    UserRole.SUPER_ADMIN,
    UserRole.COACH_ADMIN,
    UserRole.BRANCH_MANAGER,
    UserRole.COACH,
]


@router.get("/plans")
async def api_list_plans(
    active_only: bool = Query(False),
    current_user: dict = Depends(require_role_unified(_ALL)),
):
    """List subscription plans (coaches see active only)."""
    force_active = current_user.get("role") == UserRole.COACH.value or active_only
    return await list_plans(current_user=current_user, active_only=force_active)


@router.post("/plans")
async def api_create_plan(
    body: CoachSubscriptionPlanCreate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await create_plan(body, current_user=current_user)


@router.put("/plans/{plan_id}")
async def api_update_plan(
    plan_id: str,
    body: CoachSubscriptionPlanUpdate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await update_plan(plan_id, body, current_user=current_user)


@router.get("/me")
async def api_my_subscription(
    current_user: dict = Depends(require_role_unified(_COACH)),
):
    return await get_current_subscription(
        coach_id=current_user["id"], current_user=current_user
    )


@router.get("/me/history")
async def api_my_history(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(require_role_unified(_COACH)),
):
    return await list_payment_history(
        coach_id=current_user["id"],
        current_user=current_user,
        skip=skip,
        limit=limit,
    )


@router.post("/me/checkout")
async def api_my_checkout(
    body: CoachSubscriptionCheckoutBody,
    current_user: dict = Depends(require_role_unified(_COACH)),
):
    return await checkout(
        coach_id=current_user["id"], body=body, current_user=current_user
    )


@router.post("/me/verify-payment")
async def api_my_verify(
    body: CoachSubscriptionPaymentVerify,
    current_user: dict = Depends(require_role_unified(_COACH)),
):
    return await verify_payment(
        coach_id=current_user["id"], body=body, current_user=current_user
    )


@router.get("")
async def api_list_subscriptions(
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    current_user: dict = Depends(require_role_unified(_ADMIN_BM)),
):
    return await list_subscriptions_admin(
        current_user=current_user,
        status=status,
        search=search,
        skip=skip,
        limit=limit,
    )


@router.get("/coach/{coach_id}")
async def api_coach_subscription(
    coach_id: str,
    current_user: dict = Depends(require_role_unified(_ALL)),
):
    return await get_current_subscription(coach_id=coach_id, current_user=current_user)


@router.get("/coach/{coach_id}/history")
async def api_coach_history(
    coach_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(require_role_unified(_ADMIN_BM)),
):
    return await list_payment_history(
        coach_id=coach_id, current_user=current_user, skip=skip, limit=limit
    )


@router.post("/coach/{coach_id}/grant")
async def api_grant(
    coach_id: str,
    body: CoachSubscriptionGrantBody,
    current_user: dict = Depends(require_role_unified(_ADMIN_BM)),
):
    return await grant_subscription(
        coach_id=coach_id, body=body, current_user=current_user
    )


@router.post("/refresh-statuses")
async def api_refresh(
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await refresh_all_statuses(current_user=current_user)
