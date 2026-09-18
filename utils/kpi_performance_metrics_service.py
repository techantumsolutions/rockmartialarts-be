"""
M10-S05 — Student KPI / rating / ranking metrics for performance dashboard.

Additive read API. Does not modify student_performance_* skills or warrior rank.
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

COL_PERIODS = "kpi_assessment_periods"
COL_ASSESSMENTS = "student_kpi_assessments"
COL_RATINGS = "student_kpi_ratings"
COL_RANKINGS = "student_kpi_rankings"


async def get_performance_metrics(
    student_id: str,
    *,
    current_user: dict,
    period_id: Optional[str] = None,
    course_id: Optional[str] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    user = await db.users.find_one({"id": student_id, "role": UserRole.STUDENT.value})
    if not user:
        raise HTTPException(status_code=404, detail="Student not found")

    branch_ids = await _student_branch_ids(db, student_id)
    if not _can_view_dashboard(current_user, student_id, branch_ids):
        raise HTTPException(status_code=403, detail="Not allowed to view this dashboard")

    # Periods that have assessments or ratings for this student
    period_ids_from_assess = await db[COL_ASSESSMENTS].distinct(
        "period_id", {"student_id": student_id}
    )
    period_ids_from_ratings = await db[COL_RATINGS].distinct(
        "period_id", {"student_id": student_id}
    )
    period_id_set = {
        str(p) for p in (period_ids_from_assess or []) + (period_ids_from_ratings or []) if p
    }

    available_periods: List[Dict[str, Any]] = []
    if period_id_set:
        period_docs = (
            await db[COL_PERIODS]
            .find({"id": {"$in": list(period_id_set)}})
            .sort([("start_date", -1)])
            .to_list(length=100)
        )
        available_periods = [
            {
                "id": p.get("id"),
                "code": p.get("code"),
                "name": p.get("name"),
                "status": p.get("status"),
                "start_date": p.get("start_date"),
                "end_date": p.get("end_date"),
            }
            for p in period_docs
        ]
    else:
        # Still list recent open/closed periods for empty-state UX (optional)
        recent = (
            await db[COL_PERIODS]
            .find({"status": {"$in": ["open", "closed"]}})
            .sort([("start_date", -1)])
            .limit(10)
            .to_list(10)
        )
        available_periods = [
            {
                "id": p.get("id"),
                "code": p.get("code"),
                "name": p.get("name"),
                "status": p.get("status"),
                "start_date": p.get("start_date"),
                "end_date": p.get("end_date"),
                "has_data": False,
            }
            for p in recent
        ]

    selected_period_id = period_id
    if not selected_period_id and available_periods:
        # Prefer a period that has student data
        with_data = [p for p in available_periods if p.get("id") in period_id_set]
        pool = with_data or available_periods
        selected_period_id = pool[0].get("id")

    selected_period = None
    if selected_period_id:
        selected_period = next(
            (p for p in available_periods if p.get("id") == selected_period_id), None
        )
        if not selected_period:
            doc = await db[COL_PERIODS].find_one({"id": selected_period_id})
            if doc:
                selected_period = {
                    "id": doc.get("id"),
                    "code": doc.get("code"),
                    "name": doc.get("name"),
                    "status": doc.get("status"),
                    "start_date": doc.get("start_date"),
                    "end_date": doc.get("end_date"),
                }

    # Courses with assessments/ratings for this student (optionally in period)
    course_q: Dict[str, Any] = {"student_id": student_id}
    if selected_period_id:
        course_q["period_id"] = selected_period_id
    course_ids = set(
        await db[COL_ASSESSMENTS].distinct("course_id", course_q)
    ) | set(await db[COL_RATINGS].distinct("course_id", course_q))
    course_ids = {str(c) for c in course_ids if c}

    courses_out: List[Dict[str, Any]] = []
    if course_ids:
        course_docs = await db.courses.find({"id": {"$in": list(course_ids)}}).to_list(
            length=200
        )
        name_map = {
            str(c["id"]): (c.get("title") or c.get("name") or c["id"])
            for c in course_docs
            if c.get("id")
        }
        for cid in sorted(course_ids, key=lambda x: (name_map.get(x) or x).lower()):
            courses_out.append({"id": cid, "name": name_map.get(cid, cid)})

    selected_course_id = course_id
    if selected_course_id and selected_course_id not in course_ids and course_ids:
        # keep requested id but may yield empty metrics
        pass
    if not selected_course_id and courses_out:
        selected_course_id = courses_out[0]["id"]

    rating_doc = None
    assessments: List[Dict[str, Any]] = []
    rankings: List[Dict[str, Any]] = []

    if selected_period_id and selected_course_id:
        rating_doc = await db[COL_RATINGS].find_one(
            {
                "student_id": student_id,
                "period_id": selected_period_id,
                "course_id": selected_course_id,
            }
        )
        assessments = (
            await db[COL_ASSESSMENTS]
            .find(
                {
                    "student_id": student_id,
                    "period_id": selected_period_id,
                    "course_id": selected_course_id,
                }
            )
            .sort([("kpi_name", 1)])
            .to_list(length=100)
        )
        rankings = (
            await db[COL_RANKINGS]
            .find(
                {
                    "student_id": student_id,
                    "period_id": selected_period_id,
                }
            )
            .sort([("scope_type", 1), ("rank", 1)])
            .to_list(length=50)
        )
        # Prefer rankings that match this course when scope is course; keep overall/branch/category
        filtered_rankings = []
        for r in rankings:
            st = str(r.get("scope_type") or "")
            if st == "course" and str(r.get("scope_id")) != str(selected_course_id):
                continue
            if st == "course" and str(r.get("course_id")) not in (
                "",
                str(selected_course_id),
            ):
                # course-scope row should be for this course
                if str(r.get("course_id")) != str(selected_course_id):
                    continue
            filtered_rankings.append(r)
        rankings = filtered_rankings

    rating_out = None
    if rating_doc:
        rating_out = {
            "id": rating_doc.get("id"),
            "rating": rating_doc.get("rating"),
            "band": rating_doc.get("band"),
            "is_complete": rating_doc.get("is_complete"),
            "weight_sum": rating_doc.get("weight_sum"),
            "formula_version": rating_doc.get("formula_version"),
            "breakdown": rating_doc.get("breakdown") or [],
            "missing_kpi_ids": rating_doc.get("missing_kpi_ids") or [],
            "calculated_at": rating_doc.get("calculated_at"),
            "course_id": rating_doc.get("course_id"),
            "period_id": rating_doc.get("period_id"),
            "branch_id": rating_doc.get("branch_id"),
        }

    kpi_breakdown = []
    if rating_out and rating_out.get("breakdown"):
        kpi_breakdown = rating_out["breakdown"]
    else:
        for a in assessments:
            kpi_breakdown.append(
                {
                    "kpi_id": a.get("kpi_id"),
                    "kpi_code": a.get("kpi_code"),
                    "kpi_name": a.get("kpi_name"),
                    "raw_score": a.get("raw_score"),
                    "min_score": a.get("min_score"),
                    "max_score": a.get("max_score"),
                    "normalized_score": a.get("normalized_score"),
                    "weight": None,
                    "weight_unit": None,
                    "contribution": None,
                }
            )

    rankings_out = [
        {
            "id": r.get("id"),
            "scope_type": r.get("scope_type"),
            "scope_id": r.get("scope_id"),
            "rank": r.get("rank"),
            "population_size": r.get("population_size"),
            "tied": r.get("tied"),
            "rating": r.get("rating"),
            "band": r.get("band"),
            "course_id": r.get("course_id"),
            "branch_id": r.get("branch_id"),
            "category_id": r.get("category_id"),
        }
        for r in rankings
    ]

    return serialize_doc(
        {
            "student_id": student_id,
            "period": selected_period,
            "period_id": selected_period_id,
            "available_periods": available_periods,
            "courses": courses_out,
            "course_id": selected_course_id,
            "rating": rating_out,
            "rankings": rankings_out,
            "kpi_breakdown": kpi_breakdown,
            "assessments": serialize_doc(assessments),
            "has_kpi_data": bool(period_id_set),
        }
    )
