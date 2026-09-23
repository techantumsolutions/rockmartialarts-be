"""
M20-S01 Champion Profile CMS QA.

  .venv\\Scripts\\python.exe scripts/qa_m20_s01_champion_cms.py
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


def _pick_student_id():
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
            return None
        dbn = os.getenv("DB_NAME") or "marshalats"

        async def run():
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
            for name in (dbn, "marshalats", "rockmartialarts", "rock_martial_arts"):
                stu = await client[name].users.find_one(
                    {"role": "student"}, {"id": 1}
                )
                if stu and stu.get("id"):
                    return stu["id"]
            return None

        try:
            return asyncio.run(run())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(run())
            finally:
                loop.close()
    except Exception:
        return None


def _cleanup(ids):
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
        if not uri or not ids:
            return
        dbn = os.getenv("DB_NAME") or "marshalats"

        async def run():
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
            for name in (dbn, "marshalats", "rockmartialarts", "rock_martial_arts"):
                await client[name].champions.delete_many({"id": {"$in": ids}})

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
    admin = (
        FE_ROOT / "components" / "champions" / "ChampionsAdminPage.tsx"
    ).read_text(encoding="utf-8")
    api = (FE_ROOT / "lib" / "championAPI.ts").read_text(encoding="utf-8")
    page = (
        FE_ROOT / "app" / "[adminType]" / "dashboard" / "champions" / "page.tsx"
    ).read_text(encoding="utf-8")
    nav = (FE_ROOT / "lib" / "dashboard-config.ts").read_text(encoding="utf-8")
    be_models = (Path(ROOT) / "models" / "champion_models.py").read_text(
        encoding="utf-8"
    )
    be_routes = (Path(ROOT) / "routes" / "champion_routes.py").read_text(
        encoding="utf-8"
    )
    checks = [
        "Success story" in admin,
        "Student linkage" in admin,
        "uploadPhoto" in api or "Upload" in admin,
        "publish" in api,
        "unpublish" in api,
        "ChampionsAdminPage" in page,
        "/champions" in nav,
        "ChampionStatus" in be_models,
        "success_story" in be_models,
        "student_id" in be_models,
        "photo_url" in be_models,
        "publish_champion" in be_routes or "/publish" in be_routes,
        "student-options" in be_routes,
    ]
    results.append(
        ok("FE + BE champion CMS surfaces", all(checks), f"{sum(checks)}/{len(checks)}")
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
    student_id = _pick_student_id()

    try:
        st, opts = http_json(
            "GET",
            "/api/champions/student-options?limit=5",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "student options for linkage",
                isinstance(opts.get("students"), list),
                f"n={len(opts.get('students') or [])}",
            )
        )

        body = {
            "name": f"QA Champion {suffix}",
            "slug": f"qa-champion-{suffix}",
            "headline": "State Gold 2025",
            "short_bio": "Short bio for QA",
            "success_story": "Full success story content for QA champion.",
            "photo_url": "https://example.com/champ.jpg",
            "status": "draft",
            "display_order": 10,
        }
        if student_id:
            body["student_id"] = student_id

        st, created = http_json(
            "POST",
            "/api/champions",
            body=body,
            token=token,
            expect_status=201,
        )
        champ = created.get("champion") or {}
        cid = champ.get("id")
        if cid:
            ids.append(cid)
        results.append(
            ok(
                "create champion with photo + story",
                bool(cid)
                and champ.get("photo_url")
                and champ.get("success_story")
                and champ.get("status") == "draft",
                str(champ.get("slug")),
            )
        )
        if student_id:
            results.append(
                ok(
                    "student linkage on create",
                    champ.get("student_id") == student_id
                    and bool(champ.get("student_name")),
                    str(champ.get("student_name")),
                )
            )
        else:
            results.append(ok("student linkage on create", True, "no student in DB"))

        st, listed = http_json(
            "GET",
            f"/api/champions?search={suffix}",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "list/filter champions",
                any(r.get("id") == cid for r in (listed.get("champions") or [])),
                f"total={listed.get('total')}",
            )
        )

        st, got = http_json(
            "GET",
            f"/api/champions/{cid}",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "get champion by id",
                (got.get("champion") or {}).get("id") == cid,
            )
        )

        st, pub = http_json(
            "POST",
            f"/api/champions/{cid}/publish",
            body={"published": True},
            token=token,
            expect_status=200,
        )
        pp = pub.get("champion") or {}
        results.append(
            ok(
                "publish champion",
                pp.get("status") == "published" and pp.get("is_published") is True,
                str(pp.get("status")),
            )
        )

        st, unpub = http_json(
            "POST",
            f"/api/champions/{cid}/unpublish",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "unpublish champion",
                (unpub.get("champion") or {}).get("status") == "unpublished",
                str((unpub.get("champion") or {}).get("status")),
            )
        )

        st, patched = http_json(
            "PATCH",
            f"/api/champions/{cid}",
            body={
                "headline": "Updated headline",
                "success_story": "Updated story",
                "clear_student": True,
            },
            token=token,
            expect_status=200,
        )
        pc = patched.get("champion") or {}
        results.append(
            ok(
                "update story + clear student link",
                pc.get("headline") == "Updated headline"
                and pc.get("success_story") == "Updated story"
                and not pc.get("student_id"),
            )
        )

        st, archived = http_json(
            "DELETE",
            f"/api/champions/{cid}",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "archive champion",
                (archived.get("champion") or {}).get("status") == "archived",
            )
        )

        # name required
        st, bad = http_json(
            "POST",
            "/api/champions",
            body={"status": "draft"},
            token=token,
        )
        results.append(ok("rejects missing name", st == 422, str(st)))

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
