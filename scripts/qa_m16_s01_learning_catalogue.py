"""
M16-S01 Online Learning Catalogue QA.

  .venv\\Scripts\\python.exe scripts/qa_m16_s01_learning_catalogue.py
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
        raise AssertionError(f"{method} {path} expected {expect_status} got {status}: {payload}")
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


def _cleanup(course_ids):
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
        dbn = os.getenv("DB_NAME") or "marshalats"
        if not uri:
            return False

        async def run():
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
            db = client[dbn]
            for cid in course_ids:
                await db.learning_courses.delete_many({"id": cid})
            await db.learning_courses.delete_many(
                {"slug": {"$regex": "^qa-m16-s01-"}}
            )
            return True

        try:
            return asyncio.run(run())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(run())
            finally:
                loop.close()
    except Exception:
        return False


def test_openapi_fe():
    results = []
    try:
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        needed = [
            "/api/learning-courses",
            "/api/learning-courses/public",
            "/api/learning-courses/public/{slug_or_id}",
            "/api/courses/public",
        ]
        missing = [p for p in needed if p not in raw]
        results.append(
            ok("OpenAPI learning + dojo courses intact", not missing, str(missing) or BASE)
        )
    except Exception as exc:
        results.append(ok("OpenAPI learning endpoints", False, str(exc)))

    fe_checks = [
        (FE_ROOT / "lib" / "learningCourseAPI.ts").exists(),
        (FE_ROOT / "components" / "online-learning" / "LearningCoursesAdminPage.tsx").exists(),
        (FE_ROOT / "app" / "(website)" / "online-learning" / "page.tsx").exists(),
        (FE_ROOT / "app" / "(website)" / "online-learning" / "[slug]" / "page.tsx").exists(),
        (FE_ROOT / "app" / "[adminType]" / "dashboard" / "online-learning" / "courses" / "page.tsx").exists(),
        "Online Learning"
        in (FE_ROOT / "components" / "FixedTopNav.tsx").read_text(encoding="utf-8"),
        "online-learning/courses"
        in (FE_ROOT / "lib" / "dashboard-config.ts").read_text(encoding="utf-8"),
    ]
    results.append(ok("FE catalogue surfaces", all(fe_checks), str(FE_ROOT)))
    return all(results)


def test_workflow():
    results = []
    token = _mint_superadmin_token()
    if not token:
        results.append(ok("superadmin token", False))
        return all(results)
    results.append(ok("superadmin token", True))

    suffix = uuid.uuid4().hex[:8]
    slug = f"qa-m16-s01-{suffix}"
    course_ids = []

    try:
        # Draft create — not in public catalogue
        st, created = http_json(
            "POST",
            "/api/learning-courses",
            body={
                "title": f"QA Online Course {suffix}",
                "slug": slug,
                "short_description": "QA short",
                "description": "Full QA description",
                "difficulty": "Beginner",
                "status": "draft",
                "sort_order": 10,
            },
            token=token,
            expect_status=201,
        )
        course = created.get("course") or {}
        cid = course.get("id")
        if cid:
            course_ids.append(cid)
        results.append(
            ok(
                "admin create draft",
                course.get("status") == "draft" and course.get("slug") == slug,
                cid,
            )
        )

        st, pub_list = http_json(
            "GET", f"/api/learning-courses/public?search={suffix}", expect_status=200
        )
        found_draft = any(x.get("id") == cid for x in (pub_list.get("courses") or []))
        results.append(ok("draft hidden from public", not found_draft))

        # Publish
        st, updated = http_json(
            "PATCH",
            f"/api/learning-courses/{cid}",
            body={"status": "published"},
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "publish course",
                (updated.get("course") or {}).get("status") == "published",
            )
        )

        st, pub_list2 = http_json(
            "GET", f"/api/learning-courses/public?search={suffix}", expect_status=200
        )
        found = any(x.get("id") == cid for x in (pub_list2.get("courses") or []))
        results.append(ok("published in catalogue", found, f"total={pub_list2.get('total')}"))

        st, detail = http_json(
            "GET", f"/api/learning-courses/public/{slug}", expect_status=200
        )
        results.append(
            ok(
                "public detail by slug",
                (detail.get("course") or {}).get("id") == cid,
            )
        )

        st, admin_list = http_json(
            "GET",
            f"/api/learning-courses?status=published&search={suffix}",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "admin list filter",
                any(x.get("id") == cid for x in (admin_list.get("courses") or [])),
            )
        )

        # Archive
        st, archived = http_json(
            "DELETE",
            f"/api/learning-courses/{cid}",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "archive course",
                (archived.get("course") or {}).get("status") == "archived",
            )
        )

        st, pub_after = http_json(
            "GET", f"/api/learning-courses/public/{slug}", expect_status=None
        )
        results.append(ok("archived not public", st == 404, f"status={st}"))

        # Auth required for admin create
        st_unauth, _ = http_json(
            "POST",
            "/api/learning-courses",
            body={"title": "Nope"},
            expect_status=None,
        )
        results.append(
            ok("admin create requires auth", st_unauth in (401, 403), f"status={st_unauth}")
        )

        # Dojo courses public still works
        st_dojo, _ = http_json("GET", "/api/courses/public/all", expect_status=None)
        results.append(
            ok("dojo courses public intact", st_dojo == 200, f"status={st_dojo}")
        )

    except AssertionError as exc:
        results.append(ok("workflow assertion", False, str(exc)))
    finally:
        cleaned = _cleanup(course_ids)
        results.append(ok("cleanup", cleaned is not False))

    return all(results)


def main():
    print(f"M16-S01 Online Learning Catalogue QA  base={BASE}")
    r1 = test_openapi_fe()
    r2 = test_workflow()
    print(f"\nDone. {int(r1) + int(r2)}/2 passed.")
    sys.exit(0 if (r1 and r2) else 1)


if __name__ == "__main__":
    main()
