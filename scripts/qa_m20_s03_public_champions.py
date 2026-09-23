"""
M20-S03 Public Champions section QA.

  .venv\\Scripts\\python.exe scripts/qa_m20_s03_public_champions.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid
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
        with urllib.request.urlopen(req, timeout=25) as resp:
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
                    return sa
            return None

        try:
            sa = asyncio.run(run())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                sa = loop.run_until_complete(run())
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


def _cleanup(champion_ids):
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
        if not uri or not champion_ids:
            return
        dbn = os.getenv("DB_NAME") or "marshalats"

        async def run():
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
            for name in (dbn, "marshalats", "rockmartialarts", "rock_martial_arts"):
                await client[name].champions.delete_many(
                    {"id": {"$in": champion_ids}}
                )
                await client[name].champion_achievements.delete_many(
                    {"champion_id": {"$in": champion_ids}}
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
    results = []
    listing = (
        FE_ROOT / "components" / "champions" / "ChampionsListingClient.tsx"
    ).read_text(encoding="utf-8")
    detail = (
        FE_ROOT / "components" / "champions" / "ChampionDetailClient.tsx"
    ).read_text(encoding="utf-8")
    page = (FE_ROOT / "app" / "(website)" / "champions" / "page.tsx").read_text(
        encoding="utf-8"
    )
    detail_page = (
        FE_ROOT / "app" / "(website)" / "champions" / "[slug]" / "page.tsx"
    ).read_text(encoding="utf-8")
    api = (FE_ROOT / "lib" / "championAPI.ts").read_text(encoding="utf-8")
    footer = (
        FE_ROOT / "components" / "website" / "WebsiteFooter.tsx"
    ).read_text(encoding="utf-8")
    be_routes = (Path(ROOT) / "routes" / "champion_routes.py").read_text(
        encoding="utf-8"
    )
    be_svc = (Path(ROOT) / "utils" / "champion_service.py").read_text(
        encoding="utf-8"
    )
    checks = [
        "listPublic" in api,
        "getPublic" in api,
        "ChampionsListingClient" in page,
        "ChampionDetailClient" in detail_page,
        "grid-cols-1 sm:grid-cols-2" in listing or "sm:grid-cols-2" in listing,
        "Success story" in detail,
        "Achievements" in detail,
        "/champions" in footer,
        "/public" in be_routes,
        "list_public_champions" in be_svc,
        "get_public_champion" in be_svc,
    ]
    results.append(
        ok("FE + BE public surfaces", all(checks), f"{sum(checks)}/{len(checks)}")
    )
    return all(results)


def test_workflow():
    results = []
    token = _mint_superadmin_token()
    if not token:
        results.append(ok("superadmin token", False))
        return all(results)
    results.append(ok("superadmin token", True))

    suffix = uuid.uuid4().hex[:8]
    ids = []

    try:
        # Draft must not appear publicly
        st, draft = http_json(
            "POST",
            "/api/champions",
            body={
                "name": f"QA Draft Public {suffix}",
                "slug": f"qa-draft-pub-{suffix}",
                "status": "draft",
                "photo_url": "https://example.com/d.jpg",
            },
            token=token,
            expect_status=201,
        )
        draft_id = (draft.get("champion") or {}).get("id")
        if draft_id:
            ids.append(draft_id)

        st, live = http_json(
            "POST",
            "/api/champions",
            body={
                "name": f"QA Public Champ {suffix}",
                "slug": f"qa-public-champ-{suffix}",
                "headline": "State champion",
                "short_bio": "Short bio",
                "success_story": "Long success story for public page.",
                "photo_url": "https://example.com/c.jpg",
                "status": "published",
                "display_order": 5,
            },
            token=token,
            expect_status=201,
        )
        live_c = live.get("champion") or {}
        live_id = live_c.get("id")
        live_slug = live_c.get("slug")
        if live_id:
            ids.append(live_id)
        results.append(
            ok(
                "create published champion",
                bool(live_id) and live_c.get("status") == "published",
                str(live_slug),
            )
        )

        st, ach = http_json(
            "POST",
            f"/api/champions/{live_id}/achievements",
            body={
                "title": f"QA Public Gold {suffix}",
                "recognition_level": "state",
                "competition_name": "State Open",
                "award_title": "Gold",
                "images": ["https://example.com/medal.jpg"],
                "status": "active",
            },
            token=token,
            expect_status=201,
        )
        results.append(ok("attach active achievement", bool((ach.get("achievement") or {}).get("id"))))

        # Public list — no auth
        st, pub_list = http_json(
            "GET",
            f"/api/champions/public?search={suffix}",
            expect_status=200,
        )
        pub_ids = {c.get("id") for c in (pub_list.get("champions") or [])}
        results.append(
            ok(
                "public list shows published only",
                live_id in pub_ids and draft_id not in pub_ids,
                f"total={pub_list.get('total')}",
            )
        )
        hit = next(
            (c for c in (pub_list.get("champions") or []) if c.get("id") == live_id),
            {},
        )
        results.append(
            ok(
                "list includes achievement_count",
                (hit.get("achievement_count") or 0) >= 1,
                str(hit.get("achievement_count")),
            )
        )
        results.append(
            ok(
                "list omits full success_story",
                "success_story" not in hit or hit.get("success_story") is None,
            )
        )

        # Public detail by slug — no auth
        st, detail = http_json(
            "GET",
            f"/api/champions/public/{live_slug}",
            expect_status=200,
        )
        dchamp = detail.get("champion") or {}
        dach = detail.get("achievements") or []
        results.append(
            ok(
                "public detail by slug",
                dchamp.get("id") == live_id
                and dchamp.get("success_story")
                and len(dach) >= 1,
                f"ach={len(dach)}",
            )
        )

        # Draft slug 404
        st, missing = http_json(
            "GET",
            f"/api/champions/public/qa-draft-pub-{suffix}",
        )
        results.append(ok("draft not publicly accessible", st == 404, str(st)))

        # Unpublish hides from public
        st, _ = http_json(
            "POST",
            f"/api/champions/{live_id}/unpublish",
            token=token,
            expect_status=200,
        )
        st, after = http_json(
            "GET",
            f"/api/champions/public?search={suffix}",
            expect_status=200,
        )
        after_ids = {c.get("id") for c in (after.get("champions") or [])}
        results.append(
            ok(
                "unpublished hidden from public list",
                live_id not in after_ids,
                f"total={after.get('total')}",
            )
        )

    except Exception as exc:
        results.append(ok("workflow exception", False, str(exc)))
    finally:
        _cleanup(ids)

    return all(results)


def main():
    print(f"BASE={BASE}")
    a = test_fe_surfaces()
    b = test_workflow()
    print(f"\nRESULT: {int(a) + int(b)}/2 suites passed")
    sys.exit(0 if a and b else 1)


if __name__ == "__main__":
    main()
