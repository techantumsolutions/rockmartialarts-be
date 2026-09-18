"""
M08-S03 student ID card helpers.

Creates unique card numbers + non-predictable QR tokens (never raw student UUID).
Placeholder approved fields until client confirms final ID_CARD_FIELDS (T01).
"""
from __future__ import annotations

import base64
import io
import logging
import os
import secrets
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from utils.database import get_db
from utils.helpers import serialize_doc
from utils.student_status_service import assert_can_manage_student_status

logger = logging.getLogger(__name__)

# Placeholder until client confirms final visible fields (M08-S03-T01).
ID_CARD_VISIBLE_FIELDS = [
    "full_name",
    "card_number",
    "student_ref",
    "branch_name",
    "course_name",
    "account_status",
    "photo_url",
]

# Fields returned by public verify (non-sensitive subset).
# Never include: email, phone, address, student UUID, qr_token, biometric_id.
VERIFY_VISIBLE_FIELDS = [
    "full_name",
    "card_number",
    "branch_name",
    "course_name",
    "account_status",
    "photo_url",
    "card_status",
]

# Keys that must never appear in public verify payloads.
VERIFY_FORBIDDEN_KEYS = {
    "email",
    "phone",
    "mobile",
    "address",
    "student_id",
    "id",
    "qr_token",
    "biometric_id",
    "essl_user_id",
    "date_of_birth",
    "dob",
    "emergency_contact",
    "password",
    "hashed_password",
}


def _frontend_base() -> str:
    return (
        (os.getenv("FRONTEND_URL") or os.getenv("NEXT_PUBLIC_APP_URL") or os.getenv("NEXT_PUBLIC_SITE_URL") or "")
        .strip()
        .rstrip("/")
    ) or "http://localhost:3000"


def build_verify_url(qr_token: str) -> str:
    return f"{_frontend_base()}/verify/student/{qr_token}"


async def ensure_student_id_card_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database.student_id_cards.create_index("id", unique=True)
        await database.student_id_cards.create_index("card_number", unique=True)
        await database.student_id_cards.create_index("qr_token", unique=True)
        await database.student_id_cards.create_index(
            [("student_id", 1), ("status", 1), ("created_at", -1)]
        )
    except Exception:
        logger.exception("Failed ensuring student_id_cards indexes")


def generate_card_number() -> str:
    """Human-readable unique card reference (not the student UUID)."""
    stamp = datetime.utcnow().strftime("%y%m%d")
    suffix = secrets.token_hex(3).upper()
    return f"RMA-{stamp}-{suffix}"


def generate_qr_token() -> str:
    """Non-predictable opaque token for QR / public verify."""
    return secrets.token_urlsafe(32)


def _student_display_name(student: dict) -> str:
    name = (student.get("full_name") or "").strip()
    if name:
        return name
    return f"{student.get('first_name', '')} {student.get('last_name', '')}".strip() or "Student"


def _photo_url(student: dict) -> Optional[str]:
    for key in ("profile_image", "profile_photo", "photo"):
        val = student.get(key)
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    return None


async def _resolve_branch_and_course(db, student: dict) -> Dict[str, str]:
    student_id = student.get("id")
    branch_name = ""
    course_name = ""

    enrollment = await db.enrollments.find_one(
        {"student_id": student_id, "is_active": True},
        sort=[("created_at", -1)],
    )
    if not enrollment:
        enrollment = await db.enrollments.find_one(
            {"student_id": student_id},
            sort=[("created_at", -1)],
        )

    if enrollment:
        if enrollment.get("branch_id"):
            branch = await db.branches.find_one({"id": enrollment["branch_id"]})
            if branch:
                branch_name = (
                    (branch.get("branch") or {}).get("name")
                    or branch.get("name")
                    or ""
                )
        if enrollment.get("course_id"):
            course = await db.courses.find_one({"id": enrollment["course_id"]})
            if course:
                course_name = course.get("title") or course.get("name") or ""

    if not branch_name and student.get("branch_id"):
        branch = await db.branches.find_one({"id": student["branch_id"]})
        if branch:
            branch_name = (
                (branch.get("branch") or {}).get("name") or branch.get("name") or ""
            )

    return {"branch_name": branch_name or "—", "course_name": course_name or "—"}


def build_qr_png_base64(verify_url: str) -> str:
    """Return base64 PNG of QR encoding the verify URL only (no raw student id)."""
    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_M
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="QR generation dependency missing (qrcode).",
        ) from exc

    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, box_size=6, border=2)
    qr.add_data(verify_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def build_id_card_png_base64(card_payload: Dict[str, Any], verify_url: str) -> str:
    """
    Simple printable ID card PNG (Pillow).
    Uses placeholder fields until client confirms layout (T01/T05).
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="Card template dependency missing (Pillow).",
        ) from exc

    width, height = 640, 400
    img = Image.new("RGB", (width, height), color=(248, 250, 252))
    draw = ImageDraw.Draw(img)

    # Header bar
    draw.rectangle([0, 0, width, 64], fill=(37, 99, 235))
    try:
        title_font = ImageFont.truetype("arial.ttf", 22)
        body_font = ImageFont.truetype("arial.ttf", 16)
        small_font = ImageFont.truetype("arial.ttf", 13)
    except Exception:
        title_font = ImageFont.load_default()
        body_font = ImageFont.load_default()
        small_font = ImageFont.load_default()

    draw.text((20, 18), "Rock Martial Arts — Student ID", fill="white", font=title_font)

    y = 86
    lines = [
        ("Name", card_payload.get("full_name") or "—"),
        ("Card No", card_payload.get("card_number") or "—"),
        ("Branch", card_payload.get("branch_name") or "—"),
        ("Course", card_payload.get("course_name") or "—"),
        ("Status", card_payload.get("account_status") or "—"),
    ]
    for label, value in lines:
        draw.text((24, y), f"{label}:", fill=(100, 116, 139), font=small_font)
        draw.text((110, y), str(value)[:42], fill=(15, 23, 42), font=body_font)
        y += 36

    # QR on the right
    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_M

        qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, box_size=4, border=1)
        qr.add_data(verify_url)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        qr_img = qr_img.resize((150, 150))
        img.paste(qr_img, (width - 180, 90))
        draw.text((width - 170, 250), "Scan to verify", fill=(100, 116, 139), font=small_font)
    except Exception:
        logger.exception("Failed embedding QR on ID card template")

    draw.text(
        (24, height - 36),
        "Fields provisional pending client approval (M08-S03-T01)",
        fill=(148, 163, 184),
        font=small_font,
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _public_card_view(card: dict, student: dict, branch_course: Dict[str, str]) -> Dict[str, Any]:
    account_active = bool(student.get("is_active", True))
    card_status = card.get("status") or "active"
    return {
        "full_name": _student_display_name(student),
        "card_number": card.get("card_number"),
        "student_ref": (student.get("id") or "")[:8],
        "branch_name": branch_course.get("branch_name"),
        "course_name": branch_course.get("course_name"),
        "account_status": "Active" if account_active else "Inactive",
        "photo_url": _photo_url(student),
        "card_status": card_status,
        "is_valid": card_status == "active" and account_active,
    }


async def get_active_card_for_student(db, student_id: str) -> Optional[dict]:
    return await db.student_id_cards.find_one(
        {"student_id": student_id, "status": "active"},
        sort=[("created_at", -1)],
    )


async def get_or_create_id_card(
    student_id: str,
    current_user: dict,
    *,
    regenerate: bool = False,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    student = await db.users.find_one({"id": student_id})
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    if str(student.get("role") or "").lower() != "student":
        raise HTTPException(status_code=400, detail="ID cards are only for students")

    await assert_can_manage_student_status(db, student, current_user)
    await ensure_student_id_card_indexes(db)

    existing = await get_active_card_for_student(db, student_id)
    now = datetime.utcnow()

    if existing and not regenerate:
        return await _enrich_card_response(db, existing, student)

    if existing and regenerate:
        await db.student_id_cards.update_one(
            {"id": existing["id"]},
            {
                "$set": {
                    "status": "revoked",
                    "revoked_at": now,
                    "revoked_by": current_user.get("id"),
                    "updated_at": now,
                }
            },
        )

    # Unique card number / token with light retry
    card_number = None
    qr_token = None
    for _ in range(8):
        candidate = generate_card_number()
        if not await db.student_id_cards.find_one({"card_number": candidate}):
            card_number = candidate
            break
    if not card_number:
        raise HTTPException(status_code=500, detail="Could not allocate unique card number")

    for _ in range(8):
        candidate = generate_qr_token()
        if not await db.student_id_cards.find_one({"qr_token": candidate}):
            qr_token = candidate
            break
    if not qr_token:
        raise HTTPException(status_code=500, detail="Could not allocate unique QR token")

    card = {
        "id": str(uuid.uuid4()),
        "student_id": student_id,
        "card_number": card_number,
        "qr_token": qr_token,
        "status": "active",
        "visible_fields": list(ID_CARD_VISIBLE_FIELDS),
        "created_at": now,
        "updated_at": now,
        "created_by": current_user.get("id"),
        "created_by_name": current_user.get("full_name") or current_user.get("email"),
    }
    await db.student_id_cards.insert_one(card)
    return await _enrich_card_response(db, card, student)


async def get_id_card_for_student(student_id: str, current_user: dict) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    student = await db.users.find_one({"id": student_id})
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    if str(student.get("role") or "").lower() != "student":
        raise HTTPException(status_code=400, detail="ID cards are only for students")

    await assert_can_manage_student_status(db, student, current_user)

    card = await get_active_card_for_student(db, student_id)
    if not card:
        return {
            "card": None,
            "message": "No active ID card. Generate one to create card number and QR.",
            "visible_fields": ID_CARD_VISIBLE_FIELDS,
            "fields_note": "Visible fields are provisional pending client approval (M08-S03-T01).",
        }
    return await _enrich_card_response(db, card, student)


async def revoke_id_card(student_id: str, current_user: dict) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    student = await db.users.find_one({"id": student_id})
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    await assert_can_manage_student_status(db, student, current_user)

    card = await get_active_card_for_student(db, student_id)
    if not card:
        raise HTTPException(status_code=404, detail="No active ID card to revoke")

    now = datetime.utcnow()
    await db.student_id_cards.update_one(
        {"id": card["id"]},
        {
            "$set": {
                "status": "revoked",
                "revoked_at": now,
                "revoked_by": current_user.get("id"),
                "updated_at": now,
            }
        },
    )
    return {
        "message": "ID card revoked",
        "card_id": card["id"],
        "card_number": card.get("card_number"),
        "status": "revoked",
    }


async def _enrich_card_response(db, card: dict, student: dict) -> Dict[str, Any]:
    branch_course = await _resolve_branch_and_course(db, student)
    verify_url = build_verify_url(card["qr_token"])
    display = _public_card_view(card, student, branch_course)

    qr_png_b64 = build_qr_png_base64(verify_url)
    card_png_b64 = build_id_card_png_base64(display, verify_url)

    safe_card = {
        "id": card.get("id"),
        "student_id": card.get("student_id"),
        "card_number": card.get("card_number"),
        "status": card.get("status"),
        "created_at": card.get("created_at"),
        "updated_at": card.get("updated_at"),
        "created_by_name": card.get("created_by_name"),
        # qr_token returned only to authorized admins for support; never embed raw student UUID
        "qr_token": card.get("qr_token"),
        "verify_url": verify_url,
    }

    return {
        "card": serialize_doc(safe_card),
        "display": display,
        "qr_image_base64": qr_png_b64,
        "card_image_base64": card_png_b64,
        "visible_fields": ID_CARD_VISIBLE_FIELDS,
        "fields_note": "Visible fields are provisional pending client approval (M08-S03-T01).",
        "message": "Student ID card ready",
    }


async def verify_qr_token(qr_token: str) -> Dict[str, Any]:
    """
    Resolve opaque QR token to approved verification info (M08-S04).

    Security rules:
    - Lookup by qr_token only (never by raw student UUID).
    - Public payload is allowlisted; forbidden PII keys stripped.
    - Invalid / unknown tokens share a generic shape (no enumeration).
    - Revoked / expired / inactive return status-specific messages.
    """
    db = get_db()
    invalid = _invalid_verify_payload()

    token = _normalize_qr_token(qr_token)
    if not token:
        return invalid

    if db is None:
        return invalid

    # Token-only lookup — never fall through to users.id even for UUID-shaped tokens
    card = await db.student_id_cards.find_one({"qr_token": token})
    if not card:
        return invalid

    student = await db.users.find_one({"id": card.get("student_id")})
    if not student or str(student.get("role") or "").lower() != "student":
        return invalid

    card_status = str(card.get("status") or "active").lower()
    account_active = bool(student.get("is_active", True))
    branch_course = await _resolve_branch_and_course(db, student)
    display = _public_card_view(card, student, branch_course)
    public_display = _sanitize_public_display(display)

    if card_status == "revoked":
        return {
            "valid": False,
            "status": "revoked",
            "message": "This student ID card has been revoked.",
            "display": public_display,
            "visible_fields": list(VERIFY_VISIBLE_FIELDS),
        }
    if card_status == "expired":
        return {
            "valid": False,
            "status": "expired",
            "message": "This student ID card has expired.",
            "display": public_display,
            "visible_fields": list(VERIFY_VISIBLE_FIELDS),
        }
    if not account_active:
        return {
            "valid": False,
            "status": "inactive",
            "message": "This student account is inactive.",
            "display": public_display,
            "visible_fields": list(VERIFY_VISIBLE_FIELDS),
        }

    return {
        "valid": True,
        "status": "active",
        "message": "Student ID verified.",
        "display": public_display,
        "visible_fields": list(VERIFY_VISIBLE_FIELDS),
    }


def _invalid_verify_payload() -> Dict[str, Any]:
    return {
        "valid": False,
        "status": "invalid",
        "message": "This student ID could not be verified.",
        "display": None,
        "visible_fields": list(VERIFY_VISIBLE_FIELDS),
    }


def _normalize_qr_token(qr_token: Optional[str]) -> Optional[str]:
    if qr_token is None:
        return None
    token = str(qr_token).strip()
    if len(token) < 16 or len(token) > 128:
        return None
    if any(ch in token for ch in ("/", "\\", "?", "#", " ", "\n", "\r", "\t")):
        return None
    return token


def _sanitize_public_display(display: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not display:
        return None
    cleaned: Dict[str, Any] = {}
    for key in VERIFY_VISIBLE_FIELDS:
        if key in VERIFY_FORBIDDEN_KEYS:
            continue
        cleaned[key] = display.get(key)
    for key, val in list(cleaned.items()):
        if val is None or val == "":
            cleaned[key] = None
    return cleaned


def public_display_has_forbidden_keys(payload: Dict[str, Any]) -> List[str]:
    """QA helper: return forbidden keys found in a verify response tree."""
    found: List[str] = []
    display = (payload or {}).get("display") or {}
    if isinstance(display, dict):
        for key in display.keys():
            if key not in VERIFY_VISIBLE_FIELDS or key in VERIFY_FORBIDDEN_KEYS:
                found.append(key)
    for key in ("student_id", "email", "phone", "qr_token", "id"):
        if key in (payload or {}) and (payload or {}).get(key) is not None:
            found.append(key)
    return found
