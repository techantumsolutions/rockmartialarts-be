"""
M16-S01 Online Learning course catalogue service.

Collection: learning_courses — additive; does not touch physical `courses`.
Collection: online_learning_enrollments — records public student enrollments & payments.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.learning_course_models import (
    LEARNING_COURSE_STATUSES,
    CreateLearningEnrollmentOrderBody,
    LearningCourseCreate,
    LearningCourseEnrollmentCreate,
    LearningCourseStatus,
    LearningCourseUpdate,
    slugify,
)
from utils.database import get_db
from utils.helpers import serialize_doc

logger = logging.getLogger(__name__)

COL = "learning_courses"
COL_ENROLLMENTS = "online_learning_enrollments"


def _public(doc: Optional[dict]) -> Dict[str, Any]:
    return serialize_doc(doc) or {}


def _catalogue_view(doc: dict) -> Dict[str, Any]:
    """Public-safe fields only."""
    return {
        "id": doc.get("id"),
        "title": doc.get("title"),
        "slug": doc.get("slug"),
        "price": float(doc.get("price") or 0.0),
        "short_description": doc.get("short_description"),
        "description": doc.get("description"),
        "thumbnail_url": doc.get("thumbnail_url"),
        "trailer_url": doc.get("trailer_url"),
        "difficulty": doc.get("difficulty"),
        "language": doc.get("language"),
        "estimated_hours": doc.get("estimated_hours"),
        "sort_order": doc.get("sort_order", 100),
        "seo_title": doc.get("seo_title"),
        "seo_description": doc.get("seo_description"),
        "published_at": doc.get("published_at"),
        # Placeholders for S02 hierarchy counts (always 0 until levels exist)
        "levels_count": int(doc.get("levels_count") or 0),
        "lessons_count": int(doc.get("lessons_count") or 0),
    }


async def ensure_learning_course_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL].create_index("id", unique=True)
        await database[COL].create_index("slug", unique=True)
        await database[COL].create_index([("status", 1), ("sort_order", 1)])
        await database[COL].create_index([("created_at", -1)])
        await database[COL_ENROLLMENTS].create_index("id", unique=True)
        await database[COL_ENROLLMENTS].create_index([("course_id", 1), ("created_at", -1)])
        await database[COL_ENROLLMENTS].create_index([("email", 1)])
    except Exception:
        logger.exception("Failed ensuring learning course indexes")


async def _unique_slug(db, base: str, exclude_id: Optional[str] = None) -> str:
    slug = slugify(base)
    candidate = slug
    n = 2
    while True:
        q: Dict[str, Any] = {"slug": candidate}
        if exclude_id:
            q["id"] = {"$ne": exclude_id}
        exists = await db[COL].find_one(q, {"id": 1})
        if not exists:
            return candidate
        candidate = f"{slug}-{n}"[:120]
        n += 1


async def create_learning_course(
    body: LearningCourseCreate, *, current_user: dict
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_learning_course_indexes(db)

    now = datetime.utcnow()
    status = body.status.value if isinstance(body.status, LearningCourseStatus) else str(body.status)
    slug = await _unique_slug(db, body.slug or body.title)
    price = float(body.price or 0.0) if body.price is not None else 0.0
    doc = {
        "id": str(uuid.uuid4()),
        "title": body.title.strip(),
        "slug": slug,
        "price": price,
        "short_description": body.short_description,
        "description": body.description,
        "thumbnail_url": body.thumbnail_url,
        "trailer_url": body.trailer_url,
        "difficulty": body.difficulty,
        "language": body.language or "English",
        "estimated_hours": body.estimated_hours,
        "status": status,
        "sort_order": body.sort_order,
        "seo_title": body.seo_title,
        "seo_description": body.seo_description,
        "levels_count": 0,
        "lessons_count": 0,
        "published_at": now if status == LearningCourseStatus.PUBLISHED.value else None,
        "created_at": now,
        "updated_at": now,
        "created_by": current_user.get("id"),
        "updated_by": current_user.get("id"),
    }
    await db[COL].insert_one(doc)
    return {"message": "Learning course created", "course": _public(doc)}


async def update_learning_course(
    course_id: str, body: LearningCourseUpdate, *, current_user: dict
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": course_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Learning course not found")

    data = body.model_dump(exclude_unset=True)
    patch: Dict[str, Any] = {
        "updated_at": datetime.utcnow(),
        "updated_by": current_user.get("id"),
    }
    if "title" in data and data["title"] is not None:
        patch["title"] = data["title"]
    if "slug" in data and data["slug"] is not None:
        patch["slug"] = await _unique_slug(db, data["slug"], exclude_id=course_id)
    if "price" in data and data["price"] is not None:
        patch["price"] = float(data["price"])

    for key in (
        "short_description",
        "description",
        "thumbnail_url",
        "trailer_url",
        "difficulty",
        "language",
        "estimated_hours",
        "sort_order",
        "seo_title",
        "seo_description",
    ):
        if key in data:
            patch[key] = data[key]

    if "status" in data and data["status"] is not None:
        st = data["status"].value if hasattr(data["status"], "value") else str(data["status"])
        if st not in LEARNING_COURSE_STATUSES:
            raise HTTPException(status_code=400, detail=f"Invalid status: {st}")
        patch["status"] = st
        if st == LearningCourseStatus.PUBLISHED.value and not doc.get("published_at"):
            patch["published_at"] = datetime.utcnow()

    await db[COL].update_one({"id": course_id}, {"$set": patch})
    updated = await db[COL].find_one({"id": course_id})
    return {"message": "Learning course updated", "course": _public(updated)}


async def delete_learning_course(course_id: str, *, current_user: dict) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": course_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Learning course not found")
    # Soft-archive preferred over hard delete for catalogue integrity
    await db[COL].update_one(
        {"id": course_id},
        {
            "$set": {
                "status": LearningCourseStatus.ARCHIVED.value,
                "updated_at": datetime.utcnow(),
                "updated_by": current_user.get("id"),
            }
        },
    )
    updated = await db[COL].find_one({"id": course_id})
    return {"message": "Learning course archived", "course": _public(updated)}


async def get_learning_course_admin(course_id: str) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": course_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Learning course not found")
    return {"course": _public(doc)}


async def list_learning_courses_admin(
    *,
    status: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    clauses: List[Dict[str, Any]] = []
    st = (status or "").strip().lower()
    if st and st != "all":
        if st not in LEARNING_COURSE_STATUSES:
            raise HTTPException(status_code=400, detail=f"Invalid status: {st}")
        clauses.append({"status": st})

    q_search = (search or "").strip()
    if q_search:
        rx = {"$regex": re.escape(q_search), "$options": "i"}
        clauses.append(
            {
                "$or": [
                    {"title": rx},
                    {"slug": rx},
                    {"short_description": rx},
                    {"difficulty": rx},
                ]
            }
        )

    q: Dict[str, Any] = {"$and": clauses} if clauses else {}
    limit = min(max(limit, 1), 200)
    skip = max(skip, 0)
    total = await db[COL].count_documents(q)
    rows = (
        await db[COL]
        .find(q)
        .sort([("sort_order", 1), ("created_at", -1)])
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {
        "courses": [_public(r) for r in rows],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


async def list_learning_catalogue(
    *,
    search: Optional[str] = None,
    difficulty: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    clauses: List[Dict[str, Any]] = [
        {"status": LearningCourseStatus.PUBLISHED.value}
    ]
    q_search = (search or "").strip()
    if q_search:
        rx = {"$regex": re.escape(q_search), "$options": "i"}
        clauses.append(
            {
                "$or": [
                    {"title": rx},
                    {"short_description": rx},
                    {"description": rx},
                    {"difficulty": rx},
                ]
            }
        )
    diff = (difficulty or "").strip()
    if diff and diff.lower() != "all":
        clauses.append({"difficulty": {"$regex": f"^{re.escape(diff)}$", "$options": "i"}})

    q = {"$and": clauses}
    limit = min(max(limit, 1), 100)
    skip = max(skip, 0)
    total = await db[COL].count_documents(q)
    rows = (
        await db[COL]
        .find(q)
        .sort([("sort_order", 1), ("published_at", -1)])
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {
        "courses": [_catalogue_view(r) for r in rows],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


async def get_learning_catalogue_detail(slug_or_id: str) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    key = (slug_or_id or "").strip()
    if not key:
        raise HTTPException(status_code=404, detail="Course not found")
    doc = await db[COL].find_one(
        {
            "status": LearningCourseStatus.PUBLISHED.value,
            "$or": [{"slug": key}, {"id": key}],
        }
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Course not found")
    course = _catalogue_view(doc)
    try:
        from utils.learning_hierarchy_service import get_public_curriculum

        curr = await get_public_curriculum(doc["id"])
        course["curriculum"] = curr.get("curriculum") or []
        course["levels_count"] = curr.get("levels_count", course.get("levels_count") or 0)
        course["lessons_count"] = curr.get(
            "lessons_count", course.get("lessons_count") or 0
        )
    except Exception:
        course["curriculum"] = []
    return {"course": course}


# ==============================================================================
# Online Course Enrollments (Razorpay payment & admin listing)
# ==============================================================================


async def create_learning_enrollment_order(
    body: CreateLearningEnrollmentOrderBody,
    *,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    course = await db[COL].find_one({"$or": [{"id": body.course_id}, {"slug": body.course_id}]})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    price = float(course.get("price") or 0.0)
    if price <= 0:
        return {
            "fee_required": False,
            "amount_inr": 0.0,
            "amount_paise": 0,
            "currency": "INR",
        }

    from utils.razorpay_client import get_razorpay_client

    client = get_razorpay_client()
    key_id = os.getenv("RAZORPAY_KEY_ID", "").strip()
    amount_paise = int(round(price * 100))
    receipt = f"ole_{course['id'][:4]}_{secrets.token_hex(4)}"

    name = (body.student_name or "")[:80]
    email = (body.email or "")[:80]
    phone = (body.phone or "")[:32]

    order_payload = {
        "amount": amount_paise,
        "currency": "INR",
        "receipt": receipt,
        "notes": {
            "type": "online_learning_enrollment",
            "course_id": course["id"],
            "course_title": str(course.get("title", ""))[:80],
            "student_name": name,
            "email": email,
            "phone": phone,
        },
    }

    try:
        rp_order = client.order.create(data=order_payload)
        return {
            "fee_required": True,
            "order_id": rp_order["id"],
            "amount_paise": amount_paise,
            "amount_inr": price,
            "currency": "INR",
            "key_id": key_id,
        }
    except Exception as e:
        logger.exception("Failed to create Razorpay order for online learning enrollment: %s", e)
        raise HTTPException(
            status_code=400,
            detail=f"Failed to create payment order: {str(e)}",
        )


async def create_learning_enrollment(
    body: LearningCourseEnrollmentCreate,
    *,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_learning_course_indexes(db)

    course = await db[COL].find_one({"$or": [{"id": body.course_id}, {"slug": body.course_id}]})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    course_id = course["id"]
    course_title = course.get("title", "Online Course")
    course_slug = course.get("slug", "")
    price = float(course.get("price") or 0.0)

    now = datetime.utcnow()
    payment_status = "not_required"
    amount_paise = 0
    paid_at = None

    if price > 0:
        if not body.razorpay_order_id or not body.razorpay_payment_id or not body.razorpay_signature:
            raise HTTPException(
                status_code=400,
                detail=f"Payment of course fee (₹{price:.2f}) is required to complete enrollment.",
            )
        from utils.razorpay_client import verify_razorpay_signature

        valid_sig = verify_razorpay_signature(
            body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature
        )
        if not valid_sig:
            raise HTTPException(
                status_code=400,
                detail="Invalid Razorpay payment signature.",
            )
        payment_status = "paid"
        amount_paise = int(round(price * 100))
        paid_at = now
    else:
        payment_status = "free"

    doc_id = str(uuid.uuid4())
    enrollment_doc = {
        "id": doc_id,
        "course_id": course_id,
        "course_title": course_title,
        "course_slug": course_slug,
        "student_name": body.student_name.strip(),
        "email": body.email.strip().lower(),
        "phone": body.phone.strip(),
        "city": (body.city or "").strip() or None,
        "state": (body.state or "").strip() or None,
        "address": (body.address or "").strip() or None,
        "pincode": (body.pincode or "").strip() or None,
        "skill_level": (body.skill_level or "").strip() or None,
        "learning_goals": (body.learning_goals or "").strip() or None,
        "notes": (body.notes or "").strip() or None,
        "amount_paid": price,
        "amount_paise": amount_paise,
        "currency": "INR",
        "payment_status": payment_status,
        "razorpay_order_id": body.razorpay_order_id,
        "razorpay_payment_id": body.razorpay_payment_id,
        "razorpay_signature": body.razorpay_signature,
        "paid_at": paid_at,
        "created_at": now,
        "updated_at": now,
    }

    await db[COL_ENROLLMENTS].insert_one(enrollment_doc)
    return {
        "message": "Enrollment submitted successfully",
        "enrollment": serialize_doc(enrollment_doc),
    }


async def list_learning_enrollments_admin(
    *,
    course_id: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    clauses: List[Dict[str, Any]] = []
    if course_id and course_id != "all":
        clauses.append({"course_id": course_id})

    q_search = (search or "").strip()
    if q_search:
        rx = {"$regex": re.escape(q_search), "$options": "i"}
        clauses.append(
            {
                "$or": [
                    {"student_name": rx},
                    {"email": rx},
                    {"phone": rx},
                    {"city": rx},
                    {"course_title": rx},
                    {"razorpay_payment_id": rx},
                ]
            }
        )

    q: Dict[str, Any] = {"$and": clauses} if clauses else {}
    limit = min(max(limit, 1), 200)
    skip = max(skip, 0)
    total = await db[COL_ENROLLMENTS].count_documents(q)
    rows = (
        await db[COL_ENROLLMENTS]
        .find(q)
        .sort([("created_at", -1)])
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {
        "enrollments": [serialize_doc(r) for r in rows],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


async def get_learning_enrollment_stats() -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    total_count = await db[COL_ENROLLMENTS].count_documents({})
    paid_count = await db[COL_ENROLLMENTS].count_documents({"payment_status": "paid"})

    pipeline = [
        {"$match": {"payment_status": "paid"}},
        {"$group": {"_id": None, "total_revenue": {"$sum": "$amount_paid"}}},
    ]
    rev_res = await db[COL_ENROLLMENTS].aggregate(pipeline).to_list(length=1)
    total_revenue = float(rev_res[0]["total_revenue"]) if rev_res else 0.0

    return {
        "total_enrollments": total_count,
        "paid_enrollments": paid_count,
        "total_revenue": total_revenue,
    }
