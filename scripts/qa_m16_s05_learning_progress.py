"""
M16-S05 Learning Progress QA.

  .venv\\Scripts\\python.exe scripts/qa_m16_s05_learning_progress.py
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
        raise AssertionError(f"{method} {path} expected {expect_status} got {status}: {payload}")
    return status, payload


def _mongo():
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
    if not uri or "your_database" in uri:
        return None, None, None
    return AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000), dbn, asyncio


def _mint_token(role: str, user_id: str):
    from dotenv import load_dotenv
    import jwt

    load_dotenv(Path(ROOT) / ".env")
    secret = os.getenv("SECRET_KEY") or "student_management_secret_key_2025_secure"
    return jwt.encode({"sub": user_id, "role": role}, secret, algorithm="HS256")


def _mint_superadmin_token():
    try:
        client, dbn, asyncio = _mongo()
        if client is None:
            return None

        async def run():
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
        return _mint_token("superadmin", sa["id"])
    except Exception as exc:
        print(f"(mint superadmin skipped: {exc})")
        return None


def _ensure_qa_student():
    try:
        client, dbn, asyncio = _mongo()
        if client is None:
            return None, None
        suffix = uuid.uuid4().hex[:8]
        user_id = str(uuid.uuid4())

        async def run():
            from datetime import datetime

            db = client[dbn]
            await db.users.insert_one(
                {
                    "id": user_id,
                    "email": f"qa.m16s05.{suffix}@example.com",
                    "full_name": f"QA Progress {suffix}",
                    "role": "student",
                    "is_active": True,
                    "created_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                }
            )
            return user_id

        try:
            uid = asyncio.run(run())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                uid = loop.run_until_complete(run())
            finally:
                loop.close()
        return uid, _mint_token("student", uid)
    except Exception as exc:
        print(f"(ensure student skipped: {exc})")
        return None, None


def _cleanup(course_ids, user_ids=None):
    try:
        client, dbn, asyncio = _mongo()
        if client is None:
            return False

        async def run():
            db = client[dbn]
            for cid in course_ids or []:
                await db.learning_progress.delete_many({"course_id": cid})
                await db.learning_subscription_payments.delete_many({"course_id": cid})
                await db.learning_subscriptions.delete_many({"course_id": cid})
                await db.learning_subscription_plans.delete_many({"course_id": cid})
                await db.learning_lessons.delete_many({"course_id": cid})
                await db.learning_levels.delete_many({"course_id": cid})
                await db.learning_courses.delete_many({"id": cid})
            await db.learning_courses.delete_many({"slug": {"$regex": "^qa-m16-s05-"}})
            for uid in user_ids or []:
                await db.learning_progress.delete_many({"user_id": uid})
                await db.learning_subscriptions.delete_many({"user_id": uid})
                await db.users.delete_many(
                    {"id": uid, "email": {"$regex": "^qa\\.m16s05\\."}}
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
            "/api/learning-progress/me",
            "/api/learning-progress/me/courses/{course_id}",
            "/api/learning-progress/me/courses/{course_id}/resume",
            "/api/learning-progress/me/courses/{course_id}/heartbeat",
            "/api/learning-access/lessons/{lesson_id}",
            "/api/learning-courses/public",
        ]
        missing = [p for p in needed if p not in raw]
        results.append(
            ok("OpenAPI progress + access intact", not missing, str(missing) or BASE)
        )
    except Exception as exc:
        results.append(ok("OpenAPI learning-progress", False, str(exc)))

    api = FE_ROOT / "lib" / "learningProgressAPI.ts"
    player = FE_ROOT / "components" / "online-learning" / "LearningPlayerPage.tsx"
    dash = FE_ROOT / "lib" / "dashboard-config.ts"
    page = FE_ROOT / "app" / "student-dashboard" / "online-learning" / "page.tsx"
    fe_checks = [
        api.exists(),
        "heartbeat" in api.read_text(encoding="utf-8"),
        "getResume" in api.read_text(encoding="utf-8"),
        player.exists(),
        "learningProgressAPI" in player.read_text(encoding="utf-8"),
        "sendHeartbeat" in player.read_text(encoding="utf-8"),
        page.exists(),
        "Resume" in page.read_text(encoding="utf-8"),
        "student-dashboard/online-learning" in dash.read_text(encoding="utf-8"),
        # Existing dojo progress page untouched
        (FE_ROOT / "app" / "student-dashboard" / "progress" / "page.tsx").exists(),
    ]
    results.append(ok("FE progress surfaces", all(fe_checks), str(FE_ROOT)))
    return all(results)


def test_workflow():
    results = []
    token = _mint_superadmin_token()
    if not token:
        results.append(ok("superadmin token", False))
        return all(results)
    results.append(ok("superadmin token", True))

    student_id, student_token = _ensure_qa_student()
    if not student_id:
        results.append(ok("qa student", False))
        return all(results)
    results.append(ok("qa student", True, student_id))

    suffix = uuid.uuid4().hex[:8]
    slug = f"qa-m16-s05-{suffix}"
    course_ids = []
    user_ids = [student_id]

    try:
        st, created = http_json(
            "POST",
            "/api/learning-courses",
            body={
                "title": f"QA Progress Course {suffix}",
                "slug": slug,
                "status": "published",
            },
            token=token,
            expect_status=201,
        )
        cid = (created.get("course") or {}).get("id")
        course_ids.append(cid)

        st, lv = http_json(
            "POST",
            f"/api/learning-courses/{cid}/levels",
            body={"title": "L1", "status": "published"},
            token=token,
            expect_status=201,
        )
        lid = (lv.get("level") or {}).get("id")

        lessons = []
        for i, title in enumerate(["Lesson A", "Lesson B"], start=1):
            st, les = http_json(
                "POST",
                f"/api/learning-courses/{cid}/levels/{lid}/lessons",
                body={
                    "title": title,
                    "status": "published",
                    "video_url": f"https://example.com/qa-{suffix}-{i}.mp4",
                    "is_preview": i == 1,
                    "duration_seconds": 100,
                },
                token=token,
                expect_status=201,
            )
            lessons.append((les.get("lesson") or {}).get("id"))
        les_a, les_b = lessons

        # Empty progress
        st, empty = http_json(
            "GET",
            f"/api/learning-progress/me/courses/{cid}",
            token=student_token,
            expect_status=200,
        )
        results.append(
            ok(
                "empty course progress",
                empty.get("progress_percent") == 0
                and (empty.get("resume") or {}).get("available") is False,
            )
        )

        # Heartbeat on preview (no sub needed)
        st, hb1 = http_json(
            "POST",
            f"/api/learning-progress/me/courses/{cid}/heartbeat",
            body={
                "lesson_id": les_a,
                "position_seconds": 40,
                "duration_seconds": 100,
            },
            token=student_token,
            expect_status=200,
        )
        prog1 = hb1.get("progress") or {}
        results.append(
            ok(
                "heartbeat saves last-viewed",
                prog1.get("last_lesson_id") == les_a
                and float(prog1.get("last_position_seconds") or 0) == 40,
            )
        )

        st, resume = http_json(
            "GET",
            f"/api/learning-progress/me/courses/{cid}/resume",
            token=student_token,
            expect_status=200,
        )
        results.append(
            ok(
                "resume points to last lesson",
                (resume.get("resume") or {}).get("lesson_id") == les_a
                and float((resume.get("resume") or {}).get("position_seconds") or 0)
                == 40,
            )
        )

        # Auto-complete at 90%
        st, hb2 = http_json(
            "POST",
            f"/api/learning-progress/me/courses/{cid}/heartbeat",
            body={
                "lesson_id": les_a,
                "position_seconds": 91,
                "duration_seconds": 100,
            },
            token=student_token,
            expect_status=200,
        )
        prog2 = hb2.get("progress") or {}
        results.append(
            ok(
                "auto-complete at 90%",
                les_a in (prog2.get("completed_lesson_ids") or [])
                and float(prog2.get("progress_percent") or 0) == 50.0,
                f"pct={prog2.get('progress_percent')} done={prog2.get('completed_lesson_ids')}",
            )
        )

        # Locked lesson without sub → 403
        st, denied = http_json(
            "POST",
            f"/api/learning-progress/me/courses/{cid}/heartbeat",
            body={"lesson_id": les_b, "position_seconds": 10},
            token=student_token,
        )
        results.append(
            ok(
                "locked lesson heartbeat blocked",
                st == 403,
                f"status={st} detail={denied.get('detail')}",
            )
        )

        # Grant sub then complete second lesson
        st, seeded = http_json(
            "POST",
            f"/api/learning-subscriptions/plans/seed-defaults/{cid}",
            token=token,
            expect_status=201,
        )
        three = next(
            (
                p
                for p in (seeded.get("plans") or [])
                if p.get("plan_kind") == "3_month"
            ),
            None,
        )
        http_json(
            "POST",
            "/api/learning-subscriptions/grant",
            body={"user_id": student_id, "plan_id": three["id"]},
            token=token,
            expect_status=200,
        )

        st, done = http_json(
            "POST",
            f"/api/learning-progress/me/courses/{cid}/complete-lesson",
            body={"lesson_id": les_b},
            token=student_token,
            expect_status=200,
        )
        prog3 = done.get("progress") or {}
        results.append(
            ok(
                "complete lesson → 100%",
                float(prog3.get("progress_percent") or 0) == 100.0
                and set(prog3.get("completed_lesson_ids") or [])
                >= {les_a, les_b},
                f"pct={prog3.get('progress_percent')}",
            )
        )

        st, listing = http_json(
            "GET",
            "/api/learning-progress/me",
            token=student_token,
            expect_status=200,
        )
        found = next(
            (p for p in (listing.get("progress") or []) if p.get("course_id") == cid),
            None,
        )
        results.append(
            ok(
                "list my progress includes course",
                found is not None and float(found.get("progress_percent") or 0) == 100.0,
            )
        )

        # Isolation: access + catalogue still ok
        st, _ = http_json(
            "GET", f"/api/learning-courses/public/{slug}", expect_status=200
        )
        results.append(ok("LMS catalogue intact", st == 200))

    except Exception as exc:
        results.append(ok("progress workflow", False, str(exc)))
    finally:
        _cleanup(course_ids, user_ids)

    return all(results)


def main():
    print(f"M16-S05 QA against {BASE}")
    a = test_openapi_fe()
    b = test_workflow()
    print(f"\nResult: {sum([a, b])}/2 suites passed")
    sys.exit(0 if a and b else 1)


if __name__ == "__main__":
    main()
