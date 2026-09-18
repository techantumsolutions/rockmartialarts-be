"""
M21-S09 Collaboration Partner public landing aggregator.

One public payload for partner pages: branch + branding + SEO +
masters + gallery + testimonials + team. Does not mutate branch docs.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from controllers.branch_controller import BranchController
from controllers.collaboration_partner_seo_controller import (
    CollaborationPartnerSeoController,
)
from controllers.collaboration_partner_team_controller import _public_row as _team_public_row
from controllers.collaboration_partner_testimonial_controller import (
    _public_row as _testimonial_public_row,
)
from models.collaboration_partner_branding_models import empty_branding_response
from models.collaboration_partner_testimonial_models import PartnerTestimonialStatus
from utils.collaboration_partner import (
    branch_is_collaboration_partner,
    enrich_collaboration_flag,
)
from utils.database import get_db
from utils.helpers import serialize_doc


def _name_to_slug(name: str) -> str:
    if not name or not isinstance(name, str):
        return ""
    s = name.strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "partner"


def _public_master_row(doc: dict) -> dict:
    return {
        "id": doc.get("id"),
        "name": doc.get("name"),
        "designation": doc.get("designation"),
        "photo_url": doc.get("photo_url"),
        "biography": doc.get("biography"),
        "experience_years": doc.get("experience_years"),
        "experience_summary": doc.get("experience_summary"),
        "specializations": doc.get("specializations") or [],
        "achievements": doc.get("achievements") or [],
        "display_order": doc.get("display_order", 100),
    }


def _public_gallery_row(doc: dict) -> dict:
    return {
        "id": doc.get("id"),
        "title": doc.get("title"),
        "caption": doc.get("caption"),
        "media_type": doc.get("media_type") or "image",
        "media_url": doc.get("media_url"),
        "thumbnail_url": doc.get("thumbnail_url"),
        "video_url": doc.get("video_url"),
        "alt_text": doc.get("alt_text"),
        "display_order": doc.get("display_order", 100),
    }


def _public_branding(doc: Optional[dict], branch_id: str, branch: dict) -> dict:
    if not doc:
        empty = empty_branding_response(branch_id, branch)
        empty["has_branding"] = False
        return empty
    out = serialize_doc(doc)
    out["has_branding"] = True
    # Drop Mongo _id noise already handled by serialize_doc
    return out


def _public_profile_safe(doc: Optional[dict]) -> dict:
    """Public-safe partner identity only — no tax/PAN/agreement internals."""
    if not doc:
        return {
            "has_profile": False,
            "partner_id": None,
            "display_name": None,
            "trade_name": None,
        }
    reg = doc.get("registration") or {}
    return {
        "has_profile": True,
        "partner_id": doc.get("partner_id"),
        "display_name": reg.get("legal_business_name"),
        "trade_name": reg.get("trade_name"),
    }


class CollaborationPartnerLandingController:
    @staticmethod
    async def _resolve_partner_branch_by_slug(slug: str) -> dict:
        if not slug or not str(slug).strip():
            raise HTTPException(status_code=404, detail="Partner not found")
        slug_n = str(slug).strip().lower()
        db = get_db()

        stored = await db.branches.find_one({"slug": slug_n, "is_active": True})
        if stored and branch_is_collaboration_partner(stored):
            return stored

        if re.match(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            slug_n,
            re.I,
        ):
            by_id = await db.branches.find_one({"id": slug_n, "is_active": True})
            if by_id and branch_is_collaboration_partner(by_id):
                return by_id

        branches = await db.branches.find(
            {
                "is_active": True,
                "$or": [
                    {"allows_collaboration": True},
                    {"is_collaboration_partner": True},
                ],
            }
        ).to_list(length=500)
        for b in branches:
            if not branch_is_collaboration_partner(b):
                continue
            name = (b.get("branch") or {}).get("name") or b.get("name") or ""
            if name and _name_to_slug(str(name).strip()) == slug_n:
                return b
            stored_slug = str(b.get("slug") or "").strip().lower()
            if stored_slug and stored_slug == slug_n:
                return b

        raise HTTPException(status_code=404, detail="Partner not found")

    @staticmethod
    async def get_landing_by_branch_id(branch_id: str) -> dict:
        db = get_db()
        branch = await db.branches.find_one({"id": branch_id, "is_active": True})
        if not branch:
            raise HTTPException(status_code=404, detail="Partner not found")
        if not branch_is_collaboration_partner(branch):
            raise HTTPException(status_code=404, detail="Partner not found")
        return await CollaborationPartnerLandingController._build_landing(branch)

    @staticmethod
    async def get_landing_by_slug(slug: str) -> dict:
        branch = await CollaborationPartnerLandingController._resolve_partner_branch_by_slug(
            slug
        )
        return await CollaborationPartnerLandingController._build_landing(branch)

    @staticmethod
    async def list_public_partners(
        *,
        skip: int = 0,
        limit: int = 50,
        q: Optional[str] = None,
    ) -> dict:
        """Lightweight public directory of collaboration partners."""
        db = get_db()
        if limit > 100:
            limit = 100
        query: Dict[str, Any] = {
            "is_active": True,
            "$or": [
                {"allows_collaboration": True},
                {"is_collaboration_partner": True},
            ],
        }
        qn = (q or "").strip()
        if qn:
            rx = {"$regex": re.escape(qn), "$options": "i"}
            query = {
                "$and": [
                    query,
                    {
                        "$or": [
                            {"branch.name": rx},
                            {"name": rx},
                            {"slug": rx},
                            {"branch.code": rx},
                        ]
                    },
                ]
            }

        total = await db.branches.count_documents(query)
        branches = (
            await db.branches.find(query).skip(skip).limit(limit).to_list(limit)
        )
        items: List[dict] = []
        branch_ids = [b["id"] for b in branches]
        branding_by: Dict[str, dict] = {}
        if branch_ids:
            async for br in db.collaboration_partner_branding.find(
                {"branch_id": {"$in": branch_ids}}
            ):
                branding_by[br["branch_id"]] = br

        for b in branches:
            enrich_collaboration_flag(b)
            bi = b.get("branch") or {}
            name = bi.get("name") or b.get("name") or "Partner"
            slug = (b.get("slug") or _name_to_slug(str(name))).strip()
            branding = branding_by.get(b["id"]) or {}
            media = branding.get("media") or {}
            content = branding.get("content") or {}
            addr = bi.get("address") or {}
            items.append(
                {
                    "branch_id": b["id"],
                    "name": name,
                    "code": bi.get("code"),
                    "slug": slug,
                    "partner_url": f"/partners/{slug}",
                    "logo_url": media.get("logo_url"),
                    "cover_banner_url": media.get("cover_banner_url"),
                    "short_description": content.get("short_description"),
                    "city": addr.get("city"),
                    "state": addr.get("state"),
                }
            )
        return {
            "partners": items,
            "total": total,
            "skip": skip,
            "limit": limit,
        }

    @staticmethod
    async def _build_landing(branch: dict) -> dict:
        enrich_collaboration_flag(branch)
        branch_id = branch["id"]
        db = get_db()

        # Reuse public branch detail for contact/courses/map (already strips bank etc.)
        branch_public = await BranchController._public_branch_detail(db, branch)

        branding_doc = await db.collaboration_partner_branding.find_one(
            {"branch_id": branch_id}
        )
        branding = _public_branding(branding_doc, branch_id, branch)

        profile_doc = await db.collaboration_partner_profiles.find_one(
            {"branch_id": branch_id}
        )
        profile = _public_profile_safe(profile_doc)

        seo = await CollaborationPartnerSeoController.get_public(branch_id)

        masters_q = {"branch_id": branch_id, "is_active": True}
        masters_total = await db.collaboration_partner_masters.count_documents(masters_q)
        masters_docs = (
            await db.collaboration_partner_masters.find(masters_q)
            .sort([("display_order", 1), ("name", 1)])
            .limit(48)
            .to_list(48)
        )

        gallery_q = {"branch_id": branch_id, "is_active": True}
        gallery_total = await db.collaboration_partner_gallery.count_documents(gallery_q)
        gallery_docs = (
            await db.collaboration_partner_gallery.find(gallery_q)
            .sort([("display_order", 1), ("created_at", -1)])
            .limit(48)
            .to_list(48)
        )

        testi_q = {
            "branch_id": branch_id,
            "is_active": True,
            "status": PartnerTestimonialStatus.PUBLISHED.value,
        }
        testi_total = await db.collaboration_partner_testimonials.count_documents(
            testi_q
        )
        testi_docs = (
            await db.collaboration_partner_testimonials.find(testi_q)
            .sort([("display_order", 1), ("created_at", -1)])
            .limit(24)
            .to_list(24)
        )

        team_q = {"branch_id": branch_id, "is_active": True}
        team_total = await db.collaboration_partner_team.count_documents(team_q)
        team_docs = (
            await db.collaboration_partner_team.find(team_q)
            .sort([("display_order", 1), ("name", 1)])
            .limit(48)
            .to_list(48)
        )

        bi = branch.get("branch") or {}
        name = bi.get("name") or branch.get("name") or "Partner"
        slug = (branch.get("slug") or _name_to_slug(str(name))).strip()

        media = branding.get("media") or {}
        content = branding.get("content") or {}
        facilities = branding.get("facilities") or {}
        hours = branding.get("operating_hours") or []
        social = branding.get("social_links") or {}

        hero = {
            "name": profile.get("trade_name")
            or profile.get("display_name")
            or name,
            "tagline": content.get("short_description"),
            "logo_url": media.get("logo_url"),
            "logo_alt": media.get("logo_alt") or name,
            "cover_banner_url": media.get("cover_banner_url"),
            "cover_banner_alt": media.get("cover_banner_alt") or name,
            "cta_primary": {
                "label": "Book a Demo",
                "href": "/book-demo",
            },
            "cta_secondary": {
                "label": "Request Callback",
                "href": "/request-callback",
            },
        }

        about = {
            "about_content": content.get("about_content"),
            "our_story": content.get("our_story"),
            "vision": content.get("vision"),
            "mission": content.get("mission"),
            "why_choose_us": content.get("why_choose_us"),
            "why_choose_us_points": content.get("why_choose_us_points") or [],
        }

        contact = {
            "phone": bi.get("phone") or branch_public.get("phone"),
            "email": bi.get("email") or branch_public.get("email"),
            "address": bi.get("address") or branch_public.get("address"),
            "map_link": branch_public.get("map_link") or facilities.get("map_embed_url"),
            "map_embed_url": facilities.get("map_embed_url"),
            "coordinates": branch_public.get("coordinates"),
            "facilities": facilities.get("facilities")
            or branch_public.get("facilities")
            or [],
            "parking_info": facilities.get("parking_info"),
            "location_highlights": facilities.get("location_highlights") or [],
            "operating_hours": hours,
            "hours_notes": branding.get("hours_notes"),
            "social_links": social,
        }

        return {
            "branch_id": branch_id,
            "slug": slug,
            "partner_url": f"/partners/{slug}",
            "is_collaboration_partner": True,
            "branch": branch_public,
            "profile": profile,
            "branding": branding,
            "seo": seo,
            "hero": hero,
            "about": about,
            "contact": contact,
            "masters": [_public_master_row(d) for d in masters_docs],
            "masters_total": masters_total,
            "gallery": [_public_gallery_row(d) for d in gallery_docs],
            "gallery_total": gallery_total,
            "testimonials": [_testimonial_public_row(d) for d in testi_docs],
            "testimonials_total": testi_total,
            "team": [_team_public_row(d) for d in team_docs],
            "team_total": team_total,
            "courses": branch_public.get("courses") or [],
        }
