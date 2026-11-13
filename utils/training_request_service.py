"""
M12 Training request service — Home/School/College/Corporate/Residential + unified admin (S06).

Does not modify /api/requests, leads, or camp_registrations (camp stays separate).
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from pymongo import ReturnDocument

from models.training_request_models import (
    ALLOWED_STATUS_TRANSITIONS,
    CoachTrainingRequestResponse,
    CollegeTrainingRequestCreate,
    CorporateTrainingRequestCreate,
    CreateTrainingOrderBody,
    DEFAULT_RESIDENTIAL_PACKAGES,
    HomeTrainingRequestCreate,
    ResidentialTrainingRequestCreate,
    SchoolTrainingRequestCreate,
    TrainingLoginRequest,
    TrainingRequestCoachAssign,
    TrainingRequestPaymentVerify,
    TrainingRequestStatus,
    TrainingRequestStatusUpdate,
    TrainingRequestType,
)
from utils.auth import hash_password, verify_password
from utils.unified_auth import create_access_token
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.student_status_service import get_managed_branch_ids_for_user

logger = logging.getLogger(__name__)

COL = "training_requests"
COL_HISTORY = "training_request_status_history"


def _role(user: Optional[dict]) -> str:
    return str((user or {}).get("role") or "").lower()


def _actor(user: Optional[dict]) -> Dict[str, Optional[str]]:
    if not user:
        return {"id": None, "name": "public", "role": "public"}
    return {
        "id": user.get("id"),
        "name": user.get("full_name") or user.get("email") or user.get("id"),
        "role": _role(user),
    }


async def ensure_training_request_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL].create_index("id", unique=True)
        await database[COL].create_index([("type", 1), ("status", 1), ("created_at", -1)])
        await database[COL].create_index([("branch_id", 1), ("status", 1)])
        await database[COL].create_index("assigned_coach_id")
        await database[COL].create_index("notified_coach_ids")
        await database[COL].create_index("contact_phone")
        await database[COL].create_index([("created_at", -1)])
        await database[COL_HISTORY].create_index("id", unique=True)
        await database[COL_HISTORY].create_index(
            [("request_id", 1), ("created_at", -1)]
        )
    except Exception:
        logger.exception("Failed ensuring training_request indexes")


async def _append_history(
    db,
    *,
    request_id: str,
    from_status: Optional[str],
    to_status: str,
    actor: dict,
    note: Optional[str] = None,
    action: str = "status_change",
) -> None:
    doc = {
        "id": str(uuid.uuid4()),
        "request_id": request_id,
        "action": action,
        "from_status": from_status,
        "to_status": to_status,
        "note": (note or "").strip() or None,
        "actor_id": actor.get("id"),
        "actor_name": actor.get("name"),
        "actor_role": actor.get("role"),
        "created_at": datetime.utcnow(),
    }
    await db[COL_HISTORY].insert_one(doc)


def _normalize_phone(phone: str) -> str:
    return re.sub(r"\s+", "", (phone or "").strip())


def _assign_coach_ids(body: TrainingRequestCoachAssign) -> List[str]:
    raw: List[str] = []
    if body.coach_ids:
        raw.extend([str(i).strip() for i in body.coach_ids if str(i).strip()])
    if body.coach_id and str(body.coach_id).strip():
        raw.append(str(body.coach_id).strip())
    seen = set()
    unique: List[str] = []
    for cid in raw:
        if cid not in seen:
            seen.add(cid)
            unique.append(cid)
    if not unique:
        raise HTTPException(status_code=400, detail="coach_id or coach_ids is required")
    return unique


def _coach_display_name(coach: dict, fallback_id: str = "") -> str:
    return (
        coach.get("full_name")
        or f"{coach.get('first_name', '')} {coach.get('last_name', '')}".strip()
        or fallback_id
        or "Coach"
    )


def _coach_eligible(coach: dict) -> bool:
    if coach.get("is_active") is False:
        return False
    approval = str(coach.get("approval_status") or "approved").lower().strip()
    return approval in {"", "approved"}


async def _resolve_coach_record(db, coach_id: str) -> Tuple[dict, str]:
    coach = await db.coaches.find_one({"id": coach_id})
    if not coach:
        user = await db.users.find_one({"id": coach_id, "role": "coach"})
        if not user:
            raise HTTPException(status_code=404, detail=f"Coach not found: {coach_id}")
        coach = user
    if not _coach_eligible(coach):
        raise HTTPException(
            status_code=400,
            detail="Coach must be active and approved to receive assignments.",
        )
    return coach, _coach_display_name(coach, coach_id)


def _notified_entry_for(doc: dict, coach_id: str) -> Optional[dict]:
    for row in doc.get("notified_coaches") or []:
        if str(row.get("coach_id") or "") == str(coach_id):
            return row
    return None


def _coach_inbox_status(doc: dict, coach_id: str) -> str:
    """Per-coach status for coach inbox UI (backward compatible with single assign)."""
    assigned = str(doc.get("assigned_coach_id") or "").strip()
    notified_ids = [str(x) for x in (doc.get("notified_coach_ids") or [])]
    entry = _notified_entry_for(doc, coach_id)
    if assigned and assigned != str(coach_id):
        if coach_id in notified_ids or assigned:
            if entry and entry.get("status") == "rejected":
                return "rejected"
            return "notified_taken"
        return "notified_taken"
    if assigned == str(coach_id):
        return str(doc.get("coach_assignment_status") or "pending").lower() or "pending"
    if entry and entry.get("status"):
        return str(entry.get("status")).lower()
    if coach_id in notified_ids:
        return "pending"
    if assigned == str(coach_id):
        return str(doc.get("coach_assignment_status") or "pending").lower() or "pending"
    return "pending"


def _coach_visible_query(coach_id: str) -> Dict[str, Any]:
    return {
        "$or": [
            {"assigned_coach_id": coach_id},
            {"notified_coach_ids": coach_id},
        ]
    }


def _serialize_for_coach_inbox(doc: dict, coach_id: str) -> dict:
    out = serialize_doc(doc) or {}
    inbox = _coach_inbox_status(doc, coach_id)
    out["coach_inbox_status"] = inbox
    if inbox == "notified_taken":
        out["coach_assignment_status"] = "notified_taken"
    elif inbox in {"pending", "accepted", "rejected"}:
        out["coach_assignment_status"] = inbox
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
        bid = doc.get("branch_id")
        if bid and str(bid) not in {str(b) for b in managed}:
            raise HTTPException(status_code=403, detail="Request outside managed branches")
        # Unassigned branch: allow BM to view/manage (operational intake)
        return
    raise HTTPException(status_code=403, detail="Not authorized for training requests")


async def _resolve_branch(
    db, branch_id: Optional[str], branch_name: Optional[str]
) -> tuple[Optional[str], Optional[str]]:
    bid = (branch_id or "").strip() or None
    bname = (branch_name or "").strip() or None
    if bid:
        branch = await db.branches.find_one({"id": bid})
        if not branch:
            raise HTTPException(status_code=400, detail="Invalid branch_id")
        if not bname:
            bname = (
                (branch.get("branch") or {}).get("name")
                or branch.get("name")
                or bid
            )
    return bid, bname


async def _validate_training_payment(
    db,
    request_type: str,
    razorpay_order_id: Optional[str],
    razorpay_payment_id: Optional[str],
    razorpay_signature: Optional[str],
    now: datetime,
) -> tuple[str, float, int, Optional[datetime]]:
    fee_key_map = {
        "home": "home_training_fee",
        "school": "school_training_fee",
        "college": "college_training_fee",
        "corporate": "corporate_training_fee",
        "residential": "residential_training_fee",
    }
    fee_key = fee_key_map.get(request_type)
    settings_doc = await db.system_settings.find_one({}) or {}
    configured_fee = 0.0
    if fee_key and settings_doc.get(fee_key) is not None:
        try:
            configured_fee = float(settings_doc[fee_key])
        except (TypeError, ValueError):
            configured_fee = 0.0

    payment_status = "not_required"
    amount_paise = 0
    paid_at = None

    if configured_fee > 0:
        if not razorpay_order_id or not razorpay_payment_id or not razorpay_signature:
            raise HTTPException(
                status_code=400,
                detail=f"Payment of training fee (₹{configured_fee:.2f}) is required to submit this request.",
            )
        from utils.razorpay_client import verify_razorpay_signature

        valid_sig = verify_razorpay_signature(
            razorpay_order_id, razorpay_payment_id, razorpay_signature
        )
        if not valid_sig:
            raise HTTPException(
                status_code=400,
                detail="Invalid Razorpay payment signature.",
            )
        payment_status = "paid"
        amount_paise = int(round(configured_fee * 100))
        paid_at = now

    return payment_status, configured_fee, amount_paise, paid_at


def _details_dict(details) -> dict:
    if hasattr(details, "model_dump"):
        return details.model_dump()
    return details.dict()


async def _insert_training_request(
    *,
    request_type: str,
    contact_name: str,
    contact_phone: str,
    contact_email: Optional[str],
    branch_id: Optional[str],
    branch_name: Optional[str],
    source: Optional[str],
    notes: Optional[str],
    details: dict,
    current_user: Optional[dict],
    success_message: str,
    razorpay_order_id: Optional[str] = None,
    razorpay_payment_id: Optional[str] = None,
    razorpay_signature: Optional[str] = None,
    password: Optional[str] = None,
    is_phone_verified: Optional[bool] = False,
    extra_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_training_request_indexes(db)

    bid, bname = await _resolve_branch(db, branch_id, branch_name)
    now = datetime.utcnow()
    actor = _actor(current_user)

    (
        payment_status,
        configured_fee,
        amount_paise,
        paid_at,
    ) = await _validate_training_payment(
        db,
        request_type,
        razorpay_order_id,
        razorpay_payment_id,
        razorpay_signature,
        now,
    )

    norm_phone = _normalize_phone(contact_phone)
    norm_email = (contact_email or "").strip().lower()

    # Link to authenticated user or create/update training_student user
    user_id = None
    if current_user and current_user.get("id"):
        user_id = current_user.get("id")
    elif password and password.strip():
        existing_user = None
        if norm_email:
            existing_user = await db.users.find_one({"email": norm_email})
        if not existing_user and norm_phone:
            existing_user = await db.users.find_one({"phone": norm_phone})

        if existing_user:
            user_id = existing_user["id"]
            update_data: Dict[str, Any] = {
                "password": hash_password(password.strip()),
                "is_phone_verified": True,
                "updated_at": now,
            }
            if not existing_user.get("role"):
                update_data["role"] = "training_student"
            await db.users.update_one({"id": user_id}, {"$set": update_data})
        else:
            user_id = str(uuid.uuid4())
            full_name = contact_name.strip()
            name_parts = full_name.split(" ", 1)
            first_name = name_parts[0]
            last_name = name_parts[1] if len(name_parts) > 1 else ""
            new_user = {
                "id": user_id,
                "email": norm_email or f"training_{user_id[:8]}@rockmartialarts.com",
                "phone": norm_phone,
                "first_name": first_name,
                "last_name": last_name,
                "full_name": full_name,
                "role": "training_student",
                "password": hash_password(password.strip()),
                "is_active": True,
                "is_phone_verified": bool(is_phone_verified),
                "created_at": now,
                "updated_at": now,
            }
            await db.users.insert_one(new_user)

    doc_id = str(uuid.uuid4())
    doc = {
        "id": doc_id,
        "type": request_type,
        "status": TrainingRequestStatus.SUBMITTED.value,
        "contact_name": contact_name.strip(),
        "contact_phone": norm_phone,
        "contact_email": norm_email or None,
        "branch_id": bid,
        "branch_name": bname,
        "source": (source or "website").strip() or "website",
        "notes": (notes or "").strip() or None,
        "details": details,
        "payment_status": payment_status,
        "fee_total_inr": configured_fee,
        "amount_paise": amount_paise,
        "currency": "INR",
        "razorpay_order_id": razorpay_order_id,
        "razorpay_payment_id": razorpay_payment_id,
        "razorpay_signature": razorpay_signature,
        "paid_at": paid_at,
        "assigned_coach_id": None,
        "assigned_coach_name": None,
        "assigned_at": None,
        "assigned_by": None,
        "user_id": user_id,
        "created_by": user_id or (actor.get("id") if _role(current_user) in {"student", "training_student"} else None),
        "created_at": now,
        "updated_at": now,
    }
    if extra_fields:
        doc.update(extra_fields)
    await db[COL].insert_one(doc)
    await _append_history(
        db,
        request_id=doc_id,
        from_status=None,
        to_status=TrainingRequestStatus.SUBMITTED.value,
        actor=actor,
        note="Request submitted",
        action="created",
    )

    # M15-S01: additive lead traceability (best-effort; never breaks request create)
    try:
        from utils.lead_service import upsert_from_source

        st_map = {
            "home": "training_home",
            "school": "training_school",
            "college": "training_college",
            "corporate": "training_corporate",
            "residential": "training_residential",
        }
        await upsert_from_source(
            source_type=st_map.get(request_type, f"training_{request_type}"),
            source_ref_type="training_request",
            source_ref_id=doc_id,
            name=contact_name,
            phone=contact_phone,
            email=contact_email,
            course=request_type,
            branch_id=bid,
            branch_name=bname,
            source_label=f"training_{request_type}",
        )
    except Exception:
        logger.exception("Lead hook after training request failed")

    try:
        from utils.ops_email_hooks import fire_training_request

        await fire_training_request(db, doc)
    except Exception:
        logger.exception("ops email after training request failed")

    return {
        "message": success_message,
        "request": serialize_doc(doc),
    }


async def create_training_payment_order(
    body: CreateTrainingOrderBody,
    *,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    """Public (optional auth) — create Razorpay order for training requests (home, school, college, corporate, residential)."""
    import os
    import secrets
    from utils.razorpay_client import get_razorpay_client

    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    req_type = (body.type or "").strip().lower()
    fee_field_map = {
        "home": "home_training_fee",
        "school": "school_training_fee",
        "college": "college_training_fee",
        "corporate": "corporate_training_fee",
        "residential": "residential_training_fee",
    }
    field_name = fee_field_map.get(req_type)
    if not field_name:
        raise HTTPException(status_code=400, detail=f"Invalid training type: {req_type}")

    settings_doc = await db.system_settings.find_one({}) or {}
    fee = 0.0
    if settings_doc.get(field_name) is not None:
        try:
            fee = float(settings_doc[field_name])
        except (TypeError, ValueError):
            fee = 0.0

    if fee <= 0:
        return {
            "fee_required": False,
            "amount_inr": 0.0,
            "amount_paise": 0,
            "currency": "INR",
        }

    client = get_razorpay_client()
    key_id = os.getenv("RAZORPAY_KEY_ID", "").strip()
    amount_paise = int(round(fee * 100))
    receipt = f"trn_{req_type[:4]}_{secrets.token_hex(4)}"

    name = (body.contact_name or "")[:80]
    email = (body.contact_email or "")[:80]
    phone = (body.contact_phone or "")[:32]

    order_payload = {
        "amount": amount_paise,
        "currency": "INR",
        "receipt": receipt,
        "notes": {
            "type": f"training_{req_type}",
            "contact_name": name,
            "contact_email": email,
            "contact_phone": phone,
        },
    }

    try:
        rp_order = client.order.create(data=order_payload)
        return {
            "fee_required": True,
            "order_id": rp_order["id"],
            "amount_paise": amount_paise,
            "amount_inr": fee,
            "currency": "INR",
            "key_id": key_id,
        }
    except Exception as e:
        logger.exception("Failed to create Razorpay order for training request: %s", e)
        raise HTTPException(
            status_code=400,
            detail=f"Failed to create payment order: {str(e)}",
        )


async def create_home_training_request(
    body: HomeTrainingRequestCreate,
    *,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    return await _insert_training_request(
        request_type=TrainingRequestType.HOME.value,
        contact_name=body.contact_name,
        contact_phone=body.contact_phone,
        contact_email=body.contact_email,
        branch_id=body.branch_id,
        branch_name=body.branch_name,
        source=body.source,
        notes=body.notes,
        details=_details_dict(body.details),
        current_user=current_user,
        success_message="Home training request submitted",
        razorpay_order_id=body.razorpay_order_id,
        razorpay_payment_id=body.razorpay_payment_id,
        razorpay_signature=body.razorpay_signature,
        password=body.password,
        is_phone_verified=body.is_phone_verified,
    )


async def create_school_training_request(
    body: SchoolTrainingRequestCreate,
    *,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    return await _insert_training_request(
        request_type=TrainingRequestType.SCHOOL.value,
        contact_name=body.contact_name,
        contact_phone=body.contact_phone,
        contact_email=body.contact_email,
        branch_id=body.branch_id,
        branch_name=body.branch_name,
        source=body.source,
        notes=body.notes,
        details=_details_dict(body.details),
        current_user=current_user,
        success_message="School training request submitted",
        razorpay_order_id=body.razorpay_order_id,
        razorpay_payment_id=body.razorpay_payment_id,
        razorpay_signature=body.razorpay_signature,
        password=body.password,
        is_phone_verified=body.is_phone_verified,
    )


async def create_college_training_request(
    body: CollegeTrainingRequestCreate,
    *,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    return await _insert_training_request(
        request_type=TrainingRequestType.COLLEGE.value,
        contact_name=body.contact_name,
        contact_phone=body.contact_phone,
        contact_email=body.contact_email,
        branch_id=body.branch_id,
        branch_name=body.branch_name,
        source=body.source,
        notes=body.notes,
        details=_details_dict(body.details),
        current_user=current_user,
        success_message="College training request submitted",
        razorpay_order_id=body.razorpay_order_id,
        razorpay_payment_id=body.razorpay_payment_id,
        razorpay_signature=body.razorpay_signature,
        password=body.password,
        is_phone_verified=body.is_phone_verified,
    )


async def create_corporate_training_request(
    body: CorporateTrainingRequestCreate,
    *,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    return await _insert_training_request(
        request_type=TrainingRequestType.CORPORATE.value,
        contact_name=body.contact_name,
        contact_phone=body.contact_phone,
        contact_email=body.contact_email,
        branch_id=body.branch_id,
        branch_name=body.branch_name,
        source=body.source,
        notes=body.notes,
        details=_details_dict(body.details),
        current_user=current_user,
        success_message="Corporate training request submitted",
        razorpay_order_id=body.razorpay_order_id,
        razorpay_payment_id=body.razorpay_payment_id,
        razorpay_signature=body.razorpay_signature,
        password=body.password,
        is_phone_verified=body.is_phone_verified,
    )


def list_residential_packages() -> Dict[str, Any]:
    packages = [p for p in DEFAULT_RESIDENTIAL_PACKAGES if p.get("is_active", True)]
    return {"packages": packages, "total": len(packages)}


def _get_package(package_id: str) -> dict:
    pid = (package_id or "").strip()
    for p in DEFAULT_RESIDENTIAL_PACKAGES:
        if p.get("id") == pid and p.get("is_active", True):
            return dict(p)
    return {
        "id": "residential_standard",
        "name": "Residential Training Program",
        "duration_days": 30,
        "duration_label": "Standard",
        "fee_total_inr": 0,
        "fee_pay_now_inr": 0,
    }


async def create_residential_training_request(
    body: ResidentialTrainingRequestCreate,
    *,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    details = _details_dict(body.details)
    details["package_id"] = details.get("package_id") or "residential_standard"
    details["package_name"] = details.get("package_name") or "Residential Training Program"
    if details.get("participant_phone"):
        details["participant_phone"] = _normalize_phone(str(details["participant_phone"]))
    if details.get("emergency_contact_phone"):
        details["emergency_contact_phone"] = _normalize_phone(
            str(details["emergency_contact_phone"])
        )

    return await _insert_training_request(
        request_type=TrainingRequestType.RESIDENTIAL.value,
        contact_name=body.contact_name,
        contact_phone=body.contact_phone,
        contact_email=body.contact_email,
        branch_id=body.branch_id,
        branch_name=body.branch_name,
        source=body.source,
        notes=body.notes,
        details=details,
        current_user=current_user,
        success_message="Residential training request submitted",
        razorpay_order_id=body.razorpay_order_id,
        razorpay_payment_id=body.razorpay_payment_id,
        razorpay_signature=body.razorpay_signature,
        password=body.password,
        is_phone_verified=body.is_phone_verified,
    )


async def create_residential_payment_order(
    request_id: str, *, current_user: Optional[dict] = None
) -> Dict[str, Any]:
    """Public (or optional auth) — create Razorpay order for a residential request (M06)."""
    import os

    from utils.razorpay_client import get_razorpay_client

    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": request_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Training request not found")
    if doc.get("type") != TrainingRequestType.RESIDENTIAL.value:
        raise HTTPException(status_code=400, detail="Payment only applies to residential requests")
    if doc.get("payment_status") == "paid":
        raise HTTPException(status_code=409, detail="This request is already paid")
    if doc.get("payment_status") == "not_required":
        raise HTTPException(status_code=400, detail="Payment is not required for this package")
    if doc.get("status") in {
        TrainingRequestStatus.REJECTED.value,
        TrainingRequestStatus.CANCELLED.value,
    }:
        raise HTTPException(status_code=400, detail="Cannot pay for a closed request")

    amount_paise = int(doc.get("amount_paise") or 0)
    if amount_paise < 100:
        fee_pay_now = int(doc.get("fee_pay_now_inr") or 0)
        amount_paise = int(fee_pay_now * 100)
    if amount_paise < 100:
        raise HTTPException(status_code=400, detail="Invalid payment amount")

    client = get_razorpay_client()
    try:
        order = client.order.create(
            {
                "amount": amount_paise,
                "currency": "INR",
                "payment_capture": 1,
                "notes": {
                    "training_request_id": request_id,
                    "type": "residential",
                    "participant": str(
                        (doc.get("details") or {}).get("participant_name") or ""
                    )[:80],
                },
            }
        )
    except Exception:
        logger.exception("Razorpay residential order.create failed")
        raise HTTPException(
            status_code=502,
            detail="We could not reach the payment service. Please try again in a moment.",
        )

    now = datetime.utcnow()
    await db[COL].update_one(
        {"id": request_id},
        {
            "$set": {
                "razorpay_order_id": order["id"],
                "amount_paise": amount_paise,
                "payment_status": "pending",
                "updated_at": now,
            }
        },
    )
    actor = _actor(current_user)
    await _append_history(
        db,
        request_id=request_id,
        from_status=str(doc.get("status") or ""),
        to_status=str(doc.get("status") or ""),
        actor=actor,
        note=f"Payment order created: {order['id']}",
        action="payment_order_created",
    )
    return {
        "order": {"id": order["id"], "amount": amount_paise, "currency": "INR"},
        "key": os.getenv("RAZORPAY_KEY_ID", "").strip(),
        "request_id": request_id,
    }


async def verify_residential_payment(
    request_id: str,
    body: TrainingRequestPaymentVerify,
    *,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    """Public — verify Razorpay payment for residential training request (M06)."""
    from utils.razorpay_client import verify_razorpay_signature

    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": request_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Training request not found")
    if doc.get("type") != TrainingRequestType.RESIDENTIAL.value:
        raise HTTPException(status_code=400, detail="Payment only applies to residential requests")

    if (
        doc.get("payment_status") == "paid"
        and (doc.get("razorpay_payment_id") or "") == body.razorpay_payment_id
    ):
        return {
            "message": "Already paid",
            "request": serialize_doc(doc),
        }

    stored_order = (doc.get("razorpay_order_id") or "").strip()
    if stored_order and stored_order != body.razorpay_order_id:
        raise HTTPException(status_code=400, detail="Order does not match this request")

    if not verify_razorpay_signature(
        body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature
    ):
        raise HTTPException(status_code=400, detail="Invalid payment signature")

    now = datetime.utcnow()
    actor = _actor(current_user)
    await db[COL].update_one(
        {"id": request_id},
        {
            "$set": {
                "payment_status": "paid",
                "razorpay_order_id": body.razorpay_order_id,
                "razorpay_payment_id": body.razorpay_payment_id,
                "razorpay_signature": body.razorpay_signature,
                "paid_at": now,
                "updated_at": now,
            }
        },
    )
    await _append_history(
        db,
        request_id=request_id,
        from_status=str(doc.get("status") or ""),
        to_status=str(doc.get("status") or ""),
        actor=actor,
        note=f"Payment verified: {body.razorpay_payment_id}",
        action="payment_paid",
    )
    updated = await db[COL].find_one({"id": request_id})
    return {"message": "Payment verified", "request": serialize_doc(updated)}


async def _scoped_list_query(
    db,
    current_user: dict,
    *,
    type_filter: Optional[str] = None,
    status: Optional[str] = None,
    branch_id: Optional[str] = None,
    payment_status: Optional[str] = None,
    assigned_coach_id: Optional[str] = None,
    unassigned_only: bool = False,
    search: Optional[str] = None,
) -> Dict[str, Any]:
    """Shared filter builder for list + summary (BM branch scoping included)."""
    await _assert_admin_access(db, current_user)
    q: Dict[str, Any] = {}
    ands: List[Dict[str, Any]] = []
    role = _role(current_user)
    managed: List[str] = []
    if role == "branch_manager":
        managed = await get_managed_branch_ids_for_user(db, current_user)
        if not managed:
            return {"__empty__": True}
        ands.append(
            {
                "$or": [
                    {"branch_id": {"$in": managed}},
                    {"branch_id": None},
                    {"branch_id": ""},
                ]
            }
        )

    if branch_id:
        bid = str(branch_id).strip()
        if role == "branch_manager" and bid and bid not in {str(b) for b in managed}:
            raise HTTPException(
                status_code=403, detail="Branch outside managed branches"
            )
        q["branch_id"] = bid

    if type_filter:
        q["type"] = str(type_filter).strip().lower()
    if status:
        q["status"] = str(status).strip().lower()
    if payment_status:
        q["payment_status"] = str(payment_status).strip().lower()
    if unassigned_only:
        ands.append(
            {
                "$or": [
                    {"assigned_coach_id": None},
                    {"assigned_coach_id": ""},
                    {"assigned_coach_id": {"$exists": False}},
                ]
            }
        )
    elif assigned_coach_id:
        q["assigned_coach_id"] = str(assigned_coach_id).strip()

    if search:
        term = search.strip()
        if term:
            rx = {"$regex": re.escape(term), "$options": "i"}
            search_or: List[Dict[str, Any]] = [
                {"contact_name": rx},
                {"contact_phone": rx},
                {"contact_email": rx},
                {"details.participant_name": rx},
                {"details.participant_phone": rx},
                {"details.school_name": rx},
                {"details.college_name": rx},
                {"details.organization_name": rx},
                {"details.package_name": rx},
                {"details.city": rx},
                {"details.state": rx},
                {"assigned_coach_name": rx},
                {"branch_name": rx},
            ]
            digits = re.sub(r"\D+", "", term)
            if digits:
                digit_rx = {"$regex": re.escape(digits), "$options": "i"}
                search_or.extend(
                    [
                        {"contact_phone": digit_rx},
                        {"details.participant_phone": digit_rx},
                    ]
                )
            ands.append({"$or": search_or})

    if ands:
        q["$and"] = ands
    return q


async def list_training_requests(
    *,
    current_user: dict,
    type_filter: Optional[str] = None,
    status: Optional[str] = None,
    branch_id: Optional[str] = None,
    payment_status: Optional[str] = None,
    assigned_coach_id: Optional[str] = None,
    unassigned_only: bool = False,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_training_request_indexes(db)

    q = await _scoped_list_query(
        db,
        current_user,
        type_filter=type_filter,
        status=status,
        branch_id=branch_id,
        payment_status=payment_status,
        assigned_coach_id=assigned_coach_id,
        unassigned_only=unassigned_only,
        search=search,
    )
    if q.get("__empty__"):
        return {"requests": [], "total": 0, "count": 0}

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
    return {"requests": serialize_doc(rows), "total": total, "count": len(rows)}


async def summarize_training_requests(
    *,
    current_user: dict,
    branch_id: Optional[str] = None,
) -> Dict[str, Any]:
    """M12-S06 unified admin summary: totals by type and status (BM-scoped)."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_training_request_indexes(db)

    q = await _scoped_list_query(db, current_user, branch_id=branch_id)
    if q.get("__empty__"):
        return {
            "total": 0,
            "by_type": {},
            "by_status": {},
            "by_payment_status": {},
            "unassigned_coach": 0,
        }

    pipeline = [
        {"$match": q},
        {
            "$facet": {
                "total": [{"$count": "n"}],
                "by_type": [
                    {"$group": {"_id": "$type", "count": {"$sum": 1}}},
                ],
                "by_status": [
                    {"$group": {"_id": "$status", "count": {"$sum": 1}}},
                ],
                "by_payment_status": [
                    {
                        "$match": {"type": "residential"},
                    },
                    {"$group": {"_id": "$payment_status", "count": {"$sum": 1}}},
                ],
                "unassigned_coach": [
                    {
                        "$match": {
                            "$or": [
                                {"assigned_coach_id": None},
                                {"assigned_coach_id": ""},
                                {"assigned_coach_id": {"$exists": False}},
                            ],
                            "status": {
                                "$nin": ["completed", "rejected", "cancelled"]
                            },
                        }
                    },
                    {"$count": "n"},
                ],
            }
        },
    ]
    agg = await db[COL].aggregate(pipeline).to_list(length=1)
    facet = (agg or [{}])[0]

    def _map(rows):
        out: Dict[str, int] = {}
        for r in rows or []:
            key = str(r.get("_id") or "unknown")
            out[key] = int(r.get("count") or 0)
        return out

    total_rows = facet.get("total") or []
    unassigned_rows = facet.get("unassigned_coach") or []
    return {
        "total": int((total_rows[0] or {}).get("n") or 0) if total_rows else 0,
        "by_type": _map(facet.get("by_type")),
        "by_status": _map(facet.get("by_status")),
        "by_payment_status": _map(facet.get("by_payment_status")),
        "unassigned_coach": int((unassigned_rows[0] or {}).get("n") or 0)
        if unassigned_rows
        else 0,
    }


async def get_training_request(
    request_id: str, *, current_user: dict
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": request_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Training request not found")
    await _assert_admin_access(db, current_user, doc)
    history = (
        await db[COL_HISTORY]
        .find({"request_id": request_id})
        .sort([("created_at", -1)])
        .to_list(length=100)
    )
    return {
        "request": serialize_doc(doc),
        "status_history": serialize_doc(history),
    }


async def update_training_request_status(
    request_id: str,
    body: TrainingRequestStatusUpdate,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": request_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Training request not found")
    await _assert_admin_access(db, current_user, doc)

    current = str(doc.get("status") or "")
    new_status = body.status.value
    if current == new_status:
        return {"message": "Status unchanged", "request": serialize_doc(doc)}

    allowed = ALLOWED_STATUS_TRANSITIONS.get(current) or set()
    if new_status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot transition from '{current}' to '{new_status}'",
        )

    now = datetime.utcnow()
    actor = _actor(current_user)
    await db[COL].update_one(
        {"id": request_id},
        {"$set": {"status": new_status, "updated_at": now}},
    )
    await _append_history(
        db,
        request_id=request_id,
        from_status=current,
        to_status=new_status,
        actor=actor,
        note=body.note,
        action="status_change",
    )
    updated = await db[COL].find_one({"id": request_id})
    return {"message": "Status updated", "request": serialize_doc(updated)}


async def assign_coach(
    request_id: str,
    body: TrainingRequestCoachAssign,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": request_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Training request not found")
    await _assert_admin_access(db, current_user, doc)

    if doc.get("status") in {
        TrainingRequestStatus.COMPLETED.value,
        TrainingRequestStatus.REJECTED.value,
        TrainingRequestStatus.CANCELLED.value,
    }:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot assign coach when status is {doc.get('status')}",
        )

    coach_ids = _assign_coach_ids(body)
    now = datetime.utcnow()
    actor = _actor(current_user)
    prev_status = str(doc.get("status") or "")

    # —— Single-coach assign (legacy behavior) ——
    if len(coach_ids) == 1:
        coach_id = coach_ids[0]
        _, coach_name = await _resolve_coach_record(db, coach_id)
        new_status = prev_status
        if prev_status in {
            TrainingRequestStatus.SUBMITTED.value,
            TrainingRequestStatus.UNDER_REVIEW.value,
            TrainingRequestStatus.COACH_ASSIGNED.value,
        }:
            new_status = TrainingRequestStatus.COACH_ASSIGNED.value

        await db[COL].update_one(
            {"id": request_id},
            {
                "$set": {
                    "assigned_coach_id": coach_id,
                    "assigned_coach_name": coach_name,
                    "notified_coach_ids": [coach_id],
                    "notified_coaches": [
                        {
                            "coach_id": coach_id,
                            "coach_name": coach_name,
                            "notified_at": now,
                            "status": "pending",
                        }
                    ],
                    "assigned_at": now,
                    "assigned_by": actor.get("id"),
                    "status": new_status,
                    "coach_assignment_status": "pending",
                    "coach_rejection_reason": None,
                    "coach_response_note": None,
                    "coach_responded_at": None,
                    "updated_at": now,
                }
            },
        )
        await _append_history(
            db,
            request_id=request_id,
            from_status=prev_status,
            to_status=new_status,
            actor=actor,
            note=body.note or f"Assigned coach: {coach_name}",
            action="coach_assigned",
        )
        updated = await db[COL].find_one({"id": request_id})
        return {"message": "Coach assigned", "request": serialize_doc(updated)}

    # —— Multi-coach notify (first accept wins) ——
    notified: List[Dict[str, Any]] = []
    for cid in coach_ids:
        coach, coach_name = await _resolve_coach_record(db, cid)
        notified.append(
            {
                "coach_id": cid,
                "coach_name": coach_name,
                "notified_at": now,
                "status": "pending",
            }
        )

    new_status = TrainingRequestStatus.UNDER_REVIEW.value
    if prev_status in {
        TrainingRequestStatus.SUBMITTED.value,
        TrainingRequestStatus.UNDER_REVIEW.value,
    }:
        new_status = TrainingRequestStatus.UNDER_REVIEW.value
    elif prev_status == TrainingRequestStatus.COACH_ASSIGNED.value:
        new_status = TrainingRequestStatus.UNDER_REVIEW.value

    name_list = ", ".join(n["coach_name"] for n in notified)
    await db[COL].update_one(
        {"id": request_id},
        {
            "$set": {
                "assigned_coach_id": None,
                "assigned_coach_name": None,
                "notified_coach_ids": coach_ids,
                "notified_coaches": notified,
                "assigned_at": now,
                "assigned_by": actor.get("id"),
                "status": new_status,
                "coach_assignment_status": "pending_multi",
                "coach_rejection_reason": None,
                "coach_response_note": None,
                "coach_responded_at": None,
                "updated_at": now,
            }
        },
    )
    await _append_history(
        db,
        request_id=request_id,
        from_status=prev_status,
        to_status=new_status,
        actor=actor,
        note=body.note or f"Notified coaches: {name_list}",
        action="coaches_notified",
    )
    updated = await db[COL].find_one({"id": request_id})
    return {
        "message": "Coaches notified; awaiting first acceptance",
        "request": serialize_doc(updated),
    }


async def list_status_history(
    request_id: str, *, current_user: dict
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": request_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Training request not found")
    await _assert_admin_access(db, current_user, doc)
    rows = (
        await db[COL_HISTORY]
        .find({"request_id": request_id})
        .sort([("created_at", -1)])
        .to_list(length=200)
    )
    return {"history": serialize_doc(rows), "total": len(rows)}


async def list_coach_training_requests(
    *,
    current_user: dict,
    status_filter: Optional[str] = None,
    type_filter: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> Dict[str, Any]:
    """M12 List training requests assigned to the logged-in coach with tab counts."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    coach_id = str(current_user.get("id") or "")
    if not coach_id:
        raise HTTPException(status_code=401, detail="Coach authentication required")

    base_query: Dict[str, Any] = _coach_visible_query(coach_id)

    visible_rows = (
        await db[COL]
        .find(base_query)
        .sort([("assigned_at", -1), ("created_at", -1)])
        .to_list(length=500)
    )
    counts = {"all": 0, "pending": 0, "accepted": 0, "rejected": 0}
    for row in visible_rows:
        counts["all"] += 1
        st = _coach_inbox_status(row, coach_id)
        if st == "pending":
            counts["pending"] += 1
        elif st == "accepted":
            counts["accepted"] += 1
        elif st == "rejected":
            counts["rejected"] += 1

    query_clauses: List[Dict[str, Any]] = [base_query]

    if type_filter and type_filter != "all":
        query_clauses.append({"type": type_filter.lower().strip()})

    if search and search.strip():
        s = search.strip()
        pattern = re.compile(re.escape(s), re.IGNORECASE)
        query_clauses.append(
            {
                "$or": [
                    {"contact_name": pattern},
                    {"contact_phone": pattern},
                    {"contact_email": pattern},
                    {"id": pattern},
                    {"notes": pattern},
                    {"details.city": pattern},
                    {"details.address_line1": pattern},
                    {"details.school_name": pattern},
                    {"details.college_name": pattern},
                    {"details.organization_name": pattern},
                    {"details.training_type": pattern},
                    {"details.training_goals": pattern},
                ]
            }
        )

    mongo_query: Dict[str, Any] = (
        {"$and": query_clauses} if len(query_clauses) > 1 else query_clauses[0]
    )
    candidate_rows = (
        await db[COL]
        .find(mongo_query)
        .sort([("assigned_at", -1), ("created_at", -1)])
        .to_list(length=500)
    )

    sf = (status_filter or "").lower().strip()
    if sf and sf != "all":
        filtered: List[dict] = []
        for row in candidate_rows:
            st = _coach_inbox_status(row, coach_id)
            if sf == "pending" and st == "pending":
                filtered.append(row)
            elif sf == "accepted" and st == "accepted":
                filtered.append(row)
            elif sf == "rejected" and st == "rejected":
                filtered.append(row)
        candidate_rows = filtered

    total = len(candidate_rows)
    skip = max(0, skip)
    limit = max(1, min(limit, 200))
    page_rows = candidate_rows[skip : skip + limit]
    return {
        "requests": [_serialize_for_coach_inbox(r, coach_id) for r in page_rows],
        "total": total,
        "counts": counts,
    }


async def coach_respond_to_request(
    request_id: str,
    body: CoachTrainingRequestResponse,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    """Coach accepts or rejects an assigned training request. Rejection requires a reason."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    doc = await db[COL].find_one({"id": request_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Training request not found")

    coach_id = str(current_user.get("id") or "")
    if not coach_id:
        raise HTTPException(status_code=401, detail="Coach authentication required")

    assigned = str(doc.get("assigned_coach_id") or "").strip()
    notified_ids = [str(x) for x in (doc.get("notified_coach_ids") or [])]
    is_multi = (
        str(doc.get("coach_assignment_status") or "").lower() == "pending_multi"
        or (len(notified_ids) > 1 and not assigned)
    )
    is_legacy_single = assigned == coach_id
    is_notified = coach_id in notified_ids

    if not is_legacy_single and not is_notified:
        raise HTTPException(status_code=403, detail="You are not assigned to this training request")

    if body.action == "reject" and not (body.reason or "").strip():
        raise HTTPException(status_code=400, detail="Rejection reason is required")

    now = datetime.utcnow()
    actor = _actor(current_user)
    prev_status = str(doc.get("status") or "")
    coach_name = actor.get("name") or "Coach"

    if body.action == "accept":
        if is_multi or (is_notified and not assigned):
            taken = assigned and assigned != coach_id
            if taken:
                raise HTTPException(
                    status_code=409,
                    detail="This training request has already been accepted by another coach.",
                )
            _, resolved_name = await _resolve_coach_record(db, coach_id)
            coach_name = resolved_name
            filter_doc: Dict[str, Any] = {
                "id": request_id,
                "notified_coach_ids": coach_id,
                "$or": [
                    {"assigned_coach_id": None},
                    {"assigned_coach_id": ""},
                    {"assigned_coach_id": {"$exists": False}},
                ],
                "coach_assignment_status": {
                    "$in": ["pending_multi", "pending", None, ""],
                },
            }
            notified_rows = list(doc.get("notified_coaches") or [])
            updated_notified: List[Dict[str, Any]] = []
            for row in notified_rows:
                rid = str(row.get("coach_id") or "")
                item = dict(row)
                if rid == coach_id:
                    item["status"] = "accepted"
                elif rid in notified_ids:
                    item["status"] = "notified_taken"
                updated_notified.append(item)

            new_status = TrainingRequestStatus.COACH_ASSIGNED.value
            update_set = {
                "assigned_coach_id": coach_id,
                "assigned_coach_name": coach_name,
                "coach_assignment_status": "accepted",
                "coach_responded_at": now,
                "coach_response_note": body.note,
                "coach_rejection_reason": None,
                "notified_coaches": updated_notified,
                "status": new_status,
                "updated_at": now,
            }
            result = await db[COL].find_one_and_update(
                filter_doc,
                {"$set": update_set},
                return_document=ReturnDocument.AFTER,
            )
            if not result:
                fresh = await db[COL].find_one({"id": request_id}) or {}
                if str(fresh.get("assigned_coach_id") or "") not in {"", coach_id}:
                    raise HTTPException(
                        status_code=409,
                        detail="This training request has already been accepted by another coach.",
                    )
                raise HTTPException(
                    status_code=400,
                    detail="Unable to accept this request. It may no longer be available.",
                )
            note_text = body.note or f"Coach {coach_name} accepted the training request"
            await _append_history(
                db,
                request_id=request_id,
                from_status=prev_status,
                to_status=new_status,
                actor=actor,
                note=note_text,
                action="coach_accepted",
            )
            updated = await db[COL].find_one({"id": request_id})
            return {
                "message": "Training request accepted successfully",
                "request": serialize_doc(updated),
            }

        # Legacy single-coach accept
        if str(doc.get("coach_assignment_status") or "").lower() == "accepted":
            raise HTTPException(status_code=400, detail="You have already accepted this request")
        update_data = {
            "coach_assignment_status": "accepted",
            "coach_responded_at": now,
            "coach_response_note": body.note,
            "updated_at": now,
        }
        await db[COL].update_one({"id": request_id}, {"$set": update_data})
        note_text = body.note or f"Coach {coach_name} accepted the training request"
        await _append_history(
            db,
            request_id=request_id,
            from_status=prev_status,
            to_status=prev_status,
            actor=actor,
            note=note_text,
            action="coach_accepted",
        )
        updated = await db[COL].find_one({"id": request_id})
        return {
            "message": "Training request accepted successfully",
            "request": serialize_doc(updated),
        }

    # Reject
    reason_text = (body.reason or "").strip()
    if is_multi or (is_notified and not assigned):
        notified_rows = list(doc.get("notified_coaches") or [])
        updated_notified: List[Dict[str, Any]] = []
        found = False
        for row in notified_rows:
            item = dict(row)
            if str(item.get("coach_id") or "") == coach_id:
                item["status"] = "rejected"
                found = True
            updated_notified.append(item)
        if not found:
            updated_notified.append(
                {
                    "coach_id": coach_id,
                    "coach_name": coach_name,
                    "notified_at": now,
                    "status": "rejected",
                }
            )
        await db[COL].update_one(
            {"id": request_id},
            {"$set": {"notified_coaches": updated_notified, "updated_at": now}},
        )
        note_text = f"Coach {coach_name} declined: {reason_text}"
        await _append_history(
            db,
            request_id=request_id,
            from_status=prev_status,
            to_status=prev_status,
            actor=actor,
            note=note_text,
            action="coach_rejected",
        )
        updated = await db[COL].find_one({"id": request_id})
        return {
            "message": "Training request rejected successfully",
            "request": _serialize_for_coach_inbox(updated or {}, coach_id),
        }

    update_data = {
        "coach_assignment_status": "rejected",
        "coach_rejection_reason": reason_text,
        "coach_response_note": body.note,
        "coach_responded_at": now,
        "updated_at": now,
    }
    note_text = f"Coach {coach_name} rejected: {reason_text}" + (
        f" (Note: {body.note})" if body.note else ""
    )
    await db[COL].update_one({"id": request_id}, {"$set": update_data})
    await _append_history(
        db,
        request_id=request_id,
        from_status=prev_status,
        to_status=prev_status,
        actor=actor,
        note=note_text,
        action="coach_rejected",
    )
    updated = await db[COL].find_one({"id": request_id})
    return {
        "message": "Training request rejected successfully",
        "request": serialize_doc(updated),
    }


async def training_client_login(
    credentials: TrainingLoginRequest,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")

    login_identifier = (credentials.email or "").strip().lower()
    norm_phone = _normalize_phone(credentials.email)

    user = await db.users.find_one({"email": login_identifier})
    if not user and norm_phone:
        user = await db.users.find_one({"phone": norm_phone})

    if not user:
        raise HTTPException(status_code=401, detail="Invalid email/phone or password")

    stored_hash = user.get("password") or user.get("password_hash")
    if not stored_hash or not verify_password(credentials.password, stored_hash):
        raise HTTPException(status_code=401, detail="Invalid email/phone or password")

    if user.get("is_active") is False:
        raise HTTPException(status_code=400, detail="Account is deactivated. Please contact support.")

    user_role = str(user.get("role") or "training_student")
    token = create_access_token(
        data={"sub": user["id"], "role": user_role}
    )

    user_out = serialize_doc(user)
    user_out.pop("password", None)
    user_out.pop("password_hash", None)

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": user_out,
    }


async def list_my_training_requests(
    current_user: dict,
    status_filter: Optional[str] = None,
    type_filter: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    uid = current_user.get("id")
    uemail = (current_user.get("email") or "").strip().lower()
    uphone = _normalize_phone(current_user.get("phone") or "")

    or_clauses: List[Dict[str, Any]] = []
    if uid:
        or_clauses.append({"user_id": uid})
        or_clauses.append({"created_by": uid})
    if uemail:
        or_clauses.append({"contact_email": {"$regex": f"^{re.escape(uemail)}$", "$options": "i"}})
    if uphone:
        or_clauses.append({"contact_phone": uphone})

    if not or_clauses:
        return {"requests": [], "total": 0}

    q: Dict[str, Any] = {"$or": or_clauses}

    if status_filter and status_filter != "all":
        q["status"] = status_filter
    if type_filter and type_filter != "all":
        q["type"] = type_filter
    if search and search.strip():
        s = search.strip()
        q = {
            "$and": [
                {"$or": or_clauses},
                {
                    "$or": [
                        {"id": {"$regex": re.escape(s), "$options": "i"}},
                        {"contact_name": {"$regex": re.escape(s), "$options": "i"}},
                        {"contact_phone": {"$regex": re.escape(s), "$options": "i"}},
                        {"branch_name": {"$regex": re.escape(s), "$options": "i"}},
                        {"details.organization_name": {"$regex": re.escape(s), "$options": "i"}},
                        {"details.college_name": {"$regex": re.escape(s), "$options": "i"}},
                        {"details.school_name": {"$regex": re.escape(s), "$options": "i"}},
                        {"details.participant_name": {"$regex": re.escape(s), "$options": "i"}},
                    ]
                }
            ]
        }

    total = await db[COL].count_documents(q)
    cursor = db[COL].find(q).sort("created_at", -1).skip(skip).limit(limit)
    docs = await cursor.to_list(length=limit)

    return {
        "requests": [serialize_doc(d) for d in docs],
        "total": total,
    }

