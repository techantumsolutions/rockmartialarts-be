"""In-app notifications when a new lead is created (not merged)."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from bson import ObjectId
from fastapi import HTTPException, status

from models.notification_models import LeadNotification
from utils.database import get_db
from utils.helpers import serialize_doc

logger = logging.getLogger(__name__)

COL = "lead_notifications"
_ROCK_ADMIN_ROLES = ("super_admin", "superadmin", "coach_admin", "coachadmin")
_PARTNER_SA_ROLES = ("partner_super_admin", "partnersuperadmin")


async def ensure_lead_notification_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL].create_index("id", unique=True)
        await database[COL].create_index("dedupe_key", unique=True)
        await database[COL].create_index(
            [("recipient_id", 1), ("created_at", -1)]
        )
        await database[COL].create_index(
            [("recipient_id", 1), ("is_read", 1)]
        )
    except Exception:
        logger.exception("Failed ensuring lead_notifications indexes")


def _lead_display_name(lead: Dict[str, Any]) -> str:
    return str(lead.get("name") or lead.get("full_name") or "New lead").strip() or "New lead"


def _lead_detail_link(lead_id: str) -> str:
    return f"/leads?leadId={lead_id}"


async def _find_branch_manager_recipients(db, branch_id: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    bid = str(branch_id).strip()
    if not bid:
        return out
    try:
        branch = None
        if ObjectId.is_valid(bid):
            branch = await db.branches.find_one({"_id": ObjectId(bid)})
        if not branch:
            branch = (
                await db.branches.find_one({"id": bid})
                or await db.branches.find_one({"_id": bid})
            )
        if not branch:
            return out
        manager_id = branch.get("manager_id") or branch.get("branch_manager_id")
        if manager_id:
            mid = str(manager_id)
            out.append((mid, "branch_manager"))
        async for mgr in db.branch_managers.find(
            {"$or": [{"branch_id": bid}, {"managed_branches": bid}, {"branch_ids": bid}]},
            {"id": 1},
        ).limit(20):
            rid = str(mgr.get("id") or "")
            if rid:
                out.append((rid, "branch_manager"))
    except Exception:
        logger.exception("branch manager recipients for lead failed branch=%s", branch_id)
    return out


async def _resolve_new_lead_recipients(
    db, lead: Dict[str, Any]
) -> List[Tuple[str, str]]:
    recipients: List[Tuple[str, str]] = []
    partner_org_id = str(lead.get("partner_org_id") or "").strip()

    if partner_org_id:
        async for user in db.users.find(
            {
                "role": {"$in": list(_PARTNER_SA_ROLES)},
                "partner_org_id": partner_org_id,
            },
            {"id": 1, "role": 1},
        ).limit(50):
            uid = str(user.get("id") or "")
            if uid:
                recipients.append((uid, str(user.get("role") or "partner_super_admin")))
    else:
        # Rock Super Admins live in `superadmins` (JWT role super_admin).
        async for admin in db.superadmins.find(
            {"$or": [{"is_active": True}, {"is_active": {"$exists": False}}]},
            {"id": 1},
        ).limit(100):
            uid = str(admin.get("id") or "")
            if uid:
                recipients.append((uid, "super_admin"))
        async for user in db.users.find(
            {"role": {"$in": list(_ROCK_ADMIN_ROLES)}},
            {"id": 1, "role": 1},
        ).limit(100):
            uid = str(user.get("id") or "")
            if uid:
                recipients.append((uid, str(user.get("role") or "super_admin")))

    branch_id = lead.get("branch_id")
    if branch_id:
        recipients.extend(await _find_branch_manager_recipients(db, str(branch_id)))

    seen: Set[str] = set()
    unique: List[Tuple[str, str]] = []
    for rid, role in recipients:
        if rid in seen:
            continue
        seen.add(rid)
        unique.append((rid, role))
    return unique


async def create_new_lead_notifications(lead: Dict[str, Any]) -> None:
    """Best-effort: notify authorized lead managers about a newly inserted lead."""
    db = get_db()
    if db is None or not lead:
        return
    lead_id = str(lead.get("id") or "")
    if not lead_id:
        return
    await ensure_lead_notification_indexes(db)

    lead_name = _lead_display_name(lead)
    link = _lead_detail_link(lead_id)
    title = "New lead received"
    message = f"{lead_name} submitted a new lead"
    if lead.get("course"):
        message = f"{lead_name} — interest in {lead.get('course')}"
    elif lead.get("branch_name"):
        message = f"{lead_name} — {lead.get('branch_name')}"

    try:
        recipients = await _resolve_new_lead_recipients(db, lead)
        for recipient_id, recipient_role in recipients:
            dedupe_key = f"new_lead:{lead_id}:{recipient_id}"
            existing = await db[COL].find_one({"dedupe_key": dedupe_key})
            if existing:
                continue
            notification = LeadNotification(
                lead_id=lead_id,
                lead_name=lead_name,
                recipient_id=recipient_id,
                recipient_role=recipient_role,
                title=title,
                message=message,
                link=link,
                dedupe_key=dedupe_key,
                priority="high",
            )
            await db[COL].insert_one(notification.dict())
    except Exception:
        logger.exception("create_new_lead_notifications failed for lead=%s", lead_id)


async def get_lead_notifications(
    current_user: dict, skip: int = 0, limit: int = 50
) -> dict:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database not initialized")
    await ensure_lead_notification_indexes(db)
    uid = str((current_user or {}).get("id") or "")
    if not uid:
        return {"notifications": [], "total": 0, "unread_count": 0}

    query = {"recipient_id": uid}
    notifications = (
        await db[COL]
        .find(query)
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    total = await db[COL].count_documents(query)
    unread = await db[COL].count_documents({**query, "is_read": False})
    serialized = [serialize_doc(n) for n in notifications]
    return {
        "notifications": serialized,
        "total": total,
        "unread_count": unread,
    }


async def mark_lead_notification_read(notification_id: str, current_user: dict) -> dict:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database not initialized")
    uid = str((current_user or {}).get("id") or "")
    result = await db[COL].update_one(
        {"id": notification_id, "recipient_id": uid},
        {"$set": {"is_read": True, "read_at": datetime.utcnow()}},
    )
    if result.matched_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )
    return {"message": "Notification marked as read"}
