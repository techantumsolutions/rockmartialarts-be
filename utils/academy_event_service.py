"""
M17 Academy Event service (S01 CMS + S02 public landing).

Collection: academy_events — additive; does not touch legacy `events` or camp_events.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import HTTPException

from models.academy_event_models import (
    ACADEMY_EVENT_STATUSES,
    ACADEMY_EVENT_TYPES,
    AcademyEventCreate,
    AcademyEventStatus,
    AcademyEventType,
    AcademyEventUpdate,
    normalize_reminder_offsets,
    slugify,
)
from models.academy_event_registration_models import normalize_registration_fields
from utils.database import get_db
from utils.helpers import serialize_doc

logger = logging.getLogger(__name__)

COL = "academy_events"
COL_BRANCHES = "branches"


def _public(doc: Optional[dict]) -> Dict[str, Any]:
    return serialize_doc(doc) or {}


def _seats_remaining(doc: dict) -> Optional[int]:
    cap = doc.get("capacity")
    if cap is None:
        return None
    try:
        capacity = int(cap)
    except (TypeError, ValueError):
        return None
    taken = int(doc.get("registrations_count") or 0)
    return max(0, capacity - taken)


def _public_landing_view(doc: dict, *, branch_name=None, branch_code=None) -> Dict[str, Any]:
    """Public-safe fields for catalogue / landing pages."""
    seats = _seats_remaining(doc)
    registration_enabled = bool(doc.get("registration_enabled", True))
    is_full = seats is not None and seats <= 0
    return {
        "id": doc.get("id"),
        "title": doc.get("title"),
        "slug": doc.get("slug"),
        "event_type": doc.get("event_type"),
        "short_description": doc.get("short_description"),
        "description": doc.get("description"),
        "venue": doc.get("venue"),
        "venue_address": doc.get("venue_address"),
        "start_at": doc.get("start_at"),
        "end_at": doc.get("end_at"),
        "branch_id": doc.get("branch_id"),
        "branch_name": branch_name,
        "branch_code": branch_code,
        "fee_inr": float(doc.get("fee_inr") or 0),
        "capacity": doc.get("capacity"),
        "registrations_count": int(doc.get("registrations_count") or 0),
        "seats_remaining": seats,
        "is_full": is_full,
        "thumbnail_url": doc.get("thumbnail_url"),
        "status": doc.get("status"),
        "sort_order": doc.get("sort_order", 100),
        "seo_title": doc.get("seo_title"),
        "seo_description": doc.get("seo_description"),
        "registration_enabled": registration_enabled,
        "registration_open": registration_enabled and not is_full,
        "registration_fields": normalize_registration_fields(
            doc.get("registration_fields")
        ),
        "published_at": doc.get("published_at"),
    }


async def ensure_academy_event_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL].create_index("id", unique=True)
        await database[COL].create_index("slug", unique=True)
        await database[COL].create_index([("status", 1), ("start_at", 1)])
        await database[COL].create_index([("event_type", 1), ("status", 1)])
        await database[COL].create_index([("branch_id", 1), ("start_at", 1)])
        await database[COL].create_index([("sort_order", 1), ("start_at", 1)])
    except Exception:
        logger.exception("Failed ensuring academy event indexes")


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


async def _validate_branch(db, branch_id: Optional[str]) -> Optional[str]:
    if not branch_id:
        return None
    branch = await db[COL_BRANCHES].find_one({"id": branch_id})
    if not branch:
        raise HTTPException(status_code=400, detail="Branch not found")
    return branch_id


async def _enrich(db, doc: dict) -> Dict[str, Any]:
    out = _public(doc)
    out["registration_fields"] = normalize_registration_fields(
        doc.get("registration_fields")
    )
    out["reminders_enabled"] = doc.get("reminders_enabled", True) is not False
    out["reminder_offsets_hours"] = normalize_reminder_offsets(
        doc.get("reminder_offsets_hours")
    )
    bid = doc.get("branch_id")
    if bid:
        br = await db[COL_BRANCHES].find_one({"id": bid})
        if br:
            out["branch_name"] = br.get("name") or br.get("branch_name")
            out["branch_code"] = br.get("code") or br.get("branch_code")
    return out


async def list_academy_events(
    *,
    status: Optional[str] = None,
    event_type: Optional[str] = None,
    branch_id: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    q: Dict[str, Any] = {}
    if status and status != "all":
        if status not in ACADEMY_EVENT_STATUSES:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
        q["status"] = status
    if event_type and event_type != "all":
        if event_type not in ACADEMY_EVENT_TYPES:
            raise HTTPException(status_code=400, detail=f"Invalid event_type: {event_type}")
        q["event_type"] = event_type
    if branch_id and branch_id != "all":
        q["branch_id"] = branch_id
    if search and search.strip():
        s = search.strip()
        q["$or"] = [
            {"title": {"$regex": s, "$options": "i"}},
            {"slug": {"$regex": s, "$options": "i"}},
            {"venue": {"$regex": s, "$options": "i"}},
        ]

    total = await db[COL].count_documents(q)
    rows = (
        await db[COL]
        .find(q)
        .sort([("start_at", 1), ("sort_order", 1)])
        .skip(max(0, skip))
        .limit(max(1, min(limit, 200)))
        .to_list(length=max(1, min(limit, 200)))
    )
    out = []
    for row in rows:
        out.append(await _enrich(db, row))
    return {"events": out, "total": total, "skip": skip, "limit": limit}


async def get_academy_event(event_id: str) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": event_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Event not found")
    return {"event": await _enrich(db, doc)}


async def list_academy_events_public(
    *,
    event_type: Optional[str] = None,
    branch_id: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> Dict[str, Any]:
    """Published events only — public catalogue (M17-S02)."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    clauses: list = [{"status": AcademyEventStatus.PUBLISHED.value}]
    if event_type and event_type != "all":
        if event_type not in ACADEMY_EVENT_TYPES:
            raise HTTPException(status_code=400, detail=f"Invalid event_type: {event_type}")
        clauses.append({"event_type": event_type})
    if branch_id and branch_id != "all":
        clauses.append({"branch_id": branch_id})
    q_search = (search or "").strip()
    if q_search:
        rx = {"$regex": re.escape(q_search), "$options": "i"}
        clauses.append(
            {
                "$or": [
                    {"title": rx},
                    {"slug": rx},
                    {"short_description": rx},
                    {"venue": rx},
                    {"description": rx},
                ]
            }
        )

    q = {"$and": clauses}
    limit = min(max(limit, 1), 100)
    skip = max(skip, 0)
    total = await db[COL].count_documents(q)
    rows = (
        await db[COL]
        .find(q)
        .sort([("start_at", 1), ("sort_order", 1)])
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    out = []
    for row in rows:
        branch_name = branch_code = None
        bid = row.get("branch_id")
        if bid:
            br = await db[COL_BRANCHES].find_one({"id": bid})
            if br:
                branch_name = br.get("name") or br.get("branch_name")
                branch_code = br.get("code") or br.get("branch_code")
        out.append(
            _public_landing_view(row, branch_name=branch_name, branch_code=branch_code)
        )
    return {"events": out, "total": total, "skip": skip, "limit": limit}


async def get_academy_event_public(slug_or_id: str) -> Dict[str, Any]:
    """Published event by slug or id — public landing (M17-S02)."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    key = (slug_or_id or "").strip()
    if not key:
        raise HTTPException(status_code=404, detail="Event not found")
    doc = await db[COL].find_one(
        {
            "status": AcademyEventStatus.PUBLISHED.value,
            "$or": [{"slug": key}, {"id": key}],
        }
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Event not found")
    branch_name = branch_code = None
    bid = doc.get("branch_id")
    if bid:
        br = await db[COL_BRANCHES].find_one({"id": bid})
        if br:
            branch_name = br.get("name") or br.get("branch_name")
            branch_code = br.get("code") or br.get("branch_code")
    return {
        "event": _public_landing_view(
            doc, branch_name=branch_name, branch_code=branch_code
        )
    }


async def create_academy_event(
    body: AcademyEventCreate, *, current_user: dict
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_academy_event_indexes(db)

    branch_id = await _validate_branch(db, body.branch_id)
    now = datetime.utcnow()
    status = (
        body.status.value
        if isinstance(body.status, AcademyEventStatus)
        else str(body.status)
    )
    event_type = (
        body.event_type.value
        if isinstance(body.event_type, AcademyEventType)
        else str(body.event_type)
    )
    slug = await _unique_slug(db, body.slug or body.title)

    doc = {
        "id": str(uuid.uuid4()),
        "title": body.title.strip(),
        "slug": slug,
        "event_type": event_type,
        "short_description": body.short_description,
        "description": body.description,
        "venue": body.venue,
        "venue_address": body.venue_address,
        "start_at": body.start_at,
        "end_at": body.end_at,
        "branch_id": branch_id,
        "fee_inr": float(body.fee_inr or 0),
        "capacity": body.capacity,
        "thumbnail_url": body.thumbnail_url,
        "status": status,
        "sort_order": int(body.sort_order),
        "seo_title": body.seo_title,
        "seo_description": body.seo_description,
        "registration_enabled": bool(body.registration_enabled),
        "registration_fields": normalize_registration_fields(
            body.registration_fields
        ),
        "reminders_enabled": bool(getattr(body, "reminders_enabled", True)),
        "reminder_offsets_hours": normalize_reminder_offsets(
            getattr(body, "reminder_offsets_hours", None)
        ),
        "registrations_count": 0,
        "published_at": now if status == AcademyEventStatus.PUBLISHED.value else None,
        "created_at": now,
        "updated_at": now,
        "created_by": current_user.get("id"),
        "updated_by": current_user.get("id"),
    }
    await db[COL].insert_one(doc)
    return {"message": "Event created", "event": await _enrich(db, doc)}


async def update_academy_event(
    event_id: str, body: AcademyEventUpdate, *, current_user: dict
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    existing = await db[COL].find_one({"id": event_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Event not found")

    data = body.model_dump(exclude_unset=True)
    patch: Dict[str, Any] = {
        "updated_at": datetime.utcnow(),
        "updated_by": current_user.get("id"),
    }

    for key in (
        "title",
        "short_description",
        "description",
        "venue",
        "venue_address",
        "start_at",
        "end_at",
        "thumbnail_url",
        "seo_title",
        "seo_description",
        "registration_enabled",
        "sort_order",
        "fee_inr",
        "reminders_enabled",
    ):
        if key in data:
            patch[key] = data[key]

    if "registration_fields" in data and data["registration_fields"] is not None:
        patch["registration_fields"] = normalize_registration_fields(
            data["registration_fields"]
        )

    if "reminder_offsets_hours" in data and data["reminder_offsets_hours"] is not None:
        patch["reminder_offsets_hours"] = normalize_reminder_offsets(
            data["reminder_offsets_hours"]
        )

    if "clear_capacity" in data and data["clear_capacity"]:
        patch["capacity"] = None
    elif "capacity" in data:
        patch["capacity"] = data["capacity"]

    if "branch_id" in data:
        patch["branch_id"] = await _validate_branch(db, data["branch_id"])

    if "slug" in data and data["slug"] is not None:
        patch["slug"] = await _unique_slug(db, data["slug"], exclude_id=event_id)

    if "event_type" in data and data["event_type"] is not None:
        et = data["event_type"]
        et = et.value if hasattr(et, "value") else str(et)
        if et not in ACADEMY_EVENT_TYPES:
            raise HTTPException(status_code=400, detail=f"Invalid event_type: {et}")
        patch["event_type"] = et

    if "status" in data and data["status"] is not None:
        st = data["status"]
        st = st.value if hasattr(st, "value") else str(st)
        if st not in ACADEMY_EVENT_STATUSES:
            raise HTTPException(status_code=400, detail=f"Invalid status: {st}")
        patch["status"] = st
        if (
            st == AcademyEventStatus.PUBLISHED.value
            and not existing.get("published_at")
        ):
            patch["published_at"] = datetime.utcnow()

    start = patch.get("start_at", existing.get("start_at"))
    end = patch.get("end_at", existing.get("end_at"))
    if start and end and str(end) < str(start):
        raise HTTPException(status_code=400, detail="end_at must be on or after start_at")

    await db[COL].update_one({"id": event_id}, {"$set": patch})
    updated = await db[COL].find_one({"id": event_id})
    return {"message": "Event updated", "event": await _enrich(db, updated)}


async def archive_academy_event(
    event_id: str, *, current_user: dict
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    existing = await db[COL].find_one({"id": event_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Event not found")
    await db[COL].update_one(
        {"id": event_id},
        {
            "$set": {
                "status": AcademyEventStatus.ARCHIVED.value,
                "updated_at": datetime.utcnow(),
                "updated_by": current_user.get("id"),
            }
        },
    )
    updated = await db[COL].find_one({"id": event_id})
    return {"message": "Event archived", "event": await _enrich(db, updated)}
