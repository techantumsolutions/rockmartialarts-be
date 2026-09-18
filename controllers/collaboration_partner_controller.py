"""
M21-S02 Collaboration Partner profile controller (FastAPI / Mongo).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import HTTPException

from models.collaboration_partner_models import (
    CollaborationPartnerProfile,
    PartnerAgreementInfo,
    PartnerContactInfo,
    PartnerProfileUpsert,
    PartnerRegistrationTax,
    empty_profile_response,
)
from utils.collaboration_partner import (
    branch_is_collaboration_partner,
    enrich_collaboration_flag,
    get_partner_branch_for_cms,
    assert_can_view_partner_status,
    filter_partner_branch_query_for_user,
    partner_cms_permissions,
)
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.partner_id import ensure_partner_id_indexes, next_partner_id


def _dump(model) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_unset=False, mode="python")
    return model.dict()


def _dump_unset(model) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_unset=True, mode="python")
    return model.dict(exclude_unset=True)


def _merge_section(existing: Optional[dict], incoming: Optional[dict], factory) -> dict:
    """Merge nested profile section. Keys present in incoming (including null) win."""
    base = _dump(factory())
    if existing:
        for k, v in existing.items():
            if k in base:
                base[k] = v
    if incoming is not None:
        for k, v in incoming.items():
            if k in base:
                base[k] = v
    return base


def _enrich_profile_doc(doc: dict, branch: Optional[dict] = None) -> dict:
    out = serialize_doc(doc) if doc else {}
    out["has_profile"] = True
    if branch:
        enrich_collaboration_flag(branch)
        bi = branch.get("branch") or {}
        out["branch_snapshot"] = {
            "id": branch.get("id"),
            "name": bi.get("name"),
            "code": bi.get("code"),
            "email": bi.get("email"),
            "phone": bi.get("phone"),
        }
    return out


class CollaborationPartnerController:
    @staticmethod
    async def list_partner_branches(
        skip: int = 0,
        limit: int = 100,
        current_user: dict = None,
    ) -> dict:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        db = get_db()
        await ensure_partner_id_indexes(db)
        if limit > 200:
            limit = 200

        query = {
            "$or": [
                {"allows_collaboration": True},
                {"is_collaboration_partner": True},
            ]
        }
        query = await filter_partner_branch_query_for_user(db, current_user, query)
        total = await db.branches.count_documents(query)
        branches = (
            await db.branches.find(query).skip(skip).limit(limit).to_list(limit)
        )
        profiles: Dict[str, dict] = {}
        branding_ids = set()
        seo_ids = set()
        masters_counts: Dict[str, int] = {}
        gallery_counts: Dict[str, int] = {}
        testimonials_counts: Dict[str, int] = {}
        team_counts: Dict[str, int] = {}
        branch_ids = [b["id"] for b in branches]
        if branch_ids:
            async for p in db.collaboration_partner_profiles.find(
                {"branch_id": {"$in": branch_ids}}
            ):
                profiles[p["branch_id"]] = p
            async for br in db.collaboration_partner_branding.find(
                {"branch_id": {"$in": branch_ids}}, {"branch_id": 1}
            ):
                branding_ids.add(br["branch_id"])
            async for seo in db.collaboration_partner_seo.find(
                {"branch_id": {"$in": branch_ids}}, {"branch_id": 1}
            ):
                seo_ids.add(seo["branch_id"])
            async for m in db.collaboration_partner_masters.find(
                {"branch_id": {"$in": branch_ids}, "is_active": True},
                {"branch_id": 1},
            ):
                bid = m.get("branch_id")
                masters_counts[bid] = masters_counts.get(bid, 0) + 1
            async for g in db.collaboration_partner_gallery.find(
                {"branch_id": {"$in": branch_ids}, "is_active": True},
                {"branch_id": 1},
            ):
                bid = g.get("branch_id")
                gallery_counts[bid] = gallery_counts.get(bid, 0) + 1
            async for t in db.collaboration_partner_testimonials.find(
                {
                    "branch_id": {"$in": branch_ids},
                    "is_active": True,
                    "status": "published",
                },
                {"branch_id": 1},
            ):
                bid = t.get("branch_id")
                testimonials_counts[bid] = testimonials_counts.get(bid, 0) + 1
            async for tm in db.collaboration_partner_team.find(
                {"branch_id": {"$in": branch_ids}, "is_active": True},
                {"branch_id": 1},
            ):
                bid = tm.get("branch_id")
                team_counts[bid] = team_counts.get(bid, 0) + 1

        items = []
        for b in branches:
            enrich_collaboration_flag(b)
            bi = b.get("branch") or {}
            prof = profiles.get(b["id"])
            mcount = masters_counts.get(b["id"], 0)
            gcount = gallery_counts.get(b["id"], 0)
            tcount = testimonials_counts.get(b["id"], 0)
            team_count = team_counts.get(b["id"], 0)
            items.append(
                {
                    "branch_id": b["id"],
                    "branch_name": bi.get("name"),
                    "branch_code": bi.get("code"),
                    "is_active": b.get("is_active", True),
                    "is_collaboration_partner": True,
                    "has_profile": bool(prof),
                    "has_branding": b["id"] in branding_ids,
                    "has_seo": b["id"] in seo_ids,
                    "has_masters": mcount > 0,
                    "masters_count": mcount,
                    "has_gallery": gcount > 0,
                    "gallery_count": gcount,
                    "has_testimonials": tcount > 0,
                    "testimonials_count": tcount,
                    "has_team": team_count > 0,
                    "team_count": team_count,
                    "partner_id": (prof or {}).get("partner_id"),
                    "legal_business_name": (
                        ((prof or {}).get("registration") or {}).get("legal_business_name")
                    ),
                    "agreement_status": (
                        ((prof or {}).get("agreement") or {}).get("agreement_status")
                    ),
                }
            )
        return {"partners": items, "total": total, "skip": skip, "limit": limit}

    @staticmethod
    async def get_profile(branch_id: str, current_user: dict = None) -> dict:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        db = get_db()
        branch = await get_partner_branch_for_cms(db, branch_id, current_user)
        await ensure_partner_id_indexes(db)

        doc = await db.collaboration_partner_profiles.find_one({"branch_id": branch_id})
        if not doc:
            return empty_profile_response(branch_id, branch)
        return _enrich_profile_doc(doc, branch)

    @staticmethod
    async def upsert_profile(
        branch_id: str,
        payload: PartnerProfileUpsert,
        current_user: dict = None,
    ) -> dict:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        db = get_db()
        branch = await get_partner_branch_for_cms(db, branch_id, current_user)
        await ensure_partner_id_indexes(db)

        incoming = _dump_unset(payload)
        existing = await db.collaboration_partner_profiles.find_one(
            {"branch_id": branch_id}
        )
        now = datetime.utcnow()

        registration = _merge_section(
            (existing or {}).get("registration"),
            incoming.get("registration"),
            PartnerRegistrationTax,
        )
        contact = _merge_section(
            (existing or {}).get("contact"),
            incoming.get("contact"),
            PartnerContactInfo,
        )
        agreement = _merge_section(
            (existing or {}).get("agreement"),
            incoming.get("agreement"),
            PartnerAgreementInfo,
        )

        if existing:
            await db.collaboration_partner_profiles.update_one(
                {"branch_id": branch_id},
                {
                    "$set": {
                        "registration": registration,
                        "contact": contact,
                        "agreement": agreement,
                        "updated_at": now,
                    }
                },
            )
            doc = await db.collaboration_partner_profiles.find_one(
                {"branch_id": branch_id}
            )
            return {
                "message": "Partner profile updated",
                "profile": _enrich_profile_doc(doc, branch),
            }

        partner_id = await next_partner_id()
        # Seed contact from branch when creating and fields empty
        if not contact.get("primary_contact_email"):
            contact["primary_contact_email"] = (branch.get("branch") or {}).get("email")
        if not contact.get("primary_contact_phone"):
            contact["primary_contact_phone"] = (branch.get("branch") or {}).get("phone")
        if not registration.get("legal_business_name"):
            registration["legal_business_name"] = (branch.get("branch") or {}).get(
                "name"
            )

        profile = CollaborationPartnerProfile(
            branch_id=branch_id,
            partner_id=partner_id,
            registration=PartnerRegistrationTax(**registration),
            contact=PartnerContactInfo(**contact),
            agreement=PartnerAgreementInfo(**agreement),
            created_at=now,
            updated_at=now,
        )
        doc = _dump(profile)
        await db.collaboration_partner_profiles.insert_one(doc)
        return {
            "message": "Partner profile created",
            "profile": _enrich_profile_doc(doc, branch),
        }

    @staticmethod
    async def status_with_profile(branch_id: str, current_user: dict = None) -> dict:
        db = get_db()
        branch = await assert_can_view_partner_status(db, current_user, branch_id)
        enrich_collaboration_flag(branch)
        enabled = branch_is_collaboration_partner(branch)
        partner_id = None
        has_profile = False
        has_branding = False
        has_seo = False
        masters_count = 0
        gallery_count = 0
        testimonials_count = 0
        team_count = 0
        if enabled:
            prof = await db.collaboration_partner_profiles.find_one(
                {"branch_id": branch_id}, {"partner_id": 1}
            )
            if prof:
                has_profile = True
                partner_id = prof.get("partner_id")
            branding = await db.collaboration_partner_branding.find_one(
                {"branch_id": branch_id}, {"_id": 1}
            )
            has_branding = bool(branding)
            seo = await db.collaboration_partner_seo.find_one(
                {"branch_id": branch_id}, {"_id": 1}
            )
            has_seo = bool(seo)
            masters_count = await db.collaboration_partner_masters.count_documents(
                {"branch_id": branch_id, "is_active": True}
            )
            gallery_count = await db.collaboration_partner_gallery.count_documents(
                {"branch_id": branch_id, "is_active": True}
            )
            testimonials_count = (
                await db.collaboration_partner_testimonials.count_documents(
                    {
                        "branch_id": branch_id,
                        "is_active": True,
                        "status": "published",
                    }
                )
            )
            team_count = await db.collaboration_partner_team.count_documents(
                {"branch_id": branch_id, "is_active": True}
            )
        return {
            "branch_id": branch_id,
            "is_collaboration_partner": enabled,
            "allows_collaboration": enabled,
            "partner_features_enabled": enabled,
            "has_profile": has_profile,
            "has_branding": has_branding,
            "has_seo": has_seo,
            "has_masters": masters_count > 0,
            "masters_count": masters_count,
            "has_gallery": gallery_count > 0,
            "gallery_count": gallery_count,
            "has_testimonials": testimonials_count > 0,
            "testimonials_count": testimonials_count,
            "has_team": team_count > 0,
            "team_count": team_count,
            "partner_id": partner_id,
        }
