"""
M19-S03 Student-facing eligible promotions + engagement events.

- Eligible = status active + in campaign window + targeting match
- Events: view / cta / dismiss stored in student_promotion_events
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from fastapi import HTTPException

from models.student_promotion_models import (
    PromotionEventType,
    PromotionStatus,
)
from utils.database import get_db
from utils.promotion_eligibility import is_student_eligible_for_target
from utils.student_promotion_service import (
    COL,
    enrich_student_promotion,
    ensure_student_promotion_indexes,
)

logger = logging.getLogger(__name__)

EVENTS_COL = "student_promotion_events"

_STUDENT_PUBLIC_KEYS = (
    "id",
    "title",
    "slug",
    "short_description",
    "description",
    "banner_url",
    "banner_media_type",
    "cta_label",
    "cta_type",
    "cta_url",
    "start_at",
    "end_at",
    "priority",
    "status",
    "is_active",
    "in_schedule",
    "is_live",
)


async def ensure_promotion_event_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[EVENTS_COL].create_index("id", unique=True)
        await database[EVENTS_COL].create_index(
            [("student_id", 1), ("promotion_id", 1), ("event_type", 1)]
        )
        await database[EVENTS_COL].create_index([("created_at", -1)])
        await database[EVENTS_COL].create_index(
            [("promotion_id", 1), ("event_type", 1)]
        )
    except Exception:
        logger.exception("Failed ensuring promotion event indexes")


def _public_promotion(doc: dict) -> dict:
    return {k: doc.get(k) for k in _STUDENT_PUBLIC_KEYS}


async def _dismissed_promotion_ids(db, student_id: str) -> Set[str]:
    ids: Set[str] = set()
    cursor = db[EVENTS_COL].find(
        {
            "student_id": student_id,
            "event_type": PromotionEventType.DISMISS.value,
        },
        {"promotion_id": 1},
    )
    async for row in cursor:
        pid = row.get("promotion_id")
        if pid:
            ids.add(str(pid))
    return ids


async def list_eligible_promotions_for_student(
    student_id: str,
    *,
    exclude_dismissed: bool = True,
    limit: int = 5,
) -> Dict[str, Any]:
    sid = str(student_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="student_id required")

    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    await ensure_student_promotion_indexes(db)
    await ensure_promotion_event_indexes(db)

    student = await db.users.find_one({"id": sid, "role": "student"})
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    rows = (
        await db[COL]
        .find({"status": PromotionStatus.ACTIVE.value})
        .sort([("priority", 1), ("updated_at", -1)])
        .to_list(length=200)
    )

    dismissed: Set[str] = set()
    if exclude_dismissed:
        dismissed = await _dismissed_promotion_ids(db, sid)

    eligible: List[dict] = []
    for row in rows:
        enriched = enrich_student_promotion(row)
        if not enriched or not enriched.get("is_live"):
            continue
        pid = enriched.get("id")
        if not pid:
            continue
        if exclude_dismissed and pid in dismissed:
            continue
        ok = await is_student_eligible_for_target(
            sid, enriched.get("target"), db=db
        )
        if not ok:
            continue
        eligible.append(_public_promotion(enriched))
        if len(eligible) >= max(1, min(int(limit or 5), 20)):
            break

    return {
        "promotions": eligible,
        "total": len(eligible),
        "student_id": sid,
        "exclude_dismissed": exclude_dismissed,
    }


async def record_promotion_event(
    *,
    student_id: str,
    promotion_id: str,
    event_type: str,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    sid = str(student_id or "").strip()
    pid = str(promotion_id or "").strip()
    et = str(event_type or "").strip().lower()
    if not sid:
        raise HTTPException(status_code=400, detail="student_id required")
    if not pid:
        raise HTTPException(status_code=400, detail="promotion_id required")
    if et not in (
        PromotionEventType.VIEW.value,
        PromotionEventType.CTA.value,
        PromotionEventType.DISMISS.value,
    ):
        raise HTTPException(status_code=400, detail="invalid event_type")

    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    await ensure_promotion_event_indexes(db)

    student = await db.users.find_one({"id": sid, "role": "student"})
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    promo = await db[COL].find_one({"id": pid})
    if not promo:
        raise HTTPException(status_code=404, detail="Promotion not found")

    enriched = enrich_student_promotion(promo)
    # Allow recording CTA/dismiss even if just went inactive; views should be live
    if et == PromotionEventType.VIEW.value:
        if not enriched or not enriched.get("is_live"):
            raise HTTPException(
                status_code=400,
                detail="Promotion is not currently live",
            )
        eligible = await is_student_eligible_for_target(
            sid, (enriched or {}).get("target"), db=db
        )
        if not eligible:
            raise HTTPException(status_code=403, detail="Not eligible for this promotion")

    # Deduplicate rapid duplicate views within same minute (optional soft dedupe)
    if et == PromotionEventType.VIEW.value:
        recent = await db[EVENTS_COL].find_one(
            {
                "student_id": sid,
                "promotion_id": pid,
                "event_type": et,
                "created_at": {
                    "$gte": datetime.utcnow().replace(second=0, microsecond=0)
                },
            }
        )
        if recent:
            return {
                "message": "View already recorded",
                "event": {
                    "id": recent.get("id"),
                    "promotion_id": pid,
                    "event_type": et,
                    "deduped": True,
                },
            }

    doc = {
        "id": str(uuid.uuid4()),
        "promotion_id": pid,
        "student_id": sid,
        "event_type": et,
        "meta": meta if isinstance(meta, dict) else None,
        "created_at": datetime.utcnow(),
    }
    await db[EVENTS_COL].insert_one(doc)
    return {
        "message": "Event recorded",
        "event": {
            "id": doc["id"],
            "promotion_id": pid,
            "student_id": sid,
            "event_type": et,
            "created_at": doc["created_at"].isoformat() + "Z",
            "deduped": False,
        },
    }
