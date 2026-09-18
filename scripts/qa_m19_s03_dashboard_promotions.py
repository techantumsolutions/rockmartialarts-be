"""
M19-S03 Student dashboard promotion popup QA.

  .venv\\Scripts\\python.exe scripts/qa_m19_s03_dashboard_promotions.py
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
        raise AssertionError(
            f"{method} {path} expected {expect_status} got {status}: {payload}"
        )
    return status, payload


def _env_mongo():
    from dotenv import load_dotenv

    load_dotenv(Path(ROOT) / ".env")
    uri = (
        os.getenv("MONGO_URI")
        or os.getenv("MONGO_URL")
        or os.getenv("MONGODB_URL")
        or os.getenv("DATABASE_URL")
    )
    dbn = os.getenv("DB_NAME") or "marshalats"
    secret = os.getenv("SECRET_KEY") or "student_management_secret_key_2025_secure"
    return uri, dbn, secret


def _mint_token(role_payload):
    try:
        import asyncio
        import jwt
        from motor.motor_asyncio import AsyncIOMotorClient

        uri, dbn, secret = _env_mongo()
        if not uri or "your_database" in uri:
            return None

        async def run():
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
            for name in (dbn, "marshalats", "rockmartialarts", "rock_martial_arts"):
                if role_payload.get("role") == "superadmin":
                    sa = await client[name].superadmins.find_one({})
                    if sa:
                        return sa["id"], name
                else:
                    # prefer fixture student if provided
                    return None, name
            return None, dbn

        try:
            sid, _ = asyncio.run(run())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                sid, _ = loop.run_until_complete(run())
            finally:
                loop.close()
        if role_payload.get("role") == "superadmin":
            if not sid:
                return None
            return jwt.encode(
                {"sub": sid, "role": "superadmin"},
                secret,
                algorithm="HS256",
            )
        return jwt.encode(role_payload, secret, algorithm="HS256")
    except Exception as exc:
        print(f"(mint token skipped: {exc})")
        return None


def _mint_superadmin_token():
    return _mint_token({"role": "superadmin"})


def _mint_student_token(student_id: str):
    return _mint_token({"sub": student_id, "role": "student"})


def _seed():
    try:
        import asyncio
        from motor.motor_asyncio import AsyncIOMotorClient

        uri, dbn, _ = _env_mongo()
        if not uri:
            return None
        tag = f"qa_m19_s03_{uuid.uuid4().hex[:8]}"

        async def run():
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
            db = None
            for name in (dbn, "marshalats", "rockmartialarts", "rock_martial_arts"):
                if await client[name].superadmins.find_one({}):
                    db = client[name]
                    break
            if db is None:
                db = client[dbn]

            sid_ok = str(uuid.uuid4())
            sid_out = str(uuid.uuid4())
            await db.users.insert_many(
                [
                    {
                        "id": sid_ok,
                        "role": "student",
                        "full_name": f"QA Promo In {tag}",
                        "email": f"{tag}_in@example.com",
                        "phone": "9111111111",
                        "is_active": True,
                    },
                    {
                        "id": sid_out,
                        "role": "student",
                        "full_name": f"QA Promo Out {tag}",
                        "email": f"{tag}_out@example.com",
                        "phone": "9222222222",
                        "is_active": True,
                    },
                ]
            )
            return {
                "tag": tag,
                "student_in": sid_ok,
                "student_out": sid_out,
                "db_name": db.name,
            }

        try:
            return asyncio.run(run())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(run())
            finally:
                loop.close()
    except Exception as exc:
        print(f"(seed skipped: {exc})")
        return None


def _cleanup(promo_ids, fixtures):
    try:
        import asyncio
        from motor.motor_asyncio import AsyncIOMotorClient

        uri, dbn, _ = _env_mongo()
        if not uri:
            return
        dbn = (fixtures or {}).get("db_name") or dbn

        async def run():
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
            db = client[dbn]
            if promo_ids:
                await db.student_promotions.delete_many({"id": {"$in": promo_ids}})
                await db.student_promotion_events.delete_many(
                    {"promotion_id": {"$in": promo_ids}}
                )
            if fixtures:
                await db.users.delete_many(
                    {
                        "id": {
                            "$in": [
                                fixtures["student_in"],
                                fixtures["student_out"],
                            ]
                        }
                    }
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
    popup = (
        FE_ROOT / "components" / "promotions" / "StudentPromotionPopup.tsx"
    ).read_text(encoding="utf-8")
    layout = (
        FE_ROOT / "app" / "student-dashboard" / "layout.tsx"
    ).read_text(encoding="utf-8")
    api = (FE_ROOT / "lib" / "studentPromotionAPI.ts").read_text(encoding="utf-8")
    be_svc = (
        Path(ROOT) / "utils" / "student_promotion_student_service.py"
    ).read_text(encoding="utf-8")
    be_routes = (Path(ROOT) / "routes" / "student_promotion_routes.py").read_text(
        encoding="utf-8"
    )
    be_models = (Path(ROOT) / "models" / "student_promotion_models.py").read_text(
        encoding="utf-8"
    )
    checks = [
        "StudentPromotionPopup" in layout,
        "myEligible" in api,
        "recordMyEvent" in api,
        "event_type" in popup or "recordMyEvent" in popup,
        "Maybe later" in popup,
        "/me/eligible" in be_routes,
        "/me/events" in be_routes,
        "list_eligible_promotions_for_student" in be_svc,
        "record_promotion_event" in be_svc,
        "PromotionEventType" in be_models,
        "is_live" in be_svc,
    ]
    results.append(
        ok("FE + BE popup surfaces", all(checks), f"{sum(checks)}/{len(checks)}")
    )
    return all(results)


def test_workflow():
    results = []
    admin = _mint_superadmin_token()
    if not admin:
        results.append(ok("superadmin token", False))
        return all(results)
    results.append(ok("superadmin token", True))

    fixtures = _seed()
    if not fixtures:
        results.append(ok("seed students", False))
        return all(results)
    results.append(ok("seed students", True, fixtures["tag"]))

    stu_in = _mint_student_token(fixtures["student_in"])
    stu_out = _mint_student_token(fixtures["student_out"])
    if not stu_in or not stu_out:
        results.append(ok("student tokens", False))
        return all(results)
    results.append(ok("student tokens", True))

    suffix = fixtures["tag"]
    ids = []

    try:
        # Draft should not appear
        st, draft = http_json(
            "POST",
            "/api/student-promotions",
            body={
                "title": f"QA Draft Popup {suffix}",
                "status": "draft",
                "banner_url": "https://example.com/d.jpg",
                "banner_media_type": "image",
                "target": {
                    "mode": "targeted",
                    "student_ids": [fixtures["student_in"]],
                },
            },
            token=admin,
            expect_status=201,
        )
        draft_id = (draft.get("promotion") or {}).get("id")
        if draft_id:
            ids.append(draft_id)

        # Live targeted promo for student_in only
        st, live = http_json(
            "POST",
            "/api/student-promotions",
            body={
                "title": f"QA Live Popup {suffix}",
                "short_description": "Special offer for you",
                "banner_url": "https://example.com/live.jpg",
                "banner_media_type": "image",
                "cta_type": "path",
                "cta_label": "View payments",
                "cta_url": "/student-dashboard/payments",
                "start_at": "2020-01-01T00:00:00",
                "end_at": "2099-12-31T23:59:00",
                "status": "active",
                "priority": 1,
                "target": {
                    "mode": "targeted",
                    "student_ids": [fixtures["student_in"]],
                },
            },
            token=admin,
            expect_status=201,
        )
        live_promo = live.get("promotion") or {}
        live_id = live_promo.get("id")
        if live_id:
            ids.append(live_id)
        results.append(
            ok(
                "create live targeted promotion",
                bool(live_id) and live_promo.get("is_live") is True,
                str(live_promo.get("is_live")),
            )
        )

        # Outside schedule / inactive should not show
        st, expired = http_json(
            "POST",
            "/api/student-promotions",
            body={
                "title": f"QA Expired Popup {suffix}",
                "status": "active",
                "start_at": "2020-01-01T00:00:00",
                "end_at": "2020-02-01T00:00:00",
                "target": {"mode": "all"},
            },
            token=admin,
            expect_status=201,
        )
        exp_id = (expired.get("promotion") or {}).get("id")
        if exp_id:
            ids.append(exp_id)

        # Student outside target → empty
        st, out_list = http_json(
            "GET",
            "/api/student-promotions/me/eligible?limit=10",
            token=stu_out,
            expect_status=200,
        )
        out_ids = {p.get("id") for p in (out_list.get("promotions") or [])}
        results.append(
            ok(
                "non-target student sees no promo",
                live_id not in out_ids and draft_id not in out_ids,
                f"count={out_list.get('total')}",
            )
        )

        # Eligible student sees live only
        st, in_list = http_json(
            "GET",
            "/api/student-promotions/me/eligible?limit=10",
            token=stu_in,
            expect_status=200,
        )
        in_ids = {p.get("id") for p in (in_list.get("promotions") or [])}
        results.append(
            ok(
                "eligible student sees live promo",
                live_id in in_ids and draft_id not in in_ids and exp_id not in in_ids,
                f"ids={sorted(in_ids)}",
            )
        )
        results.append(
            ok(
                "campaign dates/status enforced",
                live_id in in_ids and exp_id not in in_ids and draft_id not in in_ids,
            )
        )

        # Record view
        st, view_ev = http_json(
            "POST",
            "/api/student-promotions/me/events",
            body={"promotion_id": live_id, "event_type": "view"},
            token=stu_in,
            expect_status=201,
        )
        results.append(
            ok(
                "record promotion view",
                (view_ev.get("event") or {}).get("event_type") == "view",
                str(view_ev.get("event")),
            )
        )

        # Record CTA
        st, cta_ev = http_json(
            "POST",
            "/api/student-promotions/me/events",
            body={
                "promotion_id": live_id,
                "event_type": "cta",
                "meta": {"cta_type": "path"},
            },
            token=stu_in,
            expect_status=201,
        )
        results.append(
            ok(
                "record CTA action",
                (cta_ev.get("event") or {}).get("event_type") == "cta",
            )
        )

        # Dismiss hides from eligible
        st, dis_ev = http_json(
            "POST",
            "/api/student-promotions/me/events",
            body={"promotion_id": live_id, "event_type": "dismiss"},
            token=stu_in,
            expect_status=201,
        )
        results.append(
            ok(
                "record dismiss",
                (dis_ev.get("event") or {}).get("event_type") == "dismiss",
            )
        )

        st, after = http_json(
            "GET",
            "/api/student-promotions/me/eligible?limit=10",
            token=stu_in,
            expect_status=200,
        )
        after_ids = {p.get("id") for p in (after.get("promotions") or [])}
        results.append(
            ok(
                "dismissed promo excluded",
                live_id not in after_ids,
                f"count={after.get('total')}",
            )
        )

        # Admin cannot use /me/eligible
        st, admin_me = http_json(
            "GET",
            "/api/student-promotions/me/eligible",
            token=admin,
        )
        results.append(
            ok("admin blocked from student eligible", st in (401, 403), str(st))
        )

        # Student cannot create promotions
        st, stu_create = http_json(
            "POST",
            "/api/student-promotions",
            body={"title": "hack", "status": "active"},
            token=stu_in,
        )
        results.append(
            ok("student blocked from CMS create", st in (401, 403), str(st))
        )

    except Exception as exc:
        results.append(ok("workflow exception", False, str(exc)))
    finally:
        _cleanup(ids, fixtures)

    return all(results)


def main():
    print(f"BASE={BASE}")
    a = test_fe_surfaces()
    b = test_workflow()
    print(f"\nRESULT: {int(a) + int(b)}/2 suites passed")
    sys.exit(0 if a and b else 1)


if __name__ == "__main__":
    main()
