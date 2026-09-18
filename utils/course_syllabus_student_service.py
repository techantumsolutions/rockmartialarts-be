"""
M11-S02 Student syllabus access — enrollment-gated list.

Does not change admin CMS, text syllabus, or public uploads.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from controllers.student_performance_controller import (
    _can_view_dashboard,
    _student_branch_ids,
)
from models.user_models import UserRole
from utils.database import get_db
from utils.helpers import serialize_doc

logger = logging.getLogger(__name__)

COL = "course_syllabi"


def _role(user: dict) -> str:
    return str((user or {}).get("role") or "").lower()


async def _assert_can_access_student(
    db, current_user: dict, student_id: str
) -> dict:
    """Return the target student user doc or raise 403/404."""
    student = await db.users.find_one({"id": student_id, "role": UserRole.STUDENT.value})
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    role = _role(current_user)
    uid = current_user.get("id")

    if role == UserRole.STUDENT.value:
        if uid == student_id:
            return student
        # Family / cart-created dependent under this account
        if student.get("primary_account_id") and str(student.get("primary_account_id")) == str(
            uid
        ):
            return student
        raise HTTPException(
            status_code=403,
            detail="Not allowed to view syllabi for this student",
        )

    branch_ids = await _student_branch_ids(db, student_id)
    if not _can_view_dashboard(current_user, student_id, branch_ids):
        raise HTTPException(
            status_code=403,
            detail="Not allowed to view syllabi for this student",
        )
    return student


async def list_accessible_student_profiles(
    *,
    current_user: dict,
) -> Dict[str, Any]:
    """
    Profiles the current user may select for syllabus viewing (T04 switcher).
    Students: self + dependents with primary_account_id = self.
    Staff: empty list (use explicit student_id from admin context).
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    role = _role(current_user)
    if role != UserRole.STUDENT.value:
        return {"profiles": [], "selected_student_id": None}

    uid = current_user.get("id")
    self_user = await db.users.find_one({"id": uid, "role": UserRole.STUDENT.value})
    profiles: List[Dict[str, Any]] = []
    if self_user:
        profiles.append(
            {
                "id": self_user["id"],
                "full_name": self_user.get("full_name")
                or " ".join(
                    filter(
                        None,
                        [self_user.get("first_name"), self_user.get("last_name")],
                    )
                ).strip()
                or "Me",
                "is_self": True,
            }
        )

    dependents = (
        await db.users.find(
            {
                "role": UserRole.STUDENT.value,
                "primary_account_id": uid,
                "is_active": {"$ne": False},
            }
        )
        .sort([("full_name", 1)])
        .to_list(length=50)
    )
    for d in dependents:
        if d.get("id") == uid:
            continue
        profiles.append(
            {
                "id": d["id"],
                "full_name": d.get("full_name")
                or " ".join(
                    filter(None, [d.get("first_name"), d.get("last_name")])
                ).strip()
                or d["id"],
                "is_self": False,
            }
        )

    return {
        "profiles": profiles,
        "selected_student_id": uid,
        "count": len(profiles),
    }


async def list_student_syllabi(
    student_id: str,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    """
    Active syllabus metadata for each active enrollment of the student.
    No public file_url — clients use authenticated stream (S03 / admin file).
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    student = await _assert_can_access_student(db, current_user, student_id)

    enrollments = await db.enrollments.find(
        {"student_id": student_id, "is_active": True}
    ).to_list(length=200)

    # Dedupe by course_id (keep first / latest branch)
    course_enroll: Dict[str, dict] = {}
    for e in enrollments:
        cid = e.get("course_id")
        if not cid:
            continue
        cid = str(cid)
        if cid not in course_enroll:
            course_enroll[cid] = e

    course_ids = list(course_enroll.keys())
    courses_map: Dict[str, dict] = {}
    if course_ids:
        course_docs = await db.courses.find({"id": {"$in": course_ids}}).to_list(
            length=len(course_ids)
        )
        courses_map = {str(c["id"]): c for c in course_docs if c.get("id")}

    syllabi_map: Dict[str, dict] = {}
    if course_ids:
        active_rows = await db[COL].find(
            {"course_id": {"$in": course_ids}, "is_active": True}
        ).to_list(length=len(course_ids))
        for s in active_rows:
            syllabi_map[str(s.get("course_id"))] = s

    items: List[Dict[str, Any]] = []
    for cid, enr in course_enroll.items():
        course = courses_map.get(cid) or {}
        syl = syllabi_map.get(cid)
        item: Dict[str, Any] = {
            "course_id": cid,
            "course_name": course.get("title") or course.get("name") or cid,
            "branch_id": enr.get("branch_id"),
            "enrollment_id": enr.get("id"),
            "enrollment_active": True,
            "has_syllabus": bool(syl),
            "syllabus": None,
        }
        if syl:
            item["syllabus"] = {
                "id": syl.get("id"),
                "title": syl.get("title"),
                "version": syl.get("version"),
                "original_filename": syl.get("original_filename"),
                "size_bytes": syl.get("size_bytes"),
                "effective_from": syl.get("effective_from"),
                "is_active": True,
                # Explicitly no storage_key / public URL
            }
        items.append(item)

    items.sort(key=lambda x: (x.get("course_name") or "").lower())

    student_name = (
        student.get("full_name")
        or " ".join(
            filter(None, [student.get("first_name"), student.get("last_name")])
        ).strip()
        or student_id
    )

    return serialize_doc(
        {
            "student_id": student_id,
            "student_name": student_name,
            "items": items,
            "total_courses": len(items),
            "with_syllabus": sum(1 for i in items if i.get("has_syllabus")),
        }
    )


async def assert_student_can_stream_syllabus(
    syllabus_id: str,
    *,
    current_user: dict,
    student_id: Optional[str] = None,
) -> dict:
    """
    Authorize PDF stream for a student (or staff).
    Used by S02 access checks and S03 viewer.
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    syl = await db[COL].find_one({"id": syllabus_id})
    if not syl:
        raise HTTPException(status_code=404, detail="Syllabus not found")
    if not syl.get("is_active", False):
        # Students may only open the active syllabus; staff (admin roles) handled elsewhere
        role = _role(current_user)
        if role == UserRole.STUDENT.value:
            raise HTTPException(status_code=403, detail="Syllabus is not active")

    course_id = str(syl.get("course_id") or "")
    role = _role(current_user)

    if role in {
        UserRole.SUPER_ADMIN.value,
        "superadmin",
        UserRole.COACH_ADMIN.value,
        UserRole.BRANCH_MANAGER.value,
        UserRole.COACH.value,
    }:
        # Staff: reuse admin stream permission (already gated by route) — here for student route
        if student_id:
            await _assert_can_access_student(db, current_user, student_id)
        return syl

    # Student (or family account): must have active enrollment in course
    candidates: List[str] = []
    if student_id:
        candidates.append(student_id)
    uid = current_user.get("id")
    if uid and uid not in candidates:
        candidates.append(uid)

    # Also check dependents of current user
    if role == UserRole.STUDENT.value and uid:
        deps = await db.users.find(
            {"primary_account_id": uid, "role": UserRole.STUDENT.value},
            {"id": 1},
        ).to_list(length=50)
        for d in deps:
            if d.get("id") and d["id"] not in candidates:
                candidates.append(d["id"])

    enrolled = await db.enrollments.find_one(
        {
            "student_id": {"$in": candidates},
            "course_id": course_id,
            "is_active": True,
        }
    )
    if not enrolled:
        raise HTTPException(
            status_code=403,
            detail="Not enrolled in this course (active enrollment required)",
        )

    # Ensure the enrolled student is one the caller can access
    await _assert_can_access_student(db, current_user, enrolled["student_id"])
    return syl
