"""
M21-S09 Collaboration Partner Landing Page QA.

  .venv\\Scripts\\python.exe scripts/qa_m21_s09_partner_landing.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = (
    os.getenv("STUDENT_QA_BASE")
    or os.getenv("BILLING_QA_BASE")
    or os.getenv("INVOICE_QA_BASE")
    or "http://127.0.0.1:8003"
).rstrip("/")
FE_ROOT = Path(ROOT).parent / "rockmartialarts-fe"


def ok(label, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    extra = f" — {detail}" if detail else ""
    line = f"[{status}] {label}{extra}"
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))
    return passed


def http_json(method, path, body=None, token=None, expect_status=None):
    url = f"{BASE}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            payload = json.loads(raw) if raw else {}
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="ignore")
        try:
            payload = json.loads(raw) if raw else {}
        except Exception:
            payload = {"detail": raw}
        status = exc.code
    if expect_status is not None and status != expect_status:
        raise AssertionError(
            f"{method} {path} expected {expect_status} got {status}: {payload}"
        )
    return status, payload


def _mint_superadmin_token():
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(ROOT) / ".env")
        import asyncio
        import jwt
        from motor.motor_asyncio import AsyncIOMotorClient

        uri = (
            os.getenv("MONGO_URI")
            or os.getenv("MONGO_URL")
            or os.getenv("MONGODB_URL")
            or os.getenv("DATABASE_URL")
        )
        if not uri or "your_database" in uri:
            return None
        dbn = os.getenv("DB_NAME") or "marshalats"
        secret = os.getenv("SECRET_KEY") or "student_management_secret_key_2025_secure"

        async def run():
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
            for name in (dbn, "marshalats", "rockmartialarts", "rock_martial_arts"):
                sa = await client[name].superadmins.find_one({})
                if sa:
                    return sa, name
            return None, dbn

        try:
            sa, _ = asyncio.run(run())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                sa, _ = loop.run_until_complete(run())
            finally:
                loop.close()
        if not sa:
            return None
        return jwt.encode(
            {"sub": sa["id"], "role": "superadmin"},
            secret,
            algorithm="HS256",
        )
    except Exception as exc:
        print(f"(mint token skipped: {exc})")
        return None


def _mongo_cleanup(branch_id: str, restore_flag: bool):
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(ROOT) / ".env")
        import asyncio
        from motor.motor_asyncio import AsyncIOMotorClient

        uri = (
            os.getenv("MONGO_URI")
            or os.getenv("MONGO_URL")
            or os.getenv("MONGODB_URL")
            or os.getenv("DATABASE_URL")
        )
        if not uri:
            return
        dbn = os.getenv("DB_NAME") or "marshalats"

        async def run():
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
            for name in (dbn, "marshalats", "rockmartialarts", "rock_martial_arts"):
                db = client[name]
                await db.collaboration_partner_branding.delete_many(
                    {"branch_id": branch_id}
                )
                await db.collaboration_partner_seo.delete_many(
                    {"branch_id": branch_id}
                )
                await db.collaboration_partner_masters.delete_many(
                    {"branch_id": branch_id, "name": {"$regex": "^QA Landing"}}
                )
                await db.collaboration_partner_gallery.delete_many(
                    {"branch_id": branch_id, "title": {"$regex": "^QA Landing"}}
                )
                await db.collaboration_partner_testimonials.delete_many(
                    {
                        "branch_id": branch_id,
                        "person_name": {"$regex": "^QA Landing"},
                    }
                )
                await db.branches.update_one(
                    {"id": branch_id},
                    {
                        "$set": {
                            "allows_collaboration": restore_flag,
                            "is_collaboration_partner": restore_flag,
                        }
                    },
                )

        try:
            asyncio.run(run())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(run())
            finally:
                loop.close()
    except Exception as exc:
        print(f"(cleanup skipped: {exc})")


def test_fe_surfaces():
    landing_page = (
        FE_ROOT / "app" / "(website)" / "partners" / "[slug]" / "page.tsx"
    ).read_text(encoding="utf-8")
    landing_client = (
        FE_ROOT
        / "app"
        / "(website)"
        / "partners"
        / "[slug]"
        / "partner-landing-client.tsx"
    ).read_text(encoding="utf-8")
    directory = (
        FE_ROOT / "app" / "(website)" / "partners" / "page.tsx"
    ).read_text(encoding="utf-8")
    branch_detail = (
        FE_ROOT
        / "app"
        / "(website)"
        / "branches"
        / "[slug]"
        / "branch-detail-client.tsx"
    ).read_text(encoding="utf-8")
    be_ctrl = (
        Path(ROOT) / "controllers" / "collaboration_partner_landing_controller.py"
    ).read_text(encoding="utf-8")
    be_routes = (
        Path(ROOT) / "routes" / "collaboration_partner_routes.py"
    ).read_text(encoding="utf-8")
    checks = [
        "generateMetadata" in landing_page,
        "public/by-slug" in landing_page,
        "Book a Demo" in landing_client,
        "Why choose us" in landing_client or "why_choose_us" in landing_client,
        "Masters" in landing_client,
        "Gallery" in landing_client,
        "Testimonials" in landing_client,
        "Operating hours" in landing_client or "operating_hours" in landing_client,
        "Partner Academies" in directory,
        "public/partners" in directory,
        "/partners/" in branch_detail,
        "get_landing_by_slug" in be_ctrl,
        "list_public_partners" in be_ctrl,
        "/public/by-slug/{slug}" in be_routes,
        "/public/partners" in be_routes,
        "/public/branches/{branch_id}/landing" in be_routes,
        "hero" in be_ctrl and "about" in be_ctrl and "masters" in be_ctrl,
    ]
    return ok(
        "FE + BE partner landing surfaces",
        all(checks),
        f"{sum(checks)}/{len(checks)}",
    )


def test_workflow():
    results = []
    token = _mint_superadmin_token()
    if not token:
        results.append(ok("superadmin token", False))
        return all(results)
    results.append(ok("superadmin token", True))

    branch_id = None
    original_flag = False
    try:
        st, listed = http_json(
            "GET",
            "/api/branches?skip=0&limit=10&active_only=false",
            token=token,
            expect_status=200,
        )
        branches = listed.get("branches") or []
        if not branches:
            results.append(ok("need a branch", False))
            return all(results)
        target = branches[0]
        branch_id = target["id"]
        original_flag = bool(
            target.get("is_collaboration_partner")
            or target.get("allows_collaboration")
        )
        bi = target.get("branch") or {}
        branch_name = bi.get("name") or target.get("name") or "Branch"

        _mongo_cleanup(branch_id, restore_flag=False)

        st, _ = http_json(
            "PUT",
            f"/api/branches/{branch_id}",
            body={"is_collaboration_partner": False},
            token=token,
            expect_status=200,
        )

        st, _ = http_json(
            "GET",
            f"/api/collaboration-partners/public/branches/{branch_id}/landing",
        )
        results.append(
            ok("landing 404 for normal branch", st == 404, str(st))
        )

        st, dir0 = http_json(
            "GET",
            "/api/collaboration-partners/public/partners?limit=50",
            expect_status=200,
        )
        ids0 = {p.get("branch_id") for p in (dir0.get("partners") or [])}
        results.append(
            ok(
                "directory excludes non-partner",
                branch_id not in ids0,
            )
        )

        # Normal public branch by-slug still works (regression)
        slug_hint = (target.get("slug") or "").strip()
        if slug_hint:
            st, pub_branch = http_json(
                "GET",
                f"/api/branches/public/by-slug/{slug_hint}",
            )
            results.append(
                ok(
                    "normal branch slug regression",
                    st in (200, 404),
                    str(st),
                )
            )
        else:
            results.append(ok("normal branch slug regression (skip no slug)", True))

        st, _ = http_json(
            "PUT",
            f"/api/branches/{branch_id}",
            body={"is_collaboration_partner": True},
            token=token,
            expect_status=200,
        )

        # Seed branding + seo + master + gallery + testimonial
        st, _ = http_json(
            "PUT",
            f"/api/collaboration-partners/branches/{branch_id}/branding",
            body={
                "media": {
                    "logo_url": "/uploads/images/qa-logo.jpg",
                    "cover_banner_url": "/uploads/images/qa-banner.jpg",
                },
                "content": {
                    "short_description": "QA landing tagline",
                    "about_content": "QA about content",
                    "why_choose_us": "QA why choose",
                    "why_choose_us_points": ["Point A", "Point B"],
                    "vision": "QA vision",
                    "mission": "QA mission",
                },
                "facilities": {
                    "facilities": ["Mats", "Lockers"],
                    "map_embed_url": "https://maps.example.com/qa",
                },
            },
            token=token,
            expect_status=200,
        )
        st, _ = http_json(
            "PUT",
            f"/api/collaboration-partners/branches/{branch_id}/seo",
            body={
                "meta_title": "QA Landing SEO Title",
                "meta_description": "QA landing SEO description",
                "keywords": "qa, partner, landing",
                "og_image": "/uploads/images/qa-og.jpg",
            },
            token=token,
            expect_status=200,
        )
        st, master = http_json(
            "POST",
            f"/api/collaboration-partners/branches/{branch_id}/masters",
            body={
                "name": "QA Landing Master",
                "designation": "Sensei",
                "photo_url": "/uploads/images/qa-master.jpg",
                "biography": "QA master bio",
                "display_order": 1,
            },
            token=token,
            expect_status=200,
        )
        results.append(
            ok("seed master", bool((master.get("master") or {}).get("id")))
        )

        st, gal = http_json(
            "POST",
            f"/api/collaboration-partners/branches/{branch_id}/gallery",
            body={
                "title": "QA Landing Photo",
                "media_type": "image",
                "media_url": "/uploads/images/qa-gallery.jpg",
                "display_order": 1,
            },
            token=token,
            expect_status=200,
        )
        results.append(
            ok("seed gallery", bool((gal.get("item") or {}).get("id")), str(st))
        )

        st, testi = http_json(
            "POST",
            f"/api/collaboration-partners/branches/{branch_id}/testimonials",
            body={
                "person_name": "QA Landing Student",
                "person_role": "student",
                "testimonial_text": "Great QA training experience.",
                "status": "published",
                "display_order": 1,
            },
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "seed testimonial",
                bool((testi.get("testimonial") or {}).get("id")),
                str(st),
            )
        )

        st, landing = http_json(
            "GET",
            f"/api/collaboration-partners/public/branches/{branch_id}/landing",
            expect_status=200,
        )
        results.append(
            ok(
                "landing aggregate has hero/about/contact",
                landing.get("is_collaboration_partner") is True
                and (landing.get("hero") or {}).get("tagline") == "QA landing tagline"
                and (landing.get("about") or {}).get("about_content")
                and isinstance(landing.get("contact"), dict)
                and isinstance(landing.get("masters"), list)
                and isinstance(landing.get("gallery"), list)
                and isinstance(landing.get("testimonials"), list)
                and isinstance(landing.get("team"), list)
                and isinstance(landing.get("seo"), dict),
            )
        )
        results.append(
            ok(
                "landing integrates masters/gallery/testimonials",
                any(
                    m.get("name") == "QA Landing Master"
                    for m in (landing.get("masters") or [])
                )
                and len(landing.get("gallery") or []) >= 1
                and any(
                    (t.get("name") or t.get("student_name")) == "QA Landing Student"
                    for t in (landing.get("testimonials") or [])
                ),
            )
        )
        results.append(
            ok(
                "landing SEO attached",
                (landing.get("seo") or {}).get("meta_title") == "QA Landing SEO Title",
            )
        )

        slug = (landing.get("slug") or "").strip()
        results.append(ok("landing returns slug", bool(slug), slug))

        if slug:
            st, by_slug = http_json(
                "GET",
                f"/api/collaboration-partners/public/by-slug/{slug}",
                expect_status=200,
            )
            results.append(
                ok(
                    "landing by-slug matches branch",
                    by_slug.get("branch_id") == branch_id
                    and by_slug.get("partner_url") == f"/partners/{slug}",
                )
            )

        st, directory = http_json(
            "GET",
            "/api/collaboration-partners/public/partners?limit=100",
            expect_status=200,
        )
        row = next(
            (
                p
                for p in (directory.get("partners") or [])
                if p.get("branch_id") == branch_id
            ),
            None,
        )
        results.append(
            ok(
                "directory lists partner with URL",
                bool(row)
                and str(row.get("partner_url") or "").startswith("/partners/"),
            )
        )

        # Non-partner branches public search still works
        st, search = http_json(
            "GET",
            "/api/branches/public/search?active_only=true&limit=5",
            expect_status=200,
        )
        results.append(
            ok(
                "public branch search regression",
                isinstance(search.get("branches"), list),
            )
        )

        st, _ = http_json(
            "PUT",
            f"/api/branches/{branch_id}",
            body={"is_collaboration_partner": False},
            token=token,
            expect_status=200,
        )
        st, _ = http_json(
            "GET",
            f"/api/collaboration-partners/public/by-slug/{slug or branch_id}",
        )
        results.append(
            ok("landing hidden after flag off", st == 404, str(st))
        )

    except Exception as exc:
        results.append(ok("workflow exception", False, str(exc)))
    finally:
        if branch_id:
            _mongo_cleanup(branch_id, restore_flag=original_flag)

    return all(results)


def main():
    print(f"BASE={BASE}")
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    a = test_fe_surfaces()
    b = test_workflow()
    print(f"\nRESULT: {int(a) + int(b)}/2 suites passed")
    sys.exit(0 if a and b else 1)


if __name__ == "__main__":
    main()
