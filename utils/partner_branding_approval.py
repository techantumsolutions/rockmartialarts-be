"""Partner branding edit approval — request / approve / reject / revoke."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.partner_branding_approval_models import (
    BrandingEditPermission,
    BrandingEditRequestStatus,
    branding_request_public,
    new_branding_request_id,
)
from utils.database import get_db

logger = logging.getLogger(__name__)

REQ_COL = "partner_branding_edit_requests"
ORG_COL = "partner_organizations"


async def ensure_branding_approval_indexes(db) -> None:
    try:
        await db[REQ_COL].create_index("id", unique=True)
        await db[REQ_COL].create_index([("partner_org_id", 1), ("status", 1)])
        await db[REQ_COL].create_index([("status", 1), ("requested_at", -1)])
    except Exception:
        logger.exception("Failed ensuring branding approval indexes")


def _permission_from_org(org: Optional[dict]) -> str:
    if not org:
        return BrandingEditPermission.NONE.value
    raw = (org.get("branding_edit_permission") or "").strip().lower()
    if raw in {e.value for e in BrandingEditPermission}:
        return raw
    return BrandingEditPermission.NONE.value


async def get_partner_branding_permission(partner_org_id: str) -> Dict[str, Any]:
    db = get_db()
    org = await db[ORG_COL].find_one({"id": partner_org_id})
    if not org:
        raise HTTPException(status_code=404, detail="Partner organization not found")
    permission = _permission_from_org(org)
    pending = await db[REQ_COL].find_one(
        {
            "partner_org_id": partner_org_id,
            "status": BrandingEditRequestStatus.PENDING.value,
        }
    )
    latest = await db[REQ_COL].find_one(
        {"partner_org_id": partner_org_id},
        sort=[("updated_at", -1)],
    )
    can_edit = permission == BrandingEditPermission.APPROVED.value
    can_request = permission in {
        BrandingEditPermission.NONE.value,
        BrandingEditPermission.REJECTED.value,
        BrandingEditPermission.REVOKED.value,
    } and pending is None
    return {
        "partner_org_id": partner_org_id,
        "permission": permission,
        "can_edit": can_edit,
        "can_request": can_request,
        "pending_request": branding_request_public(pending),
        "latest_request": branding_request_public(latest),
    }


async def assert_partner_can_edit_branding(partner_org_id: str) -> None:
    state = await get_partner_branding_permission(partner_org_id)
    if not state.get("can_edit"):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "branding_edit_not_approved",
                "message": "Branding edit requires Rock Martial Arts Super Admin approval.",
                "permission": state.get("permission"),
            },
        )


async def request_branding_edit(current_user: dict, note: Optional[str] = None) -> Dict[str, Any]:
    db = get_db()
    await ensure_branding_approval_indexes(db)
    org_id = current_user.get("partner_org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="Partner organization context required")
    org = await db[ORG_COL].find_one({"id": org_id})
    if not org:
        raise HTTPException(status_code=404, detail="Partner organization not found")

    state = await get_partner_branding_permission(org_id)
    if state.get("can_edit"):
        return {
            "message": "Branding edit access is already approved.",
            "permission": state,
            "request": state.get("pending_request") or state.get("latest_request"),
        }
    if state.get("pending_request"):
        raise HTTPException(
            status_code=409,
            detail="A branding edit request is already pending Super Admin approval.",
        )
    if not state.get("can_request"):
        raise HTTPException(
            status_code=400,
            detail="Cannot submit a branding edit request in the current state.",
        )

    now = datetime.utcnow()
    doc = {
        "id": new_branding_request_id(),
        "partner_org_id": org_id,
        "partner_name": org.get("name"),
        "partner_slug": org.get("slug"),
        "requested_by": current_user.get("id"),
        "requested_by_name": current_user.get("full_name")
        or current_user.get("name")
        or org.get("name"),
        "requested_by_email": current_user.get("email") or org.get("email"),
        "requested_at": now,
        "status": BrandingEditRequestStatus.PENDING.value,
        "note": (note or "").strip() or None,
        "reviewed_by": None,
        "reviewed_by_name": None,
        "reviewed_at": None,
        "review_reason": None,
        "access_revoked_at": None,
        "created_at": now,
        "updated_at": now,
    }
    await db[REQ_COL].insert_one(doc)
    await db[ORG_COL].update_one(
        {"id": org_id},
        {
            "$set": {
                "branding_edit_permission": BrandingEditPermission.PENDING.value,
                "updated_at": now,
            }
        },
    )
    return {
        "message": "Your branding edit request has been submitted for Super Admin approval.",
        "request": branding_request_public(doc),
        "permission": await get_partner_branding_permission(org_id),
    }


async def list_branding_edit_requests(
    *,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> Dict[str, Any]:
    db = get_db()
    await ensure_branding_approval_indexes(db)
    query: Dict[str, Any] = {}
    if status and status.strip().lower() != "all":
        query["status"] = status.strip().lower()
    total = await db[REQ_COL].count_documents(query)
    cursor = (
        db[REQ_COL]
        .find(query)
        .sort("requested_at", -1)
        .skip(skip)
        .limit(limit)
    )
    rows: List[Dict[str, Any]] = []
    async for doc in cursor:
        rows.append(branding_request_public(doc) or {})
    return {"requests": rows, "total": total, "skip": skip, "limit": limit}


async def _get_request_or_404(db, request_id: str) -> dict:
    doc = await db[REQ_COL].find_one({"id": request_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Branding edit request not found")
    return doc


async def approve_branding_edit_request(
    request_id: str, current_user: dict, reason: Optional[str] = None
) -> Dict[str, Any]:
    db = get_db()
    doc = await _get_request_or_404(db, request_id)
    if doc.get("status") not in {
        BrandingEditRequestStatus.PENDING.value,
        BrandingEditRequestStatus.REJECTED.value,
        BrandingEditRequestStatus.REVOKED.value,
    }:
        if doc.get("status") == BrandingEditRequestStatus.APPROVED.value:
            return {
                "message": "Request is already approved.",
                "request": branding_request_public(doc),
            }
        raise HTTPException(status_code=400, detail="Request cannot be approved in its current state.")

    now = datetime.utcnow()
    reviewer = {
        "reviewed_by": current_user.get("id"),
        "reviewed_by_name": current_user.get("full_name")
        or current_user.get("name")
        or "Super Admin",
        "reviewed_at": now,
        "review_reason": (reason or "").strip() or None,
        "status": BrandingEditRequestStatus.APPROVED.value,
        "access_revoked_at": None,
        "updated_at": now,
    }
    await db[REQ_COL].update_one({"id": request_id}, {"$set": reviewer})
    # Close any other pending requests for same partner
    await db[REQ_COL].update_many(
        {
            "partner_org_id": doc["partner_org_id"],
            "status": BrandingEditRequestStatus.PENDING.value,
            "id": {"$ne": request_id},
        },
        {
            "$set": {
                "status": BrandingEditRequestStatus.REJECTED.value,
                "review_reason": "Superseded by another approval",
                "reviewed_at": now,
                "reviewed_by": current_user.get("id"),
                "updated_at": now,
            }
        },
    )
    await db[ORG_COL].update_one(
        {"id": doc["partner_org_id"]},
        {
            "$set": {
                "branding_edit_permission": BrandingEditPermission.APPROVED.value,
                "branding_edit_approved_at": now,
                "branding_edit_approved_by": current_user.get("id"),
                "updated_at": now,
            }
        },
    )
    updated = await db[REQ_COL].find_one({"id": request_id})
    return {
        "message": "Branding edit access approved.",
        "request": branding_request_public(updated),
    }


async def reject_branding_edit_request(
    request_id: str, current_user: dict, reason: Optional[str] = None
) -> Dict[str, Any]:
    db = get_db()
    doc = await _get_request_or_404(db, request_id)
    if doc.get("status") != BrandingEditRequestStatus.PENDING.value:
        raise HTTPException(status_code=400, detail="Only pending requests can be rejected.")
    now = datetime.utcnow()
    await db[REQ_COL].update_one(
        {"id": request_id},
        {
            "$set": {
                "status": BrandingEditRequestStatus.REJECTED.value,
                "reviewed_by": current_user.get("id"),
                "reviewed_by_name": current_user.get("full_name")
                or current_user.get("name")
                or "Super Admin",
                "reviewed_at": now,
                "review_reason": (reason or "").strip() or None,
                "updated_at": now,
            }
        },
    )
    await db[ORG_COL].update_one(
        {"id": doc["partner_org_id"]},
        {
            "$set": {
                "branding_edit_permission": BrandingEditPermission.REJECTED.value,
                "updated_at": now,
            }
        },
    )
    updated = await db[REQ_COL].find_one({"id": request_id})
    return {
        "message": "Branding edit request rejected.",
        "request": branding_request_public(updated),
    }


async def revoke_branding_edit_access(
    partner_org_id: str, current_user: dict, reason: Optional[str] = None
) -> Dict[str, Any]:
    db = get_db()
    org = await db[ORG_COL].find_one({"id": partner_org_id})
    if not org:
        raise HTTPException(status_code=404, detail="Partner organization not found")
    now = datetime.utcnow()
    await db[ORG_COL].update_one(
        {"id": partner_org_id},
        {
            "$set": {
                "branding_edit_permission": BrandingEditPermission.REVOKED.value,
                "branding_edit_revoked_at": now,
                "branding_edit_revoked_by": current_user.get("id"),
                "updated_at": now,
            }
        },
    )
    # Mark latest approved request as revoked
    approved = await db[REQ_COL].find_one(
        {
            "partner_org_id": partner_org_id,
            "status": BrandingEditRequestStatus.APPROVED.value,
        },
        sort=[("reviewed_at", -1)],
    )
    if approved:
        await db[REQ_COL].update_one(
            {"id": approved["id"]},
            {
                "$set": {
                    "status": BrandingEditRequestStatus.REVOKED.value,
                    "access_revoked_at": now,
                    "review_reason": (reason or "").strip()
                    or approved.get("review_reason")
                    or "Access revoked by Super Admin",
                    "updated_at": now,
                }
            },
        )
    # Cancel pending
    await db[REQ_COL].update_many(
        {
            "partner_org_id": partner_org_id,
            "status": BrandingEditRequestStatus.PENDING.value,
        },
        {
            "$set": {
                "status": BrandingEditRequestStatus.REVOKED.value,
                "access_revoked_at": now,
                "updated_at": now,
            }
        },
    )
    return {
        "message": "Partner branding edit access revoked.",
        "permission": await get_partner_branding_permission(partner_org_id),
    }
