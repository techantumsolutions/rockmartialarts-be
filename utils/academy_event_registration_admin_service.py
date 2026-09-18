"""
M17-S05 Academy Event Registration administration.

Additive admin list / filters / CSV export / summary / status updates.
Does not alter public OTP or register/pay routes.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.academy_event_registration_models import (
    ALLOWED_ADMIN_REGISTRATION_STATUS_TRANSITIONS,
    AcademyEventRegistrationStatus,
    AcademyEventRegistrationStatusUpdate,
)
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.student_status_service import get_managed_branch_ids_for_user

logger = logging.getLogger(__name__)

COL = "academy_event_registrations"


def _role(user: dict) -> str:
    return str(user.get("role") or "").strip().lower()


def _actor(user: dict) -> Dict[str, Any]:
    return {
        "id": user.get("id"),
        "name": user.get("full_name") or user.get("email") or user.get("id"),
        "role": _role(user),
    }


def _public(doc: Optional[dict]) -> Dict[str, Any]:
    out = serialize_doc(doc) or {}
    out.pop("razorpay_signature", None)
    return out


async def _assert_admin_access(db, current_user: dict, doc: Optional[dict] = None) -> None:
    role = _role(current_user)
    if role in {"super_admin", "superadmin", "coach_admin"}:
        return
    if role == "branch_manager":
        managed = await get_managed_branch_ids_for_user(db, current_user)
        if not managed:
            raise HTTPException(status_code=403, detail="No managed branches")
        if doc is None:
            return
        bid = str(doc.get("branch_id") or "")
        if bid and bid not in {str(b) for b in managed}:
            raise HTTPException(
                status_code=403, detail="Registration outside managed branches"
            )
        # Registrations without branch_id: allow SA/coach only for BM? Allow BM to see
        # unscoped event regs if event has no branch — include them for BM when empty bid
        if not bid:
            return
        return
    raise HTTPException(status_code=403, detail="Not authorized for event registrations")


async def _scoped_registration_query(
    db,
    current_user: dict,
    *,
    event_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    status: Optional[str] = None,
    payment_status: Optional[str] = None,
    created_from: Optional[str] = None,
    created_to: Optional[str] = None,
    search: Optional[str] = None,
) -> Dict[str, Any]:
    await _assert_admin_access(db, current_user)
    try:
        from utils.academy_event_registration_service import (
            expire_stale_pending_registrations,
        )

        await expire_stale_pending_registrations(db)
    except Exception:
        logger.debug("expire pending before admin list failed", exc_info=True)

    q: Dict[str, Any] = {}
    role = _role(current_user)
    managed: List[str] = []
    if role == "branch_manager":
        managed = await get_managed_branch_ids_for_user(db, current_user)
        if not managed:
            return {"__empty__": True}
        # Include regs for managed branches OR events with no branch_id
        q["$and"] = [
            {
                "$or": [
                    {"branch_id": {"$in": [str(b) for b in managed]}},
                    {"branch_id": None},
                    {"branch_id": {"$exists": False}},
                    {"branch_id": ""},
                ]
            }
        ]

    if branch_id:
        bid = str(branch_id).strip()
        if role == "branch_manager" and bid not in {str(b) for b in managed}:
            raise HTTPException(
                status_code=403, detail="Branch outside managed branches"
            )
        q["branch_id"] = bid
        q.pop("$and", None)

    if event_id:
        q["event_id"] = str(event_id).strip()
    if status:
        q["status"] = str(status).strip().lower()
    if payment_status:
        q["payment_status"] = str(payment_status).strip().lower()

    date_clause: Dict[str, Any] = {}
    if created_from:
        try:
            date_clause["$gte"] = datetime.fromisoformat(
                str(created_from).strip()[:10]
            )
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid from date")
    if created_to:
        try:
            end = datetime.fromisoformat(str(created_to).strip()[:10]) + timedelta(
                days=1
            )
            date_clause["$lt"] = end
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid to date")
    if date_clause:
        q["created_at"] = date_clause

    if search:
        term = search.strip()
        if term:
            rx = {"$regex": re.escape(term), "$options": "i"}
            search_or = [
                {"participant_name": rx},
                {"participant_phone": rx},
                {"participant_email": rx},
                {"event_title": rx},
                {"event_slug": rx},
                {"razorpay_payment_id": rx},
                {"razorpay_order_id": rx},
                {"id": rx},
                {"notes": rx},
            ]
            if "$and" in q:
                q["$and"].append({"$or": search_or})
            else:
                q["$or"] = search_or
    return q


async def list_academy_event_registrations_admin(
    *,
    current_user: dict,
    event_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    status: Optional[str] = None,
    payment_status: Optional[str] = None,
    created_from: Optional[str] = None,
    created_to: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    q = await _scoped_registration_query(
        db,
        current_user,
        event_id=event_id,
        branch_id=branch_id,
        status=status,
        payment_status=payment_status,
        created_from=created_from,
        created_to=created_to,
        search=search,
    )
    if q.get("__empty__"):
        return {"registrations": [], "total": 0, "count": 0}

    skip = max(0, skip)
    limit = max(1, min(limit, 200))
    total = await db[COL].count_documents(q)
    rows = (
        await db[COL]
        .find(q)
        .sort([("created_at", -1)])
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {
        "registrations": [_public(r) for r in rows],
        "total": total,
        "count": len(rows),
    }


async def get_academy_event_registration_admin(
    registration_id: str, *, current_user: dict
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": registration_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Registration not found")
    await _assert_admin_access(db, current_user, doc)
    return {"registration": _public(doc)}


async def update_academy_event_registration_status_admin(
    registration_id: str,
    body: AcademyEventRegistrationStatusUpdate,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": registration_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Registration not found")
    await _assert_admin_access(db, current_user, doc)

    current = str(doc.get("status") or "")
    target = body.status.value
    if current == target:
        return {"message": "Status unchanged", "registration": _public(doc)}

    allowed = ALLOWED_ADMIN_REGISTRATION_STATUS_TRANSITIONS.get(current) or set()
    if target not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot change status from {current} to {target}",
        )

    actor = _actor(current_user)
    now = datetime.utcnow()
    updates: Dict[str, Any] = {
        "status": target,
        "updated_at": now,
        "updated_by": actor.get("id"),
        "status_note": (body.note or "").strip() or None,
    }
    if target == AcademyEventRegistrationStatus.CANCELLED.value:
        updates["cancelled_at"] = now
        if doc.get("payment_status") == "pending":
            updates["payment_status"] = "failed"
    if target == AcademyEventRegistrationStatus.EXPIRED.value:
        updates["expired_at"] = now
        if doc.get("payment_status") == "pending":
            updates["payment_status"] = "failed"

    await db[COL].update_one({"id": registration_id}, {"$set": updates})
    updated = await db[COL].find_one({"id": registration_id})

    # Resync seat count on event
    eid = (updated or doc).get("event_id")
    if eid:
        try:
            from utils.academy_event_registration_service import (
                _sync_event_registrations_count,
            )

            await _sync_event_registrations_count(db, eid)
        except Exception:
            logger.exception("Failed syncing registrations_count after status update")

    return {"message": "Registration status updated", "registration": _public(updated)}


async def export_academy_event_registrations_admin(
    *,
    current_user: dict,
    event_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    status: Optional[str] = None,
    payment_status: Optional[str] = None,
    created_from: Optional[str] = None,
    created_to: Optional[str] = None,
    search: Optional[str] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    q = await _scoped_registration_query(
        db,
        current_user,
        event_id=event_id,
        branch_id=branch_id,
        status=status,
        payment_status=payment_status,
        created_from=created_from,
        created_to=created_to,
        search=search,
    )
    if q.get("__empty__"):
        rows = []
    else:
        rows = (
            await db[COL]
            .find(q)
            .sort([("created_at", -1)])
            .to_list(length=5000)
        )

    fieldnames = [
        "id",
        "event_id",
        "event_title",
        "event_slug",
        "event_type",
        "event_start_at",
        "event_venue",
        "branch_id",
        "participant_name",
        "participant_phone",
        "participant_email",
        "participant_age",
        "notes",
        "fee_inr",
        "amount_paise",
        "status",
        "payment_status",
        "razorpay_order_id",
        "razorpay_payment_id",
        "source",
        "created_at",
        "confirmed_at",
        "cancelled_at",
        "status_note",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        csv_row = {}
        for field in fieldnames:
            value = row.get(field)
            if hasattr(value, "isoformat"):
                value = value.isoformat()
            csv_row[field] = "" if value is None else str(value)
        writer.writerow(csv_row)

    return {
        "content": output.getvalue(),
        "filename": f"academy_event_registrations_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv",
        "content_type": "text/csv",
        "total": len(rows),
    }


async def summarize_academy_event_registrations_admin(
    *,
    current_user: dict,
    branch_id: Optional[str] = None,
    event_id: Optional[str] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    q = await _scoped_registration_query(
        db, current_user, branch_id=branch_id, event_id=event_id
    )
    if q.get("__empty__"):
        return {"total": 0, "by_status": {}, "by_payment_status": {}}

    pipeline = [
        {"$match": q},
        {
            "$facet": {
                "total": [{"$count": "n"}],
                "by_status": [{"$group": {"_id": "$status", "count": {"$sum": 1}}}],
                "by_payment_status": [
                    {"$group": {"_id": "$payment_status", "count": {"$sum": 1}}}
                ],
            }
        },
    ]
    agg = await db[COL].aggregate(pipeline).to_list(length=1)
    facet = (agg or [{}])[0]

    def _map(rows):
        out: Dict[str, int] = {}
        for r in rows or []:
            out[str(r.get("_id") or "unknown")] = int(r.get("count") or 0)
        return out

    total_rows = facet.get("total") or []
    return {
        "total": int((total_rows[0] or {}).get("n") or 0) if total_rows else 0,
        "by_status": _map(facet.get("by_status")),
        "by_payment_status": _map(facet.get("by_payment_status")),
    }
