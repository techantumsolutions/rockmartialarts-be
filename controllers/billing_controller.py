"""M07-S02 billing cycle list/detail APIs."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.user_models import UserRole
from utils.billing_cycle_service import (
    ensure_billing_cycle_indexes,
    get_active_cycle_for_enrollment,
    refresh_cycle_status,
)
from utils.database import get_db
from utils.helpers import serialize_doc


def _role(user: dict) -> str:
    return str((user or {}).get("role") or "").lower()


def _managed_branch_ids(user: dict) -> List[str]:
    raw = (user or {}).get("managed_branches") or (user or {}).get("branch_ids") or []
    if isinstance(raw, str):
        return [raw]
    return [str(x) for x in raw if x]


class BillingController:
    @staticmethod
    async def list_cycles(
        current_user: dict,
        *,
        skip: int = 0,
        limit: int = 50,
        student_id: Optional[str] = None,
        enrollment_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        await ensure_billing_cycle_indexes(db)

        skip = max(0, int(skip or 0))
        limit = max(1, min(int(limit or 50), 200))
        role = _role(current_user)
        query: Dict[str, Any] = {}

        if role == UserRole.STUDENT.value:
            uid = current_user["id"]
            query["$or"] = [{"student_id": uid}, {"account_user_id": uid}]
        elif role == UserRole.BRANCH_MANAGER.value:
            branches = _managed_branch_ids(current_user)
            if not branches:
                return {"billing_cycles": [], "total": 0, "skip": skip, "limit": limit}
            query["branch_id"] = {"$in": branches}
        elif role not in (UserRole.SUPER_ADMIN.value, "superadmin"):
            raise HTTPException(status_code=403, detail="Not allowed")

        if student_id:
            if role == UserRole.STUDENT.value and student_id != current_user["id"]:
                raise HTTPException(status_code=403, detail="Not allowed")
            query["student_id"] = student_id
        if enrollment_id:
            query["enrollment_id"] = enrollment_id
        if status:
            query["status"] = status.strip().lower()

        total = await db.billing_cycles.count_documents(query)
        rows = (
            await db.billing_cycles.find(query)
            .sort("next_due_date", 1)
            .skip(skip)
            .limit(limit)
            .to_list(limit)
        )

        items = []
        for row in rows:
            try:
                refreshed = await refresh_cycle_status(row["id"])
                ser = refreshed or serialize_doc(row)
            except Exception:
                ser = serialize_doc(row)
            ser.pop("_id", None)
            course_name = None
            branch_name = None
            validity_end = None
            if ser.get("course_id"):
                c = await db.courses.find_one({"id": ser["course_id"]})
                if c:
                    course_name = c.get("title") or c.get("name")
            if ser.get("branch_id"):
                b = await db.branches.find_one({"id": ser["branch_id"]})
                if b:
                    branch_name = ((b.get("branch") or {}).get("name")) or b.get("name")
            if ser.get("enrollment_id"):
                e = await db.enrollments.find_one({"id": ser["enrollment_id"]})
                if e:
                    validity_end = e.get("end_date")
            items.append(
                {
                    "id": ser.get("id"),
                    "enrollment_id": ser.get("enrollment_id"),
                    "student_id": ser.get("student_id"),
                    "branch_id": ser.get("branch_id"),
                    "course_id": ser.get("course_id"),
                    "payment_id": ser.get("payment_id"),
                    "period_start": ser.get("period_start"),
                    "period_end": ser.get("period_end"),
                    "next_due_date": ser.get("next_due_date"),
                    "anchor_day": ser.get("anchor_day"),
                    "duration_months": ser.get("duration_months"),
                    "status": ser.get("status"),
                    "amount_hint": ser.get("amount_hint"),
                    "course_name": course_name,
                    "branch_name": branch_name,
                    "validity_end_date": validity_end,
                }
            )
        return {"billing_cycles": items, "total": total, "skip": skip, "limit": limit}

    @staticmethod
    async def get_cycle(cycle_id: str, current_user: dict) -> Dict[str, Any]:
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        doc = await refresh_cycle_status(cycle_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Billing cycle not found")
        BillingController._assert_access(doc, current_user)
        return {"billing_cycle": doc}

    @staticmethod
    async def get_for_enrollment(enrollment_id: str, current_user: dict) -> Dict[str, Any]:
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        enrollment = await db.enrollments.find_one({"id": enrollment_id})
        if not enrollment:
            raise HTTPException(status_code=404, detail="Enrollment not found")
        BillingController._assert_enrollment_access(enrollment, current_user)
        cycle = await get_active_cycle_for_enrollment(enrollment_id)
        return {
            "enrollment_id": enrollment_id,
            "validity_end_date": enrollment.get("end_date"),
            "next_due_date": (cycle or {}).get("next_due_date") or enrollment.get("next_due_date"),
            "billing_cycle": cycle,
        }

    @staticmethod
    def _assert_access(doc: dict, current_user: dict) -> None:
        role = _role(current_user)
        if role in (UserRole.SUPER_ADMIN.value, "superadmin"):
            return
        uid = current_user.get("id")
        if role == UserRole.STUDENT.value and (
            doc.get("student_id") == uid or doc.get("account_user_id") == uid
        ):
            return
        if role == UserRole.BRANCH_MANAGER.value:
            if doc.get("branch_id") in _managed_branch_ids(current_user):
                return
        raise HTTPException(status_code=403, detail="Not allowed")

    @staticmethod
    def _assert_enrollment_access(enrollment: dict, current_user: dict) -> None:
        role = _role(current_user)
        if role in (UserRole.SUPER_ADMIN.value, "superadmin"):
            return
        uid = current_user.get("id")
        if role == UserRole.STUDENT.value and enrollment.get("student_id") == uid:
            return
        if role == UserRole.BRANCH_MANAGER.value:
            if enrollment.get("branch_id") in _managed_branch_ids(current_user):
                return
        raise HTTPException(status_code=403, detail="Not allowed")
