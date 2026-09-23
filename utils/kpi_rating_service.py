"""
M10-S03 KPI rating service — persist + recalculate.

Uses pure engine in kpi_rating_engine.py. Does not touch ranking or dashboard.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.kpi_rating_models import (
    FORMULA_VERSION,
    PreviewRatingRequest,
    RecalculateRatingRequest,
)
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.kpi_rating_engine import calculate_weighted_rating
from utils.student_status_service import get_managed_branch_ids_for_user

logger = logging.getLogger(__name__)

COL_RATINGS = "student_kpi_ratings"
COL_ASSESSMENTS = "student_kpi_assessments"
COL_PERIODS = "kpi_assessment_periods"
COL_KPIS = "kpi_definitions"


def _role(user: dict) -> str:
    return str((user or {}).get("role") or "").lower()


async def ensure_kpi_rating_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL_RATINGS].create_index("id", unique=True)
        await database[COL_RATINGS].create_index(
            [("student_id", 1), ("course_id", 1), ("period_id", 1)],
            unique=True,
            name="uniq_student_course_period_rating",
        )
        await database[COL_RATINGS].create_index([("period_id", 1), ("rating", -1)])
        await database[COL_RATINGS].create_index("branch_id")
        await database[COL_RATINGS].create_index("calculated_at")
    except Exception:
        logger.exception("Failed ensuring KPI rating indexes")


async def _assert_can_calculate(db, current_user: dict, branch_id: Optional[str]) -> None:
    role = _role(current_user)
    if role in {"super_admin", "superadmin", "coach_admin"}:
        return
    if role == "branch_manager":
        managed = await get_managed_branch_ids_for_user(db, current_user)
        if not managed:
            raise HTTPException(status_code=403, detail="No managed branches")
        if branch_id and str(branch_id) not in {str(b) for b in managed}:
            raise HTTPException(status_code=403, detail="Branch out of scope for rating")
        return
    if role == "coach":
        coach_branch = current_user.get("branch_id")
        if not coach_branch:
            raise HTTPException(status_code=403, detail="Coach has no branch")
        if branch_id and str(coach_branch) != str(branch_id):
            raise HTTPException(status_code=403, detail="Coach can only rate own branch")
        return
    raise HTTPException(status_code=403, detail="Not authorized to calculate KPI ratings")


async def _assert_period_recalc_allowed(
    period: dict,
    *,
    force: bool,
    current_user: dict,
) -> None:
    status = str(period.get("status") or "").lower()
    if status == "closed":
        if not force:
            raise HTTPException(
                status_code=400,
                detail="Period is closed. Pass force=true to recalculate (admin only).",
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


async def _load_kpis(db) -> List[dict]:
    return await db[COL_KPIS].find({"is_active": True}).to_list(length=500)


async def _compute_one(
    db,
    *,
    period_id: str,
    student_id: str,
    course_id: str,
    branch_id: Optional[str],
    kpis: List[dict],
) -> Dict[str, Any]:
    q: Dict[str, Any] = {
        "period_id": period_id,
        "student_id": student_id,
        "course_id": course_id,
    }
    if branch_id:
        q["branch_id"] = branch_id
    assessments = await db[COL_ASSESSMENTS].find(q).to_list(length=500)
    if not assessments and branch_id:
        assessments = await db[COL_ASSESSMENTS].find(
            {
                "period_id": period_id,
                "student_id": student_id,
                "course_id": course_id,
            }
        ).to_list(length=500)

    resolved_branch = branch_id
    if assessments:
        resolved_branch = assessments[0].get("branch_id") or branch_id

    try:
        result = calculate_weighted_rating(
            kpis=kpis,
            assessments=assessments,
            course_id=course_id,
            branch_id=resolved_branch,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result["branch_id"] = str(resolved_branch or "")
    result["student_id"] = student_id
    result["course_id"] = course_id
    result["period_id"] = period_id
    return result


async def _persist_rating(
    db,
    computed: Dict[str, Any],
    *,
    current_user: dict,
) -> Dict[str, Any]:
    now = datetime.utcnow()
    actor = _actor(current_user)
    filter_q = {
        "student_id": computed["student_id"],
        "course_id": computed["course_id"],
        "period_id": computed["period_id"],
    }
    existing = await db[COL_RATINGS].find_one(filter_q)
    doc = {
        "student_id": computed["student_id"],
        "course_id": computed["course_id"],
        "branch_id": computed.get("branch_id") or "",
        "period_id": computed["period_id"],
        "rating": computed["rating"],
        "band": computed["band"],
        "is_complete": computed["is_complete"],
        "weight_sum": computed["weight_sum"],
        "formula_version": computed.get("formula_version") or FORMULA_VERSION,
        "missing_kpi_ids": computed.get("missing_kpi_ids") or [],
        "breakdown": computed.get("breakdown") or [],
        "applicable_kpi_count": computed.get("applicable_kpi_count"),
        "included_kpi_count": computed.get("included_kpi_count"),
        "calculated_by": actor["id"],
        "calculated_by_name": actor["name"],
        "calculated_at": now,
        "updated_at": now,
    }
    if existing:
        await db[COL_RATINGS].update_one({"id": existing["id"]}, {"$set": doc})
        doc["id"] = existing["id"]
        doc["created_at"] = existing.get("created_at") or now
        doc["action"] = "updated"
    else:
        doc["id"] = str(uuid.uuid4())
        doc["created_at"] = now
        doc["action"] = "created"
        await db[COL_RATINGS].insert_one(doc)
    return serialize_doc(doc)


async def preview_rating(
    body: PreviewRatingRequest,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_kpi_rating_indexes(db)

    period = await db[COL_PERIODS].find_one({"id": body.period_id})
    if not period:
        raise HTTPException(status_code=404, detail="Assessment period not found")

    kpis = await _load_kpis(db)
    computed = await _compute_one(
        db,
        period_id=body.period_id,
        student_id=body.student_id,
        course_id=body.course_id,
        branch_id=body.branch_id,
        kpis=kpis,
    )
    await _assert_can_calculate(db, current_user, computed.get("branch_id"))
    return {"preview": True, "formula_version": FORMULA_VERSION, **computed}


async def recalculate_ratings(
    body: RecalculateRatingRequest,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_kpi_rating_indexes(db)

    period = await db[COL_PERIODS].find_one({"id": body.period_id})
    if not period:
        raise HTTPException(status_code=404, detail="Assessment period not found")
    await _assert_period_recalc_allowed(period, force=body.force, current_user=current_user)

    kpis = await _load_kpis(db)
    if not kpis:
        raise HTTPException(status_code=400, detail="No active KPI definitions")

    # Single student mode
    if body.student_id and body.course_id:
        computed = await _compute_one(
            db,
            period_id=body.period_id,
            student_id=body.student_id,
            course_id=body.course_id,
            branch_id=body.branch_id,
            kpis=kpis,
        )
        await _assert_can_calculate(db, current_user, computed.get("branch_id"))
        saved = await _persist_rating(db, computed, current_user=current_user)
        return {
            "message": "Rating calculated",
            "formula_version": FORMULA_VERSION,
            "saved_count": 1,
            "error_count": 0,
            "ratings": [saved],
            "errors": [],
        }

    # Batch: distinct student/course from assessments in period
    match: Dict[str, Any] = {"period_id": body.period_id}
    if body.course_id:
        match["course_id"] = body.course_id
    if body.branch_id:
        match["branch_id"] = body.branch_id
    if body.student_id:
        match["student_id"] = body.student_id

    role = _role(current_user)
    if role == "branch_manager":
        managed = await get_managed_branch_ids_for_user(db, current_user)
        if not managed:
            return {"message": "No ratings", "saved_count": 0, "ratings": [], "errors": []}
        if body.branch_id and str(body.branch_id) not in {str(b) for b in managed}:
            raise HTTPException(status_code=403, detail="Branch out of scope")
        if not body.branch_id:
            match["branch_id"] = {"$in": managed}
    elif role == "coach":
        coach_branch = current_user.get("branch_id")
        if not coach_branch:
            raise HTTPException(status_code=403, detail="Coach has no branch")
        match["branch_id"] = str(coach_branch)

    pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": {
                    "student_id": "$student_id",
                    "course_id": "$course_id",
                    "branch_id": "$branch_id",
                }
            }
        },
    ]
    groups = await db[COL_ASSESSMENTS].aggregate(pipeline).to_list(length=5000)

    saved = []
    errors = []
    for g in groups:
        key = g.get("_id") or {}
        sid = key.get("student_id")
        cid = key.get("course_id")
        bid = key.get("branch_id")
        if not sid or not cid:
            continue
        try:
            # Skip inactive students
            student = await db.users.find_one({"id": sid, "role": "student"})
            if student and student.get("is_active") is False:
                errors.append(
                    {
                        "student_id": sid,
                        "course_id": cid,
                        "error": "Student inactive — skipped",
                    }
                )
                continue
            computed = await _compute_one(
                db,
                period_id=body.period_id,
                student_id=sid,
                course_id=cid,
                branch_id=bid,
                kpis=kpis,
            )
            row = await _persist_rating(db, computed, current_user=current_user)
            saved.append(row)
        except HTTPException as exc:
            errors.append(
                {
                    "student_id": sid,
                    "course_id": cid,
                    "error": str(exc.detail),
                }
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(
                {
                    "student_id": sid,
                    "course_id": cid,
                    "error": str(exc),
                }
            )

    return {
        "message": "Batch rating calculation finished",
        "formula_version": FORMULA_VERSION,
        "saved_count": len(saved),
        "error_count": len(errors),
        "ratings": saved,
        "errors": errors,
    }


async def list_ratings(
    *,
    period_id: Optional[str] = None,
    student_id: Optional[str] = None,
    course_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_kpi_rating_indexes(db)

    q: Dict[str, Any] = {}
    if period_id:
        q["period_id"] = period_id
    if student_id:
        q["student_id"] = student_id
    if course_id:
        q["course_id"] = course_id
    if branch_id:
        q["branch_id"] = branch_id

    if current_user and _role(current_user) == "branch_manager":
        managed = await get_managed_branch_ids_for_user(db, current_user)
        if not managed:
            return {"ratings": [], "total": 0, "count": 0, "formula_version": FORMULA_VERSION}
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
    total = await db[COL_RATINGS].count_documents(q)
    rows = (
        await db[COL_RATINGS]
        .find(q)
        .sort([("rating", -1), ("calculated_at", -1)])
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {
        "ratings": serialize_doc(rows),
        "total": total,
        "count": len(rows),
        "formula_version": FORMULA_VERSION,
    }


async def get_rating(rating_id: str, *, current_user: Optional[dict] = None) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL_RATINGS].find_one({"id": rating_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Rating not found")
    if current_user:
        role = _role(current_user)
        if role == "branch_manager":
            managed = await get_managed_branch_ids_for_user(db, current_user)
            if str(doc.get("branch_id")) not in {str(b) for b in managed}:
                raise HTTPException(status_code=403, detail="Branch out of scope")
        elif role == "coach":
            if str(current_user.get("branch_id")) != str(doc.get("branch_id")):
                raise HTTPException(status_code=403, detail="Branch out of scope")
    return {"rating": serialize_doc(doc), "formula_version": FORMULA_VERSION}
