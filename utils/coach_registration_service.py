"""
M14-S01 Coach self-registration service.

Additive: does not replace admin CoachController.create_coach.
Writes to coaches collection with approval_status=pending.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, UploadFile

from models.coach_models import (
    CoachApprovalStatus,
    CoachRegistrationCreate,
)
from utils.auth import hash_password
from utils.database import get_db
from utils.helpers import serialize_doc

logger = logging.getLogger(__name__)

COL = "coaches"


async def ensure_coach_registration_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL].create_index("approval_status")
        await database[COL].create_index("user_id")
        await database[COL].create_index("service_location_ids")
        await database[COL].create_index(
            [("registration_source", 1), ("created_at", -1)]
        )
    except Exception:
        logger.exception("Failed ensuring coach registration indexes")


def _normalize_phone(country_code: str, phone: str) -> tuple[str, str]:
    cc = (country_code or "+91").strip() or "+91"
    if not cc.startswith("+"):
        cc = f"+{cc}"
    local = re.sub(r"\s+", "", (phone or "").strip())
    return cc, local


async def _validate_service_locations(db, location_ids: List[str]) -> List[Dict[str, str]]:
    ids = [str(i).strip() for i in (location_ids or []) if str(i).strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="Select at least one service location")
    # de-dupe preserve order
    seen = set()
    unique = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            unique.append(i)

    resolved: List[Dict[str, str]] = []
    for bid in unique:
        branch = await db.branches.find_one({"id": bid})
        if not branch:
            raise HTTPException(status_code=400, detail=f"Invalid service location: {bid}")
        if branch.get("is_active") is False:
            raise HTTPException(
                status_code=400, detail=f"Service location is inactive: {bid}"
            )
        name = (
            (branch.get("branch") or {}).get("name")
            or branch.get("name")
            or bid
        )
        resolved.append({"id": bid, "name": str(name)})
    return resolved


async def get_coach_registration_options() -> Dict[str, Any]:
    """Public options for the registration form."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    branches = (
        await db.branches.find({"is_active": {"$ne": False}})
        .sort([("branch.name", 1)])
        .to_list(length=300)
    )
    branch_opts = []
    for b in branches:
        branch_opts.append(
            {
                "id": b.get("id"),
                "name": (b.get("branch") or {}).get("name") or b.get("name") or b.get("id"),
            }
        )

    async def _dropdown(category: str) -> List[Dict[str, Any]]:
        try:
            from controllers.dropdown_settings_controller import (
                DropdownSettingsController,
            )

            return await DropdownSettingsController.get_category_options(category)
        except Exception:
            return []

    return {
        "branches": branch_opts,
        "specializations": await _dropdown("specializations"),
        "experience_ranges": await _dropdown("experience_ranges"),
        "genders": await _dropdown("genders"),
        "designations": await _dropdown("designations"),
        "countries": await _dropdown("countries"),
    }


async def register_coach(
    body: CoachRegistrationCreate,
    *,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_coach_registration_indexes(db)

    try:
        datetime.strptime(body.date_of_birth, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="Invalid date format. Use YYYY-MM-DD"
        ) from exc

    specs = [s.strip() for s in (body.specializations or []) if s and str(s).strip()]
    if not specs:
        raise HTTPException(status_code=400, detail="Select at least one specialization")

    locations = await _validate_service_locations(db, body.service_location_ids)
    location_ids = [loc["id"] for loc in locations]
    location_names = [loc["name"] for loc in locations]

    cc, local_phone = _normalize_phone(body.country_code, body.phone)
    full_phone = f"{cc}{local_phone}"
    email = str(body.email).strip().lower()

    existing = await db[COL].find_one(
        {
            "$or": [
                {"email": email},
                {"contact_info.email": email},
                {"phone": full_phone},
                {"contact_info.phone": local_phone},
            ]
        }
    )
    if existing:
        raise HTTPException(
            status_code=400, detail="Coach with this email or phone already exists"
        )

    user_id = (body.user_id or "").strip() or None
    if current_user and current_user.get("id") and not user_id:
        # Link logged-in student/user if they self-register
        role = str(current_user.get("role") or "").lower()
        if role in {"student", "user"}:
            user_id = current_user.get("id")

    coach_id = str(uuid.uuid4())
    full_name = f"{body.first_name.strip()} {body.last_name.strip()}".strip()
    now = datetime.utcnow()
    designation = (body.designation or "Coach").strip() or "Coach"
    education = (body.education_qualification or "").strip() or "Not specified"

    doc = {
        "id": coach_id,
        "personal_info": {
            "first_name": body.first_name.strip(),
            "last_name": body.last_name.strip(),
            "gender": body.gender.strip(),
            "date_of_birth": body.date_of_birth,
        },
        "contact_info": {
            "email": email,
            "country_code": cc,
            "phone": local_phone,
        },
        "address_info": {
            "address": body.address.strip(),
            "area": (body.area or "").strip(),
            "city": body.city.strip(),
            "state": body.state.strip(),
            "zip_code": (body.zip_code or "").strip(),
            "country": (body.country or "India").strip() or "India",
        },
        "professional_info": {
            "education_qualification": education,
            "professional_experience": body.professional_experience.strip(),
            "designation_id": designation,
            "certifications": [c.strip() for c in (body.certifications or []) if c.strip()],
            "qualifications": [],
            "category_id": None,
            "sub_category_id": None,
        },
        "areas_of_expertise": specs,
        "branch_id": location_ids[0],
        "service_location_ids": location_ids,
        "service_location_names": location_names,
        "assignment_details": {"courses": [], "salary": None, "join_date": None},
        "emergency_contact": {"name": None, "phone": None, "relationship": None},
        "email": email,
        "phone": full_phone,
        "first_name": body.first_name.strip(),
        "last_name": body.last_name.strip(),
        "full_name": full_name,
        "role": "coach",
        "password_hash": hash_password(body.password),
        "profile_image_url": (body.profile_image_url or "").strip() or None,
        "about_short": (body.about_short or "").strip() or None,
        "featured_on_homepage": False,
        "homepage_rating": None,
        "display_order": None,
        # M14 registration gate
        "approval_status": CoachApprovalStatus.PENDING.value,
        "is_active": False,
        "user_id": user_id,
        "registration_source": "self",
        "created_at": now,
        "updated_at": now,
    }
    await db[COL].insert_one(doc)

    # M14-S02: seed approval history (best-effort)
    try:
        from utils.coach_approval_service import record_registration_submitted

        await record_registration_submitted(db, coach_id, current_user)
    except Exception:
        logger.exception("Failed recording registration history for %s", coach_id)

    safe = serialize_doc(doc)
    safe.pop("password_hash", None)
    return {
        "message": "Coach registration submitted. Awaiting admin approval.",
        "coach_id": coach_id,
        "approval_status": CoachApprovalStatus.PENDING.value,
        "coach": safe,
    }


async def upload_registration_photo(file: UploadFile) -> Dict[str, Any]:
    """Public image-only upload for coach registration (additive; auth upload unchanged)."""
    from controllers.upload_controller import (
        ALLOWED_IMAGES,
        MAX_IMAGE_SIZE,
        UPLOAD_ROOT,
        _safe_filename,
    )

    content_type = file.content_type or ""
    if content_type not in ALLOWED_IMAGES:
        raise HTTPException(
            status_code=400,
            detail="Only image uploads are allowed for coach registration photos",
        )
    data = await file.read()
    if len(data) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=400, detail="Image too large (max 10 MB)")

    dest_dir = UPLOAD_ROOT / "images" / "coach-registration"
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_filename(file.filename or "coach.jpg")
    dest_path = dest_dir / safe_name
    dest_path.write_bytes(data)
    file_url = f"/uploads/images/coach-registration/{safe_name}"
    return {
        "message": "Photo uploaded",
        "file_url": file_url,
        "filename": safe_name,
        "content_type": content_type,
        "size": len(data),
    }
