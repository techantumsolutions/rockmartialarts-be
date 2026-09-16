from typing import Optional

from fastapi import APIRouter, Depends, status

from controllers.discount_rule_controller import DiscountRuleController
from models.discount_rule_models import DiscountRuleCreate, DiscountRuleUpdate
from models.user_models import UserRole
from utils.unified_auth import require_role_unified

router = APIRouter()


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_discount_rule(
    body: DiscountRuleCreate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    return await DiscountRuleController.create(body, current_user=current_user)


@router.get("")
async def list_discount_rules(
    active_only: bool = False,
    skip: int = 0,
    limit: int = 100,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    return await DiscountRuleController.list_rules(active_only=active_only, skip=skip, limit=limit)


@router.get("/{rule_id}")
async def get_discount_rule(
    rule_id: str,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    return await DiscountRuleController.get(rule_id)


@router.patch("/{rule_id}")
async def update_discount_rule(
    rule_id: str,
    body: DiscountRuleUpdate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    return await DiscountRuleController.update(rule_id, body, current_user=current_user)


@router.delete("/{rule_id}")
async def delete_discount_rule(
    rule_id: str,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    return await DiscountRuleController.delete(rule_id)
