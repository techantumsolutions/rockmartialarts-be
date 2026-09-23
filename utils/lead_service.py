"""
M15-S01 unified lead capture service.

Duplicate policy (defined):
1) Idempotent: same source_ref_type + source_ref_id → return existing lead.
2) Soft-merge: same phone_canonical + source_type within DUPLICATE_WINDOW_HOURS
   (default 24h), and status not in {converted, lost} → update contact fields,
   bump capture_count, set last_captured_at (no second row).
3) Otherwise insert a new lead (every distinct source remains traceable).

Does not replace training_requests / demo_bookings / camp collections.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from models.lead_models import LEAD_FOLLOW_UP_ACTIONS, LEAD_STATUSES, LeadCreate, LeadStatus
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.indian_phone import canonical_indian_phone
from utils.student_status_service import get_managed_branch_ids_for_user

logger = logging.getLogger(__name__)

COL = "leads"
COL_FOLLOW_UPS = "lead_follow_up_events"
_LEAD_EMAIL_PLACEHOLDER = (
    os.getenv("LEAD_EMAIL_PLACEHOLDER") or "website-popup@example.com"
).strip().lower()

# Known source_type values (free-text source still accepted for legacy)
KNOWN_SOURCE_TYPES = (
    "website_popup",
    "registration_step1",
    "registration_payment",
    "training_home",
    "training_school",
    "training_college",
    "training_corporate",
    "training_residential",
    "demo_booking",
    "camp_registration",
    "callback",
    "event",
    "manual",
    "partner_landing",
    "other",
)

SOURCE_ALIASES = {
    "website_popup": "website_popup",
    "popup": "website_popup",
    "homepage_popup": "website_popup",
    "registration_step1": "registration_step1",
    "registration": "registration_step1",
    "registration_payment": "registration_payment",
    "home": "training_home",
    "home_training": "training_home",
    "training_home": "training_home",
    "school": "training_school",
    "school_training": "training_school",
    "training_school": "training_school",
    "college": "training_college",
    "college_training": "training_college",
    "training_college": "training_college",
    "corporate": "training_corporate",
    "corporate_training": "training_corporate",
    "training_corporate": "training_corporate",
    "residential": "training_residential",
    "residential_training": "training_residential",
    "training_residential": "training_residential",
    "demo": "demo_booking",
    "demo_booking": "demo_booking",
    "book_demo": "demo_booking",
    "camp": "camp_registration",
    "camp_registration": "camp_registration",
    "callback": "callback",
    "event": "event",
    "manual": "manual",
    "partner_landing": "partner_landing",
    "partner": "partner_landing",
    "collaboration_partner": "partner_landing",
    "partner_page": "partner_landing",
    "partner_contact": "partner_landing",
}


def _duplicate_window_hours() -> int:
    raw = os.getenv("LEAD_DUPLICATE_WINDOW_HOURS", "24")
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = 24
    return max(1, min(n, 24 * 30))


def normalize_source_type(
    source: Optional[str] = None, source_type: Optional[str] = None
) -> str:
    raw = (source_type or source or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not raw:
        return "other"
    if raw in SOURCE_ALIASES:
        return SOURCE_ALIASES[raw]
    if raw in KNOWN_SOURCE_TYPES:
        return raw
    if raw.startswith("training_"):
        return raw if raw in KNOWN_SOURCE_TYPES else "other"
    return "other"


def _role(user: Optional[dict]) -> str:
    return str((user or {}).get("role") or "").lower()


async def ensure_lead_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL].create_index("id", unique=True)
        await database[COL].create_index([("created_at", -1)])
        await database[COL].create_index([("status", 1), ("created_at", -1)])
        await database[COL].create_index("phone_canonical")
        await database[COL].create_index(
            [("phone_canonical", 1), ("source_type", 1), ("created_at", -1)]
        )
        await database[COL].create_index(
            [("source_ref_type", 1), ("source_ref_id", 1)]
        )
        await database[COL].create_index("branch_id")
        await database[COL].create_index("source_type")
        # M15-S03 pipeline
        await database[COL].create_index([("next_follow_up_at", 1)])
        await database[COL_FOLLOW_UPS].create_index("id", unique=True)
        await database[COL_FOLLOW_UPS].create_index([("lead_id", 1), ("created_at", -1)])
    except Exception:
        logger.exception("Failed ensuring lead indexes")


def _normalize_lead_status(raw: Any) -> LeadStatus:
    s = (str(raw).strip().lower() if raw is not None else "")
    if s in LEAD_STATUSES:
        return s  # type: ignore[return-value]
    return "new"


def lead_to_response_dict(ser: Dict[str, Any], fallback_now: Optional[datetime] = None) -> Dict[str, Any]:
    now = fallback_now or datetime.utcnow()
    created = ser.get("created_at", now)
    return {
        "id": ser["id"],
        "name": ser.get("name", "") or "",
        "email": ser.get("email") or "",
        "phone": ser.get("phone", "") or "",
        "course": ser.get("course", "") or "",
        "source": ser.get("source"),
        "source_type": ser.get("source_type") or normalize_source_type(ser.get("source")),
        "source_ref_id": ser.get("source_ref_id"),
        "source_ref_type": ser.get("source_ref_type"),
        "branch_id": ser.get("branch_id"),
        "branch_name": ser.get("branch_name"),
        "message": ser.get("message"),
        "status": _normalize_lead_status(ser.get("status")),
        "phone_canonical": ser.get("phone_canonical"),
        "capture_count": int(ser.get("capture_count") or 1),
        "last_captured_at": ser.get("last_captured_at") or created,
        "duplicate_merged": bool(ser.get("duplicate_merged")),
        "next_follow_up_at": ser.get("next_follow_up_at"),
        "last_follow_up_at": ser.get("last_follow_up_at"),
        "last_follow_up_note": ser.get("last_follow_up_note"),
        "coach_assignment_id": ser.get("coach_assignment_id"),
        "assigned_coach_id": ser.get("assigned_coach_id"),
        "assigned_coach_name": ser.get("assigned_coach_name"),
        "coach_assignment_status": ser.get("coach_assignment_status"),
        "next_session_booking_id": ser.get("next_session_booking_id"),
        "next_session_at": ser.get("next_session_at"),
        "session_booking_status": ser.get("session_booking_status"),
        "created_at": created,
    }


async def _bm_branch_filter(db, current_user: Optional[dict]) -> Optional[Dict[str, Any]]:
    if not current_user:
        return None
    role = _role(current_user)
    if role in {"superadmin", "super_admin", "coach_admin", "coachadmin"}:
        return None
    if role not in {"branch_manager", "branch_admin", "branchmanager"}:
        return None
    managed = await get_managed_branch_ids_for_user(db, current_user)
    if not managed:
        return {"branch_id": {"$in": []}}
    return {
        "$or": [
            {"branch_id": {"$in": managed}},
            {"branch_id": {"$exists": False}},
            {"branch_id": None},
            {"branch_id": ""},
        ]
    }


def _build_lead_doc(
    *,
    name: str,
    phone: str,
    email: str = "",
    course: str = "",
    source: Optional[str] = None,
    source_type: Optional[str] = None,
    source_ref_id: Optional[str] = None,
    source_ref_type: Optional[str] = None,
    branch_id: Optional[str] = None,
    branch_name: Optional[str] = None,
    message: Optional[str] = None,
) -> Dict[str, Any]:
    now = datetime.utcnow()
    email_raw = (email or "").strip().lower()
    if email_raw == _LEAD_EMAIL_PLACEHOLDER:
        email_raw = ""
    phone_raw = (phone or "").strip()
    phone_c = canonical_indian_phone(phone_raw) or re.sub(r"\D+", "", phone_raw) or None
    st = normalize_source_type(source, source_type)
    src_label = (source or "").strip() or st
    msg = (message or "").strip() or None
    return {
        "id": str(uuid.uuid4()),
        "name": (name or "").strip() or "Visitor",
        "email": email_raw,
        "phone": phone_raw,
        "phone_canonical": phone_c,
        "course": (course or "").strip(),
        "source": src_label,
        "source_type": st,
        "source_ref_id": (source_ref_id or "").strip() or None,
        "source_ref_type": (source_ref_type or "").strip() or None,
        "branch_id": (branch_id or "").strip() or None,
        "branch_name": (branch_name or "").strip() or None,
        "message": msg,
        "status": "new",
        "capture_count": 1,
        "last_captured_at": now,
        "duplicate_merged": False,
        "created_at": now,
        "updated_at": now,
    }


async def create_or_merge_lead(
    *,
    name: str,
    phone: str,
    email: str = "",
    course: str = "",
    source: Optional[str] = None,
    source_type: Optional[str] = None,
    source_ref_id: Optional[str] = None,
    source_ref_type: Optional[str] = None,
    branch_id: Optional[str] = None,
    branch_name: Optional[str] = None,
    message: Optional[str] = None,
) -> Tuple[Dict[str, Any], bool]:
    """
    Returns (lead_doc_serialized, was_merged).
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database not initialized")
    await ensure_lead_indexes(db)

    draft = _build_lead_doc(
        name=name,
        phone=phone,
        email=email,
        course=course,
        source=source,
        source_type=source_type,
        source_ref_id=source_ref_id,
        source_ref_type=source_ref_type,
        branch_id=branch_id,
        branch_name=branch_name,
        message=message,
    )
    now = datetime.utcnow()

    # 1) Idempotent by source reference
    ref_type = draft.get("source_ref_type")
    ref_id = draft.get("source_ref_id")
    if ref_type and ref_id:
        existing = await db[COL].find_one(
            {"source_ref_type": ref_type, "source_ref_id": ref_id}
        )
        if existing:
            patch: Dict[str, Any] = {
                "updated_at": now,
                "last_captured_at": now,
                "capture_count": int(existing.get("capture_count") or 1) + 1,
                "duplicate_merged": True,
            }
            if draft["name"] and draft["name"] != "Visitor":
                patch["name"] = draft["name"]
            if draft["email"]:
                patch["email"] = draft["email"]
            if draft["course"]:
                patch["course"] = draft["course"]
            if draft["branch_id"]:
                patch["branch_id"] = draft["branch_id"]
            if draft["branch_name"]:
                patch["branch_name"] = draft["branch_name"]
            if draft.get("message"):
                patch["message"] = draft["message"]
            await db[COL].update_one({"id": existing["id"]}, {"$set": patch})
            updated = await db[COL].find_one({"id": existing["id"]})
            return serialize_doc(updated) or {}, True

    # 2) Soft-merge by phone + source_type within window
    phone_c = draft.get("phone_canonical")
    st = draft.get("source_type")
    if phone_c and st:
        since = now - timedelta(hours=_duplicate_window_hours())
        existing = await db[COL].find_one(
            {
                "phone_canonical": phone_c,
                "source_type": st,
                "created_at": {"$gte": since},
                "status": {"$nin": ["converted", "lost"]},
            },
            sort=[("created_at", -1)],
        )
        if existing:
            patch = {
                "updated_at": now,
                "last_captured_at": now,
                "capture_count": int(existing.get("capture_count") or 1) + 1,
                "duplicate_merged": True,
            }
            if draft["name"] and draft["name"] != "Visitor":
                patch["name"] = draft["name"]
            if draft["email"]:
                patch["email"] = draft["email"]
            if draft["phone"]:
                patch["phone"] = draft["phone"]
            if draft["course"]:
                patch["course"] = draft["course"]
            if draft["branch_id"]:
                patch["branch_id"] = draft["branch_id"]
            if draft["branch_name"]:
                patch["branch_name"] = draft["branch_name"]
            if draft.get("message"):
                patch["message"] = draft["message"]
            if ref_id and not existing.get("source_ref_id"):
                patch["source_ref_id"] = ref_id
                patch["source_ref_type"] = ref_type
            await db[COL].update_one({"id": existing["id"]}, {"$set": patch})
            updated = await db[COL].find_one({"id": existing["id"]})
            return serialize_doc(updated) or {}, True

    await db[COL].insert_one(draft)
    return serialize_doc(draft) or {}, False


async def create_lead_from_payload(data: LeadCreate) -> Dict[str, Any]:
    ser, merged = await create_or_merge_lead(
        name=data.name,
        phone=data.phone,
        email=data.email or "",
        course=data.course or "",
        source=data.source,
        source_type=getattr(data, "source_type", None),
        source_ref_id=getattr(data, "source_ref_id", None),
        source_ref_type=getattr(data, "source_ref_type", None),
        branch_id=data.branch_id,
        branch_name=data.branch_name,
        message=getattr(data, "message", None),
    )
    out = lead_to_response_dict(ser)
    out["duplicate_merged"] = merged
    return out


async def upsert_from_source(
    *,
    source_type: str,
    source_ref_type: str,
    source_ref_id: str,
    name: str,
    phone: str,
    email: Optional[str] = None,
    course: Optional[str] = None,
    branch_id: Optional[str] = None,
    branch_name: Optional[str] = None,
    source_label: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Best-effort lead upsert from another domain (training/demo/camp). Never raises to caller."""
    try:
        if not (phone or "").strip():
            return None
        ser, _ = await create_or_merge_lead(
            name=name or "Visitor",
            phone=phone,
            email=email or "",
            course=course or "",
            source=source_label or source_type,
            source_type=source_type,
            source_ref_id=source_ref_id,
            source_ref_type=source_ref_type,
            branch_id=branch_id,
            branch_name=branch_name,
        )
        return lead_to_response_dict(ser)
    except Exception:
        logger.exception(
            "Lead upsert_from_source failed type=%s ref=%s", source_type, source_ref_id
        )
        return None


async def list_leads_service(
    *,
    skip: int = 0,
    limit: int = 50,
    search: Optional[str] = None,
    status: Optional[str] = None,
    source: Optional[str] = None,
    source_type: Optional[str] = None,
    branch_id: Optional[str] = None,
    follow_up_due: Optional[str] = None,
    sort: Optional[str] = None,
    coach_assignment_status: Optional[str] = None,
    unassigned_coach: bool = False,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database not initialized")

    clauses: List[Dict[str, Any]] = []
    scope = await _bm_branch_filter(db, current_user)
    if scope:
        clauses.append(scope)

    if search and search.strip():
        s = search.strip()
        clauses.append(
            {
                "$or": [
                    {"name": {"$regex": s, "$options": "i"}},
                    {"email": {"$regex": s, "$options": "i"}},
                    {"phone": {"$regex": s, "$options": "i"}},
                    {"phone_canonical": {"$regex": s, "$options": "i"}},
                    {"course": {"$regex": s, "$options": "i"}},
                    {"branch_name": {"$regex": s, "$options": "i"}},
                    {"branch_id": {"$regex": s, "$options": "i"}},
                    {"source": {"$regex": s, "$options": "i"}},
                    {"source_type": {"$regex": s, "$options": "i"}},
                    {"source_ref_id": {"$regex": s, "$options": "i"}},
                ]
            }
        )

    st = (status or "").strip().lower()
    if st in LEAD_STATUSES:
        if st == "new":
            clauses.append(
                {
                    "$or": [
                        {"status": "new"},
                        {"status": {"$exists": False}},
                        {"status": None},
                        {"status": ""},
                    ]
                }
            )
        else:
            clauses.append({"status": st})

    stype = (source_type or "").strip().lower()
    if stype and stype != "all":
        clauses.append({"source_type": normalize_source_type(stype, stype)})

    src = (source or "").strip()
    if src and src.lower() != "all":
        clauses.append({"source": {"$regex": re.escape(src), "$options": "i"}})

    bid = (branch_id or "").strip()
    if bid and bid.lower() != "all":
        clauses.append({"branch_id": bid})

    # M15-S03: follow-up due filters (open pipeline only for overdue/today)
    due = (follow_up_due or "").strip().lower()
    now = datetime.utcnow()
    start_today = datetime(now.year, now.month, now.day)
    end_today = start_today + timedelta(days=1)
    open_status = {
        "$nin": ["converted", "lost"],
    }
    if due == "overdue":
        clauses.append({"status": open_status})
        clauses.append({"next_follow_up_at": {"$ne": None, "$lt": start_today}})
    elif due == "today":
        clauses.append({"status": open_status})
        clauses.append(
            {"next_follow_up_at": {"$gte": start_today, "$lt": end_today}}
        )
    elif due == "upcoming":
        clauses.append({"status": open_status})
        clauses.append({"next_follow_up_at": {"$gte": end_today}})
    elif due == "unscheduled":
        clauses.append({"status": open_status})
        clauses.append(
            {
                "$or": [
                    {"next_follow_up_at": None},
                    {"next_follow_up_at": {"$exists": False}},
                ]
            }
        )

    # M15-S04 coach assignment filters
    if unassigned_coach:
        clauses.append(
            {
                "$or": [
                    {"coach_assignment_status": {"$exists": False}},
                    {"coach_assignment_status": None},
                    {"coach_assignment_status": ""},
                    {
                        "coach_assignment_status": {
                            "$nin": ["pending", "accepted"]
                        }
                    },
                ]
            }
        )
    cas = (coach_assignment_status or "").strip().lower()
    if cas and cas != "all":
        clauses.append({"coach_assignment_status": cas})

    if not clauses:
        q: Dict[str, Any] = {}
    elif len(clauses) == 1:
        q = clauses[0]
    else:
        q = {"$and": clauses}

    limit = min(max(limit, 1), 200)
    skip = max(skip, 0)
    sort_key = (sort or "").strip().lower()
    if sort_key == "next_follow_up_at":
        sort_spec = [("next_follow_up_at", 1), ("created_at", -1)]
    else:
        sort_spec = [("created_at", -1)]
    cursor = db[COL].find(q).sort(sort_spec).skip(skip).limit(limit)
    items = []
    for raw in await cursor.to_list(length=limit):
        items.append(lead_to_response_dict(serialize_doc(raw) or {}))
    total = await db[COL].count_documents(q)
    return {"leads": items, "total": total, "skip": skip, "limit": limit}


async def list_lead_source_options(
    *, current_user: Optional[dict] = None
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database not initialized")
    match: Dict[str, Any] = {}
    scope = await _bm_branch_filter(db, current_user)
    if scope:
        match = scope
    pipeline: List[Dict[str, Any]] = []
    if match:
        pipeline.append({"$match": match})
    pipeline.extend(
        [
            {
                "$group": {
                    "_id": {
                        "$ifNull": ["$source_type", {"$ifNull": ["$source", "other"]}]
                    },
                    "count": {"$sum": 1},
                }
            },
            {"$sort": {"count": -1}},
        ]
    )
    rows = await db[COL].aggregate(pipeline).to_list(length=50)
    sources = [
        {
            "value": normalize_source_type(str(r.get("_id") or "other")),
            "label": str(r.get("_id") or "other").replace("_", " ").title(),
            "count": int(r.get("count") or 0),
        }
        for r in rows
        if r.get("_id")
    ]
    # Always include known types for filter UX
    seen = {s["value"] for s in sources}
    for k in KNOWN_SOURCE_TYPES:
        if k not in seen:
            label = (
                "Partner Landing"
                if k == "partner_landing"
                else k.replace("_", " ").title()
            )
            sources.append({"value": k, "label": label, "count": 0})
    for s in sources:
        if s.get("value") == "partner_landing":
            s["label"] = "Partner Landing"
    return {"sources": sources, "duplicate_window_hours": _duplicate_window_hours()}


# ---------------------------------------------------------------------------
# M15-S03 Lead follow-up pipeline
# ---------------------------------------------------------------------------


async def _assert_lead_access(db, doc: dict, current_user: Optional[dict]) -> None:
    if not current_user:
        return
    role = _role(current_user)
    if role in {"superadmin", "super_admin", "coach_admin", "coachadmin"}:
        return
    if role not in {"branch_manager", "branch_admin", "branchmanager"}:
        raise HTTPException(status_code=403, detail="Not allowed")
    managed = await get_managed_branch_ids_for_user(db, current_user)
    bid = doc.get("branch_id")
    if bid and str(bid) not in {str(m) for m in managed}:
        raise HTTPException(
            status_code=403,
            detail="You can only manage leads for your managed branches.",
        )


def _follow_up_public(doc: dict) -> Dict[str, Any]:
    return serialize_doc(doc) or {}


async def _append_follow_up_event(
    db,
    *,
    lead_id: str,
    action: str,
    note: Optional[str],
    from_status: Optional[str],
    to_status: Optional[str],
    next_follow_up_at: Optional[datetime],
    actor: Optional[dict],
) -> Dict[str, Any]:
    act = (action or "note").strip().lower()
    if act not in LEAD_FOLLOW_UP_ACTIONS:
        act = "other"
    actor = actor or {}
    doc = {
        "id": str(uuid.uuid4()),
        "lead_id": lead_id,
        "action": act,
        "note": (note or "").strip() or None,
        "from_status": from_status,
        "to_status": to_status,
        "next_follow_up_at": next_follow_up_at,
        "actor_id": actor.get("id"),
        "actor_name": actor.get("name") or actor.get("full_name"),
        "actor_role": actor.get("role"),
        "created_at": datetime.utcnow(),
    }
    await db[COL_FOLLOW_UPS].insert_one(doc)
    return doc


async def get_lead(lead_id: str, *, current_user: Optional[dict] = None) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database not initialized")
    lid = (lead_id or "").strip()
    doc = await db[COL].find_one({"id": lid})
    if not doc:
        raise HTTPException(status_code=404, detail="Lead not found")
    await _assert_lead_access(db, doc, current_user)
    history = (
        await db[COL_FOLLOW_UPS]
        .find({"lead_id": lid})
        .sort("created_at", -1)
        .limit(50)
        .to_list(length=50)
    )
    return {
        "lead": lead_to_response_dict(serialize_doc(doc) or {}),
        "follow_ups": [_follow_up_public(h) for h in history],
    }


async def list_lead_follow_ups(
    lead_id: str,
    *,
    current_user: Optional[dict] = None,
    skip: int = 0,
    limit: int = 50,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database not initialized")
    lid = (lead_id or "").strip()
    doc = await db[COL].find_one({"id": lid})
    if not doc:
        raise HTTPException(status_code=404, detail="Lead not found")
    await _assert_lead_access(db, doc, current_user)
    limit = min(max(limit, 1), 100)
    skip = max(skip, 0)
    q = {"lead_id": lid}
    total = await db[COL_FOLLOW_UPS].count_documents(q)
    rows = (
        await db[COL_FOLLOW_UPS]
        .find(q)
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {
        "follow_ups": [_follow_up_public(r) for r in rows],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


async def create_lead_follow_up(
    lead_id: str,
    *,
    note: Optional[str] = None,
    action: str = "note",
    status: Optional[str] = None,
    next_follow_up_at: Optional[datetime] = None,
    clear_next_follow_up: bool = False,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database not initialized")
    await ensure_lead_indexes(db)
    lid = (lead_id or "").strip()
    doc = await db[COL].find_one({"id": lid})
    if not doc:
        raise HTTPException(status_code=404, detail="Lead not found")
    await _assert_lead_access(db, doc, current_user)

    note_clean = (note or "").strip() or None
    new_status = (status or "").strip().lower() or None
    if new_status and new_status not in LEAD_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status: {new_status}")

    if not note_clean and not new_status and next_follow_up_at is None and not clear_next_follow_up:
        raise HTTPException(
            status_code=400,
            detail="Provide a note, status change, or next follow-up date.",
        )

    from_status = _normalize_lead_status(doc.get("status"))
    to_status = new_status if new_status else None
    act = (action or "note").strip().lower()
    if new_status and new_status != from_status:
        act = "status_change" if act in {"note", "other"} else act
    elif next_follow_up_at is not None or clear_next_follow_up:
        if act == "note" and not note_clean:
            act = "scheduled"

    now = datetime.utcnow()
    patch: Dict[str, Any] = {"updated_at": now}

    if new_status and new_status != from_status:
        patch["status"] = new_status

    scheduled_at: Optional[datetime] = None
    if clear_next_follow_up:
        patch["next_follow_up_at"] = None
        scheduled_at = None
    elif next_follow_up_at is not None:
        dt = next_follow_up_at
        if getattr(dt, "tzinfo", None) is not None:
            from datetime import timezone as _tz

            dt = dt.astimezone(_tz.utc).replace(tzinfo=None)
        patch["next_follow_up_at"] = dt
        scheduled_at = dt

    patch["last_follow_up_at"] = now
    if note_clean:
        patch["last_follow_up_note"] = note_clean[:500]

    event_next = None
    if clear_next_follow_up:
        event_next = None
    elif scheduled_at is not None:
        event_next = scheduled_at
    else:
        event_next = doc.get("next_follow_up_at")

    event = await _append_follow_up_event(
        db,
        lead_id=lid,
        action=act,
        note=note_clean,
        from_status=from_status if to_status else None,
        to_status=to_status or (from_status if note_clean else None),
        next_follow_up_at=event_next,
        actor=current_user,
    )

    await db[COL].update_one({"id": lid}, {"$set": patch})
    updated = await db[COL].find_one({"id": lid})
    return {
        "message": "Follow-up logged",
        "lead": lead_to_response_dict(serialize_doc(updated) or {}, now),
        "follow_up": _follow_up_public(event),
    }


async def update_lead_status_with_history(
    lead_id: str,
    *,
    status: str,
    note: Optional[str] = None,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    """Backward-compatible status update that also appends follow-up history."""
    result = await create_lead_follow_up(
        lead_id,
        note=note,
        action="status_change",
        status=status,
        current_user=current_user,
    )
    return result["lead"]


async def lead_pipeline_summary(
    *, current_user: Optional[dict] = None
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database not initialized")

    match: Dict[str, Any] = {}
    scope = await _bm_branch_filter(db, current_user)
    if scope:
        match = scope

    pipeline: List[Dict[str, Any]] = []
    if match:
        pipeline.append({"$match": match})
    pipeline.append(
        {
            "$group": {
                "_id": None,
                "total": {"$sum": 1},
                "by_status": {"$push": {"$ifNull": ["$status", "new"]}},
                "next_dates": {"$push": "$next_follow_up_at"},
            }
        }
    )
    rows = await db[COL].aggregate(pipeline).to_list(length=1)
    if not rows:
        return {
            "total": 0,
            "open": 0,
            "overdue": 0,
            "due_today": 0,
            "by_status": {s: 0 for s in LEAD_STATUSES},
        }

    row = rows[0]
    by_status: Dict[str, int] = {s: 0 for s in LEAD_STATUSES}
    for s in row.get("by_status") or []:
        key = _normalize_lead_status(s)
        by_status[key] = by_status.get(key, 0) + 1

    now = datetime.utcnow()
    start_today = datetime(now.year, now.month, now.day)
    end_today = start_today + timedelta(days=1)
    overdue = 0
    due_today = 0
    closed = {"converted", "lost"}
    # Re-query with status awareness for overdue/today accuracy
    base_clauses: List[Dict[str, Any]] = []
    if match:
        base_clauses.append(match)
    base_clauses.append({"status": {"$nin": list(closed)}})
    overdue_q = {
        "$and": base_clauses
        + [{"next_follow_up_at": {"$ne": None, "$lt": start_today}}]
    }
    today_q = {
        "$and": base_clauses
        + [{"next_follow_up_at": {"$gte": start_today, "$lt": end_today}}]
    }
    overdue = await db[COL].count_documents(overdue_q)
    due_today = await db[COL].count_documents(today_q)
    open_n = sum(by_status.get(s, 0) for s in LEAD_STATUSES if s not in closed)

    return {
        "total": int(row.get("total") or 0),
        "open": open_n,
        "overdue": overdue,
        "due_today": due_today,
        "by_status": by_status,
    }
