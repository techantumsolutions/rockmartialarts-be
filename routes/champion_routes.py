"""M20 Champion Profile CMS + Achievements routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from models.champion_achievement_models import (
    ChampionAchievementCreate,
    ChampionAchievementUpdate,
)
from models.champion_models import (
    ChampionCreate,
    ChampionPublishRequest,
    ChampionUpdate,
)
from models.user_models import UserRole
from utils.champion_achievement_service import (
    archive_champion_achievement,
    create_champion_achievement,
    get_champion_achievement,
    list_champion_achievements,
    update_champion_achievement,
)
from utils.champion_service import (
    archive_champion,
    create_champion,
    get_champion,
    get_public_champion,
    list_champions,
    list_public_champions,
    publish_champion,
    search_students_for_link,
    update_champion,
)
from utils.unified_auth import require_role_unified

router = APIRouter()

_ADMIN = [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN]


# --- M20-S03 public surfaces (no auth; before /{champion_id}) ---


@router.get("/public")
async def api_list_public_champions(
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(48, ge=1, le=100),
):
    """Published champions for the public website."""
    return await list_public_champions(search=search, skip=skip, limit=limit)


@router.get("/public/{slug_or_id}")
async def api_get_public_champion(slug_or_id: str):
    """Published champion detail + active achievements."""
    return await get_public_champion(slug_or_id)


@router.get("")
async def api_list_champions(
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    include_archived: bool = Query(False),
    published_only: bool = Query(False),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await list_champions(
        status=status,
        search=search,
        skip=skip,
        limit=limit,
        include_archived=include_archived,
        published_only=published_only,
    )


@router.post("", status_code=201)
async def api_create_champion(
    body: ChampionCreate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await create_champion(body, current_user=current_user)


@router.get("/student-options")
async def api_champion_student_options(
    search: Optional[str] = Query(None),
    limit: int = Query(30, ge=1, le=100),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    """Students available to link to a champion profile."""
    return await search_students_for_link(search=search, limit=limit)


# --- M20-S02 nested achievements (before bare /{champion_id}) ---


@router.get("/{champion_id}/achievements")
async def api_list_champion_achievements(
    champion_id: str,
    include_archived: bool = Query(False),
    status: Optional[str] = Query(None),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await list_champion_achievements(
        champion_id,
        include_archived=include_archived,
        status=status,
    )


@router.post("/{champion_id}/achievements", status_code=201)
async def api_create_champion_achievement(
    champion_id: str,
    body: ChampionAchievementCreate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await create_champion_achievement(
        champion_id, body, current_user=current_user
    )


@router.get("/{champion_id}/achievements/{achievement_id}")
async def api_get_champion_achievement(
    champion_id: str,
    achievement_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await get_champion_achievement(champion_id, achievement_id)


@router.patch("/{champion_id}/achievements/{achievement_id}")
async def api_update_champion_achievement(
    champion_id: str,
    achievement_id: str,
    body: ChampionAchievementUpdate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await update_champion_achievement(
        champion_id, achievement_id, body, current_user=current_user
    )


@router.delete("/{champion_id}/achievements/{achievement_id}")
async def api_archive_champion_achievement(
    champion_id: str,
    achievement_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await archive_champion_achievement(
        champion_id, achievement_id, current_user=current_user
    )


@router.get("/{champion_id}")
async def api_get_champion(
    champion_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await get_champion(champion_id)


@router.patch("/{champion_id}")
async def api_update_champion(
    champion_id: str,
    body: ChampionUpdate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await update_champion(champion_id, body, current_user=current_user)


@router.post("/{champion_id}/publish")
async def api_publish_champion(
    champion_id: str,
    body: Optional[ChampionPublishRequest] = None,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    published = True if body is None else body.published
    return await publish_champion(
        champion_id, published=published, current_user=current_user
    )


@router.post("/{champion_id}/unpublish")
async def api_unpublish_champion(
    champion_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await publish_champion(
        champion_id, published=False, current_user=current_user
    )


@router.delete("/{champion_id}")
async def api_archive_champion(
    champion_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await archive_champion(champion_id, current_user=current_user)
