"""
M16-S04 Protected learning lesson access & video stream.

Never exposes raw video_url to unauthorized clients.
Playback uses short-lived signed tokens + proxy/embed.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from utils.database import get_db
from utils.helpers import serialize_doc
from utils.learning_subscription_service import has_active_learning_access

logger = logging.getLogger(__name__)

COL_COURSES = "learning_courses"
COL_LEVELS = "learning_levels"
COL_LESSONS = "learning_lessons"

SECRET_KEY = os.environ.get(
    "SECRET_KEY", "student_management_secret_key_2025_secure"
)
ALGORITHM = "HS256"
PLAYBACK_TOKEN_TTL_MIN = int(os.getenv("LEARNING_PLAYBACK_TOKEN_TTL_MIN", "120"))
PLAYBACK_AUD = "learning_lesson_playback"

# Hosts we treat as embed (not proxied)
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}
_VIMEO_HOSTS = {"vimeo.com", "www.vimeo.com", "player.vimeo.com"}


def _role(user: Optional[dict]) -> str:
    return str((user or {}).get("role") or "").lower().replace(" ", "_")


def _is_admin(user: Optional[dict]) -> bool:
    return _role(user) in {
        "superadmin",
        "super_admin",
        "coach_admin",
        "coachadmin",
    }


def _lesson_safe(doc: dict) -> Dict[str, Any]:
    """Client-safe lesson fields — never includes video_url."""
    return {
        "id": doc.get("id"),
        "course_id": doc.get("course_id"),
        "level_id": doc.get("level_id"),
        "title": doc.get("title"),
        "description": doc.get("description"),
        "sort_order": doc.get("sort_order", 0),
        "status": doc.get("status"),
        "duration_seconds": doc.get("duration_seconds"),
        "is_preview": bool(doc.get("is_preview")),
        "has_video": bool(doc.get("video_url") or doc.get("video_storage_key")),
    }


def _detect_playback_mode(video_url: Optional[str]) -> Tuple[str, Optional[str]]:
    """
    Returns (mode, resolved_url).
    mode: embed_youtube | embed_vimeo | stream | none
    """
    url = (video_url or "").strip()
    if not url:
        return "none", None
    try:
        parsed = urlparse(url)
    except Exception:
        return "stream", url
    host = (parsed.netloc or "").lower()
    if host in _YOUTUBE_HOSTS or host.endswith(".youtube.com"):
        vid = None
        if "youtu.be" in host:
            vid = (parsed.path or "").strip("/").split("/")[0] or None
        else:
            qs = parse_qs(parsed.query or "")
            vid = (qs.get("v") or [None])[0]
            if not vid and "/embed/" in (parsed.path or ""):
                vid = (parsed.path or "").split("/embed/")[-1].split("/")[0]
            if not vid and "/shorts/" in (parsed.path or ""):
                vid = (parsed.path or "").split("/shorts/")[-1].split("/")[0]
        if vid and re.match(r"^[\w-]{6,20}$", vid):
            return "embed_youtube", f"https://www.youtube-nocookie.com/embed/{vid}?rel=0&modestbranding=1"
        return "none", None
    if host in _VIMEO_HOSTS:
        m = re.search(r"/(\d{6,12})", parsed.path or "")
        if m:
            return "embed_vimeo", f"https://player.vimeo.com/video/{m.group(1)}"
        return "none", None
    if parsed.scheme in ("http", "https"):
        return "stream", url
    return "none", None


def issue_playback_token(
    *,
    lesson_id: str,
    course_id: str,
    user_id: Optional[str],
    is_preview: bool,
) -> str:
    now = datetime.utcnow()
    payload = {
        "aud": PLAYBACK_AUD,
        "lesson_id": lesson_id,
        "course_id": course_id,
        "sub": user_id or "anonymous",
        "preview": bool(is_preview),
        "iat": now,
        "exp": now + timedelta(minutes=PLAYBACK_TOKEN_TTL_MIN),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_playback_token(token: str) -> dict:
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            audience=PLAYBACK_AUD,
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Playback session expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid playback token")


async def _get_published_lesson(db, lesson_id: str) -> dict:
    doc = await db[COL_LESSONS].find_one({"id": lesson_id})
    if not doc or str(doc.get("status") or "") != "published":
        raise HTTPException(status_code=404, detail="Lesson not found")
    return doc


async def evaluate_access(
    *,
    lesson: dict,
    current_user: Optional[dict],
) -> Dict[str, Any]:
    """
    Decide whether the user may play this lesson.
    Preview lessons: allowed without subscription (auth optional).
    Otherwise: active/grace subscription or admin.
    """
    course_id = lesson.get("course_id")
    is_preview = bool(lesson.get("is_preview"))

    if _is_admin(current_user):
        return {
            "allowed": True,
            "reason": "admin",
            "entitled": True,
            "is_preview": is_preview,
            "subscription": None,
        }

    if is_preview:
        return {
            "allowed": True,
            "reason": "preview",
            "entitled": False,
            "is_preview": True,
            "subscription": None,
        }

    if not current_user or not current_user.get("id"):
        return {
            "allowed": False,
            "reason": "login_required",
            "entitled": False,
            "is_preview": False,
            "subscription": None,
            "status_code": 401,
            "detail": "Sign in to watch this lesson",
        }

    access = await has_active_learning_access(current_user["id"], course_id)
    if access.get("entitled"):
        return {
            "allowed": True,
            "reason": "subscription",
            "entitled": True,
            "is_preview": False,
            "subscription": access.get("subscription"),
        }

    return {
        "allowed": False,
        "reason": "subscription_required",
        "entitled": False,
        "is_preview": False,
        "subscription": access.get("subscription"),
        "status_code": 403,
        "detail": "An active subscription is required to watch this lesson",
    }


async def get_course_entitlement(
    course_id: str, *, current_user: Optional[dict]
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    course = await db[COL_COURSES].find_one({"id": course_id, "status": "published"})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    if _is_admin(current_user):
        return {
            "course_id": course_id,
            "entitled": True,
            "reason": "admin",
            "subscription": None,
        }
    if not current_user or not current_user.get("id"):
        return {
            "course_id": course_id,
            "entitled": False,
            "reason": "login_required",
            "subscription": None,
        }
    access = await has_active_learning_access(current_user["id"], course_id)
    return {
        "course_id": course_id,
        "entitled": bool(access.get("entitled")),
        "reason": "subscription" if access.get("entitled") else "subscription_required",
        "subscription": access.get("subscription"),
    }


async def get_player_curriculum(
    course_id: str, *, current_user: Optional[dict]
) -> Dict[str, Any]:
    """Published curriculum with can_play per lesson (no video URLs)."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    course = await db[COL_COURSES].find_one({"id": course_id, "status": "published"})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    entitlement = await get_course_entitlement(course_id, current_user=current_user)
    entitled = bool(entitlement.get("entitled"))

    levels = (
        await db[COL_LEVELS]
        .find({"course_id": course_id, "status": "published"})
        .sort([("sort_order", 1)])
        .to_list(length=200)
    )
    lessons = (
        await db[COL_LESSONS]
        .find({"course_id": course_id, "status": "published"})
        .sort([("sort_order", 1)])
        .to_list(length=2000)
    )
    by_level: Dict[str, list] = {}
    for les in lessons:
        safe = _lesson_safe(les)
        can_play = entitled or bool(les.get("is_preview")) or _is_admin(current_user)
        safe["can_play"] = can_play
        by_level.setdefault(str(les.get("level_id")), []).append(safe)

    curriculum = []
    for lv in levels:
        curriculum.append(
            {
                "id": lv.get("id"),
                "title": lv.get("title"),
                "description": lv.get("description"),
                "sort_order": lv.get("sort_order", 0),
                "lessons": by_level.get(str(lv.get("id")), []),
            }
        )

    return {
        "course": {
            "id": course.get("id"),
            "title": course.get("title"),
            "slug": course.get("slug"),
            "thumbnail_url": course.get("thumbnail_url"),
        },
        "entitled": entitled,
        "entitlement": entitlement,
        "curriculum": curriculum,
    }


async def get_lesson_playback(
    lesson_id: str, *, current_user: Optional[dict]
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    lesson = await _get_published_lesson(db, lesson_id)
    course = await db[COL_COURSES].find_one({"id": lesson["course_id"]})
    if not course or str(course.get("status") or "") != "published":
        raise HTTPException(status_code=404, detail="Course not found")

    access = await evaluate_access(lesson=lesson, current_user=current_user)
    if not access.get("allowed"):
        raise HTTPException(
            status_code=int(access.get("status_code") or 403),
            detail=access.get("detail") or "Access denied",
        )

    mode, resolved = _detect_playback_mode(lesson.get("video_url"))
    safe = _lesson_safe(lesson)
    level = await db[COL_LEVELS].find_one({"id": lesson.get("level_id")})

    playback: Dict[str, Any] = {
        "mode": mode,
        "download_allowed": False,
    }
    if mode in ("embed_youtube", "embed_vimeo") and resolved:
        playback["embed_url"] = resolved
    elif mode == "stream" and resolved:
        token = issue_playback_token(
            lesson_id=lesson["id"],
            course_id=lesson["course_id"],
            user_id=(current_user or {}).get("id"),
            is_preview=bool(lesson.get("is_preview")),
        )
        playback["stream_path"] = f"learning-access/stream?token={token}"
        playback["token_expires_in_minutes"] = PLAYBACK_TOKEN_TTL_MIN
    else:
        playback["mode"] = "none"
        playback["message"] = "No playable video configured for this lesson"

    # Never include video_url / storage key
    assert "video_url" not in safe
    return {
        "lesson": safe,
        "level": {
            "id": (level or {}).get("id"),
            "title": (level or {}).get("title"),
        }
        if level
        else None,
        "course": {
            "id": course.get("id"),
            "title": course.get("title"),
            "slug": course.get("slug"),
        },
        "access": {
            "allowed": True,
            "reason": access.get("reason"),
            "entitled": access.get("entitled"),
            "is_preview": access.get("is_preview"),
        },
        "playback": playback,
        "download_allowed": False,
    }


async def stream_lesson_media(token: str, request: Request) -> Response:
    """
    Authenticated/tokenized media stream — inline only, no attachment download.
    Proxies remote video so clients never receive a durable public media URL.
    """
    payload = decode_playback_token(token)
    lesson_id = payload.get("lesson_id")
    if not lesson_id:
        raise HTTPException(status_code=401, detail="Invalid playback token")

    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    lesson = await _get_published_lesson(db, lesson_id)
    if str(lesson.get("course_id")) != str(payload.get("course_id") or ""):
        raise HTTPException(status_code=401, detail="Invalid playback token")

    # Re-validate entitlement at stream time (handles expiry mid-session)
    user_id = payload.get("sub")
    current_user = None
    if user_id and user_id != "anonymous":
        current_user = {"id": user_id, "role": "student"}
        # Prefer real user role if present
        user_doc = await db.users.find_one({"id": user_id})
        if user_doc:
            current_user = serialize_doc(user_doc)
        else:
            sa = await db.superadmins.find_one({"id": user_id})
            if sa:
                current_user = {**serialize_doc(sa), "role": "super_admin"}

    access = await evaluate_access(lesson=lesson, current_user=current_user)
    if not access.get("allowed"):
        raise HTTPException(
            status_code=int(access.get("status_code") or 403),
            detail=access.get("detail") or "Access denied",
        )

    mode, source_url = _detect_playback_mode(lesson.get("video_url"))
    if mode != "stream" or not source_url:
        raise HTTPException(
            status_code=400,
            detail="This lesson uses embedded playback, not a direct stream",
        )

    range_header = request.headers.get("range") or request.headers.get("Range")
    upstream_headers = {
        "User-Agent": "RockMartialArts-LMS/1.0",
        "Accept": "*/*",
    }
    if range_header:
        upstream_headers["Range"] = range_header

    try:
        client = httpx.AsyncClient(follow_redirects=True, timeout=60.0)
        req = client.build_request("GET", source_url, headers=upstream_headers)
        upstream = await client.send(req, stream=True)
    except Exception:
        logger.exception("Upstream video fetch failed")
        raise HTTPException(
            status_code=502, detail="Could not reach video source"
        )

    if upstream.status_code >= 400:
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(
            status_code=502,
            detail=f"Video source returned {upstream.status_code}",
        )

    media_type = (
        upstream.headers.get("content-type")
        or "application/octet-stream"
    )
    # Prefer video/* when unknown
    if media_type.startswith("text/") or media_type == "application/octet-stream":
        lower = source_url.lower()
        if lower.endswith(".mp4"):
            media_type = "video/mp4"
        elif lower.endswith(".webm"):
            media_type = "video/webm"
        elif lower.endswith(".m3u8"):
            media_type = "application/vnd.apple.mpegurl"

    out_headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, private",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
        "X-Robots-Tag": "noindex, nofollow",
        "Content-Disposition": 'inline; filename="lesson"',
        # Discourage download managers / attachment handling
        "Accept-Ranges": upstream.headers.get("accept-ranges", "bytes"),
    }
    if upstream.headers.get("content-length"):
        out_headers["Content-Length"] = upstream.headers["content-length"]
    if upstream.headers.get("content-range"):
        out_headers["Content-Range"] = upstream.headers["content-range"]

    async def body_iter():
        try:
            async for chunk in upstream.aiter_bytes(chunk_size=64 * 1024):
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        body_iter(),
        status_code=upstream.status_code,
        media_type=media_type,
        headers=out_headers,
    )
