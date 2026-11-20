"""Per-academy branding resolution with Partner → RMAA field-level fallback."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


RMAA_DEFAULT_BRANDING: Dict[str, Any] = {
    "org_name": "Rock Martial Arts",
    "org_slug": None,
    "display_name": "Rock Martial Arts",
    "logo_url": None,
    "logo_alt": "Rock Martial Arts",
    "cover_banner_url": None,
    "cover_banner_alt": None,
    "primary_color": "#E1BB33",
    "secondary_color": "#4F5077",
    "tagline": None,
    "short_description": None,
    "about_content": None,
    "vision": None,
    "mission": None,
    "our_story": None,
    "why_choose_us": None,
    "facilities": [],
    "parking_info": None,
    "location_highlights": [],
    "map_embed_url": None,
    "social_links": {},
    "contact": {},
    "footer": {},
}


async def platform_default_branding(db=None) -> Dict[str, Any]:
    """Layer-0 defaults from Super Admin platform branding settings."""
    from utils.platform_branding import get_platform_branding

    platform = await get_platform_branding(db)
    base = dict(RMAA_DEFAULT_BRANDING)
    short = platform.get("platform_short_name") or platform.get("platform_name") or base["org_name"]
    full = platform.get("platform_name") or short
    base.update(
        {
            "org_name": short,
            "display_name": full,
            "logo_url": platform.get("navbar_logo") or platform.get("footer_logo"),
            "logo_alt": full,
            "primary_color": platform.get("primary_color") or base.get("primary_color"),
            "secondary_color": platform.get("secondary_color") or base.get("secondary_color"),
        }
    )
    return base


_SCALAR_KEYS = (
    "display_name",
    "logo_url",
    "logo_alt",
    "cover_banner_url",
    "cover_banner_alt",
    "primary_color",
    "secondary_color",
    "tagline",
    "short_description",
    "about_content",
    "vision",
    "mission",
    "our_story",
    "why_choose_us",
    "parking_info",
    "map_embed_url",
)


def _nonempty_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _nonempty_list(v: Any) -> Optional[List[Any]]:
    if not isinstance(v, list):
        return None
    items = [x for x in v if x is not None and str(x).strip()]
    return items if items else None


def _nonempty_dict(v: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(v, dict) or not v:
        return None
    # Keep only keys with non-empty values
    out = {}
    for k, val in v.items():
        if val is None:
            continue
        if isinstance(val, str) and not val.strip():
            continue
        if isinstance(val, (list, dict)) and not val:
            continue
        out[k] = val
    return out or None


def normalize_academy_branding_patch(raw: Optional[dict]) -> Dict[str, Any]:
    """Sanitize partner/academy branding write payload stored on the branch."""
    data = raw if isinstance(raw, dict) else {}
    media = data.get("media") if isinstance(data.get("media"), dict) else {}
    content = data.get("content") if isinstance(data.get("content"), dict) else {}
    colors = data.get("colors") if isinstance(data.get("colors"), dict) else {}
    facilities = data.get("facilities") if isinstance(data.get("facilities"), dict) else {}
    social = data.get("social_links") if isinstance(data.get("social_links"), dict) else {}
    contact = data.get("contact") if isinstance(data.get("contact"), dict) else data.get("contact_page")
    if not isinstance(contact, dict):
        contact = {}
    footer = data.get("footer") if isinstance(data.get("footer"), dict) else {}

    # Flat site_branding shape also accepted
    return {
        "display_name": _nonempty_str(data.get("display_name")),
        "logo_url": _nonempty_str(data.get("logo_url") or media.get("logo_url")),
        "logo_alt": _nonempty_str(data.get("logo_alt") or media.get("logo_alt")),
        "cover_banner_url": _nonempty_str(
            data.get("cover_banner_url") or media.get("cover_banner_url")
        ),
        "cover_banner_alt": _nonempty_str(
            data.get("cover_banner_alt") or media.get("cover_banner_alt")
        ),
        "primary_color": _nonempty_str(
            data.get("primary_color") or colors.get("primary") or colors.get("primary_color")
        ),
        "secondary_color": _nonempty_str(
            data.get("secondary_color")
            or colors.get("secondary")
            or colors.get("secondary_color")
        ),
        "tagline": _nonempty_str(data.get("tagline") or content.get("tagline")),
        "short_description": _nonempty_str(
            data.get("short_description") or content.get("short_description")
        ),
        "about_content": _nonempty_str(
            data.get("about_content") or content.get("about_content")
        ),
        "vision": _nonempty_str(data.get("vision") or content.get("vision")),
        "mission": _nonempty_str(data.get("mission") or content.get("mission")),
        "our_story": _nonempty_str(data.get("our_story") or content.get("our_story")),
        "why_choose_us": _nonempty_str(
            data.get("why_choose_us") or content.get("why_choose_us")
        ),
        "facilities": (
            list(data["facilities"])
            if isinstance(data.get("facilities"), list)
            else list(facilities.get("facilities") or [])
            if isinstance(facilities.get("facilities"), list)
            else []
        ),
        "parking_info": _nonempty_str(
            data.get("parking_info") or facilities.get("parking_info")
        ),
        "location_highlights": (
            list(data["location_highlights"])
            if isinstance(data.get("location_highlights"), list)
            else list(facilities.get("location_highlights") or [])
            if isinstance(facilities.get("location_highlights"), list)
            else []
        ),
        "map_embed_url": _nonempty_str(
            data.get("map_embed_url")
            or facilities.get("map_embed_url")
            or contact.get("map_embed_url")
        ),
        "social_links": social if isinstance(social, dict) else {},
        "contact": {
            "headline": _nonempty_str(contact.get("headline")),
            "intro": _nonempty_str(contact.get("intro")),
            "address": _nonempty_str(contact.get("address")),
            "phone": _nonempty_str(contact.get("phone")),
            "email": _nonempty_str(contact.get("email")),
        },
        "footer": {
            "logo_url": _nonempty_str(footer.get("logo_url")),
            "logo_alt": _nonempty_str(footer.get("logo_alt")),
            "description": _nonempty_str(footer.get("description")),
        },
    }


def partner_org_branding_to_flat(
    org: Optional[dict], branding_doc: Optional[dict]
) -> Dict[str, Any]:
    org = org or {}
    branding_doc = branding_doc or {}
    media = branding_doc.get("media") or {}
    content = branding_doc.get("content") or {}
    facilities = branding_doc.get("facilities") or {}
    social = branding_doc.get("social_links") or {}
    contact_page = branding_doc.get("contact_page") or {}
    footer = branding_doc.get("footer") or {}
    colors = branding_doc.get("colors") or {}
    return {
        "org_name": org.get("name"),
        "org_slug": org.get("slug"),
        "display_name": org.get("name"),
        "logo_url": media.get("logo_url"),
        "logo_alt": media.get("logo_alt"),
        "cover_banner_url": media.get("cover_banner_url"),
        "cover_banner_alt": media.get("cover_banner_alt"),
        "primary_color": colors.get("primary") or colors.get("primary_color"),
        "secondary_color": colors.get("secondary") or colors.get("secondary_color"),
        "tagline": content.get("tagline"),
        "short_description": content.get("short_description"),
        "about_content": content.get("about_content"),
        "vision": content.get("vision"),
        "mission": content.get("mission"),
        "our_story": content.get("our_story"),
        "why_choose_us": content.get("why_choose_us"),
        "facilities": facilities.get("facilities") or [],
        "parking_info": facilities.get("parking_info"),
        "location_highlights": facilities.get("location_highlights") or [],
        "map_embed_url": facilities.get("map_embed_url") or contact_page.get("map_embed_url"),
        "social_links": social if isinstance(social, dict) else {},
        "contact": {
            "headline": contact_page.get("headline"),
            "intro": contact_page.get("intro"),
            "address": contact_page.get("address") or org.get("address"),
            "phone": contact_page.get("phone") or org.get("phone"),
            "email": contact_page.get("email") or org.get("email"),
        },
        "footer": {
            "logo_url": footer.get("logo_url"),
            "logo_alt": footer.get("logo_alt"),
            "description": footer.get("description"),
        },
    }


def _merge_field_level(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Overlay wins when it has a non-empty value; otherwise keep base."""
    out = dict(base)
    for key in _SCALAR_KEYS:
        ov = _nonempty_str(overlay.get(key))
        if ov is not None:
            out[key] = ov
    for list_key in ("facilities", "location_highlights"):
        ovl = _nonempty_list(overlay.get(list_key))
        if ovl is not None:
            out[list_key] = ovl
    for dict_key in ("social_links", "contact", "footer"):
        base_d = out.get(dict_key) if isinstance(out.get(dict_key), dict) else {}
        ov_d = overlay.get(dict_key) if isinstance(overlay.get(dict_key), dict) else {}
        merged = dict(base_d)
        for k, v in ov_d.items():
            if isinstance(v, str):
                if v.strip():
                    merged[k] = v.strip()
            elif v not in (None, [], {}):
                merged[k] = v
        out[dict_key] = merged
    for meta in ("org_name", "org_slug"):
        ov = _nonempty_str(overlay.get(meta))
        if ov is not None:
            out[meta] = ov
    return out


def academy_branding_has_content(flat: Optional[dict]) -> bool:
    if not flat or not isinstance(flat, dict):
        return False
    return bool(
        _nonempty_str(flat.get("logo_url"))
        or _nonempty_str(flat.get("cover_banner_url"))
        or _nonempty_str(flat.get("tagline"))
        or _nonempty_str(flat.get("short_description"))
        or _nonempty_str(flat.get("about_content"))
        or _nonempty_str(flat.get("our_story"))
        or _nonempty_str(flat.get("display_name"))
        or _nonempty_str((flat.get("footer") or {}).get("logo_url") if isinstance(flat.get("footer"), dict) else None)
        or _nonempty_str(flat.get("primary_color"))
    )


async def resolve_public_academy_branding(db, branch: dict) -> Dict[str, Any]:
    """
    Resolve branding for public academy detail:
    1) Academy-specific site_branding on the branch
    2) Partner organization branding (when partner_org_id set)
    3) Platform branding (Super Admin) / Rock Martial Arts defaults
    """
    result = await platform_default_branding(db)
    sources: List[str] = ["platform_default"]
    org_id = str(branch.get("partner_org_id") or "").strip()
    org = None
    partner_flat: Dict[str, Any] = {}

    page_cms_enabled = True
    public_footer_enabled = False
    if org_id:
        org = await db.partner_organizations.find_one({"id": org_id})
        if org:
            from utils.partner_org_access import partner_org_public_extras

            flags = partner_org_public_extras(org)
            page_cms_enabled = bool(flags.get("page_cms_enabled", True))
            public_footer_enabled = bool(flags.get("public_footer_enabled", False))
        branding = None
        if page_cms_enabled:
            branding = await db.partner_org_branding.find_one({"partner_org_id": org_id})
        partner_flat = partner_org_branding_to_flat(org, branding) if page_cms_enabled else {}
        if page_cms_enabled and (
            academy_branding_has_content(partner_flat) or _nonempty_str((org or {}).get("name"))
        ):
            result = _merge_field_level(result, partner_flat)
            sources.append("partner_org")
            if org:
                result["org_name"] = org.get("name") or result.get("org_name")
                result["org_slug"] = org.get("slug")
                if not _nonempty_str(result.get("display_name")):
                    result["display_name"] = org.get("name")
        elif org and not page_cms_enabled:
            # Still identify the academy as partner-owned for chrome/footer rules.
            result["org_name"] = org.get("name") or result.get("org_name")
            result["org_slug"] = org.get("slug")

    academy_raw = branch.get("site_branding")
    if isinstance(academy_raw, dict) and academy_raw:
        academy_flat = normalize_academy_branding_patch(academy_raw)
        # Keep nested facilities list from normalized form
        if academy_branding_has_content(academy_flat) or _nonempty_str(academy_flat.get("display_name")):
            result = _merge_field_level(result, academy_flat)
            sources.append("academy")

    branch_name = None
    info = branch.get("branch") if isinstance(branch.get("branch"), dict) else {}
    branch_name = _nonempty_str(info.get("name")) or _nonempty_str(branch.get("name"))
    if not _nonempty_str(result.get("display_name")):
        result["display_name"] = (
            branch_name
            or (org.get("name") if org else None)
            or "Rock Martial Arts"
        )

    has_own = "academy" in sources and academy_branding_has_content(
        normalize_academy_branding_patch(branch.get("site_branding") or {})
    )
    # Partner branding counts as "own" relative to RMAA when no academy overlay,
    # matching prior has_own_branding behavior for partner pages.
    if not has_own and "partner_org" in sources and academy_branding_has_content(partner_flat):
        has_own = True

    brand_name = (
        _nonempty_str(result.get("display_name"))
        or (org.get("name") if org else None)
        or "Rock Martial Arts"
    )
    if not org_id:
        brand_name = "Rock Martial Arts"
        result["org_name"] = "Rock Martial Arts"
        result["display_name"] = result.get("display_name") or branch_name or "Rock Martial Arts"

    site_out = None
    if org_id and has_own:
        site_out = result
    elif org_id and academy_branding_has_content(partner_flat):
        site_out = result
    elif "academy" in sources and academy_branding_has_content(
        normalize_academy_branding_patch(branch.get("site_branding") or {})
    ):
        # RMAA academy with explicit site_branding overlay (rare).
        site_out = result

    return {
        "brand_name": brand_name,
        "has_own_branding": bool(has_own and (org_id or "academy" in sources)),
        "site_branding": site_out,
        "branding_sources": sources,
        "partner_org_id": org_id or None,
        "partner_org_name": (org or {}).get("name") if org else None,
        "partner_org_slug": (org or {}).get("slug") if org else None,
        "page_cms_enabled": page_cms_enabled if org_id else None,
        "public_footer_enabled": public_footer_enabled if org_id else None,
    }
