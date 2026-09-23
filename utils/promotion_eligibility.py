"""
M19-S02 Promotion targeting eligibility engine.

Rules (mode=targeted):
  AND across every non-empty dimension:
    - student_ids: student id must be listed
    - branch_ids: student has an active enrollment at one of those branches
    - course_ids: student has an active enrollment in one of those courses
    - group_ids: student.batch_ref in list OR student.group_ids intersects list
  mode=all → every student is eligible
  mode=targeted with no dimensions → nobody (incomplete target)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from fastapi import HTTPException

from models.student_promotion_models import (
    PromotionTargetMode,
    normalize_promotion_target,
)
from utils.database import get_db

logger = logging.getLogger(__name__)

_STUDENT_ROLES = ("student",)


def _target_has_filters(target: Dict[str, Any]) -> bool:
    return bool(
        target.get("student_ids")
        or target.get("branch_ids")
        or target.get("course_ids")
        or target.get("group_ids")
    )


def _student_group_keys(student: Optional[dict]) -> Set[str]:
    if not student:
        return set()
    keys: Set[str] = set()
    batch = str(student.get("batch_ref") or "").strip()
    if batch:
        keys.add(batch)
    raw = student.get("group_ids")
    if isinstance(raw, list):
        for g in raw:
            s = str(g or "").strip()
            if s:
                keys.add(s)
    elif isinstance(raw, str) and raw.strip():
        keys.add(raw.strip())
    return keys


async def _active_enrollment_student_ids(
    db,
    *,
    student_ids: Optional[Set[str]] = None,
    branch_ids: Optional[List[str]] = None,
    course_ids: Optional[List[str]] = None,
) -> Set[str]:
    q: Dict[str, Any] = {"is_active": True}
    if student_ids is not None:
        if not student_ids:
            return set()
        q["student_id"] = {"$in": list(student_ids)}
    if branch_ids:
        q["branch_id"] = {"$in": list(branch_ids)}
    if course_ids:
        q["course_id"] = {"$in": list(course_ids)}

    ids: Set[str] = set()
    cursor = db.enrollments.find(q, {"student_id": 1})
    async for row in cursor:
        sid = row.get("student_id")
        if sid:
            ids.add(str(sid))
    return ids


async def resolve_eligible_student_ids(
    target_raw: Any,
    *,
    db=None,
) -> Set[str]:
    """Return set of student user ids matching the target."""
    database = db if db is not None else get_db()
    if database is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    target = normalize_promotion_target(target_raw)
    if target["mode"] == PromotionTargetMode.ALL.value:
        rows = await database.users.find(
            {"role": {"$in": list(_STUDENT_ROLES)}}, {"id": 1}
        ).to_list(length=100000)
        return {str(r["id"]) for r in rows if r.get("id")}

    if not _target_has_filters(target):
        return set()

    candidate: Optional[Set[str]] = None

    if target["student_ids"]:
        candidate = set(target["student_ids"])

    if target["group_ids"]:
        group_q: Dict[str, Any] = {
            "role": {"$in": list(_STUDENT_ROLES)},
            "$or": [
                {"batch_ref": {"$in": target["group_ids"]}},
                {"group_ids": {"$in": target["group_ids"]}},
            ],
        }
        if candidate is not None:
            group_q["id"] = {"$in": list(candidate)}
        rows = await database.users.find(group_q, {"id": 1}).to_list(length=100000)
        candidate = {str(r["id"]) for r in rows if r.get("id")}

    if target["branch_ids"] or target["course_ids"]:
        enrolled = await _active_enrollment_student_ids(
            database,
            student_ids=candidate,
            branch_ids=target["branch_ids"] or None,
            course_ids=target["course_ids"] or None,
        )
        candidate = enrolled

    if candidate is None:
        return set()

    # Ensure candidates are real students
    if not candidate:
        return set()
    verified = await database.users.find(
        {"id": {"$in": list(candidate)}, "role": {"$in": list(_STUDENT_ROLES)}},
        {"id": 1},
    ).to_list(length=len(candidate))
    return {str(r["id"]) for r in verified if r.get("id")}


async def is_student_eligible_for_target(
    student_id: str,
    target_raw: Any,
    *,
    db=None,
) -> bool:
    sid = str(student_id or "").strip()
    if not sid:
        return False
    database = db if db is not None else get_db()
    if database is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    target = normalize_promotion_target(target_raw)
    if target["mode"] == PromotionTargetMode.ALL.value:
        student = await database.users.find_one(
            {"id": sid, "role": {"$in": list(_STUDENT_ROLES)}}
        )
        return bool(student)

    if not _target_has_filters(target):
        return False

    if target["student_ids"] and sid not in target["student_ids"]:
        return False

    student = await database.users.find_one(
        {"id": sid, "role": {"$in": list(_STUDENT_ROLES)}}
    )
    if not student:
        return False

    if target["group_ids"]:
        if not (_student_group_keys(student) & set(target["group_ids"])):
            return False

    if target["branch_ids"] or target["course_ids"]:
        eq: Dict[str, Any] = {"student_id": sid, "is_active": True}
        if target["branch_ids"]:
            eq["branch_id"] = {"$in": target["branch_ids"]}
        if target["course_ids"]:
            eq["course_id"] = {"$in": target["course_ids"]}
        hit = await database.enrollments.find_one(eq, {"_id": 1})
        if not hit:
            return False

    return True


async def preview_eligibility(
    target_raw: Any,
    *,
    sample_limit: int = 20,
    db=None,
) -> Dict[str, Any]:
    database = db if db is not None else get_db()
    if database is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    target = normalize_promotion_target(target_raw)
    eligible_ids = await resolve_eligible_student_ids(target, db=database)
    total = len(eligible_ids)
    sample: List[Dict[str, Any]] = []
    if sample_limit > 0 and eligible_ids:
        sample_ids = list(eligible_ids)[:sample_limit]
        rows = await database.users.find(
            {"id": {"$in": sample_ids}},
            {
                "id": 1,
                "full_name": 1,
                "name": 1,
                "email": 1,
                "phone": 1,
                "batch_ref": 1,
            },
        ).to_list(length=sample_limit)
        by_id = {str(r["id"]): r for r in rows if r.get("id")}
        for sid in sample_ids:
            r = by_id.get(sid) or {"id": sid}
            sample.append(
                {
                    "id": sid,
                    "name": r.get("full_name") or r.get("name") or "",
                    "email": r.get("email"),
                    "phone": r.get("phone"),
                    "batch_ref": r.get("batch_ref"),
                }
            )

    return {
        "target": target,
        "eligible_count": total,
        "sample": sample,
        "has_filters": _target_has_filters(target),
        "mode": target["mode"],
    }


async def get_targeting_options(
    *,
    student_search: Optional[str] = None,
    student_limit: int = 40,
    db=None,
) -> Dict[str, Any]:
    """Options for admin target selector UI."""
    database = db if db is not None else get_db()
    if database is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    branches = (
        await database.branches.find(
            {"$or": [{"is_active": True}, {"is_active": {"$exists": False}}]},
            {"id": 1, "name": 1, "code": 1},
        )
        .sort("name", 1)
        .to_list(length=500)
    )
    courses = (
        await database.courses.find(
            {"$or": [{"is_active": True}, {"is_active": {"$exists": False}}]},
            {"id": 1, "name": 1, "code": 1},
        )
        .sort("name", 1)
        .to_list(length=500)
    )

    # Approved groups = distinct non-empty batch_ref on students
    group_vals = await database.users.distinct(
        "batch_ref",
        {
            "role": {"$in": list(_STUDENT_ROLES)},
            "batch_ref": {"$nin": [None, ""]},
        },
    )
    groups = [
        {"id": str(g), "name": str(g), "source": "batch_ref"}
        for g in sorted({str(x).strip() for x in group_vals if x and str(x).strip()})
    ]

    student_q: Dict[str, Any] = {"role": {"$in": list(_STUDENT_ROLES)}}
    if student_search and student_search.strip():
        term = student_search.strip()
        student_q["$or"] = [
            {"full_name": {"$regex": term, "$options": "i"}},
            {"name": {"$regex": term, "$options": "i"}},
            {"email": {"$regex": term, "$options": "i"}},
            {"phone": {"$regex": term, "$options": "i"}},
            {"id": term},
        ]
    student_limit = max(1, min(int(student_limit or 40), 100))
    students = (
        await database.users.find(
            student_q,
            {
                "id": 1,
                "full_name": 1,
                "name": 1,
                "email": 1,
                "phone": 1,
                "batch_ref": 1,
            },
        )
        .sort("full_name", 1)
        .limit(student_limit)
        .to_list(length=student_limit)
    )

    def _opt(rows, label_keys=("name", "full_name", "code")):
        out = []
        for r in rows:
            rid = r.get("id")
            if not rid:
                continue
            label = ""
            for k in label_keys:
                if r.get(k):
                    label = str(r[k])
                    break
            out.append({"id": str(rid), "name": label or str(rid), "code": r.get("code")})
        return out

    return {
        "branches": _opt(branches, ("name", "code")),
        "courses": _opt(courses, ("name", "code")),
        "groups": groups,
        "students": [
            {
                "id": str(s["id"]),
                "name": s.get("full_name") or s.get("name") or str(s["id"]),
                "email": s.get("email"),
                "phone": s.get("phone"),
                "batch_ref": s.get("batch_ref"),
            }
            for s in students
            if s.get("id")
        ],
    }
