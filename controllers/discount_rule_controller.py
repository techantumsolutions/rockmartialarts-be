"""M05-S04 discount rule admin CRUD."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.discount_rule_models import DiscountRuleCreate, DiscountRuleDocument, DiscountRuleUpdate
from utils.database import get_db


class DiscountRuleController:
    @staticmethod
    def _now() -> datetime:
        return datetime.utcnow()

    @staticmethod
    def _normalize_code(code: str) -> str:
        return (code or "").strip().upper().replace(" ", "_")

    @staticmethod
    async def create(body: DiscountRuleCreate, *, current_user: dict) -> Dict[str, Any]:
        code = DiscountRuleController._normalize_code(body.code)
        if not code:
            raise HTTPException(status_code=400, detail="Rule code is required")
        db = get_db()
        if await db.discount_rules.find_one({"code": code}):
            raise HTTPException(status_code=400, detail="A rule with this code already exists")

        doc = DiscountRuleDocument(
            code=code,
            name=body.name.strip(),
            description=(body.description or "").strip() or None,
            discount_kind=body.discount_kind,
            discount_value=body.discount_value,
            trigger=body.trigger,
            apply_scope=body.apply_scope,
            min_students=body.min_students,
            min_cart_items=body.min_cart_items,
            min_courses_per_student=body.min_courses_per_student,
            min_cart_amount=body.min_cart_amount,
            max_discount_amount=body.max_discount_amount,
            stackable=body.stackable,
            priority=body.priority,
            valid_from=body.valid_from,
            valid_until=body.valid_until,
            is_active=body.is_active,
            branch_ids=[b for b in (body.branch_ids or []) if b],
            course_ids=[c for c in (body.course_ids or []) if c],
            created_by=current_user.get("id"),
        )
        payload = doc.dict()
        await db.discount_rules.insert_one(payload)
        return {"rule": payload}

    @staticmethod
    async def list_rules(
        *,
        active_only: bool = False,
        skip: int = 0,
        limit: int = 100,
    ) -> Dict[str, Any]:
        db = get_db()
        query: dict = {}
        if active_only:
            query["is_active"] = True
        total = await db.discount_rules.count_documents(query)
        cursor = (
            db.discount_rules.find(query).sort("priority", 1).skip(max(0, skip)).limit(min(limit, 200))
        )
        rules = await cursor.to_list(length=200)
        return {"rules": rules, "total": total}

    @staticmethod
    async def get(rule_id: str) -> Dict[str, Any]:
        db = get_db()
        rule = await db.discount_rules.find_one({"id": rule_id})
        if not rule:
            raise HTTPException(status_code=404, detail="Discount rule not found")
        return {"rule": rule}

    @staticmethod
    async def update(rule_id: str, body: DiscountRuleUpdate, *, current_user: dict) -> Dict[str, Any]:
        db = get_db()
        existing = await db.discount_rules.find_one({"id": rule_id})
        if not existing:
            raise HTTPException(status_code=404, detail="Discount rule not found")

        patch = body.dict(exclude_unset=True)
        if "code" in patch and patch["code"] is not None:
            code = DiscountRuleController._normalize_code(patch["code"])
            if not code:
                raise HTTPException(status_code=400, detail="Rule code is required")
            other = await db.discount_rules.find_one({"code": code, "id": {"$ne": rule_id}})
            if other:
                raise HTTPException(status_code=400, detail="A rule with this code already exists")
            patch["code"] = code
        if "name" in patch and patch["name"] is not None:
            patch["name"] = patch["name"].strip()
        if "branch_ids" in patch and patch["branch_ids"] is not None:
            patch["branch_ids"] = [b for b in patch["branch_ids"] if b]
        if "course_ids" in patch and patch["course_ids"] is not None:
            patch["course_ids"] = [c for c in patch["course_ids"] if c]

        patch["updated_at"] = DiscountRuleController._now()
        await db.discount_rules.update_one({"id": rule_id}, {"$set": patch})
        updated = await db.discount_rules.find_one({"id": rule_id})
        return {"rule": updated}

    @staticmethod
    async def delete(rule_id: str) -> Dict[str, Any]:
        db = get_db()
        res = await db.discount_rules.delete_one({"id": rule_id})
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Discount rule not found")
        return {"success": True}
