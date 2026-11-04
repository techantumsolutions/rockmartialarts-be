"""White-label partner org branding + public landing/leads (Partner SA only for writes)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import HTTPException

from models.partner_org_branding_models import (
    PartnerOrgBranding,
    PartnerOrgBrandingUpsert,
    empty_branding_response,
    _dump,
)
from utils.database import get_db
from utils.partner_tenant import (
    is_partner_super_admin,
    require_partner_org_id,
)
from utils.lead_service import create_or_merge_lead, lead_to_response_dict
from pydantic import BaseModel, Field, field_validator

COL = "partner_org_branding"
ORG_COL = "partner_organizations"


async def ensure_partner_org_branding_indexes(db) -> None:
    await db[COL].create_index("id", unique=True)
    await db[COL].create_index("partner_org_id", unique=True)


async def _partner_org_branch_docs(db, partner_org_id: str, *, active_only: bool = True) -> list:
    q: Dict[str, Any] = {"partner_org_id": partner_org_id}
    if active_only:
        q["is_active"] = True
    return await db.branches.find(q).sort([("branch.name", 1)]).to_list(200)


async def _partner_org_branches_with_courses(db, partner_org_id: str) -> list:
    """Rock-parity public branch cards + embedded courses, scoped to one partner org."""
    from controllers.branch_controller import BranchController

    branches = await _partner_org_branch_docs(db, partner_org_id, active_only=True)
    out = []
    for branch in branches:
        detail = await BranchController._public_branch_detail(db, branch)
        detail["partner_org_id"] = partner_org_id
        out.append(detail)
    return out


async def _partner_org_achievements(db, partner_org_id: str, *, limit: int = 48) -> list:
    """Showcase + enrollment achievements for branches owned by this partner org only."""
    from utils.helpers import serialize_doc

    branches = await _partner_org_branch_docs(db, partner_org_id, active_only=False)
    branch_ids = [b["id"] for b in branches if b.get("id")]
    if not branch_ids:
        return []

    showcase_cur = (
        db.student_showcase_achievements.find(
            {
                "status": "active",
                "branch_id": {"$in": branch_ids},
                "is_global": {"$ne": True},
            }
        )
        .sort([("display_order", 1), ("created_at", -1)])
        .limit(limit)
    )
    showcase_docs = await showcase_cur.to_list(limit)

    # Also include partner-stamped showcase rows (org-level, no branch)
    org_showcase = (
        await db.student_showcase_achievements.find(
            {
                "status": "active",
                "partner_org_id": partner_org_id,
            }
        )
        .sort([("display_order", 1), ("created_at", -1)])
        .limit(limit)
        .to_list(limit)
    )

    seen = set()
    merged_showcase = []
    for d in list(showcase_docs) + list(org_showcase):
        did = d.get("id")
        if did and did in seen:
            continue
        if did:
            seen.add(did)
        merged_showcase.append(serialize_doc(d))

    enroll_cur = (
        db.student_achievements.find(
            {
                "branch_id": {"$in": branch_ids},
                "is_deleted": False,
            }
        )
        .sort("created_at", -1)
        .limit(limit)
    )
    enroll_docs = await enroll_cur.to_list(limit)
    student_ids = list({a.get("student_id") for a in enroll_docs if a.get("student_id")})
    users = (
        await db.users.find({"id": {"$in": student_ids}}).to_list(length=len(student_ids))
        if student_ids
        else []
    )
    user_map = {
        u["id"]: u.get("full_name")
        or f"{u.get('first_name', '')} {u.get('last_name', '')}".strip()
        or "Student"
        for u in users
    }

    achievements = []
    for d in merged_showcase:
        achievements.append(
            {
                "id": d.get("id"),
                "source": "showcase",
                "student_name": d.get("student_name") or "Student",
                "student_photo": d.get("student_photo"),
                "achievement_title": d.get("achievement_title") or "Achievement",
                "description": d.get("description"),
                "image": d.get("image"),
                "branch_id": d.get("branch_id"),
            }
        )
    for a in enroll_docs:
        images = a.get("images") or []
        first_img = images[0] if isinstance(images, list) and images else None
        achievements.append(
            {
                "id": a.get("id"),
                "source": "enrollment",
                "student_name": user_map.get(a.get("student_id"), "Student"),
                "student_photo": None,
                "achievement_title": a.get("title") or "Achievement",
                "description": a.get("description"),
                "image": first_img,
                "branch_id": a.get("branch_id"),
                "is_featured": bool(a.get("is_featured", False)),
            }
        )
    return achievements


async def _site_sections_for_org(db, partner_org_id: str) -> Dict[str, Any]:
    branches = await _partner_org_branches_with_courses(db, partner_org_id)
    achievements = await _partner_org_achievements(db, partner_org_id)
    return {
        "branches": branches,
        "achievements": achievements,
        "branch_count": len(branches),
        "achievement_count": len(achievements),
    }


def _serialize_branding(doc: dict, org: Optional[dict] = None) -> Dict[str, Any]:
    return {
        "id": doc.get("id"),
        "partner_org_id": doc.get("partner_org_id"),
        "has_branding": True,
        "media": doc.get("media") or {},
        "content": doc.get("content") or {},
        "facilities": doc.get("facilities") or {},
        "social_links": doc.get("social_links") or {},
        "contact_page": doc.get("contact_page") or {},
        "footer": doc.get("footer") or {},
        "org_snapshot": {
            "id": doc.get("partner_org_id"),
            "name": (org or {}).get("name"),
            "slug": (org or {}).get("slug"),
            "email": (org or {}).get("email"),
            "phone": (org or {}).get("phone"),
            "address": (org or {}).get("address"),
        },
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


class PartnerOrgLandingLeadCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    phone: str = Field(..., min_length=5, max_length=32)
    email: Optional[str] = Field(default=None, max_length=200)
    message: Optional[str] = Field(default=None, max_length=2000)
    interest: Optional[str] = Field(default=None, max_length=300)

    @field_validator("name", "phone", mode="before")
    @classmethod
    def _req(cls, v):
        s = str(v or "").strip()
        if not s:
            raise ValueError("required")
        return s

    @field_validator("email", "message", "interest", mode="before")
    @classmethod
    def _opt(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class PartnerOrgBrandingController:
    @staticmethod
    async def get_my_branding(current_user: dict):
        if not is_partner_super_admin(current_user):
            raise HTTPException(status_code=403, detail="Partner admin access only")
        org_id = require_partner_org_id(current_user)
        db = get_db()
        org = await db[ORG_COL].find_one({"id": org_id})
        if not org:
            raise HTTPException(status_code=404, detail="Partner organization not found")
        doc = await db[COL].find_one({"partner_org_id": org_id})
        sections = await _site_sections_for_org(db, org_id)
        if not doc:
            out = empty_branding_response(org_id, org)
            out.update(sections)
            return out
        out = _serialize_branding(doc, org)
        out.update(sections)
        return out

    @staticmethod
    async def upsert_my_branding(body: PartnerOrgBrandingUpsert, current_user: dict):
        if not is_partner_super_admin(current_user):
            raise HTTPException(status_code=403, detail="Partner admin access only")
        org_id = require_partner_org_id(current_user)
        db = get_db()
        await ensure_partner_org_branding_indexes(db)
        org = await db[ORG_COL].find_one({"id": org_id})
        if not org:
            raise HTTPException(status_code=404, detail="Partner organization not found")
        if org.get("is_active") is False or org.get("status") == "disabled":
            raise HTTPException(status_code=403, detail="Partner organization is disabled")

        existing = await db[COL].find_one({"partner_org_id": org_id})
        now = datetime.utcnow()
        patch: Dict[str, Any] = {"updated_at": now}
        if body.media is not None:
            patch["media"] = _dump(body.media)
        if body.content is not None:
            patch["content"] = _dump(body.content)
        if body.facilities is not None:
            patch["facilities"] = _dump(body.facilities)
        if body.social_links is not None:
            patch["social_links"] = _dump(body.social_links)
        if body.contact_page is not None:
            patch["contact_page"] = _dump(body.contact_page)
        if body.footer is not None:
            patch["footer"] = _dump(body.footer)

        if existing:
            await db[COL].update_one({"id": existing["id"]}, {"$set": patch})
            doc = await db[COL].find_one({"id": existing["id"]})
        else:
            branding = PartnerOrgBranding(partner_org_id=org_id)
            doc = branding.dict()
            doc.update(patch)
            doc["id"] = doc.get("id") or str(uuid4())
            doc["created_at"] = now
            await db[COL].insert_one(doc)
            doc = await db[COL].find_one({"partner_org_id": org_id})

        branding = _serialize_branding(doc or {}, org)
        branding.update(await _site_sections_for_org(db, org_id))
        return {
            "message": "Branding saved successfully",
            "branding": branding,
        }

    @staticmethod
    async def public_directory(q: Optional[str] = None, skip: int = 0, limit: int = 50):
        db = get_db()
        query: Dict[str, Any] = {
            "is_active": True,
            "status": {"$nin": ["disabled", "pending_payment"]},
        }
        if q and q.strip():
            term = q.strip()
            query["$or"] = [
                {"name": {"$regex": term, "$options": "i"}},
                {"slug": {"$regex": term, "$options": "i"}},
                {"address": {"$regex": term, "$options": "i"}},
            ]
        total = await db[ORG_COL].count_documents(query)
        orgs = (
            await db[ORG_COL]
            .find(query)
            .sort("name", 1)
            .skip(skip)
            .limit(limit)
            .to_list(limit)
        )
        org_ids = [o["id"] for o in orgs if o.get("id")]
        branding_by_org = {}
        if org_ids:
            async for b in db[COL].find({"partner_org_id": {"$in": org_ids}}):
                branding_by_org[b["partner_org_id"]] = b

        partners = []
        for org in orgs:
            b = branding_by_org.get(org["id"]) or {}
            media = b.get("media") or {}
            content = b.get("content") or {}
            partners.append(
                {
                    "partner_org_id": org.get("id"),
                    "name": org.get("name"),
                    "slug": org.get("slug"),
                    "address": org.get("address"),
                    "email": org.get("email"),
                    "phone": org.get("phone"),
                    "logo_url": media.get("logo_url"),
                    "cover_banner_url": media.get("cover_banner_url"),
                    "short_description": content.get("short_description"),
                    "tagline": content.get("tagline"),
                    "landing_path": f"/partner-orgs/{org.get('slug')}" if org.get("slug") else None,
                }
            )
        return {"partners": partners, "total": total, "skip": skip, "limit": limit}

    @staticmethod
    async def public_landing_by_slug(slug: str):
        db = get_db()
        org = await db[ORG_COL].find_one({"slug": (slug or "").strip().lower()})
        if not org:
            # try exact slug
            org = await db[ORG_COL].find_one({"slug": (slug or "").strip()})
        if (
            not org
            or org.get("is_active") is False
            or org.get("status") == "disabled"
        ):
            raise HTTPException(status_code=404, detail="Partner not found")

        doc = await db[COL].find_one({"partner_org_id": org["id"]})
        media = (doc or {}).get("media") or {}
        content = (doc or {}).get("content") or {}
        facilities = (doc or {}).get("facilities") or {}
        social = (doc or {}).get("social_links") or {}
        contact_page = (doc or {}).get("contact_page") or {}
        footer = (doc or {}).get("footer") or {}
        sections = await _site_sections_for_org(db, org["id"])

        # Contact page CMS overrides org snapshot when set.
        contact_phone = contact_page.get("phone") or org.get("phone")
        contact_email = contact_page.get("email") or org.get("email")
        contact_address = contact_page.get("address") or org.get("address")

        return {
            "partner_org_id": org["id"],
            "slug": org.get("slug"),
            "has_branding": bool(doc),
            "hero": {
                "name": org.get("name"),
                "tagline": content.get("tagline") or content.get("short_description"),
                "logo_url": media.get("logo_url"),
                "cover_banner_url": media.get("cover_banner_url"),
            },
            "about": {
                "short_description": content.get("short_description"),
                "about_content": content.get("about_content"),
                "our_story": content.get("our_story"),
                "vision": content.get("vision"),
                "mission": content.get("mission"),
                "why_choose_us": content.get("why_choose_us"),
                "why_choose_us_points": content.get("why_choose_us_points") or [],
            },
            "contact": {
                "phone": contact_phone,
                "email": contact_email,
                "address": contact_address,
            },
            "contact_page": {
                "headline": contact_page.get("headline") or f"Contact {org.get('name') or 'us'}",
                "intro": contact_page.get("intro"),
                "address": contact_address,
                "phone": contact_phone,
                "email": contact_email,
                "map_embed_url": contact_page.get("map_embed_url")
                or facilities.get("map_embed_url"),
            },
            "footer": {
                "logo_url": footer.get("logo_url") or media.get("logo_url"),
                "logo_alt": footer.get("logo_alt") or media.get("logo_alt") or org.get("name"),
                "description": footer.get("description")
                or content.get("short_description")
                or content.get("tagline"),
            },
            "facilities": facilities,
            "social_links": social,
            "login_path": f"/partners/{org.get('slug')}/login" if org.get("slug") else None,
            **sections,
        }

    @staticmethod
    async def submit_landing_lead(slug: str, payload: PartnerOrgLandingLeadCreate):
        db = get_db()
        org = await db[ORG_COL].find_one({"slug": (slug or "").strip().lower()})
        if not org:
            org = await db[ORG_COL].find_one({"slug": (slug or "").strip()})
        if (
            not org
            or org.get("is_active") is False
            or org.get("status") == "disabled"
        ):
            raise HTTPException(status_code=404, detail="Partner not found")

        ser, merged = await create_or_merge_lead(
            name=payload.name,
            phone=payload.phone,
            email=payload.email or "",
            course=payload.interest or "",
            source="partner_org_landing",
            source_type="partner_org_landing",
            source_ref_type=None,
            source_ref_id=None,
            branch_id=None,
            branch_name=org.get("name"),
            message=payload.message,
            partner_org_id=org["id"],
        )
        out = lead_to_response_dict(ser)
        out["duplicate_merged"] = merged
        out["partner_org"] = {
            "id": org["id"],
            "name": org.get("name"),
            "slug": org.get("slug"),
        }
        return {"message": "Thank you — we received your enquiry.", "lead": out}
