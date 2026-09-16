"""Pure cart promotion math (no DB/FastAPI imports)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Set


def _round_money(value: float) -> float:
    return round(float(value or 0), 2)


def _item_subtotal(item: dict) -> float:
    return float((item.get("pricing") or {}).get("total_amount") or 0)


def _filter_eligible_items(items: List[dict], rule: dict) -> List[dict]:
    branch_ids: Set[str] = {b for b in (rule.get("branch_ids") or []) if b}
    course_ids: Set[str] = {c for c in (rule.get("course_ids") or []) if c}
    out: List[dict] = []
    for item in items or []:
        if branch_ids and item.get("branch_id") not in branch_ids:
            continue
        if course_ids and item.get("course_id") not in course_ids:
            continue
        out.append(item)
    return out


def _rule_is_active_now(rule: dict, now: Optional[datetime] = None) -> bool:
    if not rule.get("is_active", True):
        return False
    now = now or datetime.utcnow()
    vf = rule.get("valid_from")
    vu = rule.get("valid_until")
    if vf and now < vf:
        return False
    if vu and now > vu:
        return False
    return True


def _distinct_courses_for_student(items: List[dict], student_line_id: str) -> int:
    keys = {
        (i.get("course_id"), i.get("branch_id"))
        for i in items
        if i.get("student_line_id") == student_line_id and i.get("course_id")
    }
    return len(keys)


def _trigger_matches(rule: dict, *, students: List[dict], items: List[dict], subtotal: float) -> bool:
    trigger = rule.get("trigger") or ""
    student_count = len(students or [])
    item_count = len(items or [])

    if trigger == "multi_student":
        return student_count >= int(rule.get("min_students") or 2)
    if trigger == "multi_course_cart":
        return item_count >= int(rule.get("min_cart_items") or 2)
    if trigger == "multi_course_student":
        need = int(rule.get("min_courses_per_student") or 2)
        for s in students or []:
            sid = s.get("student_line_id")
            if sid and _distinct_courses_for_student(items, sid) >= need:
                return True
        return False
    if trigger == "combination":
        need_students = int(rule.get("min_students") or 2)
        need_items = int(rule.get("min_cart_items") or 2)
        need_per = int(rule.get("min_courses_per_student") or 2)
        if student_count < need_students or item_count < need_items:
            return False
        for s in students or []:
            sid = s.get("student_line_id")
            if sid and _distinct_courses_for_student(items, sid) >= need_per:
                return True
        return False
    if trigger == "cart_min_amount":
        return subtotal >= float(rule.get("min_cart_amount") or 0)
    return False


def _compute_rule_discount(rule: dict, eligible_subtotal: float) -> float:
    if eligible_subtotal <= 0:
        return 0.0
    kind = rule.get("discount_kind") or "percentage"
    value = float(rule.get("discount_value") or 0)
    if kind == "percentage":
        amount = eligible_subtotal * (value / 100.0)
        cap = rule.get("max_discount_amount")
        if cap is not None:
            amount = min(amount, float(cap))
    else:
        amount = min(value, eligible_subtotal)
    return _round_money(max(0.0, amount))


@dataclass
class CartPromotionResult:
    subtotal_amount: float = 0.0
    promo_discount_total: float = 0.0
    total_amount: float = 0.0
    discount_lines: List[dict] = field(default_factory=list)
    audit_rules: List[dict] = field(default_factory=list)

    def to_snapshot(self) -> dict:
        return {
            "computed_at": datetime.utcnow().isoformat() + "Z",
            "subtotal_amount": self.subtotal_amount,
            "promo_discount_total": self.promo_discount_total,
            "total_amount": self.total_amount,
            "rules_applied": self.audit_rules,
            "breakdown": self.discount_lines,
        }


def compute_cart_promotions(
    cart: dict,
    *,
    rules: Optional[List[dict]] = None,
    now: Optional[datetime] = None,
) -> CartPromotionResult:
    items = list(cart.get("items") or [])
    students = list(cart.get("students") or [])
    subtotal = _round_money(sum(_item_subtotal(i) for i in items))
    result = CartPromotionResult(subtotal_amount=subtotal, total_amount=subtotal)

    if not items or subtotal <= 0:
        return result

    active_rules = [r for r in (rules or []) if _rule_is_active_now(r, now)]
    active_rules.sort(key=lambda r: (int(r.get("priority") or 100), r.get("code") or ""))

    candidates: List[tuple] = []
    for rule in active_rules:
        if not _trigger_matches(rule, students=students, items=items, subtotal=subtotal):
            continue
        scope = rule.get("apply_scope") or "cart"
        if scope == "per_student_line":
            trigger = rule.get("trigger") or ""
            if trigger in ("multi_student", "multi_course_cart", "cart_min_amount", "combination"):
                continue
            for s in students:
                sid = s.get("student_line_id")
                if not sid:
                    continue
                line_items = [i for i in items if i.get("student_line_id") == sid]
                eligible_items = _filter_eligible_items(line_items, rule)
                eligible_sub = _round_money(sum(_item_subtotal(i) for i in eligible_items))
                if eligible_sub <= 0:
                    continue
                if not _trigger_matches(
                    rule,
                    students=[s],
                    items=line_items,
                    subtotal=eligible_sub,
                ):
                    continue
                amt = _compute_rule_discount(rule, eligible_sub)
                if amt > 0:
                    candidates.append((rule, amt, sid, eligible_sub))
        else:
            eligible_items = _filter_eligible_items(items, rule)
            eligible_sub = _round_money(sum(_item_subtotal(i) for i in eligible_items))
            if eligible_sub <= 0:
                continue
            amt = _compute_rule_discount(rule, eligible_sub)
            if amt > 0:
                candidates.append((rule, amt, None, eligible_sub))

    if not candidates:
        return result

    stackable_any = any(r.get("stackable") for r, *_ in candidates)
    selected: List[tuple] = []
    if stackable_any:
        remaining = subtotal
        for rule, amt, sid, eligible_sub in candidates:
            if not rule.get("stackable"):
                continue
            applied = min(amt, remaining)
            if applied <= 0:
                continue
            selected.append((rule, applied, sid, eligible_sub))
            remaining = _round_money(remaining - applied)
            if remaining <= 0:
                break
        if not selected:
            rule, amt, sid, eligible_sub = max(candidates, key=lambda x: x[1])
            selected = [(rule, min(amt, subtotal), sid, eligible_sub)]
    else:
        rule, amt, sid, eligible_sub = max(candidates, key=lambda x: x[1])
        selected = [(rule, min(amt, subtotal), sid, eligible_sub)]

    total_discount = 0.0
    lines: List[dict] = []
    audit: List[dict] = []
    for rule, applied, sid, eligible_sub in selected:
        total_discount += applied
        line = {
            "rule_id": rule.get("id"),
            "rule_code": rule.get("code"),
            "rule_name": rule.get("name"),
            "trigger": rule.get("trigger"),
            "discount_kind": rule.get("discount_kind"),
            "discount_value": rule.get("discount_value"),
            "amount": _round_money(applied),
            "apply_scope": rule.get("apply_scope") or "cart",
            "student_line_id": sid,
            "eligible_subtotal": eligible_sub,
        }
        lines.append(line)
        audit.append(
            {
                **line,
                "rule_description": rule.get("description"),
                "stackable": bool(rule.get("stackable")),
                "priority": rule.get("priority"),
                "valid_from": rule.get("valid_from"),
                "valid_until": rule.get("valid_until"),
            }
        )

    total_discount = _round_money(min(total_discount, subtotal))
    result.promo_discount_total = total_discount
    result.total_amount = _round_money(subtotal - total_discount)
    result.discount_lines = lines
    result.audit_rules = audit
    return result
