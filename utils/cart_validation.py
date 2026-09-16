"""Server-side cart validation (M05-S01-T04)."""
from typing import List, Optional, Tuple

from fastapi import HTTPException

from models.cart_models import CartValidationIssue
from utils.branch_courses import assert_course_available_at_branch
from utils.branch_geography import assert_branch_accepts_enrollment
from utils.cart_duplicates import course_branch_tuple
from utils.database import get_db


def _item_key(item: dict) -> tuple:
    return (
        item.get("student_line_id"),
        item.get("course_id"),
        item.get("branch_id"),
        item.get("duration_id"),
        (item.get("batch_ref") or "").strip(),
    )


async def validate_cart_document(
    cart: dict,
    *,
    current_user: Optional[dict] = None,
    reprice_check: bool = True,
) -> Tuple[List[CartValidationIssue], List[dict]]:
    """Return issues and optionally repriced items when fees drift."""
    db = get_db()
    issues: List[CartValidationIssue] = []
    students = {s.get("student_line_id"): s for s in (cart.get("students") or []) if s.get("student_line_id")}
    items = cart.get("items") or []

    seen_keys = set()
    seen_course_branch: set = set()
    for item in items:
        iid = item.get("id")
        sid = item.get("student_line_id")
        if sid not in students:
            issues.append(
                CartValidationIssue(
                    code="unknown_student_line",
                    message="Cart item references a student that is not in this cart.",
                    item_id=iid,
                    student_line_id=sid,
                )
            )
            continue

        student_row = students[sid]
        student_id = (student_row.get("student_id") or "").strip()
        if student_id and current_user:
            role = current_user.get("role")
            if role == "student" and current_user.get("id") != student_id:
                issues.append(
                    CartValidationIssue(
                        code="student_ownership",
                        message="You can only add enrollments for your own student account.",
                        item_id=iid,
                        student_line_id=sid,
                    )
                )

        key = _item_key(item)
        if key in seen_keys:
            issues.append(
                CartValidationIssue(
                    code="duplicate_selection",
                    message="This course, branch, and duration is already in the cart for this student.",
                    item_id=iid,
                    student_line_id=sid,
                )
            )
        seen_keys.add(key)

        cb_key = course_branch_tuple(sid, item.get("course_id"), item.get("branch_id"))
        if cb_key in seen_course_branch:
            issues.append(
                CartValidationIssue(
                    code="duplicate_course",
                    message="This course is already in the cart for this student at this branch.",
                    item_id=iid,
                    student_line_id=sid,
                )
            )
        seen_course_branch.add(cb_key)

        branch_id = item.get("branch_id")
        course_id = item.get("course_id")
        try:
            await assert_branch_accepts_enrollment(db, branch_id)
        except HTTPException as exc:
            issues.append(
                CartValidationIssue(
                    code="branch_inactive",
                    message=str(exc.detail),
                    item_id=iid,
                    student_line_id=sid,
                )
            )
            continue

        try:
            await assert_course_available_at_branch(
                db,
                branch_id,
                course_id,
                allow_if_enrolled_student_id=student_id or None,
            )
        except HTTPException as exc:
            issues.append(
                CartValidationIssue(
                    code="course_unavailable",
                    message=str(exc.detail),
                    item_id=iid,
                    student_line_id=sid,
                )
            )
            continue

        if student_id:
            active = await db.enrollments.find_one(
                {
                    "student_id": student_id,
                    "course_id": course_id,
                    "is_active": True,
                    "payment_status": {"$in": ["paid", "pending"]},
                }
            )
            if active:
                issues.append(
                    CartValidationIssue(
                        code="already_enrolled",
                        message="This student already has an active enrollment for this course.",
                        item_id=iid,
                        student_line_id=sid,
                    )
                )

    repriced_items = items
    if reprice_check:
        from controllers.cart_controller import CartController

        repriced_items, drift_issues = await CartController.reprice_items(
            items,
            current_user=current_user,
            students_by_line=students,
        )
        issues.extend(drift_issues)

    return issues, repriced_items
