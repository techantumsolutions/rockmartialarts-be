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

# Visible fields on the printable Student ID card (matches approved warrior design).
ID_CARD_VISIBLE_FIELDS = [
    "full_name",
    "card_number",
    "student_ref",
    "branch_name",
    "course_name",
    "belt_rank",
    "batch_name",
    "date_of_joining",
    "valid_until",
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
    "belt_rank",
    "batch_name",
    "date_of_joining",
    "valid_until",
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


def _format_card_date(value: Any) -> str:
    """Format dates like '12 MAY 2025' for the ID card."""
    if value is None or value == "":
        return ""
    dt = value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return ""
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            try:
                parts = raw.split("T")[0].split("-")
                if len(parts) == 3:
                    dt = datetime(int(parts[0]), int(parts[1]), int(parts[2]))
                else:
                    return raw.upper()
            except Exception:
                return raw.upper()
    if isinstance(dt, datetime):
        return dt.strftime("%d %b %Y").upper()
    return str(value).upper()


def _resolve_batch_name(branch: Optional[dict], enrollment: Optional[dict]) -> str:
    if not enrollment:
        return ""
    explicit = (
        enrollment.get("batch_name")
        or enrollment.get("batch")
        or enrollment.get("slot_name")
        or ""
    )
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    batch_ref = str(
        enrollment.get("batch_ref")
        or enrollment.get("batch_id")
        or enrollment.get("slot_id")
        or ""
    ).strip()
    if not batch_ref or not branch:
        return ""

    schedule = branch.get("course_schedule") or branch.get("assignments") or {}
    entries = []
    if isinstance(schedule, dict):
        entries = schedule.get("course_schedule") or schedule.get("courses") or []
        if not entries and isinstance(schedule.get("batches"), list):
            entries = [schedule]
    elif isinstance(schedule, list):
        entries = schedule

    course_id = str(enrollment.get("course_id") or "").strip()
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        entry_course = str(entry.get("course_id") or entry.get("id") or "").strip()
        if course_id and entry_course and entry_course != course_id:
            continue
        for bat in entry.get("batches") or []:
            if not isinstance(bat, dict):
                continue
            bid = str(bat.get("batch_id") or bat.get("id") or bat.get("batch_ref") or "").strip()
            bref = str(bat.get("batch_ref") or "").strip()
            if batch_ref in (bid, bref) or batch_ref == str(bat.get("name") or "").strip():
                return str(bat.get("batch_name") or bat.get("name") or batch_ref).strip()
    return batch_ref


async def _resolve_branch_and_course(db, student: dict) -> Dict[str, str]:
    student_id = student.get("id")
    branch_name = ""
    course_name = ""
    batch_name = ""
    date_of_joining = ""
    valid_until = ""
    branch = None
    enrollment = None

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
        batch_name = _resolve_batch_name(branch, enrollment)
        date_of_joining = _format_card_date(
            enrollment.get("start_date") or enrollment.get("enrollment_date")
        )
        valid_until = _format_card_date(
            enrollment.get("end_date") or enrollment.get("next_due_date")
        )

    if not branch_name and student.get("branch_id"):
        branch = await db.branches.find_one({"id": student["branch_id"]})
        if branch:
            branch_name = (
                (branch.get("branch") or {}).get("name") or branch.get("name") or ""
            )

    belt_rank = (
        student.get("current_belt")
        or student.get("belt_rank")
        or student.get("student_level")
        or student.get("level_or_belt")
        or ""
    )
    if isinstance(belt_rank, str):
        belt_rank = belt_rank.strip()
    else:
        belt_rank = str(belt_rank or "").strip()

    if not date_of_joining:
        date_of_joining = _format_card_date(
            student.get("enrollment_start_date")
            or student.get("joining_date")
            or student.get("date_of_joining")
        )
    if not valid_until:
        valid_until = _format_card_date(
            student.get("enrollment_end_date") or student.get("valid_until")
        )

    return {
        "branch_name": branch_name or "—",
        "course_name": course_name or "—",
        "belt_rank": belt_rank or "—",
        "batch_name": batch_name or "—",
        "date_of_joining": date_of_joining or "—",
        "valid_until": valid_until or "—",
    }


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


def _id_card_fonts():
    from PIL import ImageFont

    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    bold = regular = None
    for path in candidates:
        if os.path.exists(path):
            try:
                if "Bold" in path and bold is None:
                    bold = path
                if regular is None:
                    regular = path
            except Exception:
                continue
    bold = bold or regular
    try:
        return {
            "title": ImageFont.truetype(bold, 42) if bold else ImageFont.load_default(),
            "subtitle": ImageFont.truetype(bold, 22) if bold else ImageFont.load_default(),
            "name": ImageFont.truetype(bold, 48) if bold else ImageFont.load_default(),
            "label": ImageFont.truetype(regular, 20) if regular else ImageFont.load_default(),
            "value": ImageFont.truetype(bold, 24) if bold else ImageFont.load_default(),
            "small": ImageFont.truetype(regular, 18) if regular else ImageFont.load_default(),
            "tiny": ImageFont.truetype(bold, 16) if bold else ImageFont.load_default(),
            "footer": ImageFont.truetype(bold, 14) if bold else ImageFont.load_default(),
        }
    except Exception:
        d = ImageFont.load_default()
        return {k: d for k in ("title", "subtitle", "name", "label", "value", "small", "tiny", "footer")}


def _resolve_local_asset(*relative_parts: str) -> Optional[str]:
    roots = [
        os.path.join(os.path.dirname(__file__), ".."),
        os.path.join(os.path.dirname(__file__), "..", "..", "rockmartialarts-fe", "public"),
        "/root/rockmartialarts-fe/public",
        "/root/rockmartialarts-be",
    ]
    for root in roots:
        path = os.path.abspath(os.path.join(root, *relative_parts))
        if os.path.isfile(path):
            return path
    return None


def _load_card_photo(photo_url: Optional[str], size: tuple[int, int]):
    from PIL import Image

    target_w, target_h = size
    img = None
    url = (photo_url or "").strip()
    try:
        if url.startswith("/uploads/") or url.startswith("uploads/"):
            rel = url.lstrip("/")
            # Prefer FE public uploads, then BE public uploads
            for base in (
                "/root/rockmartialarts-fe/public",
                os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "public")),
                os.getenv("UPLOAD_ROOT") or "",
            ):
                if not base:
                    continue
                candidate = os.path.join(base, rel) if not rel.startswith(base) else rel
                # UPLOAD_ROOT may already point at .../uploads
                if base.endswith("uploads") and rel.startswith("uploads/"):
                    candidate = os.path.join(base, rel.split("uploads/", 1)[-1])
                if os.path.isfile(candidate):
                    img = Image.open(candidate).convert("RGB")
                    break
        elif url.startswith("http://") or url.startswith("https://"):
            import requests

            resp = requests.get(url, timeout=8)
            if resp.ok:
                img = Image.open(io.BytesIO(resp.content)).convert("RGB")
        elif url and os.path.isfile(url):
            img = Image.open(url).convert("RGB")
    except Exception:
        logger.exception("Failed loading student photo for ID card")

    if img is None:
        fallback = _resolve_local_asset("assets", "img", "glry_img3.jpg") or _resolve_local_asset(
            "static", "logo.png"
        )
        if fallback:
            img = Image.open(fallback).convert("RGB")
        else:
            img = Image.new("RGB", (target_w, target_h), (226, 232, 240))

    # Cover-fit crop
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w, new_h = int(src_w * scale), int(src_h * scale)
    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = max(0, (new_w - target_w) // 2)
    top = max(0, (new_h - target_h) // 5)  # bias toward head
    return img.crop((left, top, left + target_w, top + target_h))


def _rounded_rectangle(draw, xy, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def build_id_card_png_base64(card_payload: Dict[str, Any], verify_url: str) -> str:
    """
    High-resolution printable Student ID card (warrior black/gold design).
    Canvas: 2048×1364 (2× of 1024×682 design) for crisp print / PDF embedding.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="Card template dependency missing (Pillow).",
        ) from exc

    width, height = 2048, 1364
    scale = 2
    img = Image.new("RGB", (width, height), color=(8, 8, 8))
    draw = ImageDraw.Draw(img)
    fonts = _id_card_fonts()

    GOLD = (255, 183, 15)
    WHITE = (255, 255, 255)
    MUTED = (220, 220, 220)

    # Soft gold glow corners
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.ellipse([-200, -200, 700, 500], fill=(255, 183, 15, 28))
    odraw.ellipse([width - 650, -120, width + 180, 520], fill=(255, 183, 15, 34))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    # Punch hole
    hole_w, hole_h = 108, 28
    hx, hy = (width - hole_w) // 2, 20
    draw.rounded_rectangle([hx, hy, hx + hole_w, hy + hole_h], radius=14, outline=GOLD, width=4, fill=(17, 17, 17))

    # Seal
    seal_path = (
        _resolve_local_asset("assets", "img", "rmaa-seal.png")
        or _resolve_local_asset("static", "logo.png")
        or _resolve_local_asset("logo.png")
    )
    seal_size = 92 * scale
    seal_xy = (28 * scale, 36 * scale)
    if seal_path:
        try:
            seal = Image.open(seal_path).convert("RGBA").resize((seal_size, seal_size), Image.Resampling.LANCZOS)
            mask = Image.new("L", (seal_size, seal_size), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, seal_size, seal_size), fill=255)
            img.paste(seal, seal_xy, mask)
            draw.ellipse(
                [seal_xy[0] - 4, seal_xy[1] - 4, seal_xy[0] + seal_size + 4, seal_xy[1] + seal_size + 4],
                outline=GOLD,
                width=6,
            )
        except Exception:
            logger.exception("Failed pasting seal on ID card")

    title_x = seal_xy[0] + seal_size + 28
    draw.text((title_x, 42 * scale), "ROCK", fill=WHITE, font=fonts["title"])
    draw.text((title_x, 78 * scale), "MARTIAL ARTS ACADEMY", fill=GOLD, font=fonts["subtitle"])
    draw.text(
        (title_x, 112 * scale),
        "BECOME THE STRONGEST VERSION OF YOURSELF",
        fill=MUTED,
        font=fonts["tiny"],
    )

    # Kick mark (simple circle + silhouette block)
    kick_cx, kick_cy = width - 160, 120
    draw.ellipse([kick_cx - 90, kick_cy - 90, kick_cx + 90, kick_cy + 90], fill=GOLD)
    draw.ellipse([kick_cx - 18, kick_cy - 70, kick_cx + 18, kick_cy - 34], fill=(0, 0, 0))
    draw.polygon(
        [
            (kick_cx - 10, kick_cy - 30),
            (kick_cx + 12, kick_cy - 30),
            (kick_cx + 22, kick_cy + 40),
            (kick_cx - 20, kick_cy + 40),
        ],
        fill=(0, 0, 0),
    )
    draw.polygon(
        [
            (kick_cx + 10, kick_cy - 8),
            (kick_cx + 78, kick_cy - 40),
            (kick_cx + 86, kick_cy - 24),
            (kick_cx + 22, kick_cy + 10),
        ],
        fill=(0, 0, 0),
    )

    # Photo
    photo = _load_card_photo(card_payload.get("photo_url"), (168 * scale, 188 * scale))
    photo_xy = (28 * scale, 160 * scale)
    draw.rounded_rectangle(
        [photo_xy[0] - 6, photo_xy[1] - 6, photo_xy[0] + photo.size[0] + 6, photo_xy[1] + photo.size[1] + 6],
        radius=18,
        outline=GOLD,
        width=6,
    )
    photo_rgba = photo.convert("RGBA")
    pmask = Image.new("L", photo.size, 0)
    ImageDraw.Draw(pmask).rounded_rectangle([0, 0, photo.size[0], photo.size[1]], radius=14, fill=255)
    img.paste(photo_rgba, photo_xy, pmask)
    motto = "WARRIOR. DISCIPLINE. RESPECT."
    try:
        bbox = draw.textbbox((0, 0), motto, font=fonts["tiny"])
        tw = bbox[2] - bbox[0]
    except Exception:
        tw = 220
    draw.text(
        (photo_xy[0] + (photo.size[0] - tw) // 2, photo_xy[1] + photo.size[1] + 10),
        motto,
        fill=GOLD,
        font=fonts["tiny"],
    )

    # Fields
    info_x = photo_xy[0] + photo.size[0] + 36 * scale
    rows = [
        ("STUDENT NAME", str(card_payload.get("full_name") or "—")),
        ("STUDENT ID", str(card_payload.get("card_number") or "—")),
        ("COURSE", str(card_payload.get("course_name") or "—")),
        ("BELT RANK", str(card_payload.get("belt_rank") or "—")),
        ("BRANCH", str(card_payload.get("branch_name") or "—")),
        ("BATCH", str(card_payload.get("batch_name") or "—")),
        ("DATE OF JOINING", str(card_payload.get("date_of_joining") or "—")),
        ("VALID UNTIL", str(card_payload.get("valid_until") or "—")),
    ]
    row_y = 168 * scale
    for label, value in rows:
        draw.text((info_x, row_y), label, fill=GOLD, font=fonts["tiny"])
        draw.text((info_x + 280, row_y), ":", fill=GOLD, font=fonts["tiny"])
        draw.text((info_x + 300, row_y - 2), value[:36].upper(), fill=WHITE, font=fonts["small"])
        if label == "BELT RANK" and value and value != "—":
            draw.rounded_rectangle(
                [info_x + 300 + 18 * len(value[:20]), row_y + 6, info_x + 300 + 18 * len(value[:20]) + 70, row_y + 18],
                radius=3,
                fill=GOLD,
            )
        row_y += 42 * scale

    # QR panel
    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_M

        qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, box_size=8, border=1)
        qr.add_data(verify_url)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        qr_size = 148 * scale
        qr_img = qr_img.resize((qr_size, qr_size), Image.Resampling.LANCZOS)
        panel_x = width - 28 * scale - 168 * scale
        panel_y = 168 * scale
        draw.rounded_rectangle(
            [panel_x - 8, panel_y - 8, panel_x + qr_size + 8, panel_y + qr_size + 8],
            radius=18,
            fill=WHITE,
            outline=GOLD,
            width=3,
        )
        img.paste(qr_img, (panel_x, panel_y))
        badge_y = panel_y + qr_size + 18
        draw.rounded_rectangle(
            [panel_x - 8, badge_y, panel_x + qr_size + 8, badge_y + 40],
            radius=8,
            fill=GOLD,
        )
        draw.text((panel_x + 18, badge_y + 10), "SCAN TO VIEW PROFILE", fill=(0, 0, 0), font=fonts["tiny"])
    except Exception:
        logger.exception("Failed embedding QR on ID card template")

    # Footer values
    footer_y = height - 110
    values = ["TRAIN HARD", "STAY FOCUSED", "ACHIEVE GREATNESS", "BE A WARRIOR"]
    vx = 28 * scale
    for val in values:
        draw.ellipse([vx, footer_y, vx + 28, footer_y + 28], fill=GOLD)
        draw.text((vx + 36, footer_y + 6), val, fill=GOLD, font=fonts["tiny"])
        vx += 280

    # Silhouette strip
    strip_x = width - 560
    draw.polygon(
        [
            (strip_x, footer_y - 6),
            (width - 24, footer_y + 6),
            (width - 36, footer_y + 70),
            (strip_x - 20, footer_y + 58),
        ],
        fill=GOLD,
    )
    for i in range(6):
        sx = strip_x + 30 + i * 80
        draw.ellipse([sx + 18, footer_y + 4, sx + 38, footer_y + 24], fill=(0, 0, 0))
        draw.rectangle([sx + 20, footer_y + 24, sx + 36, footer_y + 54], fill=(0, 0, 0))

    site = "WWW.ROCKMARTIALARTSACADEMY.COM"
    try:
        bbox = draw.textbbox((0, 0), site, font=fonts["footer"])
        tw = bbox[2] - bbox[0]
    except Exception:
        tw = 280
    draw.text(((width - tw) // 2, height - 36), site, fill=WHITE, font=fonts["footer"])

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
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
        "belt_rank": branch_course.get("belt_rank") or "—",
        "batch_name": branch_course.get("batch_name") or "—",
        "date_of_joining": branch_course.get("date_of_joining") or "—",
        "valid_until": branch_course.get("valid_until") or "—",
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
            "fields_note": (
                "ID card shows name, student ID, course, belt, branch, batch, "
                "joining/valid dates, photo, and verification QR."
            ),
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
        "fields_note": (
            "ID card shows name, student ID, course, belt, branch, batch, "
            "joining/valid dates, photo, and verification QR."
        ),
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
