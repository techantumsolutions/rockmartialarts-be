"""
M14-S04 Coach subscription service.

Additive: does not replace student billing, camp, or residential payments.
Reuses shared Razorpay helpers. Business rules live on subscription plans.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.coach_subscription_models import (
    DEFAULT_PLAN,
    CoachSubscriptionCheckoutBody,
    CoachSubscriptionGrantBody,
    CoachSubscriptionPaymentStatus,
    CoachSubscriptionPaymentVerify,
    CoachSubscriptionPlanCreate,
    CoachSubscriptionPlanUpdate,
    CoachSubscriptionStatus,
)
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.student_status_service import get_managed_branch_ids_for_user

logger = logging.getLogger(__name__)

COL_PLANS = "coach_subscription_plans"
COL_SUBS = "coach_subscriptions"
COL_PAYMENTS = "coach_subscription_payments"
COL_COACHES = "coaches"


def _role(user: Optional[dict]) -> str:
    return str((user or {}).get("role") or "").lower()


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


def _is_bm(user: Optional[dict]) -> bool:
    return _role(user) in {"branch_manager", "branch_admin", "branchmanager"}


async def ensure_coach_subscription_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL_PLANS].create_index("id", unique=True)
        await database[COL_PLANS].create_index([("is_active", 1), ("sort_order", 1)])
        await database[COL_SUBS].create_index("id", unique=True)
        await database[COL_SUBS].create_index([("coach_id", 1), ("created_at", -1)])
        await database[COL_SUBS].create_index([("status", 1), ("ends_at", 1)])
        await database[COL_SUBS].create_index("razorpay_order_id")
        await database[COL_PAYMENTS].create_index("id", unique=True)
        await database[COL_PAYMENTS].create_index(
            [("coach_id", 1), ("paid_at", -1)]
        )
        await database[COL_PAYMENTS].create_index("subscription_id")
    except Exception:
        logger.exception("Failed ensuring coach subscription indexes")


async def seed_default_plan_if_empty(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        count = await database[COL_PLANS].count_documents({})
        if count > 0:
            return
        now = datetime.utcnow()
        doc = {
            "id": str(uuid.uuid4()),
            **DEFAULT_PLAN,
            "created_at": now,
            "updated_at": now,
        }
        await database[COL_PLANS].insert_one(doc)
        logger.info("Seeded default coach subscription plan")
    except Exception:
        logger.exception("Failed seeding default coach subscription plan")


def _plan_public(doc: dict) -> Dict[str, Any]:
    return serialize_doc(doc) or {}


async def list_plans(
    *,
    current_user: Optional[dict] = None,
    active_only: bool = False,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await seed_default_plan_if_empty(db)

    q: Dict[str, Any] = {}
    # Coaches / public see active only
    if active_only or (current_user and _role(current_user) == "coach") or not current_user:
        q["is_active"] = True
    rows = (
        await db[COL_PLANS]
        .find(q)
        .sort([("sort_order", 1), ("fee_inr", 1)])
        .to_list(length=100)
    )
    return {"plans": [_plan_public(r) for r in rows], "total": len(rows)}


async def create_plan(
    body: CoachSubscriptionPlanCreate, *, current_user: dict
) -> Dict[str, Any]:
    if not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Only admins can create plans")
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    now = datetime.utcnow()
    doc = {
        "id": str(uuid.uuid4()),
        "name": body.name,
        "description": (body.description or "").strip() or None,
        "fee_inr": float(body.fee_inr),
        "duration_days": int(body.duration_days),
        "grace_period_days": int(body.grace_period_days),
        "deactivate_on_grace_expiry": bool(body.deactivate_on_grace_expiry),
        "is_active": bool(body.is_active),
        "is_default": bool(body.is_default),
        "sort_order": int(body.sort_order),
        "created_at": now,
        "updated_at": now,
        "created_by": current_user.get("id"),
    }
    if body.is_default:
        await db[COL_PLANS].update_many({}, {"$set": {"is_default": False}})
    await db[COL_PLANS].insert_one(doc)
    return {"message": "Plan created", "plan": _plan_public(doc)}


async def update_plan(
    plan_id: str, body: CoachSubscriptionPlanUpdate, *, current_user: dict
) -> Dict[str, Any]:
    if not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Only admins can update plans")
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    existing = await db[COL_PLANS].find_one({"id": plan_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Plan not found")

    patch = body.dict(exclude_unset=True)
    if "name" in patch and patch["name"] is not None:
        patch["name"] = str(patch["name"]).strip()
    if "description" in patch and patch["description"] is not None:
        patch["description"] = str(patch["description"]).strip() or None
    if patch.get("is_default") is True:
        await db[COL_PLANS].update_many(
            {"id": {"$ne": plan_id}}, {"$set": {"is_default": False}}
        )
    patch["updated_at"] = datetime.utcnow()
    await db[COL_PLANS].update_one({"id": plan_id}, {"$set": patch})
    updated = await db[COL_PLANS].find_one({"id": plan_id})
    return {"message": "Plan updated", "plan": _plan_public(updated)}


async def _get_coach(db, coach_id: str) -> dict:
    coach = await db[COL_COACHES].find_one({"id": coach_id})
    if not coach:
        raise HTTPException(status_code=404, detail="Coach not found")
    return coach


async def _assert_coach_access(db, coach: dict, current_user: dict) -> None:
    role = _role(current_user)
    if _is_admin(current_user):
        return
    if role == "coach":
        if current_user.get("id") != coach.get("id"):
            raise HTTPException(status_code=403, detail="Not your subscription")
        return
    if _is_bm(current_user):
        managed = await get_managed_branch_ids_for_user(db, current_user)
        locs = set()
        if coach.get("branch_id"):
            locs.add(str(coach["branch_id"]))
        for x in coach.get("service_location_ids") or []:
            locs.add(str(x))
        if not locs.intersection({str(m) for m in managed}):
            raise HTTPException(status_code=403, detail="Coach not in your branches")
        return
    raise HTTPException(status_code=403, detail="Not allowed")


def _compute_period(
    *,
    duration_days: int,
    grace_period_days: int,
    start: Optional[datetime] = None,
    stack_from: Optional[datetime] = None,
) -> Dict[str, datetime]:
    now = start or datetime.utcnow()
    # If renewing while still active/grace, stack from prior end
    if stack_from and stack_from > now:
        period_start = stack_from
    else:
        period_start = now
    ends_at = period_start + timedelta(days=int(duration_days))
    grace_ends_at = ends_at + timedelta(days=int(grace_period_days))
    return {
        "starts_at": period_start,
        "ends_at": ends_at,
        "grace_ends_at": grace_ends_at,
    }


async def refresh_subscription_status(db, sub: dict) -> dict:
    """Move active→grace→expired and optionally deactivate coach."""
    status = str(sub.get("status") or "")
    if status in {
        CoachSubscriptionStatus.PENDING_PAYMENT.value,
        CoachSubscriptionStatus.CANCELLED.value,
        CoachSubscriptionStatus.EXPIRED.value,
    }:
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
            new_status = CoachSubscriptionStatus.GRACE.value
        else:
            new_status = CoachSubscriptionStatus.EXPIRED.value

    if new_status != status:
        await db[COL_SUBS].update_one(
            {"id": sub["id"]},
            {"$set": {"status": new_status, "updated_at": now}},
        )
        sub = {**sub, "status": new_status, "updated_at": now}

        if new_status == CoachSubscriptionStatus.EXPIRED.value:
            plan = sub.get("plan_snapshot") or {}
            if plan.get("deactivate_on_grace_expiry", True):
                await db[COL_COACHES].update_one(
                    {"id": sub["coach_id"]},
                    {
                        "$set": {
                            "is_active": False,
                            "subscription_status": "expired",
                            "updated_at": now,
                        }
                    },
                )
    return sub


async def get_current_subscription(
    *, coach_id: str, current_user: dict
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    coach = await _get_coach(db, coach_id)
    await _assert_coach_access(db, coach, current_user)

    # Prefer latest non-cancelled paid/active-ish sub
    rows = (
        await db[COL_SUBS]
        .find({"coach_id": coach_id})
        .sort([("created_at", -1)])
        .to_list(length=20)
    )
    current = None
    for row in rows:
        refreshed = await refresh_subscription_status(db, row)
        if refreshed.get("status") in {
            CoachSubscriptionStatus.ACTIVE.value,
            CoachSubscriptionStatus.GRACE.value,
            CoachSubscriptionStatus.PENDING_PAYMENT.value,
            CoachSubscriptionStatus.EXPIRED.value,
        }:
            current = refreshed
            if refreshed.get("status") in {
                CoachSubscriptionStatus.ACTIVE.value,
                CoachSubscriptionStatus.GRACE.value,
                CoachSubscriptionStatus.PENDING_PAYMENT.value,
            }:
                break

    return {
        "coach_id": coach_id,
        "subscription": serialize_doc(current) if current else None,
        "coach_is_active": bool(coach.get("is_active", True)),
        "approval_status": coach.get("approval_status") or "approved",
    }


async def list_payment_history(
    *, coach_id: str, current_user: dict, skip: int = 0, limit: int = 50
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    coach = await _get_coach(db, coach_id)
    await _assert_coach_access(db, coach, current_user)
    q = {"coach_id": coach_id}
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


async def checkout(
    *,
    coach_id: str,
    body: CoachSubscriptionCheckoutBody,
    current_user: dict,
) -> Dict[str, Any]:
    """Create pending subscription + Razorpay order."""
    import os
    from utils.razorpay_client import get_razorpay_client

    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    coach = await _get_coach(db, coach_id)
    await _assert_coach_access(db, coach, current_user)

    approval = str(coach.get("approval_status") or "approved").lower()
    if approval != "approved":
        raise HTTPException(
            status_code=403,
            detail="Coach must be approved before purchasing a subscription.",
        )

    plan = await db[COL_PLANS].find_one({"id": body.plan_id, "is_active": True})
    if not plan:
        raise HTTPException(status_code=404, detail="Active plan not found")

    fee = float(plan.get("fee_inr") or 0)
    amount_paise = int(round(fee * 100))
    if amount_paise < 100:
        raise HTTPException(status_code=400, detail="Plan fee must be at least ₹1")

    # Stack renewals from current active/grace end
    current_resp = await get_current_subscription(
        coach_id=coach_id, current_user=current_user
    )
    current = current_resp.get("subscription")
    stack_from = None
    if current and current.get("status") in {
        CoachSubscriptionStatus.ACTIVE.value,
        CoachSubscriptionStatus.GRACE.value,
    }:
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
        duration_days=int(plan["duration_days"]),
        grace_period_days=int(plan.get("grace_period_days") or 0),
        start=now,
        stack_from=stack_from,
    )

    sub_id = str(uuid.uuid4())
    plan_snapshot = {
        "id": plan["id"],
        "name": plan.get("name"),
        "fee_inr": fee,
        "duration_days": plan.get("duration_days"),
        "grace_period_days": plan.get("grace_period_days"),
        "deactivate_on_grace_expiry": plan.get("deactivate_on_grace_expiry", True),
    }
    sub_doc = {
        "id": sub_id,
        "coach_id": coach_id,
        "plan_id": plan["id"],
        "plan_snapshot": plan_snapshot,
        "status": CoachSubscriptionStatus.PENDING_PAYMENT.value,
        "payment_status": CoachSubscriptionPaymentStatus.PENDING.value,
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
            CoachSubscriptionStatus.ACTIVE.value,
            CoachSubscriptionStatus.GRACE.value,
            CoachSubscriptionStatus.EXPIRED.value,
        }
        else "new",
    }

    client = get_razorpay_client()
    try:
        order = client.order.create(
            {
                "amount": amount_paise,
                "currency": "INR",
                "payment_capture": 1,
                "notes": {
                    "coach_subscription_id": sub_id,
                    "coach_id": coach_id,
                    "plan_id": plan["id"],
                    "type": "coach_subscription",
                },
            }
        )
    except Exception:
        logger.exception("Razorpay coach subscription order.create failed")
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
    coach_id = sub["coach_id"]
    plan_snap = sub.get("plan_snapshot") or {}

    # Recompute period from now if pending was delayed (unless stacking dates already set)
    starts_at = sub.get("starts_at") or now
    if isinstance(starts_at, str):
        try:
            starts_at = datetime.fromisoformat(starts_at.replace("Z", ""))
        except Exception:
            starts_at = now
    if starts_at < now - timedelta(minutes=5):
        # Stale pending checkout — start from now
        period = _compute_period(
            duration_days=int(plan_snap.get("duration_days") or 30),
            grace_period_days=int(plan_snap.get("grace_period_days") or 0),
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
                "status": CoachSubscriptionStatus.ACTIVE.value,
                "payment_status": payment_status,
                "starts_at": starts_at,
                "ends_at": ends_at,
                "grace_ends_at": grace_ends_at,
                "razorpay_order_id": razorpay_order_id or sub.get("razorpay_order_id"),
                "razorpay_payment_id": razorpay_payment_id,
                "razorpay_signature": razorpay_signature,
                "activated_at": now,
                "updated_at": now,
            }
        },
    )

    # Cancel other pending checkouts for this coach
    await db[COL_SUBS].update_many(
        {
            "coach_id": coach_id,
            "id": {"$ne": sub["id"]},
            "status": CoachSubscriptionStatus.PENDING_PAYMENT.value,
        },
        {
            "$set": {
                "status": CoachSubscriptionStatus.CANCELLED.value,
                "updated_at": now,
            }
        },
    )

    # Activate coach account (approved coaches)
    await db[COL_COACHES].update_one(
        {"id": coach_id},
        {
            "$set": {
                "is_active": True,
                "subscription_status": CoachSubscriptionStatus.ACTIVE.value,
                "subscription_ends_at": ends_at,
                "subscription_grace_ends_at": grace_ends_at,
                "updated_at": now,
            }
        },
    )

    pay_doc = {
        "id": str(uuid.uuid4()),
        "coach_id": coach_id,
        "subscription_id": sub["id"],
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

    updated = await db[COL_SUBS].find_one({"id": sub["id"]})
    return updated


async def verify_payment(
    *,
    coach_id: str,
    body: CoachSubscriptionPaymentVerify,
    current_user: dict,
) -> Dict[str, Any]:
    from utils.razorpay_client import verify_razorpay_signature

    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    coach = await _get_coach(db, coach_id)
    await _assert_coach_access(db, coach, current_user)

    sub = await db[COL_SUBS].find_one({"id": body.subscription_id, "coach_id": coach_id})
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")

    if (
        sub.get("status") == CoachSubscriptionStatus.ACTIVE.value
        and sub.get("payment_status") == CoachSubscriptionPaymentStatus.PAID.value
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
        payment_status=CoachSubscriptionPaymentStatus.PAID.value,
        actor=_actor(current_user),
        note="Razorpay payment verified",
    )
    return {
        "message": "Subscription activated",
        "subscription": serialize_doc(updated),
    }


async def grant_subscription(
    *,
    coach_id: str,
    body: CoachSubscriptionGrantBody,
    current_user: dict,
) -> Dict[str, Any]:
    if not (_is_admin(current_user) or _is_bm(current_user)):
        raise HTTPException(status_code=403, detail="Admin only")
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    coach = await _get_coach(db, coach_id)
    await _assert_coach_access(db, coach, current_user)

    plan = await db[COL_PLANS].find_one({"id": body.plan_id})
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    now = datetime.utcnow()
    current_resp = await get_current_subscription(
        coach_id=coach_id, current_user=current_user
    )
    current = current_resp.get("subscription")
    stack_from = None
    if current and current.get("status") in {
        CoachSubscriptionStatus.ACTIVE.value,
        CoachSubscriptionStatus.GRACE.value,
    }:
        ends = current.get("ends_at")
        if isinstance(ends, str):
            try:
                stack_from = datetime.fromisoformat(ends.replace("Z", ""))
            except Exception:
                stack_from = None
        elif isinstance(ends, datetime):
            stack_from = ends

    period = _compute_period(
        duration_days=int(plan["duration_days"]),
        grace_period_days=int(plan.get("grace_period_days") or 0),
        start=now,
        stack_from=stack_from,
    )
    fee = float(plan.get("fee_inr") or 0)
    sub_id = str(uuid.uuid4())
    plan_snapshot = {
        "id": plan["id"],
        "name": plan.get("name"),
        "fee_inr": fee,
        "duration_days": plan.get("duration_days"),
        "grace_period_days": plan.get("grace_period_days"),
        "deactivate_on_grace_expiry": plan.get("deactivate_on_grace_expiry", True),
    }
    sub_doc = {
        "id": sub_id,
        "coach_id": coach_id,
        "plan_id": plan["id"],
        "plan_snapshot": plan_snapshot,
        "status": CoachSubscriptionStatus.PENDING_PAYMENT.value,
        "payment_status": CoachSubscriptionPaymentStatus.PENDING.value,
        "amount_paise": int(round(fee * 100)),
        "currency": "INR",
        "starts_at": period["starts_at"],
        "ends_at": period["ends_at"],
        "grace_ends_at": period["grace_ends_at"],
        "created_at": now,
        "updated_at": now,
        "created_by": current_user.get("id"),
        "action": "grant",
    }
    await db[COL_SUBS].insert_one(sub_doc)
    updated = await _activate_paid_subscription(
        db,
        sub_doc,
        razorpay_order_id=None,
        razorpay_payment_id=None,
        razorpay_signature=None,
        payment_status=CoachSubscriptionPaymentStatus.WAIVED.value,
        actor=_actor(current_user),
        note=body.note or "Admin granted subscription",
    )
    return {
        "message": "Subscription granted",
        "subscription": serialize_doc(updated),
    }


async def list_subscriptions_admin(
    *,
    current_user: dict,
    status: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 25,
) -> Dict[str, Any]:
    if not (_is_admin(current_user) or _is_bm(current_user)):
        raise HTTPException(status_code=403, detail="Admin only")
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    q: Dict[str, Any] = {}
    if status and status != "all":
        q["status"] = status

    if _is_bm(current_user) and not _is_admin(current_user):
        managed = await get_managed_branch_ids_for_user(db, current_user)
        coaches = await db[COL_COACHES].find(
            {
                "$or": [
                    {"branch_id": {"$in": managed}},
                    {"service_location_ids": {"$in": managed}},
                ]
            },
            {"id": 1},
        ).to_list(length=5000)
        coach_ids = [c["id"] for c in coaches]
        q["coach_id"] = {"$in": coach_ids}

    if search and search.strip():
        rx = {"$regex": search.strip(), "$options": "i"}
        matched = await db[COL_COACHES].find(
            {"$or": [{"full_name": rx}, {"email": rx}, {"id": rx}]},
            {"id": 1},
        ).to_list(length=200)
        ids = [c["id"] for c in matched]
        if "coach_id" in q and isinstance(q["coach_id"], dict):
            allowed = set(q["coach_id"].get("$in") or [])
            ids = [i for i in ids if i in allowed]
        q["coach_id"] = {"$in": ids}

    total = await db[COL_SUBS].count_documents(q)
    rows = (
        await db[COL_SUBS]
        .find(q)
        .sort([("created_at", -1)])
        .skip(max(0, skip))
        .limit(max(1, min(limit, 100)))
        .to_list(length=max(1, min(limit, 100)))
    )

    # Refresh statuses for listed rows
    out = []
    for row in rows:
        refreshed = await refresh_subscription_status(db, row)
        coach = await db[COL_COACHES].find_one({"id": refreshed.get("coach_id")})
        item = serialize_doc(refreshed) or {}
        item["coach_name"] = (coach or {}).get("full_name")
        item["coach_email"] = (coach or {}).get("email") or (
            (coach or {}).get("contact_info") or {}
        ).get("email")
        out.append(item)

    return {"subscriptions": out, "total": total, "skip": skip, "limit": limit}


async def refresh_all_statuses(*, current_user: dict) -> Dict[str, Any]:
    if not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin only")
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    rows = await db[COL_SUBS].find(
        {
            "status": {
                "$in": [
                    CoachSubscriptionStatus.ACTIVE.value,
                    CoachSubscriptionStatus.GRACE.value,
                ]
            }
        }
    ).to_list(length=5000)
    changed = 0
    for row in rows:
        before = row.get("status")
        after = await refresh_subscription_status(db, row)
        if after.get("status") != before:
            changed += 1
    return {"message": "Statuses refreshed", "checked": len(rows), "changed": changed}


async def assert_coach_subscription_allows_login(coach: dict) -> None:
    """
    Soft gate used by coach login.
    Only blocks coaches who have a subscription record that is expired past grace.
    Coaches with no subscription history remain unaffected (legacy / admin-created).
    """
    db = get_db()
    if db is None:
        return
    coach_id = coach.get("id")
    if not coach_id:
        return

    # Fast path from denormalized coach fields
    sub_status = str(coach.get("subscription_status") or "").lower()
    if sub_status == CoachSubscriptionStatus.EXPIRED.value:
        raise HTTPException(
            status_code=403,
            detail=(
                "Your coach subscription has expired. "
                "Please renew to continue accessing the coach dashboard."
            ),
        )

    latest = (
        await db[COL_SUBS]
        .find(
            {
                "coach_id": coach_id,
                "status": {
                    "$in": [
                        CoachSubscriptionStatus.ACTIVE.value,
                        CoachSubscriptionStatus.GRACE.value,
                        CoachSubscriptionStatus.EXPIRED.value,
                    ]
                },
            }
        )
        .sort([("created_at", -1)])
        .to_list(length=1)
    )
    if not latest:
        return
    refreshed = await refresh_subscription_status(db, latest[0])
    if refreshed.get("status") == CoachSubscriptionStatus.EXPIRED.value:
        raise HTTPException(
            status_code=403,
            detail=(
                "Your coach subscription has expired. "
                "Please renew to continue accessing the coach dashboard."
            ),
        )
