"""
Partner SMS / WhatsApp message quota accounting.

Independent per-channel cumulative quotas (no monthly reset).
0 allocated = channel disabled.
Missing fields are migrated to env defaults (never treated as unlimited).

Concurrency-safe via atomic Mongo find_one_and_update with remaining guards
plus a unique idempotency ledger.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from typing import Any, Dict, Literal, Optional, Tuple

from fastapi import HTTPException
from pymongo import ReturnDocument

from utils.database import get_db

logger = logging.getLogger(__name__)

COL_ORGS = "partner_organizations"
COL_LEDGER = "partner_message_quota_ledger"

Channel = Literal["sms", "whatsapp"]
MESSAGE_QUOTA_EXCEEDED = "MESSAGE_QUOTA_EXCEEDED"

DEFAULT_SMS_QUOTA = 100000
DEFAULT_WHATSAPP_QUOTA = 100000


def default_sms_allocated() -> int:
    try:
        return max(0, int(os.getenv("PARTNER_DEFAULT_SMS_QUOTA", str(DEFAULT_SMS_QUOTA)) or DEFAULT_SMS_QUOTA))
    except (TypeError, ValueError):
        return DEFAULT_SMS_QUOTA


def default_whatsapp_allocated() -> int:
    try:
        return max(
            0,
            int(
                os.getenv("PARTNER_DEFAULT_WHATSAPP_QUOTA", str(DEFAULT_WHATSAPP_QUOTA))
                or DEFAULT_WHATSAPP_QUOTA
            ),
        )
    except (TypeError, ValueError):
        return DEFAULT_WHATSAPP_QUOTA


def messaging_quota_defaults() -> Dict[str, Any]:
    """Fields to stamp on new partner org documents."""
    return {
        "sms_allocated": default_sms_allocated(),
        "whatsapp_allocated": default_whatsapp_allocated(),
        "sms_utilized": 0,
        "whatsapp_utilized": 0,
        "sms_reserved": 0,
        "whatsapp_reserved": 0,
    }


def _as_nonneg_int(value: Any, fallback: int = 0) -> int:
    try:
        if value is None:
            return fallback
        return max(0, int(value))
    except (TypeError, ValueError):
        return fallback


def _channel_keys(channel: str) -> Tuple[str, str, str]:
    ch = (channel or "").strip().lower()
    if ch in ("sms", "text"):
        return "sms_allocated", "sms_utilized", "sms_reserved"
    if ch in ("whatsapp", "wa", "whats_app"):
        return "whatsapp_allocated", "whatsapp_utilized", "whatsapp_reserved"
    raise HTTPException(status_code=400, detail="channel must be sms or whatsapp")


def normalize_channel(channel: str) -> Channel:
    ch = (channel or "").strip().lower()
    if ch in ("sms", "text"):
        return "sms"
    if ch in ("whatsapp", "wa", "whats_app"):
        return "whatsapp"
    raise HTTPException(status_code=400, detail="channel must be sms or whatsapp")


def remaining_units(allocated: int, utilized: int, reserved: int) -> int:
    return max(0, int(allocated) - int(utilized) - int(reserved))


def quota_summary_from_doc(doc: Optional[dict]) -> Dict[str, Any]:
    """Build a public quota summary from a partner org document (after ensure).

    Missing allocated fields use env defaults (never treated as unlimited, never
    silently shown as disabled=0 before migration).
    """
    if not doc:
        return {
            "sms": {
                "allocated": 0,
                "utilized": 0,
                "reserved": 0,
                "remaining": 0,
                "disabled": True,
                "state": "disabled",
            },
            "whatsapp": {
                "allocated": 0,
                "utilized": 0,
                "reserved": 0,
                "remaining": 0,
                "disabled": True,
                "state": "disabled",
            },
            "updated_at": None,
        }

    defaults = messaging_quota_defaults()

    def _channel_block(prefix: str) -> Dict[str, Any]:
        alloc_key = f"{prefix}_allocated"
        # Explicit 0 is disabled; missing key uses legacy env default until migrate.
        if alloc_key in doc and doc.get(alloc_key) is not None:
            allocated = _as_nonneg_int(doc.get(alloc_key), 0)
        else:
            allocated = _as_nonneg_int(defaults.get(alloc_key), default_sms_allocated() if prefix == "sms" else default_whatsapp_allocated())
        utilized = _as_nonneg_int(doc.get(f"{prefix}_utilized"), 0)
        reserved = _as_nonneg_int(doc.get(f"{prefix}_reserved"), 0)
        rem = remaining_units(allocated, utilized, reserved)
        disabled = allocated == 0
        if disabled:
            state = "disabled"
        elif rem <= 0:
            state = "exhausted"
        elif rem <= max(1, int(allocated * 0.1)):
            state = "low"
        else:
            state = "ok"
        return {
            "allocated": allocated,
            "utilized": utilized,
            "reserved": reserved,
            "remaining": rem,
            "disabled": disabled,
            "state": state,
        }

    return {
        "sms": _channel_block("sms"),
        "whatsapp": _channel_block("whatsapp"),
        "updated_at": doc.get("messaging_quota_updated_at") or doc.get("updated_at"),
    }


async def ensure_partner_messaging_quota_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL_LEDGER].create_index("idempotency_key", unique=True)
        await database[COL_LEDGER].create_index([("partner_org_id", 1), ("created_at", -1)])
        await database[COL_LEDGER].create_index([("partner_org_id", 1), ("channel", 1), ("status", 1)])
        await database[COL_LEDGER].create_index("reservation_id", unique=True)
    except Exception:
        logger.exception("Failed ensuring partner message quota ledger indexes")


async def ensure_partner_quota_fields(db, partner_org_id: str) -> Optional[dict]:
    """
    Initialize missing quota fields from env defaults (backward compatible).
    Never leaves missing fields as 'unlimited'.
    """
    pid = str(partner_org_id or "").strip()
    if not pid:
        return None
    org = await db[COL_ORGS].find_one({"id": pid})
    if not org:
        return None

    defaults = messaging_quota_defaults()
    patch: Dict[str, Any] = {}
    for key, default in defaults.items():
        if key not in org or org.get(key) is None:
            patch[key] = default

    if patch:
        patch["messaging_quota_updated_at"] = datetime.utcnow()
        patch["updated_at"] = datetime.utcnow()
        await db[COL_ORGS].update_one({"id": pid}, {"$set": patch})
        org = await db[COL_ORGS].find_one({"id": pid})
    return org


async def migrate_all_partner_quotas(db=None) -> Dict[str, Any]:
    """Backfill quota fields for all partner orgs missing them."""
    database = db if db is not None else get_db()
    if database is None:
        return {"updated": 0, "scanned": 0}
    await ensure_partner_messaging_quota_indexes(database)
    defaults = messaging_quota_defaults()
    scanned = 0
    updated = 0
    async for org in database[COL_ORGS].find({}, {"id": 1, **{k: 1 for k in defaults}}):
        scanned += 1
        patch = {}
        for key, default in defaults.items():
            if key not in org or org.get(key) is None:
                patch[key] = default
        if patch:
            patch["messaging_quota_updated_at"] = datetime.utcnow()
            patch["updated_at"] = datetime.utcnow()
            await database[COL_ORGS].update_one({"id": org["id"]}, {"$set": patch})
            updated += 1
    return {"scanned": scanned, "updated": updated, "defaults": defaults}


async def get_quota_summary(partner_id: str) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    org = await ensure_partner_quota_fields(db, partner_id)
    if not org:
        raise HTTPException(status_code=404, detail="Partner organization not found")
    summary = quota_summary_from_doc(org)
    summary["partner_org_id"] = partner_id
    return summary


def _quota_exceeded_detail(channel: Channel, allocated: int, remaining: int) -> Dict[str, Any]:
    if allocated == 0:
        msg = f"{channel.upper()} messaging is disabled for this partner (quota = 0)."
    else:
        msg = (
            f"{channel.upper()} message quota exceeded. "
            f"Remaining: {remaining}, allocated: {allocated}."
        )
    return {
        "code": MESSAGE_QUOTA_EXCEEDED,
        "message": msg,
        "channel": channel,
        "allocated": allocated,
        "remaining": remaining,
    }


async def reserve_units(
    partner_id: str,
    channel: str,
    units: int = 1,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Atomically reserve units if remaining >= units.
    Idempotent when idempotency_key is provided.
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    pid = str(partner_id or "").strip()
    if not pid:
        raise HTTPException(status_code=400, detail="partner_id is required")
    units_n = int(units or 0)
    if units_n <= 0:
        raise HTTPException(status_code=400, detail="units must be a positive integer")

    ch = normalize_channel(channel)
    alloc_key, util_key, res_key = _channel_keys(ch)
    await ensure_partner_messaging_quota_indexes(db)
    org = await ensure_partner_quota_fields(db, pid)
    if not org:
        raise HTTPException(status_code=404, detail="Partner organization not found")

    key = (idempotency_key or "").strip() or None
    if key:
        existing = await db[COL_LEDGER].find_one({"idempotency_key": key})
        if existing:
            return {
                "reservation_id": existing.get("reservation_id"),
                "idempotency_key": key,
                "partner_org_id": pid,
                "channel": ch,
                "units": existing.get("units", units_n),
                "status": existing.get("status"),
                "duplicate": True,
            }

    reservation_id = str(uuid.uuid4())
    now = datetime.utcnow()

    # Atomic guard: allocated - utilized - reserved >= units, and allocated > 0
    # (allocated == 0 means disabled → remaining always 0)
    updated = await db[COL_ORGS].find_one_and_update(
        {
            "id": pid,
            alloc_key: {"$gt": 0},
            "$expr": {
                "$gte": [
                    {
                        "$subtract": [
                            {"$subtract": [f"${alloc_key}", f"${util_key}"]},
                            f"${res_key}",
                        ]
                    },
                    units_n,
                ]
            },
        },
        {
            "$inc": {res_key: units_n},
            "$set": {
                "messaging_quota_updated_at": now,
                "updated_at": now,
            },
        },
        return_document=ReturnDocument.AFTER,
    )

    if not updated:
        # Re-read for error detail
        fresh = await db[COL_ORGS].find_one({"id": pid}) or org
        allocated = _as_nonneg_int(fresh.get(alloc_key), 0)
        utilized = _as_nonneg_int(fresh.get(util_key), 0)
        reserved = _as_nonneg_int(fresh.get(res_key), 0)
        rem = remaining_units(allocated, utilized, reserved)
        raise HTTPException(
            status_code=402,
            detail=_quota_exceeded_detail(ch, allocated, rem),
        )

    ledger = {
        "id": str(uuid.uuid4()),
        "reservation_id": reservation_id,
        "idempotency_key": key or reservation_id,
        "partner_org_id": pid,
        "channel": ch,
        "units": units_n,
        "status": "reserved",
        "created_at": now,
        "updated_at": now,
        "confirmed_at": None,
        "released_at": None,
    }
    try:
        await db[COL_LEDGER].insert_one(dict(ledger))
    except Exception as exc:
        # Unique idempotency race — roll back reservation and return existing
        await db[COL_ORGS].update_one(
            {"id": pid},
            {
                "$inc": {res_key: -units_n},
                "$set": {"messaging_quota_updated_at": datetime.utcnow(), "updated_at": datetime.utcnow()},
            },
        )
        if key:
            existing = await db[COL_LEDGER].find_one({"idempotency_key": key})
            if existing:
                return {
                    "reservation_id": existing.get("reservation_id"),
                    "idempotency_key": key,
                    "partner_org_id": pid,
                    "channel": ch,
                    "units": existing.get("units", units_n),
                    "status": existing.get("status"),
                    "duplicate": True,
                }
        logger.exception("quota ledger insert failed partner=%s", pid)
        raise HTTPException(status_code=500, detail=f"Quota reservation failed: {exc}") from exc

    return {
        "reservation_id": reservation_id,
        "idempotency_key": ledger["idempotency_key"],
        "partner_org_id": pid,
        "channel": ch,
        "units": units_n,
        "status": "reserved",
        "duplicate": False,
        "quota": quota_summary_from_doc(updated),
    }


async def confirm_reservation(reservation_id: str) -> Dict[str, Any]:
    """Move reserved units into utilized (send succeeded)."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    rid = str(reservation_id or "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="reservation_id is required")

    row = await db[COL_LEDGER].find_one({"reservation_id": rid})
    if not row:
        raise HTTPException(status_code=404, detail="Reservation not found")
    if row.get("status") == "confirmed":
        return {"reservation_id": rid, "status": "confirmed", "duplicate": True}
    if row.get("status") == "released":
        raise HTTPException(status_code=400, detail="Reservation already released")

    ch = normalize_channel(str(row.get("channel") or ""))
    _, util_key, res_key = _channel_keys(ch)
    units_n = max(1, int(row.get("units") or 1))
    pid = str(row.get("partner_org_id") or "")
    now = datetime.utcnow()

    await db[COL_ORGS].update_one(
        {"id": pid, res_key: {"$gte": units_n}},
        {
            "$inc": {res_key: -units_n, util_key: units_n},
            "$set": {"messaging_quota_updated_at": now, "updated_at": now},
        },
    )
    await db[COL_LEDGER].update_one(
        {"reservation_id": rid, "status": "reserved"},
        {"$set": {"status": "confirmed", "confirmed_at": now, "updated_at": now}},
    )
    return {"reservation_id": rid, "status": "confirmed", "duplicate": False}


async def release_reservation(reservation_id: str) -> Dict[str, Any]:
    """Release reserved units back to remaining (send failed / aborted)."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    rid = str(reservation_id or "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="reservation_id is required")

    row = await db[COL_LEDGER].find_one({"reservation_id": rid})
    if not row:
        raise HTTPException(status_code=404, detail="Reservation not found")
    if row.get("status") == "released":
        return {"reservation_id": rid, "status": "released", "duplicate": True}
    if row.get("status") == "confirmed":
        raise HTTPException(status_code=400, detail="Reservation already confirmed")

    ch = normalize_channel(str(row.get("channel") or ""))
    _, _, res_key = _channel_keys(ch)
    units_n = max(1, int(row.get("units") or 1))
    pid = str(row.get("partner_org_id") or "")
    now = datetime.utcnow()

    await db[COL_ORGS].update_one(
        {"id": pid, res_key: {"$gte": units_n}},
        {
            "$inc": {res_key: -units_n},
            "$set": {"messaging_quota_updated_at": now, "updated_at": now},
        },
    )
    await db[COL_LEDGER].update_one(
        {"reservation_id": rid, "status": "reserved"},
        {"$set": {"status": "released", "released_at": now, "updated_at": now}},
    )
    return {"reservation_id": rid, "status": "released", "duplicate": False}


async def set_partner_messaging_limits(
    partner_org_id: str,
    *,
    sms_allocated: Optional[int] = None,
    whatsapp_allocated: Optional[int] = None,
    actor_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Super-admin update of allocated limits. Does not reset utilized."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    org = await ensure_partner_quota_fields(db, partner_org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Partner organization not found")

    patch: Dict[str, Any] = {
        "messaging_quota_updated_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    if actor_user_id:
        patch["messaging_quota_updated_by"] = actor_user_id

    if sms_allocated is not None:
        if int(sms_allocated) < 0:
            raise HTTPException(status_code=400, detail="sms_allocated must be >= 0")
        patch["sms_allocated"] = int(sms_allocated)
    if whatsapp_allocated is not None:
        if int(whatsapp_allocated) < 0:
            raise HTTPException(status_code=400, detail="whatsapp_allocated must be >= 0")
        patch["whatsapp_allocated"] = int(whatsapp_allocated)

    if len(patch) <= 2 and sms_allocated is None and whatsapp_allocated is None:
        raise HTTPException(status_code=400, detail="No quota fields to update")

    await db[COL_ORGS].update_one({"id": partner_org_id}, {"$set": patch})
    updated = await db[COL_ORGS].find_one({"id": partner_org_id})
    return quota_summary_from_doc(updated)


async def dispatch_with_partner_quota(
    partner_org_id: str,
    channel: str,
    send_coro_factory,
    *,
    units: int = 1,
    idempotency_key: Optional[str] = None,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Reserve → await send_coro_factory() → confirm on truthy success / release on failure.
    send_coro_factory should be an async callable returning bool (or truthy).
    Returns (ok, error_message, reservation_id).
    Raises HTTPException MESSAGE_QUOTA_EXCEEDED on reserve failure.
    """
    reservation = await reserve_units(
        partner_org_id, channel, units=units, idempotency_key=idempotency_key
    )
    rid = reservation.get("reservation_id")
    if reservation.get("status") == "confirmed":
        # Prior idempotent success — do not re-send / re-charge
        return True, None, rid
    if reservation.get("duplicate") and reservation.get("status") == "reserved":
        # Prior reserve without confirm — attempt send then confirm/release
        pass

    try:
        ok = await send_coro_factory()
    except Exception as exc:
        try:
            await release_reservation(str(rid))
        except Exception:
            logger.exception("release after send exception failed rid=%s", rid)
        return False, str(exc)[:240], rid

    if ok:
        try:
            await confirm_reservation(str(rid))
        except Exception:
            logger.exception("confirm reservation failed rid=%s", rid)
        return True, None, rid

    try:
        await release_reservation(str(rid))
    except Exception:
        logger.exception("release after send failure failed rid=%s", rid)
    return False, "Provider returned failure", rid


async def resolve_partner_org_id_for_actor(
    current_user: Optional[dict] = None,
    *,
    branch_id: Optional[str] = None,
    doc: Optional[dict] = None,
) -> Optional[str]:
    """Best-effort partner org resolution for messaging quota."""
    if doc:
        oid = str(doc.get("partner_org_id") or "").strip()
        if oid:
            return oid
    if current_user:
        oid = str(current_user.get("partner_org_id") or "").strip()
        if oid:
            return oid
        try:
            from utils.partner_tenant import resolve_staff_partner_org_id

            db = get_db()
            if db is not None:
                resolved = await resolve_staff_partner_org_id(db, current_user)
                if resolved:
                    return resolved
        except Exception:
            logger.exception("resolve_staff_partner_org_id failed")
    bid = str(branch_id or (doc or {}).get("branch_id") or "").strip()
    if bid:
        try:
            from utils.partner_tenant import resolve_partner_org_id_for_branch

            db = get_db()
            if db is not None:
                return await resolve_partner_org_id_for_branch(db, bid)
        except Exception:
            logger.exception("resolve_partner_org_id_for_branch failed")
    return None
