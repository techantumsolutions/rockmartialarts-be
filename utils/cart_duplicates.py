"""Duplicate detection for cart line items (M05-S02)."""
from typing import List, Optional


def course_branch_tuple(student_line_id: str, course_id: str, branch_id: str) -> tuple:
    return (student_line_id, course_id, branch_id)


def exact_line_tuple(item: dict) -> tuple:
    return (
        item.get("student_line_id"),
        item.get("course_id"),
        item.get("branch_id"),
        item.get("duration_id"),
        (item.get("batch_ref") or "").strip(),
    )


def find_same_course_at_branch(
    items: List[dict],
    *,
    student_line_id: str,
    course_id: str,
    branch_id: str,
    exclude_item_id: Optional[str] = None,
) -> Optional[dict]:
    for item in items:
        if exclude_item_id and item.get("id") == exclude_item_id:
            continue
        if (
            item.get("student_line_id") == student_line_id
            and item.get("course_id") == course_id
            and item.get("branch_id") == branch_id
        ):
            return item
    return None


def find_exact_duplicate(
    items: List[dict],
    *,
    student_line_id: str,
    course_id: str,
    branch_id: str,
    duration_id: str,
    batch_ref: Optional[str],
    exclude_item_id: Optional[str] = None,
) -> Optional[dict]:
    target = (
        student_line_id,
        course_id,
        branch_id,
        duration_id,
        (batch_ref or "").strip(),
    )
    for item in items:
        if exclude_item_id and item.get("id") == exclude_item_id:
            continue
        if exact_line_tuple(item) == target:
            return item
    return None
