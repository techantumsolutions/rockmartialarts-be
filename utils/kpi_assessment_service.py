"""
M10-S02 KPI assessment periods + student assessments.

Does not modify attendance, payments, biometric, or student_performance_* skills.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.kpi_assessment_models import (
    AssessmentPeriodCreate,
    AssessmentPeriodDocument,
    AssessmentPeriodStatus,
    AssessmentPeriodUpdate,
    StudentKpiAssessmentUpsert,
)
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.student_status_service import get_managed_branch_ids_for_user

logger = logging.getLogger(__name__)

COL_PERIODS = "kpi_assessment_periods"
COL_ASSESSMENTS = "student_kpi_assessments"
COL_KPIS = "kpi_definitions"


def _role(user: dict) -> str:
    return str((user or {}).get("role") or "").lower()


def _normalize_code(code: str) -> str:
    return (code or "").strip().upper().replace(" ", "_")


async def ensure_kpi_assessment_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL_PERIODS].create_index("id", unique=True)
        await database[COL_PERIODS].create_index("code", unique=True)
        await database[COL_PERIODS].create_index([("status", 1), ("start_date", -1)])
        await database[COL_ASSESSMENTS].create_index("id", unique=True)
        await database[COL_ASSESSMENTS].create_index(
            [("student_id", 1), ("course_id", 1), ("period_id", 1), ("kpi_id", 1)],
            unique=True,
            name="uniq_student_course_period_kpi",
        )
        await database[COL_ASSESSMENTS].create_index([("period_id", 1), ("student_id", 1)])
        await database[COL_ASSESSMENTS].create_index("evaluator_id")
        await database[COL_ASSESSMENTS].create_index("branch_id")
    except Exception:
        logger.exception("Failed ensuring KPI assessment indexes")


def _parse_dt(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def normalize_score(raw: float, min_score: float, max_score: float) -> float:
    if max_score <= min_score:
        return 0.0
    clamped = max(min_score, min(max_score, float(raw)))
    return round(((clamped - min_score) / (max_score - min_score)) * 100.0, 4)


# ---- Periods ----


async def list_periods(
    *,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_kpi_assessment_indexes(db)
    q: Dict[str, Any] = {}
    if status:
        q["status"] = str(status).strip().lower()
    skip = max(0, skip)
    limit = max(1, min(limit, 200))
    total = await db[COL_PERIODS].count_documents(q)
    rows = (
        await db[COL_PERIODS]
        .find(q)
        .sort([("start_date", -1), ("name", 1)])
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {"periods": serialize_doc(rows), "total": total, "count": len(rows)}


async def get_period(period_id: str) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL_PERIODS].find_one({"id": period_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Assessment period not found")
    return {"period": serialize_doc(doc)}


async def create_period(body: AssessmentPeriodCreate, *, current_user: dict) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_kpi_assessment_indexes(db)

    code = _normalize_code(body.code)
    if not code:
        raise HTTPException(status_code=400, detail="Period code is required")
    if await db[COL_PERIODS].find_one({"code": code}):
        raise HTTPException(status_code=409, detail="A period with this code already exists")
    if body.end_date < body.start_date:
        raise HTTPException(status_code=400, detail="end_date must be on or after start_date")

    doc = AssessmentPeriodDocument(
        code=code,
        name=body.name.strip(),
        description=(body.description or "").strip() or None,
        start_date=body.start_date,
        end_date=body.end_date,
        status=body.status,
        branch_ids=[b for b in (body.branch_ids or []) if b],
        course_ids=[c for c in (body.course_ids or []) if c],
        created_by=current_user.get("id"),
        updated_by=current_user.get("id"),
    )
    payload = doc.dict()
    payload["status"] = body.status.value
    await db[COL_PERIODS].insert_one(payload)
    return {"period": serialize_doc(payload), "message": "Assessment period created"}


async def update_period(
    period_id: str,
    body: AssessmentPeriodUpdate,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    existing = await db[COL_PERIODS].find_one({"id": period_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Assessment period not found")

    patch = body.dict(exclude_unset=True)
    if "code" in patch and patch["code"] is not None:
        code = _normalize_code(patch["code"])
        other = await db[COL_PERIODS].find_one({"code": code, "id": {"$ne": period_id}})
        if other:
            raise HTTPException(status_code=409, detail="A period with this code already exists")
        patch["code"] = code
    if "name" in patch and patch["name"] is not None:
        patch["name"] = str(patch["name"]).strip()
    if "description" in patch and patch["description"] is not None:
        patch["description"] = str(patch["description"]).strip() or None
    if "status" in patch and patch["status"] is not None:
        st = patch["status"]
        patch["status"] = st.value if hasattr(st, "value") else str(st)
    if "branch_ids" in patch and patch["branch_ids"] is not None:
        patch["branch_ids"] = [b for b in patch["branch_ids"] if b]
    if "course_ids" in patch and patch["course_ids"] is not None:
        patch["course_ids"] = [c for c in patch["course_ids"] if c]

    start = patch.get("start_date", existing.get("start_date"))
    end = patch.get("end_date", existing.get("end_date"))
    start_dt = _parse_dt(start) or start
    end_dt = _parse_dt(end) or end
    if isinstance(start_dt, datetime) and isinstance(end_dt, datetime) and end_dt < start_dt:
        raise HTTPException(status_code=400, detail="end_date must be on or after start_date")

    patch["updated_at"] = datetime.utcnow()
    patch["updated_by"] = current_user.get("id")
    await db[COL_PERIODS].update_one({"id": period_id}, {"$set": patch})
    updated = await db[COL_PERIODS].find_one({"id": period_id})
    return {"period": serialize_doc(updated), "message": "Assessment period updated"}


async def delete_period(period_id: str) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    used = await db[COL_ASSESSMENTS].count_documents({"period_id": period_id})
    if used > 0:
        raise HTTPException(
            status_code=400,
            detail="Period has assessments. Close it instead of deleting.",
        )
    res = await db[COL_PERIODS].delete_one({"id": period_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Assessment period not found")
    return {"success": True}


# ---- Assessments ----


async def _assert_can_assess(db, current_user: dict, branch_id: str) -> None:
    role = _role(current_user)
    if role in {"super_admin", "superadmin", "coach_admin"}:
        return
    if role == "branch_manager":
        managed = await get_managed_branch_ids_for_user(db, current_user)
        if str(branch_id) not in {str(b) for b in managed}:
            raise HTTPException(
                status_code=403,
                detail="Branch Manager cannot assess students outside managed branches",
            )
        return
    if role == "coach":
        coach_branch = current_user.get("branch_id")
        if not coach_branch or str(coach_branch) != str(branch_id):
            raise HTTPException(
                status_code=403,
                detail="Coach can only assess students in their branch",
            )
        return
    raise HTTPException(status_code=403, detail="Not authorized to submit KPI assessments")


async def _resolve_enrollment(
    db,
    *,
    student_id: str,
    course_id: str,
    branch_id: Optional[str],
) -> dict:
    student = await db.users.find_one({"id": student_id, "role": "student"})
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    if student.get("is_active") is False:
        raise HTTPException(status_code=400, detail="Cannot assess an inactive student")

    q: Dict[str, Any] = {"student_id": student_id, "course_id": course_id}
    if branch_id:
        q["branch_id"] = branch_id
    enr = await db.enrollments.find_one(q, sort=[("updated_at", -1), ("created_at", -1)])
    if not enr and branch_id:
        # fallback without branch filter then validate
        enr = await db.enrollments.find_one(
            {"student_id": student_id, "course_id": course_id},
            sort=[("updated_at", -1), ("created_at", -1)],
        )
    if not enr:
        raise HTTPException(
            status_code=400,
            detail="Student is not enrolled in the selected course",
        )
    resolved_branch = enr.get("branch_id") or branch_id
    if not resolved_branch:
        raise HTTPException(status_code=400, detail="Could not resolve branch for enrollment")
    if branch_id and str(enr.get("branch_id")) != str(branch_id):
        raise HTTPException(
            status_code=400,
            detail="Selected branch does not match the student's enrollment for this course",
        )
    return {"enrollment": enr, "branch_id": str(resolved_branch), "student": student}


async def upsert_student_assessment(
    body: StudentKpiAssessmentUpsert,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_kpi_assessment_indexes(db)

    period = await db[COL_PERIODS].find_one({"id": body.period_id})
    if not period:
        raise HTTPException(status_code=404, detail="Assessment period not found")
    status = str(period.get("status") or "").lower()
    if status != AssessmentPeriodStatus.OPEN.value:
        raise HTTPException(
            status_code=400,
            detail=f"Assessments only allowed when period status is open (current: {status or 'unknown'})",
        )

    # Optional period course/branch scope
    period_courses = period.get("course_ids") or []
    period_branches = period.get("branch_ids") or []
    if period_courses and body.course_id not in period_courses:
        raise HTTPException(status_code=400, detail="Course is not in this period's scope")

    resolved = await _resolve_enrollment(
        db,
        student_id=body.student_id,
        course_id=body.course_id,
        branch_id=body.branch_id,
    )
    branch_id = resolved["branch_id"]
    if period_branches and branch_id not in {str(b) for b in period_branches}:
        raise HTTPException(status_code=400, detail="Branch is not in this period's scope")

    await _assert_can_assess(db, current_user, branch_id)

    if not body.scores:
        raise HTTPException(status_code=400, detail="At least one KPI score is required")

    now = datetime.utcnow()
    evaluator_id = current_user.get("id")
    evaluator_name = (
        current_user.get("full_name")
        or current_user.get("email")
        or evaluator_id
    )
    saved = []
    errors = []

    for score in body.scores:
        kpi = await db[COL_KPIS].find_one({"id": score.kpi_id})
        if not kpi:
            errors.append({"kpi_id": score.kpi_id, "error": "KPI not found"})
            continue
        if not kpi.get("is_active", True):
            errors.append({"kpi_id": score.kpi_id, "error": "KPI is inactive"})
            continue

        min_score = float(kpi.get("min_score") or 0)
        max_score = float(kpi.get("max_score") or 100)
        raw = float(score.raw_score)
        if raw < min_score or raw > max_score:
            errors.append(
                {
                    "kpi_id": score.kpi_id,
                    "error": f"Score must be between {min_score} and {max_score}",
                }
            )
            continue

        normalized = normalize_score(raw, min_score, max_score)
        filter_q = {
            "student_id": body.student_id,
            "course_id": body.course_id,
            "period_id": body.period_id,
            "kpi_id": score.kpi_id,
        }
        existing = await db[COL_ASSESSMENTS].find_one(filter_q)
        doc = {
            "student_id": body.student_id,
            "course_id": body.course_id,
            "branch_id": branch_id,
            "period_id": body.period_id,
            "kpi_id": score.kpi_id,
            "kpi_code": kpi.get("code"),
            "kpi_name": kpi.get("name"),
            "raw_score": raw,
            "min_score": min_score,
            "max_score": max_score,
            "normalized_score": normalized,
            "notes": (score.notes or body.notes or "").strip() or None,
            "evaluator_id": evaluator_id,
            "evaluator_name": evaluator_name,
            "evaluated_at": now,
            "updated_at": now,
        }
        if existing:
            await db[COL_ASSESSMENTS].update_one({"id": existing["id"]}, {"$set": doc})
            doc["id"] = existing["id"]
            doc["created_at"] = existing.get("created_at")
            doc["action"] = "updated"
        else:
            doc["id"] = str(uuid.uuid4())
            doc["created_at"] = now
            doc["action"] = "created"
            await db[COL_ASSESSMENTS].insert_one(doc)
        saved.append(serialize_doc(doc))

    if not saved and errors:
        raise HTTPException(status_code=400, detail={"message": "No scores saved", "errors": errors})

    return {
        "message": "Assessment saved",
        "assessments": saved,
        "saved_count": len(saved),
        "errors": errors,
        "period_id": body.period_id,
        "student_id": body.student_id,
        "course_id": body.course_id,
        "branch_id": branch_id,
        "evaluator_id": evaluator_id,
        "evaluated_at": now.isoformat(),
    }


async def list_assessments(
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
            return {"assessments": [], "total": 0, "count": 0}
        if branch_id and str(branch_id) not in {str(b) for b in managed}:
            raise HTTPException(status_code=403, detail="Branch out of scope")
        if not branch_id:
            q["branch_id"] = {"$in": managed}

    skip = max(0, skip)
    limit = max(1, min(limit, 500))
    total = await db[COL_ASSESSMENTS].count_documents(q)
    rows = (
        await db[COL_ASSESSMENTS]
        .find(q)
        .sort([("evaluated_at", -1), ("kpi_name", 1)])
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {"assessments": serialize_doc(rows), "total": total, "count": len(rows)}


async def list_eligible_students(
    *,
    course_id: str,
    branch_id: Optional[str] = None,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    """Students enrolled in course (optionally branch), for assessment form selection."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    if not course_id:
        raise HTTPException(status_code=400, detail="course_id is required")

    q: Dict[str, Any] = {"course_id": course_id}
    if branch_id:
        q["branch_id"] = branch_id

    if current_user and _role(current_user) == "branch_manager":
        managed = await get_managed_branch_ids_for_user(db, current_user)
        if not managed:
            return {"students": [], "total": 0}
        if branch_id and str(branch_id) not in {str(b) for b in managed}:
            raise HTTPException(status_code=403, detail="Branch out of scope")
        if not branch_id:
            q["branch_id"] = {"$in": managed}

    enrollments = await db.enrollments.find(q).to_list(length=2000)
    student_ids = list({e.get("student_id") for e in enrollments if e.get("student_id")})
    if not student_ids:
        return {"students": [], "total": 0}

    users = await db.users.find(
        {"id": {"$in": student_ids}, "role": "student"}
    ).to_list(length=2000)
    user_map = {u["id"]: u for u in users if u.get("id")}
    enr_by_student = {}
    for e in enrollments:
        sid = e.get("student_id")
        if sid and sid not in enr_by_student:
            enr_by_student[sid] = e

    out = []
    for sid in student_ids:
        u = user_map.get(sid)
        if not u:
            continue
        if u.get("is_active") is False:
            continue
        enr = enr_by_student.get(sid) or {}
        out.append(
            {
                "id": sid,
                "full_name": u.get("full_name")
                or " ".join(filter(None, [u.get("first_name"), u.get("last_name")])).strip()
                or sid,
                "email": u.get("email"),
                "branch_id": enr.get("branch_id"),
                "course_id": course_id,
                "is_active": bool(u.get("is_active", True)),
            }
        )
    out.sort(key=lambda x: (x.get("full_name") or "").lower())
    return {"students": out, "total": len(out)}
