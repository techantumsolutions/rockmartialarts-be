#!/usr/bin/env python3
"""QA: Partner–Academy gaps — visibility, branding resolve, ownership audit helpers."""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_visibility_helpers():
    from utils.academy_visibility import (
        genuine_academy_mongo_clause,
        is_academy_publicly_visible,
        is_genuine_academy,
        partner_org_public_ownership_clause,
        public_academy_mongo_clause,
    )

    stub = {"created_via": "partner_org_auto_branch", "is_active": True}
    assert not is_genuine_academy(stub)
    assert not is_academy_publicly_visible(stub)

    active = {"is_active": True, "created_via": "manual"}
    assert is_academy_publicly_visible(active)  # missing is_public ⇒ visible

    hidden = {"is_active": True, "is_public": False}
    assert not is_academy_publicly_visible(hidden)

    inactive = {"is_active": False, "is_public": True}
    assert not is_academy_publicly_visible(inactive)

    clause = public_academy_mongo_clause(active_only=True)
    assert clause["$and"]
    assert genuine_academy_mongo_clause()["created_via"]["$ne"] == "partner_org_auto_branch"
    ownership = partner_org_public_ownership_clause(["org-1"])
    assert {"partner_org_id": {"$in": ["org-1"]}} in ownership["$or"]
    print("OK visibility helpers")


def test_branding_merge():
    from utils.academy_branding import (
        _merge_field_level,
        academy_branding_has_content,
        normalize_academy_branding_patch,
        partner_org_branding_to_flat,
        RMAA_DEFAULT_BRANDING,
    )

    partner = partner_org_branding_to_flat(
        {"name": "Nrutyanjali", "slug": "nrutyanjali"},
        {
            "media": {"logo_url": "https://cdn/logo.png"},
            "content": {"tagline": "Partner tag"},
            "colors": {"primary": "#111111"},
        },
    )
    base = dict(RMAA_DEFAULT_BRANDING)
    mid = _merge_field_level(base, partner)
    assert mid["logo_url"] == "https://cdn/logo.png"
    assert mid["tagline"] == "Partner tag"

    academy = normalize_academy_branding_patch(
        {
            "display_name": "Balaji Kick Boxing Hub",
            "primary_color": "#E1BB33",
            "tagline": "",  # empty should not wipe partner tagline
        }
    )
    final = _merge_field_level(mid, academy)
    assert final["display_name"] == "Balaji Kick Boxing Hub"
    assert final["primary_color"] == "#E1BB33"
    assert final["tagline"] == "Partner tag"
    assert academy_branding_has_content(academy)
    print("OK branding merge / field-level fallback")


async def test_resolve_mock():
    class FakeFind:
        def __init__(self, doc):
            self.doc = doc

        async def find_one(self, *a, **k):
            return self.doc

    class FakeDB:
        def __init__(self):
            self.partner_organizations = FakeFind(
                {"id": "org1", "name": "Partner Org", "slug": "partner-org"}
            )
            self.partner_org_branding = FakeFind(
                {
                    "partner_org_id": "org1",
                    "media": {"logo_url": "https://cdn/partner.png"},
                    "content": {"tagline": "Org tag"},
                }
            )

    from utils.academy_branding import resolve_public_academy_branding

    branch = {
        "id": "b1",
        "partner_org_id": "org1",
        "branch": {"name": "Academy One"},
        "site_branding": {
            "display_name": "Academy One Special",
            "logo_url": "https://cdn/academy.png",
        },
    }
    resolved = await resolve_public_academy_branding(FakeDB(), branch)
    assert resolved["has_own_branding"] is True
    assert "academy" in resolved["branding_sources"]
    assert resolved["site_branding"]["logo_url"] == "https://cdn/academy.png"
    assert resolved["site_branding"]["tagline"] == "Org tag"
    assert resolved["brand_name"] == "Academy One Special"
    print("OK resolve_public_academy_branding")


def main():
    test_visibility_helpers()
    test_branding_merge()
    asyncio.run(test_resolve_mock())
    print("ALL PASS")


if __name__ == "__main__":
    main()
