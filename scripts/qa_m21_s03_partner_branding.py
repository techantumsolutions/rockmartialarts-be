"""
M21-S03 Collaboration Partner branding & content QA.

  .venv\\Scripts\\python.exe scripts/qa_m21_s03_partner_branding.py
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
    page = (
        FE_ROOT
        / "app"
        / "[adminType]"
        / "dashboard"
        / "partner-branding"
        / "page.tsx"
    ).read_text(encoding="utf-8")
    nav = (FE_ROOT / "lib" / "dashboard-config.ts").read_text(encoding="utf-8")
    branches = (
        FE_ROOT / "app" / "[adminType]" / "dashboard" / "branches" / "page.tsx"
    ).read_text(encoding="utf-8")
    be_models = (
        Path(ROOT) / "models" / "collaboration_partner_branding_models.py"
    ).read_text(encoding="utf-8")
    be_routes = (
        Path(ROOT) / "routes" / "collaboration_partner_routes.py"
    ).read_text(encoding="utf-8")
    checks = [
        "Partner Branding & Content" in page,
        "Cover banner" in page or "cover banner" in page.lower(),
        "Operating hours" in page,
        "Social links" in page,
        "Vision" in page and "Mission" in page,
        "Partner Branding" in nav,
        "partner-branding" in nav,
        "partner-branding?branchId=" in branches,
        "logo_url" in be_models,
        "cover_banner_url" in be_models,
        "operating_hours" in be_models,
        "social_links" in be_models,
        "why_choose_us" in be_models,
        "/branches/{branch_id}/branding" in be_routes,
    ]
    return ok(
        "FE + BE partner branding surfaces",
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
    other_id = None
    original_flag = False
    other_flag = False
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
        if len(branches) > 1:
            other_id = branches[1]["id"]
            other_flag = bool(
                branches[1].get("is_collaboration_partner")
                or branches[1].get("allows_collaboration")
            )

        _mongo_cleanup(branch_id, restore_flag=False)

        # Snapshot other branch doc keys that must not gain partner branding fields
        st, other_before = (
            http_json(
                "GET",
                f"/api/branches/{other_id}",
                token=token,
                expect_status=200,
            )
            if other_id
            else (200, {})
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
            f"/api/collaboration-partners/branches/{branch_id}/branding",
            token=token,
        )
        results.append(ok("GET branding 403 for normal branch", st == 403, str(st)))

        st, _ = http_json(
            "PUT",
            f"/api/branches/{branch_id}",
            body={"is_collaboration_partner": True},
            token=token,
            expect_status=200,
        )

        st, empty = http_json(
            "GET",
            f"/api/collaboration-partners/branches/{branch_id}/branding",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "GET empty branding scaffold",
                empty.get("has_branding") is False
                and empty.get("branch_id") == branch_id
                and isinstance(empty.get("operating_hours"), list)
                and len(empty.get("operating_hours") or []) == 7,
            )
        )

        body = {
            "media": {
                "logo_url": "/uploads/images/qa-partner-logo.png",
                "logo_alt": "QA Logo",
                "cover_banner_url": "/uploads/images/qa-partner-banner.jpg",
                "cover_banner_alt": "QA Banner",
            },
            "content": {
                "short_description": "QA short desc",
                "about_content": "QA about content for partner only",
                "vision": "QA vision",
                "mission": "QA mission",
                "our_story": "QA story",
                "why_choose_us": "Because QA",
                "why_choose_us_points": ["Point A", "Point B"],
            },
            "operating_hours": [
                {
                    "day": "monday",
                    "open_time": "07:00",
                    "close_time": "20:00",
                    "is_closed": False,
                },
                {
                    "day": "sunday",
                    "open_time": None,
                    "close_time": None,
                    "is_closed": True,
                },
            ],
            "hours_notes": "QA holiday note",
            "facilities": {
                "facilities": ["Mats", "Lockers"],
                "parking_info": "Street parking",
                "location_highlights": ["Near metro"],
                "map_embed_url": "https://maps.example.com/qa",
            },
            "social_links": {
                "website": "https://qa-partner.example.com",
                "instagram": "https://instagram.com/qa",
                "facebook": None,
                "youtube": None,
                "linkedin": None,
                "twitter": None,
                "whatsapp": "919999999999",
            },
        }
        st, created = http_json(
            "PUT",
            f"/api/collaboration-partners/branches/{branch_id}/branding",
            body=body,
            token=token,
            expect_status=200,
        )
        branding = created.get("branding") or {}
        results.append(
            ok(
                "create branding with logo/banner/content",
                branding.get("has_branding") is True
                and (branding.get("media") or {}).get("logo_url")
                == "/uploads/images/qa-partner-logo.png"
                and (branding.get("content") or {}).get("vision") == "QA vision"
                and "Point A"
                in ((branding.get("content") or {}).get("why_choose_us_points") or []),
            )
        )

        hours = branding.get("operating_hours") or []
        mon = next((h for h in hours if h.get("day") == "monday"), None)
        sun = next((h for h in hours if h.get("day") == "sunday"), None)
        results.append(
            ok(
                "operating hours normalized (7 days)",
                len(hours) == 7
                and mon
                and mon.get("open_time") == "07:00"
                and sun
                and sun.get("is_closed") is True,
            )
        )

        st, status = http_json(
            "GET",
            f"/api/collaboration-partners/branches/{branch_id}/status",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "status has_branding true",
                status.get("has_branding") is True
                and status.get("partner_features_enabled") is True,
            )
        )

        # Update social + verify isolation: branch master GET should not get branding fields
        st, updated = http_json(
            "PUT",
            f"/api/collaboration-partners/branches/{branch_id}/branding",
            body={
                "social_links": {
                    "website": "https://qa-partner-updated.example.com",
                    "instagram": "https://instagram.com/qa2",
                },
                "content": {"short_description": "Updated short"},
            },
            token=token,
            expect_status=200,
        )
        ub = updated.get("branding") or {}
        results.append(
            ok(
                "update branding keeps media + new social",
                (ub.get("media") or {}).get("logo_url")
                == "/uploads/images/qa-partner-logo.png"
                and (ub.get("social_links") or {}).get("website")
                == "https://qa-partner-updated.example.com"
                and (ub.get("content") or {}).get("short_description")
                == "Updated short",
            )
        )

        st, branch_doc = http_json(
            "GET",
            f"/api/branches/{branch_id}",
            token=token,
            expect_status=200,
        )
        # Partner branding must not pollute branch master with logo_url at root
        results.append(
            ok(
                "branch master not polluted with branding root fields",
                branch_doc.get("logo_url") is None
                and branch_doc.get("cover_banner_url") is None
                and branch_doc.get("social_links") is None
                and branch_doc.get("our_story") is None,
            )
        )

        if other_id:
            st, other_after = http_json(
                "GET",
                f"/api/branches/{other_id}",
                token=token,
                expect_status=200,
            )
            results.append(
                ok(
                    "other branch unchanged by partner branding",
                    other_after.get("id") == other_id
                    and other_after.get("logo_url") is None
                    and (other_before.get("branch") or {}).get("name")
                    == (other_after.get("branch") or {}).get("name"),
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
            f"/api/collaboration-partners/branches/{branch_id}/branding",
            token=token,
        )
        results.append(ok("branding gated after flag off", st == 403, str(st)))

        st, still = http_json(
            "GET",
            "/api/branches?skip=0&limit=5&active_only=false",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "normal branch list regression",
                len(still.get("branches") or []) >= 1,
            )
        )

    except Exception as exc:
        results.append(ok("workflow exception", False, str(exc)))
    finally:
        if branch_id:
            _mongo_cleanup(branch_id, restore_flag=original_flag)
        if other_id is not None:
            try:
                # restore other flag only
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
                dbn = os.getenv("DB_NAME") or "marshalats"

                async def run():
                    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
                    for name in (
                        dbn,
                        "marshalats",
                        "rockmartialarts",
                        "rock_martial_arts",
                    ):
                        await client[name].branches.update_one(
                            {"id": other_id},
                            {
                                "$set": {
                                    "allows_collaboration": other_flag,
                                    "is_collaboration_partner": other_flag,
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
            except Exception:
                pass

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
