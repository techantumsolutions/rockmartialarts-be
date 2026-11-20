"""Audit trail for academy (branch) partner ownership assign / transfer / remove."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from utils.database import get_db

logger = logging.getLogger(__name__)

COL = "academy_ownership_audit"


async def ensure_ownership_audit_indexes(db) -> None:
    try:
        await db[COL].create_index("id", unique=True)
        await db[COL].create_index([("branch_id", 1), ("created_at", -1)])
        await db[COL].create_index([("previous_partner_org_id", 1)])
        await db[COL].create_index([("new_partner_org_id", 1)])
    except Exception:
        logger.exception("Failed ensuring academy ownership audit indexes")


def _actor(user: Optional[dict]) -> Dict[str, Any]:
    user = user or {}
    return {
        "id": user.get("id"),
        "name": user.get("full_name") or user.get("name") or user.get("email"),
        "email": user.get("email"),
        "role": user.get("role"),
    }


async def record_academy_ownership_change(
    *,
    branch_id: str,
    branch_name: Optional[str],
    previous_partner_org_id: Optional[str],
    new_partner_org_id: Optional[str],
    current_user: Optional[dict] = None,
    reason: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    prev = (previous_partner_org_id or "").strip() or None
    new = (new_partner_org_id or "").strip() or None
    if prev == new:
        return None

    if prev and new:
        action = "transfer"
    elif new and not prev:
        action = "assign"
    else:
        action = "remove"

    db = get_db()
    await ensure_ownership_audit_indexes(db)
    now = datetime.utcnow()
    doc = {
        "id": str(uuid4()),
        "branch_id": branch_id,
        "branch_name": branch_name,
        "action": action,
        "previous_partner_org_id": prev,
        "new_partner_org_id": new,
        "modified_by": _actor(current_user),
        "reason": (reason or "").strip() or None,
        "created_at": now,
        "timestamp": now,
    }
    try:
        await db[COL].insert_one(doc)
    except Exception:
        logger.exception(
            "Failed to write ownership audit branch=%s %s→%s",
            branch_id,
            prev,
            new,
        )
        return None
    return doc
