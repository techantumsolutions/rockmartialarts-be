"""Atomic sequential invoice number generation (M07-S01-T02)."""
from datetime import datetime
from typing import Optional

from pymongo import ReturnDocument

from utils.database import get_db

COUNTER_ID = "invoice_number"
DEFAULT_PREFIX = "INV"


async def ensure_invoice_counter_indexes(db=None) -> None:
    db = db or get_db()
    if db is None:
        return
    try:
        await db.invoice_counters.create_index("id", unique=True)
    except Exception:
        pass


async def next_invoice_number(*, prefix: Optional[str] = None, paid_at: Optional[datetime] = None) -> str:
    """
    Generate a unique invoice reference: INV-YYYYMMDD-000001

    Uses an atomic Mongo counter so concurrent payments cannot collide.
    """
    db = get_db()
    if db is None:
        raise RuntimeError("Database connection not available")

    await ensure_invoice_counter_indexes(db)

    now = paid_at or datetime.utcnow()
    day_key = now.strftime("%Y%m%d")
    pref = (prefix or DEFAULT_PREFIX).strip().upper() or DEFAULT_PREFIX
    counter_key = f"{pref}-{day_key}"

    result = await db.invoice_counters.find_one_and_update(
        {"id": counter_key},
        {
            "$inc": {"seq": 1},
            "$setOnInsert": {"id": counter_key, "prefix": pref, "day": day_key, "created_at": now},
            "$set": {"updated_at": now},
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    seq = int((result or {}).get("seq") or 1)
    return f"{pref}-{day_key}-{seq:06d}"
