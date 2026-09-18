"""
M16-S02 Online Learning Hierarchy QA (Course → Level → Lesson → Video meta).

  .venv\\Scripts\\python.exe scripts/qa_m16_s02_learning_hierarchy.py
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


def _cleanup(course_ids, level_ids=None, lesson_ids=None):
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
            for cid in course_ids or []:
                await db.learning_lessons.delete_many({"course_id": cid})
                await db.learning_levels.delete_many({"course_id": cid})
                await db.learning_courses.delete_many({"id": cid})
            if level_ids:
                await db.learning_levels.delete_many({"id": {"$in": list(level_ids)}})
            if lesson_ids:
                await db.learning_lessons.delete_many({"id": {"$in": list(lesson_ids)}})
            await db.learning_courses.delete_many(
                {"slug": {"$regex": "^qa-m16-s02-"}}
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
            "/api/learning-courses/{course_id}/levels",
            "/api/learning-courses/{course_id}/levels/reorder",
            "/api/learning-courses/{course_id}/levels/{level_id}/lessons",
            "/api/learning-courses/{course_id}/levels/{level_id}/lessons/reorder",
            "/api/learning-courses/public",
            "/api/courses/public",
        ]
        missing = [p for p in needed if p not in raw]
        results.append(
            ok("OpenAPI hierarchy + dojo intact", not missing, str(missing) or BASE)
        )
    except Exception as exc:
        results.append(ok("OpenAPI hierarchy endpoints", False, str(exc)))

    fe_checks = [
        (FE_ROOT / "lib" / "learningCourseAPI.ts").exists(),
        "listLevels"
        in (FE_ROOT / "lib" / "learningCourseAPI.ts").read_text(encoding="utf-8"),
        (FE_ROOT / "components" / "online-learning" / "LearningCurriculumAdminPage.tsx").exists(),
        (
            FE_ROOT
            / "app"
            / "[adminType]"
            / "dashboard"
            / "online-learning"
            / "courses"
            / "[courseId]"
            / "curriculum"
            / "page.tsx"
        ).exists(),
        "CurriculumOutline"
        in (
            FE_ROOT / "app" / "(website)" / "online-learning" / "[slug]" / "page.tsx"
        ).read_text(encoding="utf-8"),
        "Curriculum"
        in (
            FE_ROOT / "components" / "online-learning" / "LearningCoursesAdminPage.tsx"
        ).read_text(encoding="utf-8"),
    ]
    results.append(ok("FE hierarchy surfaces", all(fe_checks), str(FE_ROOT)))
    return all(results)


def test_workflow():
    results = []
    token = _mint_superadmin_token()
    if not token:
        results.append(ok("superadmin token", False))
        return all(results)
    results.append(ok("superadmin token", True))

    suffix = uuid.uuid4().hex[:8]
    slug = f"qa-m16-s02-{suffix}"
    course_ids = []
    secret_video = f"https://cdn.example.com/private/qa-{suffix}.mp4"

    try:
        st, created = http_json(
            "POST",
            "/api/learning-courses",
            body={
                "title": f"QA Hierarchy Course {suffix}",
                "slug": slug,
                "short_description": "QA hierarchy",
                "status": "published",
                "sort_order": 10,
            },
            token=token,
            expect_status=201,
        )
        course = created.get("course") or {}
        cid = course.get("id")
        if cid:
            course_ids.append(cid)
        results.append(ok("create published course", bool(cid), cid))

        # Level A + B
        st, lv_a = http_json(
            "POST",
            f"/api/learning-courses/{cid}/levels",
            body={"title": "Level Alpha", "status": "published"},
            token=token,
            expect_status=201,
        )
        lid_a = (lv_a.get("level") or {}).get("id")
        st, lv_b = http_json(
            "POST",
            f"/api/learning-courses/{cid}/levels",
            body={"title": "Level Beta", "status": "published"},
            token=token,
            expect_status=201,
        )
        lid_b = (lv_b.get("level") or {}).get("id")
        results.append(ok("create two levels", bool(lid_a and lid_b), f"{lid_a},{lid_b}"))

        # Lessons under A
        st, les1 = http_json(
            "POST",
            f"/api/learning-courses/{cid}/levels/{lid_a}/lessons",
            body={
                "title": "Lesson One",
                "status": "published",
                "video_url": secret_video,
                "duration_seconds": 120,
                "is_preview": True,
            },
            token=token,
            expect_status=201,
        )
        les1_id = (les1.get("lesson") or {}).get("id")
        st, les2 = http_json(
            "POST",
            f"/api/learning-courses/{cid}/levels/{lid_a}/lessons",
            body={
                "title": "Lesson Two",
                "status": "published",
                "video_url": secret_video + "-2",
                "duration_seconds": 300,
            },
            token=token,
            expect_status=201,
        )
        les2_id = (les2.get("lesson") or {}).get("id")
        results.append(
            ok(
                "create lessons with video meta",
                bool(les1_id and les2_id)
                and (les1.get("lesson") or {}).get("video_url") == secret_video,
            )
        )

        # Draft lesson — excluded from public
        st, les_draft = http_json(
            "POST",
            f"/api/learning-courses/{cid}/levels/{lid_a}/lessons",
            body={"title": "Draft Lesson", "status": "draft"},
            token=token,
            expect_status=201,
        )
        les_draft_id = (les_draft.get("lesson") or {}).get("id")

        # Reorder levels: Beta before Alpha
        st, reordered = http_json(
            "POST",
            f"/api/learning-courses/{cid}/levels/reorder",
            body={"ordered_ids": [lid_b, lid_a]},
            token=token,
            expect_status=200,
        )
        lvl_order = [x.get("id") for x in (reordered.get("levels") or []) if x.get("status") != "archived"]
        results.append(
            ok(
                "reorder levels",
                len(lvl_order) >= 2 and lvl_order[0] == lid_b and lvl_order[1] == lid_a,
                str(lvl_order[:2]),
            )
        )

        # Reorder lessons: Two before One
        st, les_re = http_json(
            "POST",
            f"/api/learning-courses/{cid}/levels/{lid_a}/lessons/reorder",
            body={"ordered_ids": [les2_id, les1_id]},
            token=token,
            expect_status=200,
        )
        les_ids = [x.get("id") for x in (les_re.get("lessons") or [])]
        # Filter to our two published (draft may still be in list)
        pub_order = [x for x in les_ids if x in (les2_id, les1_id)]
        results.append(
            ok(
                "reorder lessons",
                pub_order[:2] == [les2_id, les1_id],
                str(pub_order[:2]),
            )
        )

        # Admin list includes video_url
        st, admin_levels = http_json(
            "GET",
            f"/api/learning-courses/{cid}/levels",
            token=token,
            expect_status=200,
        )
        flat_lessons = []
        for lv in admin_levels.get("levels") or []:
            flat_lessons.extend(lv.get("lessons") or [])
        admin_video = next(
            (x for x in flat_lessons if x.get("id") == les1_id),
            {},
        )
        results.append(
            ok(
                "admin sees video_url",
                admin_video.get("video_url") == secret_video,
            )
        )

        # Public detail: curriculum outline, NO video URLs, draft excluded
        st, pub = http_json(
            "GET",
            f"/api/learning-courses/public/{slug}",
            expect_status=200,
        )
        course_pub = pub.get("course") or {}
        curriculum = course_pub.get("curriculum") or []
        results.append(ok("public has curriculum", len(curriculum) >= 2, str(len(curriculum))))

        # First level should be Beta after reorder
        results.append(
            ok(
                "public level order",
                curriculum and curriculum[0].get("id") == lid_b,
                curriculum[0].get("title") if curriculum else None,
            )
        )

        alpha = next((x for x in curriculum if x.get("id") == lid_a), None)
        alpha_lessons = (alpha or {}).get("lessons") or []
        alpha_ids = [x.get("id") for x in alpha_lessons]
        results.append(
            ok(
                "public lesson order + no draft",
                alpha_ids[:2] == [les2_id, les1_id] and les_draft_id not in alpha_ids,
                str(alpha_ids),
            )
        )

        pub_blob = json.dumps(course_pub)
        results.append(
            ok(
                "public hides video_url",
                secret_video not in pub_blob and "video_url" not in pub_blob,
            )
        )
        has_video_flags = [x.get("has_video") for x in alpha_lessons if x.get("id") == les1_id]
        results.append(
            ok(
                "public has_video flag",
                has_video_flags == [True],
                str(has_video_flags),
            )
        )

        # Counts denorm
        results.append(
            ok(
                "course counts denorm",
                (course_pub.get("levels_count") or 0) >= 2
                and (course_pub.get("lessons_count") or 0) >= 2,
                f"L={course_pub.get('levels_count')} les={course_pub.get('lessons_count')}",
            )
        )

        # Soft-archive level archives lessons
        st, _ = http_json(
            "DELETE",
            f"/api/learning-courses/{cid}/levels/{lid_b}",
            token=token,
            expect_status=200,
        )
        st, pub2 = http_json(
            "GET",
            f"/api/learning-courses/public/{slug}",
            expect_status=200,
        )
        curr2 = (pub2.get("course") or {}).get("curriculum") or []
        results.append(
            ok(
                "archived level hidden publicly",
                all(x.get("id") != lid_b for x in curr2),
                str([x.get("id") for x in curr2]),
            )
        )

        # LMS catalogue + OpenAPI dojo path already prove isolation; keep public LMS healthy
        st, pub_list = http_json(
            "GET",
            f"/api/learning-courses/public?search={suffix}",
            expect_status=200,
        )
        results.append(
            ok(
                "public LMS catalogue intact",
                st == 200 and isinstance(pub_list.get("courses"), list),
            )
        )

    except Exception as exc:
        results.append(ok("hierarchy workflow", False, str(exc)))
    finally:
        _cleanup(course_ids)

    return all(results)


def main():
    print(f"M16-S02 QA against {BASE}")
    a = test_openapi_fe()
    b = test_workflow()
    print(f"\nResult: {sum([a, b])}/2 suites passed")
    sys.exit(0 if a and b else 1)


if __name__ == "__main__":
    main()
