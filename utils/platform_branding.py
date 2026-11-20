"""
Platform-level branding (Super Admin / platform admin).

Single source of truth for platform name, logos, and colors used by the
website, all portals, academy defaults, emails, and invoices.
Partner/academy overlays still stack on top via academy_branding.py.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Dict, Optional

from utils.database import get_db

DEFAULT_PLATFORM_BRANDING: Dict[str, Any] = {
    "platform_name": "Rock Martial Arts Academy",
    "platform_short_name": "Rock Martial Arts",
    "legal_name": "Rock Martial Arts Academy",
    "navbar_logo": None,
    "footer_logo": None,
    "favicon": None,
    "site_loader_image": None,
    # When True, academy labels render as "{Academy} powered by {Platform}"
    "show_academy_powered_by": False,
    "primary_color": "#E1BB33",
    "secondary_color": "#4F5077",
    "accent_color": "#FFB70F",
    "dark_color": "#171A26",
    "support_email": None,
    "support_phone": None,
    "website_url": "https://rockmartialartsacademy.com",
}

# Process-local cache for sync email/PDF helpers
_RUNTIME_CACHE: Dict[str, Any] = deepcopy(DEFAULT_PLATFORM_BRANDING)


def get_cached_platform_branding() -> Dict[str, Any]:
    return deepcopy(_RUNTIME_CACHE)


def _set_runtime_cache(branding: Dict[str, Any]) -> None:
    _RUNTIME_CACHE.clear()
    _RUNTIME_CACHE.update(branding or DEFAULT_PLATFORM_BRANDING)

_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def normalize_hex_color(value: Any, fallback: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return fallback
    if not raw.startswith("#"):
        raw = f"#{raw}"
    if _HEX_RE.match(raw):
        if len(raw) == 4:
            raw = "#" + "".join(ch * 2 for ch in raw[1:])
        return raw.upper()
    return fallback


def _nonempty(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def normalize_platform_branding_patch(raw: Optional[dict]) -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    out = deepcopy(DEFAULT_PLATFORM_BRANDING)
    for key in (
        "platform_name",
        "platform_short_name",
        "legal_name",
        "navbar_logo",
        "footer_logo",
        "favicon",
        "site_loader_image",
        "support_email",
        "support_phone",
        "website_url",
    ):
        if key in data:
            val = _nonempty(data.get(key))
            out[key] = val if val is not None else out[key]
    if "show_academy_powered_by" in data:
        raw_flag = data.get("show_academy_powered_by")
        if isinstance(raw_flag, bool):
            out["show_academy_powered_by"] = raw_flag
        else:
            out["show_academy_powered_by"] = str(raw_flag).strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
    out["primary_color"] = normalize_hex_color(
        data.get("primary_color", out["primary_color"]), out["primary_color"]
    )
    out["secondary_color"] = normalize_hex_color(
        data.get("secondary_color", out["secondary_color"]), out["secondary_color"]
    )
    out["accent_color"] = normalize_hex_color(
        data.get("accent_color", out["accent_color"]), out["accent_color"]
    )
    out["dark_color"] = normalize_hex_color(
        data.get("dark_color", out["dark_color"]), out["dark_color"]
    )
    # Keep short name derived if blank
    if not out.get("platform_short_name"):
        out["platform_short_name"] = out["platform_name"]
    if not out.get("legal_name"):
        out["legal_name"] = out["platform_name"]
    return out


def branding_from_cms_doc(doc: Optional[dict]) -> Dict[str, Any]:
    branding = (doc or {}).get("branding") if isinstance(doc, dict) else {}
    if not isinstance(branding, dict):
        branding = {}
    merged = deepcopy(DEFAULT_PLATFORM_BRANDING)
    for key, val in branding.items():
        if key in merged or key in (
            "platform_name",
            "platform_short_name",
            "legal_name",
            "primary_color",
            "secondary_color",
            "accent_color",
            "dark_color",
            "support_email",
            "support_phone",
            "website_url",
            "navbar_logo",
            "footer_logo",
            "favicon",
            "site_loader_image",
            "show_academy_powered_by",
        ):
            if key == "show_academy_powered_by":
                merged[key] = val
            elif val is not None and str(val).strip() != "":
                merged[key] = val
    return normalize_platform_branding_patch(merged)


def format_academy_powered_by(academy_name: Optional[str], platform_name: Optional[str] = None) -> str:
    """Return '{Academy} powered by {Platform}' when the global toggle is on."""
    academy = str(academy_name or "").strip()
    if not academy:
        return ""
    branding = get_cached_platform_branding()
    if not bool(branding.get("show_academy_powered_by")):
        return academy
    platform = str(
        platform_name
        or branding.get("platform_short_name")
        or branding.get("platform_name")
        or ""
    ).strip()
    if not platform:
        return academy
    if "powered by" in academy.lower():
        return academy
    if academy.lower() == platform.lower():
        return academy
    return f"{academy} powered by {platform}"


async def get_platform_branding(db=None) -> Dict[str, Any]:
    """Load platform branding from cms_content.branding (with safe defaults)."""
    database = db if db is not None else get_db()
    if database is None:
        return deepcopy(DEFAULT_PLATFORM_BRANDING)
    doc = await database.cms_content.find_one({})
    branding = branding_from_cms_doc(doc)
    _set_runtime_cache(branding)
    return branding


async def save_platform_branding(patch: dict, db=None) -> Dict[str, Any]:
    """Merge + persist platform branding fields into cms_content.branding."""
    from datetime import datetime

    database = db if db is not None else get_db()
    if database is None:
        raise RuntimeError("Database connection not available")

    existing = await database.cms_content.find_one({})
    current = branding_from_cms_doc(existing)
    # Preserve existing logo URLs when patch omits them
    for logo_key in ("navbar_logo", "footer_logo", "favicon", "site_loader_image"):
        if logo_key not in patch:
            patch = {**patch, logo_key: current.get(logo_key)}
    normalized = normalize_platform_branding_patch({**current, **(patch or {})})

    # Keep logo fields that were explicitly cleared
    for logo_key in ("navbar_logo", "footer_logo", "favicon", "site_loader_image"):
        if logo_key in (patch or {}) and (patch.get(logo_key) in (None, "")):
            normalized[logo_key] = None

    set_payload = {f"branding.{k}": v for k, v in normalized.items()}
    set_payload["updated_at"] = datetime.utcnow()

    if existing:
        await database.cms_content.update_one({"_id": existing["_id"]}, {"$set": set_payload})
    else:
        await database.cms_content.insert_one(
            {
                "homepage": {},
                "footer": {},
                "branding": normalized,
                "page_seo": {},
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
        )

    # Keep invoice system_name in sync
    try:
        await database.system_settings.update_one(
            {},
            {
                "$set": {
                    "system_name": normalized.get("legal_name")
                    or normalized.get("platform_name"),
                    "updated_at": datetime.utcnow(),
                }
            },
            upsert=True,
        )
    except Exception:
        pass

    _set_runtime_cache(normalized)
    return normalized
