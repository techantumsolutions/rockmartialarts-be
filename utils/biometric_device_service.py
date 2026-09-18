"""
M09-S01 biometric device registry helpers.

Devices are always bound to exactly one branch so punch events cannot be
attributed to another branch once ingest uses resolve_device_branch().
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from models.biometric_device_models import (
    BiometricDeviceCreate,
    BiometricDeviceStatus,
    BiometricDeviceUpdate,
)
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.student_status_service import get_managed_branch_ids_for_user

logger = logging.getLogger(__name__)

COLLECTION = "biometric_devices"


def _norm_vendor(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").strip().lower())


def _norm_device_id(value: str) -> str:
    return (value or "").strip()


async def ensure_biometric_device_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        coll = database[COLLECTION]
        await coll.create_index("id", unique=True)
        await coll.create_index(
            [("vendor", 1), ("vendor_device_id", 1)],
            unique=True,
            name="uniq_vendor_device",
        )
        await coll.create_index([("branch_id", 1), ("status", 1)])
        await coll.create_index("status")
    except Exception:
        logger.exception("Failed ensuring biometric_devices indexes")


async def _assert_branch_exists(db, branch_id: str) -> dict:
    branch = await db.branches.find_one({"id": branch_id})
    if not branch:
        raise HTTPException(status_code=400, detail="Branch not found. Select an approved branch.")
    return branch


async def _assert_can_access_branch(db, current_user: dict, branch_id: str) -> None:
    role = str((current_user or {}).get("role") or "").lower()
    if role in {"super_admin", "superadmin", "coach_admin"}:
        return
    if role != "branch_manager":
        raise HTTPException(status_code=403, detail="Not allowed to manage biometric devices")
    managed = await get_managed_branch_ids_for_user(db, current_user)
    if branch_id not in managed:
        raise HTTPException(
            status_code=403,
            detail="You can only manage devices for your assigned branches.",
        )


async def _branch_scope_filter(db, current_user: dict) -> Optional[Dict[str, Any]]:
    """None = all branches (SA). Dict filter for BM. Empty list → no access."""
    role = str((current_user or {}).get("role") or "").lower()
    if role in {"super_admin", "superadmin", "coach_admin"}:
        return None
    if role != "branch_manager":
        raise HTTPException(status_code=403, detail="Not allowed to view biometric devices")
    managed = await get_managed_branch_ids_for_user(db, current_user)
    if not managed:
        return {"branch_id": {"$in": []}}
    return {"branch_id": {"$in": managed}}


def _branch_display_name(branch: Optional[dict]) -> str:
    if not branch:
        return ""
    return (branch.get("branch") or {}).get("name") or branch.get("name") or ""


async def enrich_device(db, device: dict) -> dict:
    out = serialize_doc(device)
    branch = await db.branches.find_one({"id": device.get("branch_id")})
    out["branch_name"] = _branch_display_name(branch)
    out["branch_code"] = (branch.get("branch") or {}).get("code") if branch else ""
    return out


async def create_device(body: BiometricDeviceCreate, current_user: dict) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    await ensure_biometric_device_indexes(db)
    vendor = _norm_vendor(body.vendor)
    vendor_device_id = _norm_device_id(body.vendor_device_id)
    if not vendor_device_id:
        raise HTTPException(status_code=400, detail="vendor_device_id is required")

    await _assert_branch_exists(db, body.branch_id)
    await _assert_can_access_branch(db, current_user, body.branch_id)

    existing = await db[COLLECTION].find_one(
        {"vendor": vendor, "vendor_device_id": vendor_device_id}
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail="A device with this vendor and device identifier already exists.",
        )

    now = datetime.utcnow()
    doc = {
        "id": str(uuid.uuid4()),
        "name": body.name.strip(),
        "vendor": vendor,
        "vendor_device_id": vendor_device_id,
        "branch_id": body.branch_id,
        "status": body.status.value if isinstance(body.status, BiometricDeviceStatus) else str(body.status),
        "location_note": (body.location_note or "").strip() or None,
        "ip_address": (body.ip_address or "").strip() or None,
        "created_by": (current_user or {}).get("id"),
        "created_at": now,
        "updated_at": now,
    }
    await db[COLLECTION].insert_one(doc)
    return {"device": await enrich_device(db, doc), "message": "Biometric device created"}


async def list_devices(
    current_user: dict,
    *,
    branch_id: Optional[str] = None,
    status: Optional[str] = None,
    vendor: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    await ensure_biometric_device_indexes(db)
    query: Dict[str, Any] = {}
    scope = await _branch_scope_filter(db, current_user)
    if scope is not None:
        query.update(scope)

    if branch_id:
        role = str((current_user or {}).get("role") or "").lower()
        if role == "branch_manager":
            await _assert_can_access_branch(db, current_user, branch_id)
        query["branch_id"] = branch_id

    if status and status != "all":
        query["status"] = status.strip().lower()
    if vendor:
        query["vendor"] = _norm_vendor(vendor)

    skip = max(0, int(skip or 0))
    limit = max(1, min(int(limit or 100), 500))
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).sort("created_at", -1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)
    devices = [await enrich_device(db, row) for row in rows]
    return {
        "devices": devices,
        "total": total,
        "count": len(devices),
        "skip": skip,
        "limit": limit,
    }


async def get_device(device_id: str, current_user: dict) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    device = await db[COLLECTION].find_one({"id": device_id})
    if not device:
        raise HTTPException(status_code=404, detail="Biometric device not found")
    await _assert_can_access_branch(db, current_user, device["branch_id"])
    return {"device": await enrich_device(db, device)}


async def update_device(
    device_id: str,
    body: BiometricDeviceUpdate,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    device = await db[COLLECTION].find_one({"id": device_id})
    if not device:
        raise HTTPException(status_code=404, detail="Biometric device not found")
    await _assert_can_access_branch(db, current_user, device["branch_id"])

    updates: Dict[str, Any] = {}
    data = body.dict(exclude_unset=True)

    if "branch_id" in data and data["branch_id"]:
        await _assert_branch_exists(db, data["branch_id"])
        await _assert_can_access_branch(db, current_user, data["branch_id"])
        updates["branch_id"] = data["branch_id"]

    if "name" in data and data["name"] is not None:
        updates["name"] = data["name"].strip()
    if "location_note" in data:
        updates["location_note"] = (data["location_note"] or "").strip() or None
    if "ip_address" in data:
        updates["ip_address"] = (data["ip_address"] or "").strip() or None
    if "status" in data and data["status"] is not None:
        status_val = data["status"]
        updates["status"] = (
            status_val.value if isinstance(status_val, BiometricDeviceStatus) else str(status_val)
        )

    new_vendor = _norm_vendor(data["vendor"]) if "vendor" in data and data["vendor"] else device["vendor"]
    new_vdid = (
        _norm_device_id(data["vendor_device_id"])
        if "vendor" in data or "vendor_device_id" in data
        else device["vendor_device_id"]
    )
    if "vendor" in data and data["vendor"]:
        updates["vendor"] = new_vendor
    if "vendor_device_id" in data and data["vendor_device_id"]:
        updates["vendor_device_id"] = new_vdid

    if "vendor" in updates or "vendor_device_id" in updates:
        conflict = await db[COLLECTION].find_one(
            {
                "vendor": updates.get("vendor", device["vendor"]),
                "vendor_device_id": updates.get("vendor_device_id", device["vendor_device_id"]),
                "id": {"$ne": device_id},
            }
        )
        if conflict:
            raise HTTPException(
                status_code=409,
                detail="Another device already uses this vendor and device identifier.",
            )

    if not updates:
        return {"device": await enrich_device(db, device), "message": "No changes"}

    updates["updated_at"] = datetime.utcnow()
    await db[COLLECTION].update_one({"id": device_id}, {"$set": updates})
    refreshed = await db[COLLECTION].find_one({"id": device_id})
    return {"device": await enrich_device(db, refreshed), "message": "Biometric device updated"}


async def deactivate_device(device_id: str, current_user: dict) -> Dict[str, Any]:
    """Soft-delete: set status inactive (preserves branch mapping history)."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    device = await db[COLLECTION].find_one({"id": device_id})
    if not device:
        raise HTTPException(status_code=404, detail="Biometric device not found")
    await _assert_can_access_branch(db, current_user, device["branch_id"])
    await db[COLLECTION].update_one(
        {"id": device_id},
        {"$set": {"status": BiometricDeviceStatus.INACTIVE.value, "updated_at": datetime.utcnow()}},
    )
    refreshed = await db[COLLECTION].find_one({"id": device_id})
    return {
        "device": await enrich_device(db, refreshed),
        "message": "Biometric device deactivated",
    }


async def resolve_device_branch(
    vendor: str,
    vendor_device_id: str,
    *,
    active_only: bool = True,
) -> Tuple[Optional[str], Optional[dict]]:
    """
    Resolve vendor device → branch_id for attendance attribution (S01-T03/T05, S03).
    Returns (branch_id, device_doc) or (None, None).
    """
    db = get_db()
    if db is None:
        return None, None
    query: Dict[str, Any] = {
        "vendor": _norm_vendor(vendor),
        "vendor_device_id": _norm_device_id(vendor_device_id),
    }
    if active_only:
        query["status"] = BiometricDeviceStatus.ACTIVE.value
    device = await db[COLLECTION].find_one(query)
    if not device:
        return None, None
    return device.get("branch_id"), device


def assert_event_branch_matches_device(
    device_branch_id: Optional[str],
    event_branch_id: Optional[str],
) -> bool:
    """
    QA / ingest guard: punch must not be attributed to a different branch than the device.
    Returns True if attribution is allowed.
    """
    if not device_branch_id:
        return False
    if not event_branch_id:
        return True  # will use device branch
    return str(device_branch_id) == str(event_branch_id)
