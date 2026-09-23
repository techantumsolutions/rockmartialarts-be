"""
Student Performance Dashboard — additive module.

Reads: users, enrollments, courses, branches, coaches, attendance, payments.
Writes: separate `student_performance_*` collections only.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import HTTPException

from models.student_performance_models import (
    CoachFeedbackUpsert,
    GoalsUpsert,
    MedalStatsUpsert,
    ProfilePerformanceUpsert,
    SkillMetricsUpsert,
    WarriorStatsUpsert,
)
from models.user_models import UserRole
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.subscription_dates import is_subscription_period_over

logger = logging.getLogger(__name__)

COL_MEDALS = "student_performance_medal_stats"
COL_SKILLS = "student_performance_skill_metrics"
COL_GOALS = "student_performance_goals"
COL_FEEDBACK = "student_performance_coach_feedback"
COL_WARRIOR = "student_performance_warrior_stats"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_date(val: Any) -> Optional[date]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date() if val.tzinfo is None else val.astimezone(timezone.utc).date()
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        try:
            if "T" in s:
                return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


async def _student_branch_ids(db, student_id: str) -> List[str]:
    out: Set[str] = set()
    user = await db.users.find_one({"id": student_id, "role": UserRole.STUDENT.value})
    if not user:
        return []
    if user.get("branch_id"):
        out.add(str(user["branch_id"]))
    async for en in db.enrollments.find(
        {"student_id": student_id, "is_active": True}, {"branch_id": 1}
    ):
        b = en.get("branch_id")
        if b:
            out.add(str(b))
    return list(out)


def _normalize_payment_state(payment_doc: Optional[Dict[str, Any]]) -> Optional[str]:
    if not payment_doc:
        return None
    ps = str(payment_doc.get("payment_status") or "").strip().lower()
    if ps:
        return ps
    raw = str(payment_doc.get("status") or "").strip().lower()
    if raw in {"success", "captured", "paid", "completed"}:
        return "paid"
    if raw in {"authorized", "processing"}:
        return "processing"
    if raw in {"initiated", "created", "pending"}:
        return "pending"
    if raw in {"failed", "error"}:
        return "failed"
    if raw in {"cancelled", "canceled", "refunded"}:
        return "cancelled"
    return None


def _fee_status_label(ps: str) -> str:
    p = (ps or "").strip().lower()
    if p == "paid":
        return "Paid"
    if p == "overdue":
        return "Overdue"
    if p == "processing":
        return "Processing"
    if p == "pending":
        return "Pending"
    if p in ("cancelled", "canceled"):
        return "Cancelled"
    if p == "failed":
        return "Failed"
    return p.title() if p else "Unknown"


async def _compute_fee_status(
    db,
    student_id: str,
    primary: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Read-only fee snapshot from payments + primary enrollment (no payment flow changes)."""
    pending_filter = {
        "student_id": student_id,
        "$or": [
            {"payment_status": {"$in": ["pending", "processing", "overdue"]}},
            {"status": {"$in": ["pending", "initiated", "created", "processing", "authorized"]}},
        ],
    }
    pend_rows = (
        await db.payments.find(pending_filter)
        .sort([("due_date", 1), ("created_at", 1)])
        .limit(1)
        .to_list(1)
    )
    if pend_rows:
        p = pend_rows[0]
        ps = _normalize_payment_state(p) or "pending"
        return {
            "status": _fee_status_label(ps),
            "next_due_date": p.get("due_date"),
            "source": "payment",
        }

    enrollment_ps = ""
    if primary:
        enrollment_ps = str(primary.get("payment_status") or "").strip().lower()
        enr_id = primary.get("id")
        if enr_id:
            latest_enr_pay = await db.payments.find_one(
                {"student_id": student_id, "enrollment_id": enr_id},
                sort=[("created_at", -1), ("payment_date", -1)],
            )
            nps = _normalize_payment_state(latest_enr_pay)
            if nps:
                enrollment_ps = nps

        if enrollment_ps in ("pending", "processing", "overdue"):
            return {
                "status": _fee_status_label(enrollment_ps),
                "next_due_date": primary.get("next_due_date") or primary.get("end_date"),
                "source": "enrollment",
            }

        if enrollment_ps == "paid":
            end_date = primary.get("end_date")
            next_due = primary.get("next_due_date") or end_date
            billing = None
            billing_state_snap = None
            try:
                from utils.billing_cycle_service import get_active_cycle_for_enrollment
                from utils.billing_state import compute_enrollment_billing_state

                billing = await get_active_cycle_for_enrollment(primary.get("id"))
                if billing and billing.get("next_due_date"):
                    next_due = billing.get("next_due_date")
                billing_state_snap = compute_enrollment_billing_state(end_date)
            except Exception:
                billing = None
                billing_state_snap = None
            status = "Paid"
            if billing_state_snap and billing_state_snap.get("billing_state") == "grace":
                status = "Grace period"
            elif billing_state_snap and billing_state_snap.get("billing_state") == "overdue":
                status = "Due for renewal"
            elif end_date and is_subscription_period_over(end_date):
                status = "Due for renewal"
            elif billing and billing.get("status") == "due_soon":
                status = "Due soon"
            elif billing and billing.get("status") == "overdue":
                status = "Due for renewal"
            return {
                "status": status,
                "next_due_date": next_due,
                "period_start": (billing or {}).get("period_start") or primary.get("billing_period_start"),
                "period_end": (billing or {}).get("period_end") or primary.get("billing_period_end") or end_date,
                "billing_status": (billing_state_snap or {}).get("billing_state")
                or (billing or {}).get("status"),
                "overdue_days": (billing_state_snap or {}).get("overdue_days"),
                "grace_days_remaining": (billing_state_snap or {}).get("grace_days_remaining"),
                "grace_days_total": (billing_state_snap or {}).get("grace_days_total"),
                "is_within_grace": (billing_state_snap or {}).get("is_within_grace"),
                "duration_months": (billing or {}).get("duration_months"),
                "source": "enrollment",
            }

    latest_any = (
        await db.payments.find({"student_id": student_id})
        .sort([("payment_date", -1), ("created_at", -1)])
        .limit(1)
        .to_list(1)
    )
    if latest_any:
        p = latest_any[0]
        ps = _normalize_payment_state(p) or str(p.get("payment_status") or "").lower()
        return {
            "status": _fee_status_label(ps or "pending"),
            "next_due_date": p.get("due_date"),
            "source": "payment",
        }

    return {"status": "Paid", "next_due_date": None, "source": "default"}


def _can_view_dashboard(current_user: dict, student_id: str, branch_ids: List[str]) -> bool:
    role = (current_user.get("role") or "").lower()
    if role == UserRole.STUDENT.value and current_user.get("id") == student_id:
        return True
    if role in (UserRole.SUPER_ADMIN.value, "superadmin"):
        return True
    if not branch_ids:
        return False
    bset = set(branch_ids)
    if role == UserRole.BRANCH_MANAGER.value:
        managed = set(current_user.get("managed_branches") or [])
        return bool(managed.intersection(bset))
    if role in (UserRole.COACH.value, UserRole.COACH_ADMIN.value):
        bid = current_user.get("branch_id")
        return bool(bid and str(bid) in bset)
    return False


def _can_write_performance(current_user: dict, student_id: str, branch_ids: List[str]) -> bool:
    role = (current_user.get("role") or "").lower()
    if role == UserRole.STUDENT.value:
        return False
    return _can_view_dashboard(current_user, student_id, branch_ids)


async def _compute_attendance_block(db, student_id: str) -> Dict[str, Any]:
    cur = db.attendance.find({"student_id": student_id})
    rows = await cur.to_list(length=None)
    attended = 0
    missed = 0
    present_days: Set[date] = set()
    for r in rows:
        present = r.get("is_present", True)
        st = (r.get("status") or "").lower()
        if present is False or st == "absent":
            missed += 1
        else:
            attended += 1
            dk = _to_date(r.get("attendance_date"))
            if dk:
                present_days.add(dk)

    total = attended + missed
    pct = round((attended / total) * 100, 1) if total > 0 else None

    streak = _training_streak_days(present_days)
    return {
        "classes_attended": attended,
        "classes_missed": missed,
        "attendance_percent": pct,
        "training_streak_days": streak,
    }


def _training_streak_days(present_days: Set[date]) -> int:
    if not present_days:
        return 0
    sorted_desc = sorted(present_days, reverse=True)
    streak = 1
    expected = sorted_desc[0] - timedelta(days=1)
    for d in sorted_desc[1:]:
        if d == expected:
            streak += 1
            expected = d - timedelta(days=1)
        else:
            break
    return streak


async def _pick_primary_enrollment(db, student_id: str) -> Optional[Dict[str, Any]]:
    enrollments = await db.enrollments.find({"student_id": student_id}).sort([("updated_at", -1), ("created_at", -1)]).to_list(80)
    if not enrollments:
        return None

    def score(e: Dict[str, Any]) -> Tuple[int, float]:
        ps = str(e.get("payment_status") or "").lower()
        st = str(e.get("status") or "").lower()
        pr = 0
        if ps in ("cancelled", "canceled", "refunded") or st in ("cancelled", "canceled"):
            pr = 0
        elif ps == "paid" and e.get("is_active", True) is not False:
            pr = 100
        elif ps == "paid":
            pr = 80
        elif ps in ("pending", "overdue", "processing"):
            pr = 60
        elif ps == "failed":
            pr = 40
        else:
            pr = 20
        ts = 0.0
        for k in ("start_date", "enrollment_date", "updated_at", "created_at"):
            dk = _to_date(e.get(k))
            if dk:
                ts = max(ts, float(dk.toordinal()))
        return pr, ts

    return max(enrollments, key=score)


async def _resolve_display_coach_name(db, branch_id: Optional[str]) -> str:
    if not branch_id:
        return ""
    rows = (
        await db.coaches.find({"branch_id": branch_id, "is_active": True})
        .sort([("created_at", 1)])
        .limit(1)
        .to_list(1)
    )
    coach = rows[0] if rows else None
    if not coach:
        return ""
    return (coach.get("full_name") or f"{coach.get('first_name', '')} {coach.get('last_name', '')}").strip()


class StudentPerformanceController:
    @staticmethod
    async def get_dashboard(student_id: str, current_user: dict) -> Dict[str, Any]:
        db = get_db()
        user = await db.users.find_one({"id": student_id, "role": UserRole.STUDENT.value})
        if not user:
            raise HTTPException(status_code=404, detail="Student not found")

        branch_ids = await _student_branch_ids(db, student_id)
        if not _can_view_dashboard(current_user, student_id, branch_ids):
            raise HTTPException(status_code=403, detail="Not allowed to view this dashboard")

        primary = await _pick_primary_enrollment(db, student_id)
        course_name = ""
        branch_name = ""
        martial_art = ""
        joining = None
        branch_id = None
        if primary:
            branch_id = primary.get("branch_id")
            course = await db.courses.find_one({"id": primary.get("course_id")})
            branch = await db.branches.find_one({"id": branch_id})
            course_name = (course or {}).get("title") or (course or {}).get("name") or ""
            martial_art = course_name
            if branch:
                branch_name = (branch.get("branch") or {}).get("name") or branch.get("name") or ""
            joining = primary.get("start_date") or primary.get("enrollment_date")

        coach_name = await _resolve_display_coach_name(db, str(branch_id) if branch_id else None)

        belt = user.get("current_belt") or user.get("belt_rank") or user.get("student_level") or ""

        medals = await db[COL_MEDALS].find_one({"student_id": student_id})
        skills = await db[COL_SKILLS].find_one({"student_id": student_id})
        goals = await db[COL_GOALS].find_one({"student_id": student_id})
        feedback = await db[COL_FEEDBACK].find_one({"student_id": student_id})
        warrior = await db[COL_WARRIOR].find_one({"student_id": student_id})

        attendance_block = await _compute_attendance_block(db, student_id)
        fee = await _compute_fee_status(db, student_id, primary)

        profile = {
            "student_id": student_id,
            "name": user.get("full_name") or f"{user.get('first_name', '')} {user.get('last_name', '')}".strip(),
            "level_or_belt": belt,
            "branch": branch_name,
            "martial_art": martial_art or course_name,
            "date_of_joining": joining,
            "coach": coach_name or (feedback or {}).get("coach_name_snapshot") or "",
        }

        warrior_out: Dict[str, Any] = {}
        if warrior:
            for k, v in warrior.items():
                if k != "_id":
                    warrior_out[k] = v
        warrior_out.setdefault("rank", None)
        warrior_out.setdefault("next_level_progress", None)
        if warrior_out.get("training_streak") is None:
            warrior_out["training_streak"] = attendance_block["training_streak_days"]

        return {
            "profile": serialize_doc(profile),
            "achievements": serialize_doc(
                medals
                or {
                    "gold_medals": 0,
                    "silver_medals": 0,
                    "bronze_medals": 0,
                    "competitions_participated": 0,
                    "certificates_earned": 0,
                }
            ),
            "skills": serialize_doc(
                skills
                or {"strength": None, "speed": None, "flexibility": None, "technique": None}
            ),
            "attendance": attendance_block,
            "coach_feedback": serialize_doc(
                {
                    "feedback": (feedback or {}).get("feedback") or "",
                    "coach_id": (feedback or {}).get("coach_id"),
                    "updated_at": (feedback or {}).get("updated_at"),
                }
            ),
            "fee_status": serialize_doc(fee),
            "goal": serialize_doc(
                goals
                or {"current_goal": None, "target_belt": None, "progress_percentage": None}
            ),
            "warrior": serialize_doc(warrior_out),
        }

    @staticmethod
    async def _merge_upsert(
        coll: str,
        student_id: str,
        patch: Dict[str, Any],
        *,
        actor_id: Optional[str],
        extra_top: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        db = get_db()
        now = _utcnow()
        existing = await db[coll].find_one({"student_id": student_id})
        base: Dict[str, Any] = {"student_id": student_id}
        if existing:
            for k, v in existing.items():
                if k != "_id":
                    base[k] = v
        base.update({k: v for k, v in patch.items() if v is not None})
        base["updated_at"] = now
        if "created_at" not in base:
            base["created_at"] = now
        if actor_id:
            base["updated_by"] = actor_id
        if extra_top:
            base.update(extra_top)
        await db[coll].update_one({"student_id": student_id}, {"$set": base}, upsert=True)
        saved = await db[coll].find_one({"student_id": student_id})
        return serialize_doc(saved or base)

    @staticmethod
    async def put_medals(student_id: str, body: MedalStatsUpsert, current_user: dict) -> Dict[str, Any]:
        db = get_db()
        branch_ids = await _student_branch_ids(db, student_id)
        if not _can_write_performance(current_user, student_id, branch_ids):
            raise HTTPException(status_code=403, detail="Not allowed to update achievements")
        data = body.dict(exclude_unset=True)
        return await StudentPerformanceController._merge_upsert(
            COL_MEDALS, student_id, data, actor_id=current_user.get("id")
        )

    @staticmethod
    async def put_skills(student_id: str, body: SkillMetricsUpsert, current_user: dict) -> Dict[str, Any]:
        db = get_db()
        branch_ids = await _student_branch_ids(db, student_id)
        if not _can_write_performance(current_user, student_id, branch_ids):
            raise HTTPException(status_code=403, detail="Not allowed to update skills")
        data = body.dict(exclude_unset=True)
        return await StudentPerformanceController._merge_upsert(
            COL_SKILLS, student_id, data, actor_id=current_user.get("id")
        )

    @staticmethod
    async def put_goals(student_id: str, body: GoalsUpsert, current_user: dict) -> Dict[str, Any]:
        db = get_db()
        branch_ids = await _student_branch_ids(db, student_id)
        if not _can_write_performance(current_user, student_id, branch_ids):
            raise HTTPException(status_code=403, detail="Not allowed to update goals")
        data = body.dict(exclude_unset=True)
        return await StudentPerformanceController._merge_upsert(
            COL_GOALS, student_id, data, actor_id=current_user.get("id")
        )

    @staticmethod
    async def put_feedback(student_id: str, body: CoachFeedbackUpsert, current_user: dict) -> Dict[str, Any]:
        db = get_db()
        branch_ids = await _student_branch_ids(db, student_id)
        if not _can_write_performance(current_user, student_id, branch_ids):
            raise HTTPException(status_code=403, detail="Not allowed to update coach feedback")
        coach_name_snapshot = ""
        role = (current_user.get("role") or "").lower()
        if role in (UserRole.COACH.value, UserRole.COACH_ADMIN.value):
            coach_name_snapshot = (
                current_user.get("full_name")
                or f"{current_user.get('first_name', '')} {current_user.get('last_name', '')}".strip()
            )
        extra = {
            "feedback": body.feedback,
            "coach_id": current_user.get("id"),
            "coach_name_snapshot": coach_name_snapshot or None,
        }
        return await StudentPerformanceController._merge_upsert(
            COL_FEEDBACK, student_id, {}, actor_id=current_user.get("id"), extra_top=extra
        )

    @staticmethod
    async def put_profile(student_id: str, body: ProfilePerformanceUpsert, current_user: dict) -> Dict[str, Any]:
        db = get_db()
        branch_ids = await _student_branch_ids(db, student_id)
        if not _can_write_performance(current_user, student_id, branch_ids):
            raise HTTPException(status_code=403, detail="Not allowed to update student profile")

        user = await db.users.find_one({"id": student_id, "role": UserRole.STUDENT.value})
        if not user:
            raise HTTPException(status_code=404, detail="Student not found")

        patch: Dict[str, Any] = {"updated_at": _utcnow()}
        data = body.dict(exclude_unset=True)
        belt = data.get("level_or_belt")
        if belt is not None:
            patch["current_belt"] = belt
            patch["student_level"] = data.get("student_level") or belt
        elif data.get("student_level") is not None:
            patch["student_level"] = data["student_level"]

        if len(patch) <= 1:
            raise HTTPException(status_code=400, detail="No profile fields to update")

        await db.users.update_one({"id": student_id}, {"$set": patch})
        updated = await db.users.find_one({"id": student_id})
        return serialize_doc(
            {
                "student_id": student_id,
                "level_or_belt": updated.get("current_belt")
                or updated.get("belt_rank")
                or updated.get("student_level"),
                "student_level": updated.get("student_level"),
            }
        )

    @staticmethod
    async def put_warrior(student_id: str, body: WarriorStatsUpsert, current_user: dict) -> Dict[str, Any]:
        db = get_db()
        branch_ids = await _student_branch_ids(db, student_id)
        if not _can_write_performance(current_user, student_id, branch_ids):
            raise HTTPException(status_code=403, detail="Not allowed to update warrior stats")
        data = body.dict(exclude_unset=True)
        return await StudentPerformanceController._merge_upsert(
            COL_WARRIOR, student_id, data, actor_id=current_user.get("id")
        )


async def ensure_student_performance_indexes(db) -> None:
    """Idempotent index creation for performance module collections."""
    for coll, fields in (
        (COL_MEDALS, [("student_id", 1)]),
        (COL_SKILLS, [("student_id", 1)]),
        (COL_GOALS, [("student_id", 1)]),
        (COL_FEEDBACK, [("student_id", 1)]),
        (COL_WARRIOR, [("student_id", 1)]),
    ):
        try:
            await db[coll].create_index(fields, unique=True, name="student_id_1")
        except Exception:
            logger.exception("Failed creating index on %s", coll)
