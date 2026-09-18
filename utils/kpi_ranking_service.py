"""
M10-S04 KPI ranking service — recalculate + list.

Uses pure engine in kpi_ranking_engine.py. Does not touch warrior rank or dashboard.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.kpi_ranking_models import RANKING_VERSION, RecalculateRankingRequest, RankingScopeType
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.kpi_ranking_engine import compute_all_rankings
from utils.student_status_service import get_managed_branch_ids_for_user

logger = logging.getLogger(__name__)

COL_RANKINGS = "student_kpi_rankings"
COL_RATINGS = "student_kpi_ratings"
COL_PERIODS = "kpi_assessment_periods"


def _role(user: dict) -> str:
    return str((user or {}).get("role") or "").lower()


async def ensure_kpi_ranking_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL_RANKINGS].create_index("id", unique=True)
        await database[COL_RANKINGS].create_index(
            [("period_id", 1), ("scope_type", 1), ("scope_id", 1), ("student_id", 1)],
            unique=True,
            name="uniq_period_scope_student_rank",
        )
        await database[COL_RANKINGS].create_index(
            [("period_id", 1), ("scope_type", 1), ("scope_id", 1), ("rank", 1)]
        )
        await database[COL_RANKINGS].create_index("branch_id")
        await database[COL_RANKINGS].create_index("student_id")
    except Exception:
        logger.exception("Failed ensuring KPI ranking indexes")


async def _assert_can_rank(db, current_user: dict, branch_id: Optional[str] = None) -> None:
    role = _role(current_user)
    if role in {"super_admin", "superadmin", "coach_admin"}:
        return
    if role == "branch_manager":
        managed = await get_managed_branch_ids_for_user(db, current_user)
        if not managed:
            raise HTTPException(status_code=403, detail="No managed branches")
        if branch_id and str(branch_id) not in {str(b) for b in managed}:
            raise HTTPException(status_code=403, detail="Branch out of scope for ranking")
        return
    if role == "coach":
        coach_branch = current_user.get("branch_id")
        if not coach_branch:
            raise HTTPException(status_code=403, detail="Coach has no branch")
        if branch_id and str(coach_branch) != str(branch_id):
            raise HTTPException(status_code=403, detail="Coach can only rank own branch")
        return
    raise HTTPException(status_code=403, detail="Not authorized to calculate rankings")


async def _assert_period_recalc_allowed(
    period: dict, *, force: bool, current_user: dict
) -> None:
    status = str(period.get("status") or "").lower()
    if status == "closed":
        if not force:
            raise HTTPException(
                status_code=400,
                detail="Period is closed. Pass force=true to recalculate rankings (admin only).",
            )
        role = _role(current_user)
        if role not in {"super_admin", "superadmin", "coach_admin"}:
            raise HTTPException(
                status_code=403,
                detail="Only Super Admin / Coach Admin can force-recalculate closed periods",
            )


def _actor(current_user: dict) -> Dict[str, Optional[str]]:
    return {
        "id": current_user.get("id"),
        "name": current_user.get("full_name")
        or current_user.get("email")
        or current_user.get("id"),
    }


async def recalculate_rankings(
    body: RecalculateRankingRequest,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_kpi_ranking_indexes(db)

    period = await db[COL_PERIODS].find_one({"id": body.period_id})
    if not period:
        raise HTTPException(status_code=404, detail="Assessment period not found")
    await _assert_period_recalc_allowed(period, force=body.force, current_user=current_user)

    role = _role(current_user)
    branch_filter = body.branch_id
    if role == "branch_manager":
        managed = await get_managed_branch_ids_for_user(db, current_user)
        if not managed:
            return {
                "message": "No rankings",
                "saved_count": 0,
                "rankings": [],
                "formula_version": RANKING_VERSION,
            }
        if branch_filter and str(branch_filter) not in {str(b) for b in managed}:
            raise HTTPException(status_code=403, detail="Branch out of scope")
        if not branch_filter:
            # BM without branch filter: constrain to managed branches via post-load filter
            branch_filter = None
            managed_set = {str(b) for b in managed}
        else:
            managed_set = None
        await _assert_can_rank(db, current_user, branch_filter)
    elif role == "coach":
        coach_branch = current_user.get("branch_id")
        if not coach_branch:
            raise HTTPException(status_code=403, detail="Coach has no branch")
        branch_filter = str(coach_branch)
        managed_set = None
        await _assert_can_rank(db, current_user, branch_filter)
    else:
        managed_set = None
        await _assert_can_rank(db, current_user, branch_filter)

    rating_q: Dict[str, Any] = {"period_id": body.period_id}
    if branch_filter:
        rating_q["branch_id"] = branch_filter
    if body.course_id:
        rating_q["course_id"] = body.course_id

    ratings = await db[COL_RATINGS].find(rating_q).to_list(length=10000)
    if managed_set is not None:
        ratings = [r for r in ratings if str(r.get("branch_id")) in managed_set]

    if not ratings:
        raise HTTPException(
            status_code=400,
            detail="No ratings found for this period/filters. Calculate ratings (S03) first.",
        )

    student_ids = list({str(r.get("student_id")) for r in ratings if r.get("student_id")})
    inactive_ids = set()
    if student_ids:
        inactive_users = await db.users.find(
            {"id": {"$in": student_ids}, "role": "student", "is_active": False}
        ).to_list(length=len(student_ids))
        inactive_ids = {str(u["id"]) for u in inactive_users if u.get("id")}

    course_ids = list({str(r.get("course_id")) for r in ratings if r.get("course_id")})
    course_category_map: Dict[str, str] = {}
    if course_ids:
        courses = await db.courses.find({"id": {"$in": course_ids}}).to_list(
            length=len(course_ids)
        )
        for c in courses:
            if c.get("id") and c.get("category_id"):
                course_category_map[str(c["id"])] = str(c["category_id"])

    scope_types = (
        [s.value for s in body.scope_types]
        if body.scope_types
        else [s.value for s in RankingScopeType]
    )

    # For BM without explicit branch: still build branch/course/category/overall
    # but overall is limited to managed branches via filtered ratings already.
    computed = compute_all_rankings(
        ratings,
        inactive_student_ids=inactive_ids,
        course_category_map=course_category_map,
        scope_types=scope_types,
        branch_id=branch_filter,
        course_id=body.course_id,
        category_id=body.category_id,
    )

    if not computed:
        raise HTTPException(
            status_code=400,
            detail=(
                "No eligible complete ratings to rank "
                "(incomplete scores and inactive students are excluded)."
            ),
        )

    # Replace rankings for this period + requested scopes (+ optional filters)
    delete_q: Dict[str, Any] = {
        "period_id": body.period_id,
        "scope_type": {"$in": scope_types},
    }
    if branch_filter:
        delete_q["branch_id"] = branch_filter
    elif managed_set is not None:
        delete_q["branch_id"] = {"$in": list(managed_set)}
    if body.course_id:
        # When filtering by course, only replace course-scope for that course +
        # other scopes that were constrained — safest: delete matching computed scopes
        pass
    if body.course_id and RankingScopeType.COURSE.value in scope_types:
        # Narrow course-scope deletes
        await db[COL_RANKINGS].delete_many(
            {
                "period_id": body.period_id,
                "scope_type": RankingScopeType.COURSE.value,
                "scope_id": body.course_id,
            }
        )
        # Also delete other scopes for same period that we'll rewrite from this run
        other_scopes = [s for s in scope_types if s != RankingScopeType.COURSE.value]
        if other_scopes:
            oq = {
                "period_id": body.period_id,
                "scope_type": {"$in": other_scopes},
            }
            if branch_filter:
                oq["branch_id"] = branch_filter
            elif managed_set is not None:
                oq["branch_id"] = {"$in": list(managed_set)}
            await db[COL_RANKINGS].delete_many(oq)
    else:
        await db[COL_RANKINGS].delete_many(delete_q)

    now = datetime.utcnow()
    actor = _actor(current_user)
    docs = []
    for row in computed:
        doc = {
            "id": str(uuid.uuid4()),
            "period_id": body.period_id,
            "scope_type": row["scope_type"],
            "scope_id": row["scope_id"],
            "student_id": str(row.get("student_id")),
            "course_id": str(row.get("course_id") or ""),
            "branch_id": str(row.get("branch_id") or ""),
            "category_id": row.get("category_id")
            or course_category_map.get(str(row.get("course_id") or ""), None),
            "rating": float(row.get("rating") or 0),
            "band": row.get("band"),
            "rank": int(row["rank"]),
            "population_size": int(row.get("population_size") or 0),
            "tied": bool(row.get("tied")),
            "is_complete": True,
            "formula_version": RANKING_VERSION,
            "calculated_by": actor["id"],
            "calculated_by_name": actor["name"],
            "calculated_at": now,
            "created_at": now,
            "updated_at": now,
        }
        docs.append(doc)

    if docs:
        await db[COL_RANKINGS].insert_many(docs)

    return {
        "message": "Rankings recalculated",
        "formula_version": RANKING_VERSION,
        "saved_count": len(docs),
        "excluded_inactive": len(inactive_ids),
        "scope_types": scope_types,
        "rankings": serialize_doc(docs),
    }


async def list_rankings(
    *,
    period_id: Optional[str] = None,
    scope_type: Optional[str] = None,
    scope_id: Optional[str] = None,
    student_id: Optional[str] = None,
    course_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    category_id: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_kpi_ranking_indexes(db)

    q: Dict[str, Any] = {}
    if period_id:
        q["period_id"] = period_id
    if scope_type:
        q["scope_type"] = str(scope_type).lower()
    if scope_id:
        q["scope_id"] = scope_id
    if student_id:
        q["student_id"] = student_id
    if course_id:
        q["course_id"] = course_id
    if branch_id:
        q["branch_id"] = branch_id
    if category_id:
        q["category_id"] = category_id

    if current_user and _role(current_user) == "branch_manager":
        managed = await get_managed_branch_ids_for_user(db, current_user)
        if not managed:
            return {
                "rankings": [],
                "total": 0,
                "count": 0,
                "formula_version": RANKING_VERSION,
            }
        if branch_id and str(branch_id) not in {str(b) for b in managed}:
            raise HTTPException(status_code=403, detail="Branch out of scope")
        if not branch_id:
            q["branch_id"] = {"$in": managed}
    elif current_user and _role(current_user) == "coach":
        coach_branch = current_user.get("branch_id")
        if coach_branch:
            q["branch_id"] = str(coach_branch)

    skip = max(0, skip)
    limit = max(1, min(limit, 500))
    total = await db[COL_RANKINGS].count_documents(q)
    rows = (
        await db[COL_RANKINGS]
        .find(q)
        .sort([("rank", 1), ("rating", -1), ("student_id", 1)])
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {
        "rankings": serialize_doc(rows),
        "total": total,
        "count": len(rows),
        "formula_version": RANKING_VERSION,
    }


async def get_ranking(ranking_id: str, *, current_user: Optional[dict] = None) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL_RANKINGS].find_one({"id": ranking_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Ranking not found")
    if current_user:
        role = _role(current_user)
        if role == "branch_manager":
            managed = await get_managed_branch_ids_for_user(db, current_user)
            if str(doc.get("branch_id")) not in {str(b) for b in managed}:
                raise HTTPException(status_code=403, detail="Branch out of scope")
        elif role == "coach":
            if str(current_user.get("branch_id")) != str(doc.get("branch_id")):
                raise HTTPException(status_code=403, detail="Branch out of scope")
    return {"ranking": serialize_doc(doc), "formula_version": RANKING_VERSION}
