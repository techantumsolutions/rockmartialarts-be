"""Visitor → branch distance calculation (routing + Haversine fallback)."""
from __future__ import annotations

import hashlib
import logging
import math
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0

# In-memory cache: key -> (expires_at, payload)
_CACHE: Dict[str, Tuple[float, dict]] = {}


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def distance_mode() -> str:
    mode = _env("BRANCH_DISTANCE_MODE", "routing").lower()
    return mode if mode in ("routing", "straight_line") else "routing"


def distance_provider() -> str:
    return _env("BRANCH_DISTANCE_PROVIDER", "google").lower()


def cache_ttl_seconds() -> int:
    try:
        return max(0, int(_env("BRANCH_DISTANCE_CACHE_TTL", "300") or "300"))
    except ValueError:
        return 300


def google_maps_api_key() -> str:
    return _env("GOOGLE_MAPS_API_KEY") or _env("GOOGLE_DISTANCE_API_KEY")


def validate_coordinates(lat: Any, lng: Any) -> Tuple[float, float]:
    try:
        la = float(lat)
        lo = float(lng)
    except (TypeError, ValueError):
        raise ValueError("Invalid latitude/longitude")
    if not (-90.0 <= la <= 90.0) or not (-180.0 <= lo <= 180.0):
        raise ValueError("Latitude/longitude out of range")
    return la, lo


def extract_branch_coords(branch: dict) -> Optional[Tuple[float, float]]:
    """Trusted coordinates from branch document (never from visitor)."""
    if not isinstance(branch, dict):
        return None
    lat = branch.get("latitude")
    lng = branch.get("longitude")
    coords = branch.get("coordinates")
    if (lat is None or lng is None) and isinstance(coords, dict):
        lat = coords.get("lat") if coords.get("lat") is not None else coords.get("latitude")
        lng = coords.get("lng") if coords.get("lng") is not None else coords.get("longitude")
    if (lat is None or lng is None) and isinstance(coords, (list, tuple)) and len(coords) >= 2:
        # GeoJSON-ish [lng, lat] or [lat, lng] — prefer explicit keys above
        # Only accept list when values look like Hyderabad-ish if first is > 90? Prefer [lat, lng]
        a, b = coords[0], coords[1]
        try:
            a_f, b_f = float(a), float(b)
            if abs(a_f) <= 90 and abs(b_f) <= 180:
                lat, lng = a_f, b_f
        except (TypeError, ValueError):
            return None
    if lat is None or lng is None:
        return None
    try:
        return validate_coordinates(lat, lng)
    except ValueError:
        return None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rlat1, rlon1, rlat2, rlon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    )
    return EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(a))


def format_distance_label(km: float, *, approximate: bool) -> str:
    if km < 0 or math.isnan(km) or math.isinf(km):
        return ""
    rounded = round(float(km), 1)
    if rounded <= 0:
        rounded = 0.1
    text = f"{rounded:.1f} km away"
    return f"Approx. {text}" if approximate else text


def _round_origin(lat: float, lng: float, decimals: int = 3) -> Tuple[float, float]:
    """Coarse rounding for cache keys — avoids tying cache to precise GPS."""
    return round(lat, decimals), round(lng, decimals)


def _cache_get(key: str) -> Optional[dict]:
    entry = _CACHE.get(key)
    if not entry:
        return None
    expires, payload = entry
    if time.time() > expires:
        _CACHE.pop(key, None)
        return None
    return payload


def _cache_set(key: str, payload: dict) -> None:
    ttl = cache_ttl_seconds()
    if ttl <= 0:
        return
    _CACHE[key] = (time.time() + ttl, payload)


async def _google_driving_distances(
    origin: Tuple[float, float],
    destinations: List[Tuple[str, float, float]],
) -> Dict[str, float]:
    """Return branch_id -> km using Google Distance Matrix. Partial results OK."""
    key = google_maps_api_key()
    if not key or not destinations:
        return {}
    # Matrix allows multiple destinations in one request (max ~25)
    dest_str = "|".join(f"{lat},{lng}" for _, lat, lng in destinations)
    params = {
        "origins": f"{origin[0]},{origin[1]}",
        "destinations": dest_str,
        "units": "metric",
        "mode": "driving",
        "key": key,
    }
    base = _env("BRANCH_DISTANCE_API_BASE_URL") or "https://maps.googleapis.com/maps/api/distancematrix/json"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(base, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        logger.warning("Google Distance Matrix request failed", exc_info=False)
        return {}

    if data.get("status") != "OK":
        logger.warning("Distance Matrix status=%s", data.get("status"))
        return {}

    rows = data.get("rows") or []
    if not rows:
        return {}
    elements = (rows[0] or {}).get("elements") or []
    out: Dict[str, float] = {}
    for idx, dest in enumerate(destinations):
        branch_id = dest[0]
        if idx >= len(elements):
            break
        el = elements[idx] or {}
        if el.get("status") != "OK":
            continue
        meters = ((el.get("distance") or {}).get("value"))
        if meters is None:
            continue
        try:
            out[branch_id] = float(meters) / 1000.0
        except (TypeError, ValueError):
            continue
    return out


async def calculate_branch_distances(
    visitor_lat: float,
    visitor_lng: float,
    branches: List[dict],
) -> dict:
    """
    Calculate distances for a list of branch documents.

    Returns:
      {
        distance_type: driving|straight_line,
        unit: km,
        results: [{branch_id, distance_km, distance_label, source, nearest?}]
      }
    """
    origin = validate_coordinates(visitor_lat, visitor_lng)
    branch_ids = [str(b.get("id") or "").strip() for b in branches if b.get("id")]
    o_lat, o_lng = _round_origin(origin[0], origin[1])
    cache_key = hashlib.sha256(
        f"{o_lat}:{o_lng}:{','.join(sorted(branch_ids))}:{distance_mode()}:{distance_provider()}".encode()
    ).hexdigest()
    cached = _cache_get(cache_key)
    if cached:
        return cached

    prepared: List[Tuple[str, float, float]] = []
    missing: List[str] = []
    for b in branches:
        bid = str(b.get("id") or "").strip()
        if not bid:
            continue
        coords = extract_branch_coords(b)
        if not coords:
            missing.append(bid)
            continue
        prepared.append((bid, coords[0], coords[1]))

    results: List[dict] = []
    driving_map: Dict[str, float] = {}
    use_routing = distance_mode() == "routing" and bool(google_maps_api_key())

    if use_routing and prepared:
        # chunk destinations (25 max)
        for i in range(0, len(prepared), 25):
            chunk = prepared[i : i + 25]
            part = await _google_driving_distances(origin, chunk)
            driving_map.update(part)

    for bid, blat, blng in prepared:
        if bid in driving_map:
            km = driving_map[bid]
            results.append(
                {
                    "branch_id": bid,
                    "distance_km": round(km, 1),
                    "distance_label": format_distance_label(km, approximate=False),
                    "source": "routing",
                }
            )
        else:
            km = haversine_km(origin[0], origin[1], blat, blng)
            results.append(
                {
                    "branch_id": bid,
                    "distance_km": round(km, 1),
                    "distance_label": format_distance_label(km, approximate=True),
                    "source": "haversine",
                }
            )

    for bid in missing:
        results.append(
            {
                "branch_id": bid,
                "distance_km": None,
                "distance_label": None,
                "source": "unavailable",
                "error": "Branch coordinates not configured",
            }
        )

    valid = [r for r in results if isinstance(r.get("distance_km"), (int, float))]
    nearest_id = None
    if valid:
        nearest = min(valid, key=lambda r: float(r["distance_km"]))
        nearest_id = nearest["branch_id"]
        for r in results:
            if r["branch_id"] == nearest_id and r.get("distance_km") is not None:
                r["nearest"] = True

    # Prefer reporting driving when any routing result exists
    any_routing = any(r.get("source") == "routing" for r in results)
    payload = {
        "distance_type": "driving" if any_routing else "straight_line",
        "unit": "km",
        "nearest_branch_id": nearest_id,
        "results": results,
    }
    _cache_set(cache_key, payload)
    return payload
