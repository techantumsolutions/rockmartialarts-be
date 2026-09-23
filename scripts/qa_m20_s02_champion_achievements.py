"""
M20-S02 Champion Achievements & Media QA.

  .venv\\Scripts\\python.exe scripts/qa_m20_s02_champion_achievements.py
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


def _cleanup(champion_ids, achievement_ids):
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
                if achievement_ids:
                    await client[name].champion_achievements.delete_many(
                        {"id": {"$in": achievement_ids}}
                    )
                if champion_ids:
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
    panel = (
        FE_ROOT / "components" / "champions" / "ChampionAchievementsPanel.tsx"
    ).read_text(encoding="utf-8")
    admin = (
        FE_ROOT / "components" / "champions" / "ChampionsAdminPage.tsx"
    ).read_text(encoding="utf-8")
    api = (FE_ROOT / "lib" / "championAPI.ts").read_text(encoding="utf-8")
    be_models = (
        Path(ROOT) / "models" / "champion_achievement_models.py"
    ).read_text(encoding="utf-8")
    be_routes = (Path(ROOT) / "routes" / "champion_routes.py").read_text(
        encoding="utf-8"
    )
    checks = [
        "ChampionAchievementsPanel" in admin,
        "Recognition level" in panel or "recognition_level" in panel,
        "competition_name" in panel,
        "award_title" in panel,
        "images" in panel and "videos" in panel and "documents" in panel,
        "listAchievements" in api,
        "createAchievement" in api,
        "RecognitionLevel" in be_models,
        "competition_name" in be_models,
        "award_title" in be_models,
        "images" in be_models and "videos" in be_models,
        "/achievements" in be_routes,
    ]
    results.append(
        ok(
            "FE + BE achievement surfaces",
            all(checks),
            f"{sum(checks)}/{len(checks)}",
        )
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
    champ_ids = []
    ach_ids = []

    try:
        st, created = http_json(
            "POST",
            "/api/champions",
            body={
                "name": f"QA Ach Champ {suffix}",
                "slug": f"qa-ach-champ-{suffix}",
                "status": "draft",
            },
            token=token,
            expect_status=201,
        )
        cid = (created.get("champion") or {}).get("id")
        if cid:
            champ_ids.append(cid)
        results.append(ok("create parent champion", bool(cid)))

        st, ach = http_json(
            "POST",
            f"/api/champions/{cid}/achievements",
            body={
                "title": f"QA National Gold {suffix}",
                "description": "Won gold at nationals",
                "recognition_level": "national",
                "competition_name": "All India Open",
                "award_title": "Gold Medal",
                "place": "1st",
                "event_year": 2024,
                "event_date": "2024-08-12",
                "images": ["https://example.com/a.jpg", "https://example.com/b.jpg"],
                "videos": ["https://example.com/v.mp4"],
                "documents": ["https://example.com/cert.pdf"],
                "display_order": 1,
                "status": "active",
            },
            token=token,
            expect_status=201,
        )
        a = ach.get("achievement") or {}
        aid = a.get("id")
        if aid:
            ach_ids.append(aid)
        results.append(
            ok(
                "create achievement with recognition + competition + media",
                bool(aid)
                and a.get("recognition_level") == "national"
                and a.get("competition_name") == "All India Open"
                and a.get("award_title") == "Gold Medal"
                and len(a.get("images") or []) == 2
                and len(a.get("videos") or []) == 1
                and len(a.get("documents") or []) == 1
                and a.get("media_count") == 4,
                str(a.get("media_count")),
            )
        )

        st, listed = http_json(
            "GET",
            f"/api/champions/{cid}/achievements",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "list achievements for champion",
                any(r.get("id") == aid for r in (listed.get("achievements") or [])),
                f"total={listed.get('total')}",
            )
        )

        st, got = http_json(
            "GET",
            f"/api/champions/{cid}/achievements/{aid}",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "get achievement by id",
                (got.get("achievement") or {}).get("id") == aid,
            )
        )

        st, patched = http_json(
            "PATCH",
            f"/api/champions/{cid}/achievements/{aid}",
            body={
                "recognition_level": "international",
                "place": "2nd",
                "images": ["https://example.com/only.jpg"],
            },
            token=token,
            expect_status=200,
        )
        p = patched.get("achievement") or {}
        results.append(
            ok(
                "update recognition + media",
                p.get("recognition_level") == "international"
                and p.get("place") == "2nd"
                and p.get("images") == ["https://example.com/only.jpg"],
            )
        )

        # invalid recognition rejected
        st, bad = http_json(
            "POST",
            f"/api/champions/{cid}/achievements",
            body={
                "title": "Bad level",
                "recognition_level": "galaxy",
            },
            token=token,
        )
        results.append(ok("rejects invalid recognition level", st == 422, str(st)))

        # title required
        st, bad2 = http_json(
            "POST",
            f"/api/champions/{cid}/achievements",
            body={"recognition_level": "state"},
            token=token,
        )
        results.append(ok("rejects missing title", st == 422, str(st)))

        st, archived = http_json(
            "DELETE",
            f"/api/champions/{cid}/achievements/{aid}",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "archive achievement",
                (archived.get("achievement") or {}).get("status") == "archived",
            )
        )

        st, after = http_json(
            "GET",
            f"/api/champions/{cid}/achievements",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "archived hidden from default list",
                not any(
                    r.get("id") == aid for r in (after.get("achievements") or [])
                ),
                f"total={after.get('total')}",
            )
        )

    except Exception as exc:
        results.append(ok("workflow exception", False, str(exc)))
    finally:
        _cleanup(champ_ids, ach_ids)

    return all(results)


def main():
    print(f"BASE={BASE}")
    a = test_fe_surfaces()
    b = test_workflow()
    print(f"\nRESULT: {int(a) + int(b)}/2 suites passed")
    sys.exit(0 if a and b else 1)


if __name__ == "__main__":
    main()
