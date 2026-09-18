"""M16-S04 Protected lesson access & stream routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from utils.unified_auth import get_optional_current_user_or_superadmin
from utils.learning_access_service import (
    get_course_entitlement,
    get_lesson_playback,
    get_player_curriculum,
    stream_lesson_media,
)

router = APIRouter()


@router.get("/courses/{course_id}/entitlement")
async def api_entitlement(
    course_id: str,
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    return await get_course_entitlement(course_id, current_user=current_user)


@router.get("/courses/{course_id}/player")
async def api_player_curriculum(
    course_id: str,
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """Curriculum with can_play flags (no video URLs)."""
    return await get_player_curriculum(course_id, current_user=current_user)


@router.get("/lessons/{lesson_id}")
async def api_lesson_playback(
    lesson_id: str,
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """
    Entitlement-gated lesson playback payload.
    Never returns raw video_url. download_allowed is always false.
    """
    return await get_lesson_playback(lesson_id, current_user=current_user)


@router.get("/stream")
async def api_stream(
    request: Request,
    token: str = Query(..., min_length=20),
):
    """Short-lived tokenized media proxy (inline, no download disposition)."""
    return await stream_lesson_media(token, request)
