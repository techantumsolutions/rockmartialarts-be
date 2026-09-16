"""Cart promotion service — loads rules from DB, delegates math to discount_engine_core."""
from typing import List

from utils.database import get_db
from utils.discount_engine_core import CartPromotionResult, compute_cart_promotions

__all__ = ["CartPromotionResult", "compute_cart_promotions", "compute_cart_promotions_for_cart", "load_active_discount_rules"]


async def load_active_discount_rules() -> List[dict]:
    db = get_db()
    cursor = db.discount_rules.find({"is_active": True}).sort("priority", 1)
    return await cursor.to_list(length=200)


async def compute_cart_promotions_for_cart(cart: dict) -> CartPromotionResult:
    rules = await load_active_discount_rules()
    return compute_cart_promotions(cart, rules=rules)
