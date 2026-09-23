"""
M16-S04 Protected Lesson Access QA.

  .venv\\Scripts\\python.exe scripts/qa_m16_s04_learning_access.py
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

# Small public sample MP4 (used only as upstream for proxy after entitlement)
SAMPLE_MP4 = (
    "https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4"
)


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
        with urllib.request.urlopen(req, timeout=40) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            payload = json.loads(raw) if raw else {}
            status = resp.status
            resp_headers = dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="ignore")
        try:
            payload = json.loads(raw) if raw else {}
        except Exception:
            payload = {"detail": raw}
        status = exc.code
        resp_headers = dict(exc.headers.items()) if exc.headers else {}
    if expect_status is not None and status != expect_status:
        raise AssertionError(f"{method} {path} expected {expect_status} got {status}: {payload}")
    return status, payload, resp_headers


def http_bytes(path, token=None, extra_headers=None):
    url = f"{BASE}{path}"
    headers = {"Accept": "*/*"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read(), dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers.items()) if exc.headers else {}


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
                    "email": f"qa.m16s04.{suffix}@example.com",
                    "full_name": f"QA Access {suffix}",
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
                await db.learning_subscription_payments.delete_many({"course_id": cid})
                await db.learning_subscriptions.delete_many({"course_id": cid})
                await db.learning_subscription_plans.delete_many({"course_id": cid})
                await db.learning_lessons.delete_many({"course_id": cid})
                await db.learning_levels.delete_many({"course_id": cid})
                await db.learning_courses.delete_many({"id": cid})
            await db.learning_courses.delete_many({"slug": {"$regex": "^qa-m16-s04-"}})
            for uid in user_ids or []:
                await db.learning_subscription_payments.delete_many({"user_id": uid})
                await db.learning_subscriptions.delete_many({"user_id": uid})
                await db.users.delete_many(
                    {"id": uid, "email": {"$regex": "^qa\\.m16s04\\."}}
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
            "/api/learning-access/lessons/{lesson_id}",
            "/api/learning-access/stream",
            "/api/learning-access/courses/{course_id}/player",
            "/api/learning-subscriptions/me/checkout",
            "/api/learning-courses/public",
        ]
        missing = [p for p in needed if p not in raw]
        results.append(
            ok("OpenAPI learning-access intact", not missing, str(missing) or BASE)
        )
    except Exception as exc:
        results.append(ok("OpenAPI learning-access", False, str(exc)))

    api = FE_ROOT / "lib" / "learningAccessAPI.ts"
    player = (
        FE_ROOT / "components" / "online-learning" / "LearningPlayerPage.tsx"
    )
    page = (
        FE_ROOT
        / "app"
        / "(website)"
        / "online-learning"
        / "[slug]"
        / "learn"
        / "page.tsx"
    )
    detail = (
        FE_ROOT / "app" / "(website)" / "online-learning" / "[slug]" / "page.tsx"
    )
    fe_checks = [
        api.exists(),
        "getLessonPlayback" in api.read_text(encoding="utf-8"),
        player.exists(),
        "nodownload" in player.read_text(encoding="utf-8"),
        "onContextMenu" in player.read_text(encoding="utf-8"),
        page.exists(),
        "/learn" in detail.read_text(encoding="utf-8"),
        "Open lesson player" in detail.read_text(encoding="utf-8"),
    ]
    results.append(ok("FE player surfaces", all(fe_checks), str(FE_ROOT)))
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
    slug = f"qa-m16-s04-{suffix}"
    course_ids = []
    user_ids = [student_id]
    secret_url = SAMPLE_MP4 + f"?qa={suffix}"

    try:
        st, created, _ = http_json(
            "POST",
            "/api/learning-courses",
            body={
                "title": f"QA Access Course {suffix}",
                "slug": slug,
                "status": "published",
            },
            token=token,
            expect_status=201,
        )
        cid = (created.get("course") or {}).get("id")
        course_ids.append(cid)

        st, lv, _ = http_json(
            "POST",
            f"/api/learning-courses/{cid}/levels",
            body={"title": "L1", "status": "published"},
            token=token,
            expect_status=201,
        )
        lid = (lv.get("level") or {}).get("id")

        st, locked, _ = http_json(
            "POST",
            f"/api/learning-courses/{cid}/levels/{lid}/lessons",
            body={
                "title": "Locked Lesson",
                "status": "published",
                "video_url": secret_url,
                "is_preview": False,
                "duration_seconds": 30,
            },
            token=token,
            expect_status=201,
        )
        locked_id = (locked.get("lesson") or {}).get("id")

        st, preview, _ = http_json(
            "POST",
            f"/api/learning-courses/{cid}/levels/{lid}/lessons",
            body={
                "title": "Preview Lesson",
                "status": "published",
                "video_url": secret_url,
                "is_preview": True,
                "duration_seconds": 15,
            },
            token=token,
            expect_status=201,
        )
        preview_id = (preview.get("lesson") or {}).get("id")

        # Public course detail must not leak video_url
        st, pub, _ = http_json(
            "GET", f"/api/learning-courses/public/{slug}", expect_status=200
        )
        pub_blob = json.dumps(pub)
        results.append(
            ok(
                "public detail hides video_url",
                secret_url not in pub_blob and "video_url" not in pub_blob,
            )
        )

        # Unauthorized locked lesson
        st, denied, _ = http_json(
            "GET",
            f"/api/learning-access/lessons/{locked_id}",
            expect_status=None,
        )
        results.append(
            ok(
                "anonymous locked → 401/403",
                st in (401, 403),
                f"status={st}",
            )
        )
        results.append(
            ok(
                "denied payload hides video_url",
                secret_url not in json.dumps(denied)
                and "video_url" not in json.dumps(denied),
            )
        )

        # Student without sub
        st, denied2, _ = http_json(
            "GET",
            f"/api/learning-access/lessons/{locked_id}",
            token=student_token,
            expect_status=None,
        )
        results.append(
            ok(
                "student without sub → 403",
                st == 403,
                f"status={st} detail={denied2.get('detail')}",
            )
        )

        # Preview allowed without sub
        st, prev_play, _ = http_json(
            "GET",
            f"/api/learning-access/lessons/{preview_id}",
            expect_status=200,
        )
        results.append(
            ok(
                "preview playable without sub",
                (prev_play.get("access") or {}).get("reason") == "preview",
            )
        )
        results.append(
            ok(
                "preview payload hides video_url",
                "video_url" not in json.dumps(prev_play)
                and secret_url not in json.dumps(prev_play),
            )
        )
        results.append(
            ok(
                "download_allowed false",
                prev_play.get("download_allowed") is False
                and (prev_play.get("playback") or {}).get("download_allowed")
                is False,
            )
        )

        # Seed plan + grant entitlement
        st, seeded, _ = http_json(
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
            body={"user_id": student_id, "plan_id": three["id"], "note": "QA"},
            token=token,
            expect_status=200,
        )

        st, entitled_play, _ = http_json(
            "GET",
            f"/api/learning-access/lessons/{locked_id}",
            token=student_token,
            expect_status=200,
        )
        results.append(
            ok(
                "entitled student can load locked lesson",
                (entitled_play.get("access") or {}).get("allowed") is True,
            )
        )
        results.append(
            ok(
                "entitled payload hides video_url",
                secret_url not in json.dumps(entitled_play)
                and "video_url" not in json.dumps(entitled_play),
            )
        )

        playback = entitled_play.get("playback") or {}
        stream_path = playback.get("stream_path") or ""
        results.append(
            ok(
                "stream mode + token path",
                playback.get("mode") == "stream" and "token=" in stream_path,
                playback.get("mode"),
            )
        )

        # Stream with token
        if stream_path:
            st_s, body, hdrs = http_bytes(f"/api/{stream_path}")
            cd = (hdrs.get("Content-Disposition") or hdrs.get("content-disposition") or "").lower()
            results.append(
                ok(
                    "stream returns media",
                    st_s in (200, 206) and len(body) > 1000,
                    f"status={st_s} bytes={len(body)}",
                )
            )
            results.append(
                ok(
                    "stream is inline (no attachment)",
                    "attachment" not in cd,
                    cd or "(empty)",
                )
            )
        else:
            results.append(ok("stream returns media", False, "no stream_path"))
            results.append(ok("stream is inline (no attachment)", False))

        # Bogus token
        st_b, _, _ = http_bytes("/api/learning-access/stream?token=not-a-valid-jwt-token-xxx")
        results.append(ok("bogus stream token rejected", st_b in (401, 422), f"status={st_b}"))

        # Player curriculum can_play flags
        st, curr, _ = http_json(
            "GET",
            f"/api/learning-access/courses/{cid}/player",
            token=student_token,
            expect_status=200,
        )
        lessons = []
        for lv in curr.get("curriculum") or []:
            lessons.extend(lv.get("lessons") or [])
        locked_row = next((x for x in lessons if x.get("id") == locked_id), {})
        results.append(
            ok(
                "player marks locked lesson can_play",
                locked_row.get("can_play") is True and curr.get("entitled") is True,
            )
        )
        results.append(
            ok(
                "player curriculum hides video_url",
                "video_url" not in json.dumps(curr),
            )
        )

        # Expiry: force expire subscription then deny
        client, dbn, asyncio = _mongo()
        if client is not None:

            async def expire():
                from datetime import datetime, timedelta

                db = client[dbn]
                past = datetime.utcnow() - timedelta(days=2)
                await db.learning_subscriptions.update_many(
                    {"user_id": student_id, "course_id": cid},
                    {
                        "$set": {
                            "status": "active",
                            "is_lifetime": False,
                            "ends_at": past,
                            "grace_ends_at": past,
                        }
                    },
                )

            try:
                asyncio.run(expire())
            except RuntimeError:
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(expire())
                finally:
                    loop.close()

            st, after_exp, _ = http_json(
                "GET",
                f"/api/learning-access/lessons/{locked_id}",
                token=student_token,
                expect_status=None,
            )
            results.append(
                ok(
                    "expired sub blocks locked lesson",
                    st == 403,
                    f"status={st}",
                )
            )
        else:
            results.append(ok("expired sub blocks locked lesson", False, "no mongo"))

    except Exception as exc:
        results.append(ok("access workflow", False, str(exc)))
    finally:
        _cleanup(course_ids, user_ids)

    return all(results)


def main():
    print(f"M16-S04 QA against {BASE}")
    a = test_openapi_fe()
    b = test_workflow()
    print(f"\nResult: {sum([a, b])}/2 suites passed")
    sys.exit(0 if a and b else 1)


if __name__ == "__main__":
    main()
