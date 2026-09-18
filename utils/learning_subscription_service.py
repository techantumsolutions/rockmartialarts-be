"""
M16-S03 Learning subscription service.

Additive LMS plans/checkout. Does not touch coach subscriptions, dojo billing,
camp, or residential payments. Reuses shared Razorpay helpers.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.learning_subscription_models import (
    DEFAULT_LIFETIME,
    DEFAULT_THREE_MONTH,
    LIFETIME_DAYS,
    THREE_MONTH_DAYS,
    LearningPlanKind,
    LearningSubscribeCheckoutBody,
    LearningSubscribeGrantBody,
    LearningSubscribePaymentVerify,
    LearningSubscriptionPaymentStatus,
    LearningSubscriptionPlanCreate,
    LearningSubscriptionPlanUpdate,
    LearningSubscriptionStatus,
)
from utils.database import get_db
from utils.helpers import serialize_doc

logger = logging.getLogger(__name__)

COL_PLANS = "learning_subscription_plans"
COL_SUBS = "learning_subscriptions"
COL_PAYMENTS = "learning_subscription_payments"
COL_COURSES = "learning_courses"
COL_STUDENTS = "students"


def _role(user: Optional[dict]) -> str:
    return str((user or {}).get("role") or "").lower().replace(" ", "_")


def _actor(user: Optional[dict]) -> Dict[str, Optional[str]]:
    if not user:
        return {"id": None, "name": "system", "role": "system"}
    return {
        "id": user.get("id"),
        "name": user.get("full_name") or user.get("email") or user.get("id"),
        "role": _role(user),
    }


def _is_admin(user: Optional[dict]) -> bool:
    return _role(user) in {
        "superadmin",
        "super_admin",
        "coach_admin",
        "coachadmin",
    }


def _plan_public(doc: dict) -> Dict[str, Any]:
    return serialize_doc(doc) or {}


async def ensure_learning_subscription_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL_PLANS].create_index("id", unique=True)
        await database[COL_PLANS].create_index(
            [("course_id", 1), ("is_active", 1), ("sort_order", 1)]
        )
        await database[COL_SUBS].create_index("id", unique=True)
        await database[COL_SUBS].create_index(
            [("user_id", 1), ("course_id", 1), ("created_at", -1)]
        )
        await database[COL_SUBS].create_index([("status", 1), ("ends_at", 1)])
        await database[COL_SUBS].create_index("razorpay_order_id")
        await database[COL_PAYMENTS].create_index("id", unique=True)
        await database[COL_PAYMENTS].create_index(
            [("user_id", 1), ("paid_at", -1)]
        )
        await database[COL_PAYMENTS].create_index("subscription_id")
    except Exception:
        logger.exception("Failed ensuring learning subscription indexes")


async def _get_course(db, course_id: str) -> dict:
    doc = await db[COL_COURSES].find_one({"id": course_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Learning course not found")
    return doc


def _normalize_plan_fields(
    *,
    plan_kind: str,
    duration_days: Optional[int],
    grace_period_days: int,
    is_lifetime: Optional[bool],
) -> Dict[str, Any]:
    kind = str(plan_kind or LearningPlanKind.CUSTOM.value)
    if kind == LearningPlanKind.THREE_MONTH.value:
        return {
            "plan_kind": kind,
            "duration_days": THREE_MONTH_DAYS,
            "grace_period_days": int(grace_period_days),
            "is_lifetime": False,
        }
    if kind == LearningPlanKind.LIFETIME.value:
        return {
            "plan_kind": kind,
            "duration_days": LIFETIME_DAYS,
            "grace_period_days": 0,
            "is_lifetime": True,
        }
    life = bool(is_lifetime) if is_lifetime is not None else False
    days = int(duration_days or THREE_MONTH_DAYS)
    if life:
        days = max(days, LIFETIME_DAYS)
        return {
            "plan_kind": kind,
            "duration_days": days,
            "grace_period_days": 0,
            "is_lifetime": True,
        }
    return {
        "plan_kind": kind,
        "duration_days": days,
        "grace_period_days": int(grace_period_days),
        "is_lifetime": False,
    }


async def list_plans(
    *,
    course_id: Optional[str] = None,
    active_only: bool = False,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    q: Dict[str, Any] = {}
    if course_id:
        q["course_id"] = course_id
    if active_only or not current_user or not _is_admin(current_user):
        q["is_active"] = True
    rows = (
        await db[COL_PLANS]
        .find(q)
        .sort([("sort_order", 1), ("fee_inr", 1)])
        .to_list(length=200)
    )
    return {"plans": [_plan_public(r) for r in rows], "total": len(rows)}


async def list_plans_public(course_id: str) -> Dict[str, Any]:
    if not (course_id or "").strip():
        raise HTTPException(status_code=400, detail="course_id required")
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    course = await db[COL_COURSES].find_one(
        {"id": course_id, "status": "published"}
    )
    if not course:
        raise HTTPException(status_code=404, detail="Published learning course not found")
    return await list_plans(course_id=course_id, active_only=True, current_user=None)


async def create_plan(
    body: LearningSubscriptionPlanCreate, *, current_user: dict
) -> Dict[str, Any]:
    if not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Only admins can create plans")
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_learning_subscription_indexes(db)
    await _get_course(db, body.course_id)

    kind = (
        body.plan_kind.value
        if isinstance(body.plan_kind, LearningPlanKind)
        else str(body.plan_kind)
    )
    norms = _normalize_plan_fields(
        plan_kind=kind,
        duration_days=body.duration_days,
        grace_period_days=body.grace_period_days,
        is_lifetime=body.is_lifetime,
    )
    now = datetime.utcnow()
    doc = {
        "id": str(uuid.uuid4()),
        "course_id": body.course_id,
        "name": body.name,
        "description": body.description,
        "fee_inr": float(body.fee_inr),
        "is_active": bool(body.is_active),
        "sort_order": int(body.sort_order),
        "created_at": now,
        "updated_at": now,
        "created_by": current_user.get("id"),
        **norms,
    }
    await db[COL_PLANS].insert_one(doc)
    return {"message": "Plan created", "plan": _plan_public(doc)}


async def ensure_default_plans_for_course(
    course_id: str, *, current_user: dict
) -> Dict[str, Any]:
    """Create 3-month + Lifetime plans if the course has none (admin helper)."""
    if not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Only admins can seed plans")
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await _get_course(db, course_id)
    existing = await db[COL_PLANS].count_documents({"course_id": course_id})
    if existing > 0:
        return await list_plans(
            course_id=course_id, active_only=False, current_user=current_user
        )

    created = []
    for template in (DEFAULT_THREE_MONTH, DEFAULT_LIFETIME):
        body = LearningSubscriptionPlanCreate(
            course_id=course_id,
            name=template["name"],
            description=template["description"],
            plan_kind=template["plan_kind"],
            fee_inr=template["fee_inr"],
            duration_days=template["duration_days"],
            grace_period_days=template["grace_period_days"],
            is_lifetime=template["is_lifetime"],
            is_active=True,
            sort_order=template["sort_order"],
        )
        res = await create_plan(body, current_user=current_user)
        created.append(res["plan"])
    return {
        "message": "Default 3-month and Lifetime plans created",
        "plans": created,
        "total": len(created),
    }


async def update_plan(
    plan_id: str, body: LearningSubscriptionPlanUpdate, *, current_user: dict
) -> Dict[str, Any]:
    if not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Only admins can update plans")
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    existing = await db[COL_PLANS].find_one({"id": plan_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Plan not found")

    data = body.model_dump(exclude_unset=True)
    patch: Dict[str, Any] = {"updated_at": datetime.utcnow()}
    for key in ("name", "description", "fee_inr", "is_active", "sort_order"):
        if key in data:
            patch[key] = data[key]

    kind = data.get("plan_kind")
    if kind is not None:
        kind = kind.value if hasattr(kind, "value") else str(kind)
    else:
        kind = existing.get("plan_kind") or LearningPlanKind.CUSTOM.value

    if any(
        k in data
        for k in ("plan_kind", "duration_days", "grace_period_days", "is_lifetime")
    ):
        norms = _normalize_plan_fields(
            plan_kind=kind,
            duration_days=data.get("duration_days", existing.get("duration_days")),
            grace_period_days=int(
                data.get(
                    "grace_period_days",
                    existing.get("grace_period_days", 7),
                )
            ),
            is_lifetime=data.get("is_lifetime", existing.get("is_lifetime")),
        )
        patch.update(norms)

    await db[COL_PLANS].update_one({"id": plan_id}, {"$set": patch})
    updated = await db[COL_PLANS].find_one({"id": plan_id})
    return {"message": "Plan updated", "plan": _plan_public(updated)}


def _compute_period(
    *,
    duration_days: int,
    grace_period_days: int,
    is_lifetime: bool,
    start: Optional[datetime] = None,
    stack_from: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = start or datetime.utcnow()
    if stack_from and stack_from > now and not is_lifetime:
        period_start = stack_from
    else:
        period_start = now
    if is_lifetime:
        ends_at = period_start + timedelta(days=LIFETIME_DAYS)
        grace_ends_at = ends_at
        return {
            "starts_at": period_start,
            "ends_at": ends_at,
            "grace_ends_at": grace_ends_at,
            "is_lifetime": True,
        }
    ends_at = period_start + timedelta(days=int(duration_days))
    grace_ends_at = ends_at + timedelta(days=int(grace_period_days))
    return {
        "starts_at": period_start,
        "ends_at": ends_at,
        "grace_ends_at": grace_ends_at,
        "is_lifetime": False,
    }


async def refresh_subscription_status(db, sub: dict) -> dict:
    status = str(sub.get("status") or "")
    if status in {
        LearningSubscriptionStatus.PENDING_PAYMENT.value,
        LearningSubscriptionStatus.CANCELLED.value,
        LearningSubscriptionStatus.EXPIRED.value,
    }:
        return sub
    if sub.get("is_lifetime") or (sub.get("plan_snapshot") or {}).get("is_lifetime"):
        return sub

    now = datetime.utcnow()
    ends_at = sub.get("ends_at")
    grace_ends_at = sub.get("grace_ends_at")
    if isinstance(ends_at, str):
        try:
            ends_at = datetime.fromisoformat(ends_at.replace("Z", ""))
        except Exception:
            ends_at = None
    if isinstance(grace_ends_at, str):
        try:
            grace_ends_at = datetime.fromisoformat(grace_ends_at.replace("Z", ""))
        except Exception:
            grace_ends_at = None

    new_status = status
    if ends_at and now >= ends_at:
        if grace_ends_at and now < grace_ends_at:
            new_status = LearningSubscriptionStatus.GRACE.value
        else:
            new_status = LearningSubscriptionStatus.EXPIRED.value

    if new_status != status:
        await db[COL_SUBS].update_one(
            {"id": sub["id"]},
            {"$set": {"status": new_status, "updated_at": now}},
        )
        sub = {**sub, "status": new_status, "updated_at": now}
    return sub


async def has_active_learning_access(
    user_id: str, course_id: str
) -> Dict[str, Any]:
    """S04 helper: True when user has active/grace entitlement for course."""
    db = get_db()
    if db is None:
        return {"entitled": False, "subscription": None}
    rows = (
        await db[COL_SUBS]
        .find(
            {
                "user_id": user_id,
                "course_id": course_id,
                "status": {
                    "$in": [
                        LearningSubscriptionStatus.ACTIVE.value,
                        LearningSubscriptionStatus.GRACE.value,
                    ]
                },
            }
        )
        .sort([("created_at", -1)])
        .to_list(length=10)
    )
    for row in rows:
        refreshed = await refresh_subscription_status(db, row)
        if refreshed.get("status") in {
            LearningSubscriptionStatus.ACTIVE.value,
            LearningSubscriptionStatus.GRACE.value,
        }:
            return {
                "entitled": True,
                "subscription": serialize_doc(refreshed),
            }
    return {"entitled": False, "subscription": None}


async def get_my_subscription(
    *, user_id: str, course_id: Optional[str], current_user: dict
) -> Dict[str, Any]:
    if current_user.get("id") != user_id and not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Not your subscription")
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    q: Dict[str, Any] = {"user_id": user_id}
    if course_id:
        q["course_id"] = course_id
    rows = (
        await db[COL_SUBS]
        .find(q)
        .sort([("created_at", -1)])
        .to_list(length=30)
    )
    current = None
    for row in rows:
        refreshed = await refresh_subscription_status(db, row)
        if refreshed.get("status") in {
            LearningSubscriptionStatus.ACTIVE.value,
            LearningSubscriptionStatus.GRACE.value,
            LearningSubscriptionStatus.PENDING_PAYMENT.value,
            LearningSubscriptionStatus.EXPIRED.value,
        }:
            current = refreshed
            if refreshed.get("status") in {
                LearningSubscriptionStatus.ACTIVE.value,
                LearningSubscriptionStatus.GRACE.value,
                LearningSubscriptionStatus.PENDING_PAYMENT.value,
            }:
                break

    entitled = False
    if current and current.get("status") in {
        LearningSubscriptionStatus.ACTIVE.value,
        LearningSubscriptionStatus.GRACE.value,
    }:
        entitled = True

    return {
        "user_id": user_id,
        "course_id": course_id,
        "subscription": serialize_doc(current) if current else None,
        "entitled": entitled,
    }


async def list_payment_history(
    *, user_id: str, current_user: dict, skip: int = 0, limit: int = 50
) -> Dict[str, Any]:
    if current_user.get("id") != user_id and not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Not allowed")
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    q = {"user_id": user_id}
    total = await db[COL_PAYMENTS].count_documents(q)
    rows = (
        await db[COL_PAYMENTS]
        .find(q)
        .sort([("paid_at", -1), ("created_at", -1)])
        .skip(max(0, skip))
        .limit(max(1, min(limit, 100)))
        .to_list(length=max(1, min(limit, 100)))
    )
    return {
        "payments": serialize_doc(rows),
        "total": total,
        "skip": skip,
        "limit": limit,
    }


async def list_subscriptions_admin(
    *,
    current_user: dict,
    course_id: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 25,
) -> Dict[str, Any]:
    if not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin only")
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    q: Dict[str, Any] = {}
    if course_id:
        q["course_id"] = course_id
    if status:
        q["status"] = status
    if search:
        s = search.strip()
        q["$or"] = [
            {"user_id": {"$regex": s, "$options": "i"}},
            {"user_email": {"$regex": s, "$options": "i"}},
            {"user_name": {"$regex": s, "$options": "i"}},
        ]
    total = await db[COL_SUBS].count_documents(q)
    rows = (
        await db[COL_SUBS]
        .find(q)
        .sort([("created_at", -1)])
        .skip(max(0, skip))
        .limit(max(1, min(limit, 100)))
        .to_list(length=max(1, min(limit, 100)))
    )
    out = []
    for row in rows:
        out.append(serialize_doc(await refresh_subscription_status(db, row)))
    return {"subscriptions": out, "total": total, "skip": skip, "limit": limit}


async def checkout(
    *,
    user_id: str,
    body: LearningSubscribeCheckoutBody,
    current_user: dict,
) -> Dict[str, Any]:
    from utils.razorpay_client import get_razorpay_client

    if current_user.get("id") != user_id and not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Not allowed")

    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    plan = await db[COL_PLANS].find_one({"id": body.plan_id, "is_active": True})
    if not plan:
        raise HTTPException(status_code=404, detail="Active plan not found")
    course = await _get_course(db, plan["course_id"])
    if str(course.get("status") or "") != "published":
        raise HTTPException(
            status_code=400,
            detail="Course must be published before purchasing a plan",
        )

    fee = float(plan.get("fee_inr") or 0)
    amount_paise = int(round(fee * 100))
    is_lifetime = bool(plan.get("is_lifetime"))

    current_resp = await get_my_subscription(
        user_id=user_id,
        course_id=plan["course_id"],
        current_user=current_user,
    )
    current = current_resp.get("subscription")
    if current and current.get("is_lifetime") and current.get("status") in {
        LearningSubscriptionStatus.ACTIVE.value,
        LearningSubscriptionStatus.GRACE.value,
    }:
        raise HTTPException(
            status_code=400,
            detail="You already have lifetime access to this course",
        )

    stack_from = None
    if (
        current
        and not is_lifetime
        and current.get("status")
        in {
            LearningSubscriptionStatus.ACTIVE.value,
            LearningSubscriptionStatus.GRACE.value,
        }
    ):
        ends = current.get("ends_at")
        if isinstance(ends, str):
            try:
                stack_from = datetime.fromisoformat(ends.replace("Z", ""))
            except Exception:
                stack_from = None
        elif isinstance(ends, datetime):
            stack_from = ends

    now = datetime.utcnow()
    period = _compute_period(
        duration_days=int(plan.get("duration_days") or THREE_MONTH_DAYS),
        grace_period_days=int(plan.get("grace_period_days") or 0),
        is_lifetime=is_lifetime,
        start=now,
        stack_from=stack_from,
    )

    sub_id = str(uuid.uuid4())
    plan_snapshot = {
        "id": plan["id"],
        "name": plan.get("name"),
        "plan_kind": plan.get("plan_kind"),
        "fee_inr": fee,
        "duration_days": plan.get("duration_days"),
        "grace_period_days": plan.get("grace_period_days"),
        "is_lifetime": is_lifetime,
    }
    user_email = current_user.get("email")
    user_name = (
        current_user.get("full_name")
        or current_user.get("name")
        or current_user.get("email")
    )
    sub_doc = {
        "id": sub_id,
        "user_id": user_id,
        "user_email": user_email,
        "user_name": user_name,
        "course_id": plan["course_id"],
        "course_title": course.get("title"),
        "course_slug": course.get("slug"),
        "plan_id": plan["id"],
        "plan_snapshot": plan_snapshot,
        "is_lifetime": is_lifetime,
        "status": LearningSubscriptionStatus.PENDING_PAYMENT.value,
        "payment_status": LearningSubscriptionPaymentStatus.PENDING.value,
        "amount_paise": amount_paise,
        "currency": "INR",
        "starts_at": period["starts_at"],
        "ends_at": period["ends_at"],
        "grace_ends_at": period["grace_ends_at"],
        "razorpay_order_id": None,
        "razorpay_payment_id": None,
        "razorpay_signature": None,
        "created_at": now,
        "updated_at": now,
        "created_by": current_user.get("id"),
        "action": "renew"
        if current
        and current.get("status")
        in {
            LearningSubscriptionStatus.ACTIVE.value,
            LearningSubscriptionStatus.GRACE.value,
            LearningSubscriptionStatus.EXPIRED.value,
        }
        else "new",
    }

    # Free plan — activate immediately
    if amount_paise <= 0:
        sub_doc["status"] = LearningSubscriptionStatus.ACTIVE.value
        sub_doc["payment_status"] = LearningSubscriptionPaymentStatus.WAIVED.value
        sub_doc["activated_at"] = now
        await db[COL_SUBS].insert_one(sub_doc)
        await db[COL_PAYMENTS].insert_one(
            {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "subscription_id": sub_id,
                "course_id": plan["course_id"],
                "plan_id": plan["id"],
                "plan_name": plan.get("name"),
                "amount_paise": 0,
                "currency": "INR",
                "payment_status": LearningSubscriptionPaymentStatus.WAIVED.value,
                "action": sub_doc["action"],
                "period_start": period["starts_at"],
                "period_end": period["ends_at"],
                "note": "Free plan auto-activated",
                "actor_id": current_user.get("id"),
                "paid_at": now,
                "created_at": now,
            }
        )
        return {
            "subscription_id": sub_id,
            "subscription": serialize_doc(sub_doc),
            "order": None,
            "key": None,
            "plan": _plan_public(plan),
            "activated": True,
        }

    if amount_paise < 100:
        raise HTTPException(status_code=400, detail="Plan fee must be at least ₹1")

    client = get_razorpay_client()
    try:
        order = client.order.create(
            {
                "amount": amount_paise,
                "currency": "INR",
                "payment_capture": 1,
                "notes": {
                    "learning_subscription_id": sub_id,
                    "user_id": user_id,
                    "course_id": plan["course_id"],
                    "plan_id": plan["id"],
                    "type": "learning_subscription",
                },
            }
        )
    except Exception:
        logger.exception("Razorpay learning subscription order.create failed")
        raise HTTPException(
            status_code=502,
            detail="We could not reach the payment service. Please try again in a moment.",
        )

    sub_doc["razorpay_order_id"] = order["id"]
    await db[COL_SUBS].insert_one(sub_doc)

    return {
        "subscription_id": sub_id,
        "subscription": serialize_doc(sub_doc),
        "order": {"id": order["id"], "amount": amount_paise, "currency": "INR"},
        "key": os.getenv("RAZORPAY_KEY_ID", "").strip(),
        "plan": _plan_public(plan),
        "activated": False,
    }


async def _activate_paid_subscription(
    db,
    sub: dict,
    *,
    razorpay_order_id: Optional[str],
    razorpay_payment_id: Optional[str],
    razorpay_signature: Optional[str],
    payment_status: str,
    actor: dict,
    note: Optional[str] = None,
) -> dict:
    now = datetime.utcnow()
    plan_snap = sub.get("plan_snapshot") or {}
    is_lifetime = bool(sub.get("is_lifetime") or plan_snap.get("is_lifetime"))

    starts_at = sub.get("starts_at") or now
    if isinstance(starts_at, str):
        try:
            starts_at = datetime.fromisoformat(starts_at.replace("Z", ""))
        except Exception:
            starts_at = now
    if starts_at < now - timedelta(minutes=5):
        period = _compute_period(
            duration_days=int(plan_snap.get("duration_days") or THREE_MONTH_DAYS),
            grace_period_days=int(plan_snap.get("grace_period_days") or 0),
            is_lifetime=is_lifetime,
            start=now,
        )
        starts_at = period["starts_at"]
        ends_at = period["ends_at"]
        grace_ends_at = period["grace_ends_at"]
    else:
        ends_at = sub.get("ends_at")
        grace_ends_at = sub.get("grace_ends_at")

    await db[COL_SUBS].update_one(
        {"id": sub["id"]},
        {
            "$set": {
                "status": LearningSubscriptionStatus.ACTIVE.value,
                "payment_status": payment_status,
                "starts_at": starts_at,
                "ends_at": ends_at,
                "grace_ends_at": grace_ends_at,
                "is_lifetime": is_lifetime,
                "razorpay_order_id": razorpay_order_id or sub.get("razorpay_order_id"),
                "razorpay_payment_id": razorpay_payment_id,
                "razorpay_signature": razorpay_signature,
                "activated_at": now,
                "updated_at": now,
            }
        },
    )

    await db[COL_SUBS].update_many(
        {
            "user_id": sub["user_id"],
            "course_id": sub["course_id"],
            "id": {"$ne": sub["id"]},
            "status": LearningSubscriptionStatus.PENDING_PAYMENT.value,
        },
        {
            "$set": {
                "status": LearningSubscriptionStatus.CANCELLED.value,
                "updated_at": now,
            }
        },
    )

    pay_doc = {
        "id": str(uuid.uuid4()),
        "user_id": sub["user_id"],
        "subscription_id": sub["id"],
        "course_id": sub.get("course_id"),
        "plan_id": sub.get("plan_id"),
        "plan_name": plan_snap.get("name"),
        "amount_paise": sub.get("amount_paise") or 0,
        "currency": sub.get("currency") or "INR",
        "razorpay_order_id": razorpay_order_id or sub.get("razorpay_order_id"),
        "razorpay_payment_id": razorpay_payment_id,
        "razorpay_signature": razorpay_signature,
        "payment_status": payment_status,
        "action": sub.get("action") or "new",
        "period_start": starts_at,
        "period_end": ends_at,
        "grace_ends_at": grace_ends_at,
        "note": note,
        "actor_id": actor.get("id"),
        "actor_name": actor.get("name"),
        "paid_at": now,
        "created_at": now,
    }
    await db[COL_PAYMENTS].insert_one(pay_doc)
    return await db[COL_SUBS].find_one({"id": sub["id"]})


async def verify_payment(
    *,
    user_id: str,
    body: LearningSubscribePaymentVerify,
    current_user: dict,
) -> Dict[str, Any]:
    from utils.razorpay_client import verify_razorpay_signature

    if current_user.get("id") != user_id and not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Not allowed")

    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    sub = await db[COL_SUBS].find_one({"id": body.subscription_id, "user_id": user_id})
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")

    if (
        sub.get("status") == LearningSubscriptionStatus.ACTIVE.value
        and sub.get("payment_status") == LearningSubscriptionPaymentStatus.PAID.value
        and (sub.get("razorpay_payment_id") or "") == body.razorpay_payment_id
    ):
        return {
            "message": "Already verified",
            "subscription": serialize_doc(sub),
        }

    stored_order = (sub.get("razorpay_order_id") or "").strip()
    if stored_order and stored_order != body.razorpay_order_id:
        raise HTTPException(status_code=400, detail="Order id mismatch")

    if not verify_razorpay_signature(
        body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature
    ):
        raise HTTPException(status_code=400, detail="Invalid payment signature")

    updated = await _activate_paid_subscription(
        db,
        sub,
        razorpay_order_id=body.razorpay_order_id,
        razorpay_payment_id=body.razorpay_payment_id,
        razorpay_signature=body.razorpay_signature,
        payment_status=LearningSubscriptionPaymentStatus.PAID.value,
        actor=_actor(current_user),
        note="Razorpay payment verified",
    )
    return {
        "message": "Payment verified",
        "subscription": serialize_doc(updated),
    }


async def grant_subscription(
    body: LearningSubscribeGrantBody, *, current_user: dict
) -> Dict[str, Any]:
    if not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin only")
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    plan = await db[COL_PLANS].find_one({"id": body.plan_id})
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    course = await _get_course(db, plan["course_id"])

    student = await db[COL_STUDENTS].find_one({"id": body.user_id})
    user_email = (student or {}).get("email")
    user_name = None
    if student:
        user_name = (
            f"{student.get('first_name') or ''} {student.get('last_name') or ''}".strip()
            or student.get("email")
        )

    is_lifetime = bool(plan.get("is_lifetime"))
    now = datetime.utcnow()
    period = _compute_period(
        duration_days=int(plan.get("duration_days") or THREE_MONTH_DAYS),
        grace_period_days=int(plan.get("grace_period_days") or 0),
        is_lifetime=is_lifetime,
        start=now,
    )
    plan_snapshot = {
        "id": plan["id"],
        "name": plan.get("name"),
        "plan_kind": plan.get("plan_kind"),
        "fee_inr": float(plan.get("fee_inr") or 0),
        "duration_days": plan.get("duration_days"),
        "grace_period_days": plan.get("grace_period_days"),
        "is_lifetime": is_lifetime,
    }
    sub_id = str(uuid.uuid4())
    sub_doc = {
        "id": sub_id,
        "user_id": body.user_id,
        "user_email": user_email,
        "user_name": user_name,
        "course_id": plan["course_id"],
        "course_title": course.get("title"),
        "course_slug": course.get("slug"),
        "plan_id": plan["id"],
        "plan_snapshot": plan_snapshot,
        "is_lifetime": is_lifetime,
        "status": LearningSubscriptionStatus.PENDING_PAYMENT.value,
        "payment_status": LearningSubscriptionPaymentStatus.PENDING.value,
        "amount_paise": 0,
        "currency": "INR",
        "starts_at": period["starts_at"],
        "ends_at": period["ends_at"],
        "grace_ends_at": period["grace_ends_at"],
        "created_at": now,
        "updated_at": now,
        "created_by": current_user.get("id"),
        "action": "grant",
        "grant_note": body.note,
    }
    await db[COL_SUBS].insert_one(sub_doc)
    updated = await _activate_paid_subscription(
        db,
        sub_doc,
        razorpay_order_id=None,
        razorpay_payment_id=None,
        razorpay_signature=None,
        payment_status=LearningSubscriptionPaymentStatus.WAIVED.value,
        actor=_actor(current_user),
        note=body.note or "Admin grant",
    )
    return {
        "message": "Subscription granted",
        "subscription": serialize_doc(updated),
        "plan": _plan_public(plan),
    }


async def refresh_all_statuses(*, current_user: dict) -> Dict[str, Any]:
    if not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin only")
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    rows = (
        await db[COL_SUBS]
        .find(
            {
                "status": {
                    "$in": [
                        LearningSubscriptionStatus.ACTIVE.value,
                        LearningSubscriptionStatus.GRACE.value,
                    ]
                },
                "is_lifetime": {"$ne": True},
            }
        )
        .to_list(length=5000)
    )
    changed = 0
    for row in rows:
        before = row.get("status")
        after = await refresh_subscription_status(db, row)
        if after.get("status") != before:
            changed += 1
    return {"message": "Statuses refreshed", "checked": len(rows), "changed": changed}
