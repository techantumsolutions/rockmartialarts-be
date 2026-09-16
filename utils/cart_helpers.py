"""Cart totals, serialization, and index setup (M05-S01)."""
import logging
from typing import Any, Dict, List, Optional

from models.cart_models import CartTotals, CartValidationIssue


def recompute_cart_totals(items: List[dict], *, student_count: int = 0) -> dict:
    currency = "INR"
    course_sub = 0.0
    admission_sub = 0.0
    total = 0.0
    for row in items or []:
        pricing = row.get("pricing") or {}
        currency = pricing.get("currency") or currency
        course_sub += float(pricing.get("course_fee") or 0)
        admission_sub += float(pricing.get("admission_fee") or 0)
        total += float(pricing.get("total_amount") or 0)
    if student_count <= 0:
        student_count = len({i.get("student_line_id") for i in (items or []) if i.get("student_line_id")})
    subtotal = round(total, 2)
    totals = CartTotals(
        currency=currency,
        course_fee_subtotal=round(course_sub, 2),
        admission_fee_subtotal=round(admission_sub, 2),
        subtotal_amount=subtotal,
        promo_discount_total=0.0,
        total_amount=subtotal,
        item_count=len(items or []),
        student_count=student_count,
    )
    return totals.dict()


async def build_cart_public_payload(
    cart: dict,
    validation: Optional[List[CartValidationIssue]] = None,
    bulk_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    items = cart.get("items") or []
    students = cart.get("students") or []
    grouped: Dict[str, List[dict]] = {}
    for s in students:
        sid = s.get("student_line_id")
        if sid:
            grouped[sid] = []
    for item in items:
        sid = item.get("student_line_id")
        if sid not in grouped:
            grouped[sid] = []
        grouped[sid].append(item)
    student_groups = []
    for s in students:
        sid = s.get("student_line_id")
        student_groups.append(
            {
                **s,
                "items": grouped.get(sid, []),
                "line_total": round(
                    sum(float((i.get("pricing") or {}).get("total_amount") or 0) for i in grouped.get(sid, [])),
                    2,
                ),
            }
        )
    issues = [i.dict() for i in (validation or [])]
    blocking = [i for i in issues if i.get("code") not in ("fee_drift",)]
    base_totals = recompute_cart_totals(items, student_count=len(students))
    from utils.discount_engine import compute_cart_promotions_for_cart

    promo = await compute_cart_promotions_for_cart(cart)
    totals = {
        **base_totals,
        "subtotal_amount": promo.subtotal_amount,
        "promo_discount_total": promo.promo_discount_total,
        "total_amount": promo.total_amount,
    }
    payload: Dict[str, Any] = {
        "cart": {
            "id": cart.get("id"),
            "owner_user_id": cart.get("owner_user_id"),
            "guest_token": cart.get("guest_token"),
            "status": cart.get("status"),
            "students": students,
            "items": items,
            "student_groups": student_groups,
            "totals": totals,
            "discount_breakdown": promo.discount_lines,
            "discount_snapshot": cart.get("discount_snapshot"),
            "validation_issues": issues,
            "is_valid": len(blocking) == 0,
            "updated_at": cart.get("updated_at"),
        }
    }
    if bulk_summary is not None:
        payload["bulk_summary"] = bulk_summary
    return payload


async def ensure_cart_indexes(mongo_db) -> None:
    try:
        await mongo_db.carts.create_index(
            [("owner_user_id", 1), ("status", 1)],
            name="carts_owner_status",
            partialFilterExpression={"owner_user_id": {"$type": "string"}, "status": "active"},
        )
    except Exception:
        logging.exception("Failed to create carts owner index")
    try:
        await mongo_db.carts.create_index(
            [("guest_token", 1), ("status", 1)],
            name="carts_guest_status",
            partialFilterExpression={"guest_token": {"$type": "string"}, "status": "active"},
        )
    except Exception:
        logging.exception("Failed to create carts guest index")
