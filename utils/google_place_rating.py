"""Fetch and cache Google Places rating / review count for branches.

Uses Places API (New). When the API key cannot access Places, stored
branch.rating / branch.review_count values are left unchanged.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import httpx

from utils.branch_distance import extract_branch_coords, google_maps_api_key

logger = logging.getLogger(__name__)

# In-process: skip hammering Places after permission failures.
_PLACES_DISABLED_UNTIL = 0.0
_PLACES_DISABLE_TTL_SEC = 6 * 60 * 60

# Default refresh window for cached Google ratings on branch docs.
_DEFAULT_TTL_HOURS = 24 * 7


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int((os.getenv(name) or str(default)).strip()))
    except ValueError:
        return default


def rating_cache_ttl_hours() -> int:
    return _env_int("GOOGLE_PLACE_RATING_TTL_HOURS", _DEFAULT_TTL_HOURS)


def _places_disabled() -> bool:
    return time.time() < _PLACES_DISABLED_UNTIL


def _disable_places_temporarily(reason: str) -> None:
    global _PLACES_DISABLED_UNTIL
    _PLACES_DISABLED_UNTIL = time.time() + _PLACES_DISABLE_TTL_SEC
    logger.warning("Google Places rating sync disabled temporarily: %s", reason)


def _branch_display_name(branch: dict) -> str:
    info = branch.get("branch") if isinstance(branch.get("branch"), dict) else {}
    return str(info.get("name") or branch.get("name") or "").strip()


def _branch_address_bits(branch: dict) -> Tuple[str, str]:
    info = branch.get("branch") if isinstance(branch.get("branch"), dict) else {}
    addr = info.get("address") if isinstance(info.get("address"), dict) else {}
    if not addr and isinstance(branch.get("address"), dict):
        addr = branch.get("address") or {}
    area = str(addr.get("area") or "").strip()
    city = str(addr.get("city") or "").strip()
    return area, city


def _synced_recently(branch: dict, ttl_hours: Optional[int] = None) -> bool:
    ttl = rating_cache_ttl_hours() if ttl_hours is None else max(0, int(ttl_hours))
    if ttl <= 0:
        return False
    raw = branch.get("google_rating_synced_at")
    if not raw:
        return False
    try:
        if isinstance(raw, datetime):
            ts = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
        else:
            ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
        return age_h < ttl
    except Exception:
        return False


def _has_usable_rating(branch: dict) -> bool:
    rating = branch.get("rating")
    reviews = branch.get("review_count")
    try:
        if rating is None:
            return False
        r = float(rating)
        if r <= 0:
            return False
        if reviews is None:
            return True
        return int(reviews) >= 0
    except (TypeError, ValueError):
        return False


async def fetch_place_rating(
    *,
    text_query: str,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    place_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return {place_id, rating, review_count, name} or None."""
    if _places_disabled():
        return None
    key = google_maps_api_key()
    if not key:
        return None

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": (
            "places.id,places.displayName,places.rating,places.userRatingCount,"
            "places.formattedAddress,places.location"
        ),
    }

    # Prefer Place Details when we already have a Places resource id.
    pid = (place_id or "").strip()
    if pid:
        # Accept both "places/xxx" and bare ids.
        resource = pid if pid.startswith("places/") else f"places/{pid}"
        detail_url = f"https://places.googleapis.com/v1/{resource}"
        detail_headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": key,
            "X-Goog-FieldMask": "id,displayName,rating,userRatingCount",
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(detail_url, headers=detail_headers)
            if resp.status_code in (401, 403):
                _disable_places_temporarily(f"details HTTP {resp.status_code}")
                return None
            if resp.status_code == 200:
                data = resp.json() or {}
                rating = data.get("rating")
                count = data.get("userRatingCount")
                if rating is None:
                    return None
                return {
                    "place_id": data.get("id") or pid,
                    "rating": float(rating),
                    "review_count": int(count or 0),
                    "name": ((data.get("displayName") or {}).get("text") if isinstance(data.get("displayName"), dict) else None),
                }
        except Exception as exc:
            logger.info("Places details failed: %s", exc)

    query = (text_query or "").strip()
    if not query:
        return None

    body: Dict[str, Any] = {"textQuery": query, "maxResultCount": 1}
    if latitude is not None and longitude is not None:
        try:
            body["locationBias"] = {
                "circle": {
                    "center": {"latitude": float(latitude), "longitude": float(longitude)},
                    "radius": 2500.0,
                }
            }
        except (TypeError, ValueError):
            pass

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://places.googleapis.com/v1/places:searchText",
                headers=headers,
                json=body,
            )
        if resp.status_code in (401, 403):
            _disable_places_temporarily(f"searchText HTTP {resp.status_code}")
            return None
        if resp.status_code != 200:
            logger.info("Places searchText HTTP %s: %s", resp.status_code, (resp.text or "")[:200])
            return None
        places = (resp.json() or {}).get("places") or []
        if not places:
            return None
        place = places[0] or {}
        rating = place.get("rating")
        if rating is None:
            return None
        count = place.get("userRatingCount")
        return {
            "place_id": place.get("id"),
            "rating": float(rating),
            "review_count": int(count or 0),
            "name": ((place.get("displayName") or {}).get("text") if isinstance(place.get("displayName"), dict) else None),
        }
    except Exception as exc:
        logger.info("Places searchText failed: %s", exc)
        return None


async def ensure_branch_google_rating(db, branch: dict, *, force: bool = False) -> dict:
    """Ensure branch dict has rating/review_count; persist when freshly fetched.

    Returns the (possibly updated) branch dict.
    """
    if not isinstance(branch, dict):
        return branch

    if not force and _has_usable_rating(branch) and _synced_recently(branch):
        return branch

    # Keep existing real values if Places is unavailable / disabled.
    if not force and _has_usable_rating(branch) and _places_disabled():
        return branch

    name = _branch_display_name(branch)
    area, city = _branch_address_bits(branch)
    coords = extract_branch_coords(branch)
    lat = coords[0] if coords else None
    lng = coords[1] if coords else None
    place_id = str(branch.get("google_place_id") or branch.get("place_id") or "").strip() or None

    # Prefer brand + locality for Rock academies; fall back to stored name.
    query_parts = [p for p in ["Rock Martial Arts", name, area, city, "Hyderabad"] if p]
    # De-dupe while preserving order
    seen = set()
    query_parts_unique = []
    for p in query_parts:
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        query_parts_unique.append(p)
    text_query = " ".join(query_parts_unique[:5]).strip()

    result = await fetch_place_rating(
        text_query=text_query,
        latitude=lat,
        longitude=lng,
        place_id=place_id,
    )
    if not result:
        return branch

    now = datetime.now(timezone.utc)
    patch = {
        "rating": result["rating"],
        "review_count": result["review_count"],
        "google_rating_synced_at": now,
    }
    if result.get("place_id"):
        patch["google_place_id"] = result["place_id"]

    branch_id = branch.get("id")
    if db is not None and branch_id:
        try:
            await db.branches.update_one({"id": branch_id}, {"$set": patch})
        except Exception as exc:
            logger.info("Failed to persist Google rating for branch %s: %s", branch_id, exc)

    branch.update(patch)
    return branch


async def enrich_branches_google_ratings(db, branches: list) -> list:
    """Best-effort rating enrichment for a list of branch docs (mutates in place)."""
    if not branches:
        return branches
    for branch in branches:
        try:
            await ensure_branch_google_rating(db, branch, force=False)
        except Exception as exc:
            logger.info("Rating enrich skipped: %s", exc)
    return branches
