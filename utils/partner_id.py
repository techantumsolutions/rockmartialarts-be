"""
M21-S02-T02 — atomic Collaboration Partner ID generation.

Format: CP-YYYYMMDD-000001 (same counter pattern as invoices).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pymongo import ReturnDocument

from utils.database import get_db

COUNTER_ID_PREFIX = "partner_id"
DEFAULT_PREFIX = "CP"


async def ensure_partner_id_indexes(db=None) -> None:
    db = db if db is not None else get_db()
    if db is None:
        return
    try:
        await db.partner_id_counters.create_index("id", unique=True)
    except Exception:
        pass
    try:
        await db.collaboration_partner_profiles.create_index("branch_id", unique=True)
        await db.collaboration_partner_profiles.create_index("partner_id", unique=True)
    except Exception:
        pass


async def next_partner_id(*, prefix: Optional[str] = None, at: Optional[datetime] = None) -> str:
    """Generate a unique partner reference: CP-YYYYMMDD-000001."""
    db = get_db()
    if db is None:
        raise RuntimeError("Database connection not available")

    await ensure_partner_id_indexes(db)

    now = at or datetime.utcnow()
    day_key = now.strftime("%Y%m%d")
    pref = (prefix or DEFAULT_PREFIX).strip().upper() or DEFAULT_PREFIX
    counter_key = f"{COUNTER_ID_PREFIX}:{pref}-{day_key}"

    result = await db.partner_id_counters.find_one_and_update(
        {"id": counter_key},
        {
            "$inc": {"seq": 1},
            "$setOnInsert": {
                "id": counter_key,
                "prefix": pref,
                "day": day_key,
                "created_at": now,
            },
            "$set": {"updated_at": now},
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    seq = int((result or {}).get("seq") or 1)
    return f"{pref}-{day_key}-{seq:06d}"
