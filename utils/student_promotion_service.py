"""
M19-S01 Student Promotion CMS service.

Admin CRUD for student_promotions (banner, CTA, schedule, status).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.student_promotion_models import (
    PromotionCtaType,
    PromotionMediaType,
    PromotionStatus,
    StudentPromotionCreate,
    StudentPromotionUpdate,
    normalize_promotion_target,
    slugify_promotion,
)
from utils.database import get_db
from utils.helpers import serialize_doc

logger = logging.getLogger(__name__)

COL = "student_promotions"


async def ensure_student_promotion_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL].create_index("id", unique=True)
        await database[COL].create_index("slug", unique=True)
        await database[COL].create_index([("status", 1), ("priority", 1)])
        await database[COL].create_index([("start_at", 1), ("end_at", 1)])
        await database[COL].create_index([("updated_at", -1)])
    except Exception:
        logger.exception("Failed ensuring student promotion indexes")


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1]
        if "+" in text[10:]:
            text = text.split("+")[0]
        if len(text) == 16:
            text = text + ":00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _campaign_window_active(doc: dict, now: Optional[datetime] = None) -> bool:
    clock = now or datetime.utcnow()
    start = _parse_dt(doc.get("start_at"))
    end = _parse_dt(doc.get("end_at"))
    if start and clock < start:
        return False
    if end and clock > end:
        return False
    return True


def enrich_student_promotion(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    out = serialize_doc(doc)
    status = (out.get("status") or PromotionStatus.DRAFT.value).lower()
    out["status"] = status
    out["is_active"] = status == PromotionStatus.ACTIVE.value
    out["in_schedule"] = _campaign_window_active(out)
    out["is_live"] = out["is_active"] and out["in_schedule"]
    out["banner_media_type"] = (
        out.get("banner_media_type") or PromotionMediaType.NONE.value
    )
    out["cta_type"] = out.get("cta_type") or PromotionCtaType.NONE.value
    out["cta_label"] = out.get("cta_label") or None
    out["cta_url"] = out.get("cta_url") or None
    out["banner_url"] = out.get("banner_url") or None
    out["priority"] = int(out.get("priority") or 100)
    target = normalize_promotion_target(out.get("target"))
    out["target"] = target
    out["target_summary"] = {
        "mode": target["mode"],
        "student_count": len(target["student_ids"]),
        "branch_count": len(target["branch_ids"]),
        "course_count": len(target["course_ids"]),
        "group_count": len(target["group_ids"]),
    }
    return out


async def _unique_slug(db, base: str, *, exclude_id: Optional[str] = None) -> str:
    slug = slugify_promotion(base)
    candidate = slug
    n = 2
    while True:
        q: Dict[str, Any] = {"slug": candidate}
        if exclude_id:
            q["id"] = {"$ne": exclude_id}
        exists = await db[COL].find_one(q)
        if not exists:
            return candidate
        candidate = f"{slug}-{n}"
        n += 1
        if n > 50:
            return f"{slug}-{uuid.uuid4().hex[:6]}"


async def list_student_promotions(
    *,
    status: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    include_archived: bool = False,
    live_only: bool = False,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_student_promotion_indexes(db)

    q: Dict[str, Any] = {}
    if status and status != "all":
        q["status"] = status
    elif not include_archived:
        q["status"] = {"$ne": PromotionStatus.ARCHIVED.value}

    if search and search.strip():
        term = search.strip()
        q["$or"] = [
            {"title": {"$regex": term, "$options": "i"}},
            {"slug": {"$regex": term, "$options": "i"}},
            {"short_description": {"$regex": term, "$options": "i"}},
            {"cta_label": {"$regex": term, "$options": "i"}},
        ]

    skip = max(0, skip)
    limit = max(1, min(limit, 200))
    total = await db[COL].count_documents(q)
    rows = (
        await db[COL]
        .find(q)
        .sort([("priority", 1), ("updated_at", -1)])
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    promotions = [enrich_student_promotion(r) for r in rows]
    if live_only:
        promotions = [p for p in promotions if p and p.get("is_live")]
        total = len(promotions)
    return {
        "promotions": promotions,
        "total": total,
        "skip": skip,
        "limit": limit,
    }


async def get_student_promotion(promotion_id: str) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": promotion_id})
    if not doc:
        doc = await db[COL].find_one({"slug": promotion_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Promotion not found")
    return {"promotion": enrich_student_promotion(doc)}


async def create_student_promotion(
    body: StudentPromotionCreate,
    *,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_student_promotion_indexes(db)

    slug = await _unique_slug(db, body.slug or body.title)
    now = datetime.utcnow()
    status = body.status.value
    doc = {
        "id": str(uuid.uuid4()),
        "title": body.title,
        "slug": slug,
        "short_description": body.short_description,
        "description": body.description,
        "banner_url": body.banner_url,
        "banner_media_type": body.banner_media_type.value,
        "cta_label": body.cta_label,
        "cta_type": body.cta_type.value,
        "cta_url": body.cta_url
        if body.cta_type != PromotionCtaType.NONE
        else None,
        "start_at": body.start_at,
        "end_at": body.end_at,
        "status": status,
        "is_active": status == PromotionStatus.ACTIVE.value,
        "priority": body.priority,
        "target": normalize_promotion_target(
            body.target.model_dump(mode="json") if body.target is not None else None
        ),
        "created_by": (current_user or {}).get("id"),
        "created_at": now,
        "updated_at": now,
    }
    await db[COL].insert_one(doc)
    return {
        "message": "Promotion created",
        "promotion": enrich_student_promotion(doc),
    }


async def update_student_promotion(
    promotion_id: str,
    body: StudentPromotionUpdate,
    *,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": promotion_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Promotion not found")

    data = body.model_dump(exclude_unset=True)
    patch: Dict[str, Any] = {"updated_at": datetime.utcnow()}
    if current_user and current_user.get("id"):
        patch["updated_by"] = current_user["id"]

    if data.pop("clear_banner", None):
        patch["banner_url"] = None
        patch["banner_media_type"] = PromotionMediaType.NONE.value

    if data.pop("clear_cta", None):
        patch["cta_type"] = PromotionCtaType.NONE.value
        patch["cta_url"] = None
        patch["cta_label"] = None

    if data.pop("clear_dates", None):
        patch["start_at"] = None
        patch["end_at"] = None

    for key in (
        "title",
        "short_description",
        "description",
        "banner_url",
        "cta_label",
        "cta_url",
        "start_at",
        "end_at",
        "priority",
    ):
        if key in data:
            patch[key] = data[key]

    if "slug" in data and data["slug"]:
        patch["slug"] = await _unique_slug(
            db, data["slug"], exclude_id=promotion_id
        )

    if "banner_media_type" in data and data["banner_media_type"] is not None:
        mt = data["banner_media_type"]
        patch["banner_media_type"] = mt.value if hasattr(mt, "value") else str(mt)

    if "cta_type" in data and data["cta_type"] is not None:
        ct = data["cta_type"]
        ct_val = ct.value if hasattr(ct, "value") else str(ct)
        patch["cta_type"] = ct_val
        if ct_val == PromotionCtaType.NONE.value:
            patch["cta_url"] = None

    if "status" in data and data["status"] is not None:
        st = data["status"]
        st_val = st.value if hasattr(st, "value") else str(st)
        patch["status"] = st_val
        patch["is_active"] = st_val == PromotionStatus.ACTIVE.value
    elif "is_active" in data and data["is_active"] is not None:
        if data["is_active"]:
            patch["status"] = PromotionStatus.ACTIVE.value
            patch["is_active"] = True
        else:
            patch["status"] = PromotionStatus.INACTIVE.value
            patch["is_active"] = False

    if "target" in data and data["target"] is not None:
        patch["target"] = normalize_promotion_target(data["target"])

    # Validate CTA / dates against merged state
    merged = {**doc, **patch}
    cta_type = merged.get("cta_type") or PromotionCtaType.NONE.value
    if cta_type in (PromotionCtaType.URL.value, PromotionCtaType.PATH.value):
        if not merged.get("cta_url"):
            raise HTTPException(
                status_code=400, detail="cta_url required when cta_type is url or path"
            )
        if not merged.get("cta_label"):
            patch["cta_label"] = "Learn more"
    start = merged.get("start_at")
    end = merged.get("end_at")
    if start and end and str(end) < str(start):
        raise HTTPException(status_code=400, detail="end_at must be on or after start_at")

    await db[COL].update_one({"id": promotion_id}, {"$set": patch})
    updated = await db[COL].find_one({"id": promotion_id})
    return {
        "message": "Promotion updated",
        "promotion": enrich_student_promotion(updated),
    }


async def archive_student_promotion(
    promotion_id: str,
    *,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": promotion_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Promotion not found")
    patch = {
        "status": PromotionStatus.ARCHIVED.value,
        "is_active": False,
        "updated_at": datetime.utcnow(),
    }
    if current_user and current_user.get("id"):
        patch["updated_by"] = current_user["id"]
    await db[COL].update_one({"id": promotion_id}, {"$set": patch})
    updated = await db[COL].find_one({"id": promotion_id})
    return {
        "message": "Promotion archived",
        "promotion": enrich_student_promotion(updated),
    }
