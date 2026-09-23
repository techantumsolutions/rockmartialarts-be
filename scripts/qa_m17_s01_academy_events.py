"""
M17-S01 Academy Event CMS QA.

  .venv\\Scripts\\python.exe scripts/qa_m17_s01_academy_events.py
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


def _cleanup(event_ids):
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
            for eid in event_ids or []:
                await db.academy_events.delete_many({"id": eid})
            await db.academy_events.delete_many(
                {"slug": {"$regex": "^qa-m17-s01-"}}
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
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        needed = [
            "/api/academy-events",
            "/api/academy-events/{event_id}",
            "/api/events",
            "/api/camp-registrations",
            "/api/demo-schedules",
        ]
        missing = [p for p in needed if p not in raw]
        results.append(
            ok(
                "OpenAPI academy-events + legacy intact",
                not missing,
                str(missing) or BASE,
            )
        )
    except Exception as exc:
        results.append(ok("OpenAPI academy-events", False, str(exc)))

    fe_checks = [
        (FE_ROOT / "lib" / "academyEventAPI.ts").exists(),
        (FE_ROOT / "components" / "events" / "AcademyEventsAdminPage.tsx").exists(),
        (
            FE_ROOT
            / "app"
            / "[adminType]"
            / "dashboard"
            / "events"
            / "page.tsx"
        ).exists(),
        "/events"
        in (FE_ROOT / "lib" / "dashboard-config.ts").read_text(encoding="utf-8"),
        "Events, Seminars"
        in (
            FE_ROOT / "components" / "events" / "AcademyEventsAdminPage.tsx"
        ).read_text(encoding="utf-8"),
    ]
    results.append(ok("FE Event CMS surfaces", all(fe_checks), str(FE_ROOT)))
    return all(results)


def test_workflow():
    results = []
    token = _mint_superadmin_token()
    if not token:
        results.append(ok("superadmin token", False))
        return all(results)
    results.append(ok("superadmin token", True))

    suffix = uuid.uuid4().hex[:8]
    slug = f"qa-m17-s01-{suffix}"
    event_ids = []

    try:
        # Create draft seminar
        st, created = http_json(
            "POST",
            "/api/academy-events",
            body={
                "title": f"QA Seminar {suffix}",
                "slug": slug,
                "event_type": "seminar",
                "short_description": "QA short",
                "description": "Full QA description",
                "venue": "Main Hall",
                "venue_address": "1 Test Street",
                "start_at": "2030-06-15T10:00:00",
                "end_at": "2030-06-15T13:00:00",
                "fee_inr": 499,
                "capacity": 40,
                "status": "draft",
                "sort_order": 10,
                "registration_enabled": True,
            },
            token=token,
            expect_status=201,
        )
        event = created.get("event") or {}
        eid = event.get("id")
        if eid:
            event_ids.append(eid)
        results.append(
            ok(
                "create draft seminar",
                event.get("status") == "draft"
                and event.get("event_type") == "seminar"
                and event.get("slug") == slug
                and float(event.get("fee_inr") or 0) == 499
                and int(event.get("capacity") or 0) == 40,
                eid,
            )
        )

        # Types: workshop + event
        st, ws = http_json(
            "POST",
            "/api/academy-events",
            body={
                "title": f"QA Workshop {suffix}",
                "event_type": "workshop",
                "start_at": "2030-07-01T09:00:00",
                "fee_inr": 0,
                "status": "published",
            },
            token=token,
            expect_status=201,
        )
        wid = (ws.get("event") or {}).get("id")
        if wid:
            event_ids.append(wid)
        ws_ev = ws.get("event") or {}
        fee_raw = ws_ev.get("fee_inr")
        try:
            fee_val = float(fee_raw) if fee_raw is not None else None
        except (TypeError, ValueError):
            fee_val = None
        results.append(
            ok(
                "create free published workshop",
                ws_ev.get("event_type") == "workshop"
                and fee_val == 0
                and ws_ev.get("status") == "published",
                f"fee={fee_raw} status={ws_ev.get('status')}",
            )
        )

        st, ev = http_json(
            "POST",
            "/api/academy-events",
            body={
                "title": f"QA Event {suffix}",
                "event_type": "event",
                "start_at": "2030-08-01T18:00:00",
                "end_at": "2030-08-01T20:00:00",
                "status": "draft",
            },
            token=token,
            expect_status=201,
        )
        evid = (ev.get("event") or {}).get("id")
        if evid:
            event_ids.append(evid)
        results.append(
            ok(
                "create event type",
                (ev.get("event") or {}).get("event_type") == "event",
            )
        )

        # Filter by type
        st, listed = http_json(
            "GET",
            f"/api/academy-events?event_type=seminar&search={suffix}",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "filter by seminar type",
                any(x.get("id") == eid for x in (listed.get("events") or [])),
            )
        )

        # Publish + venue update
        st, updated = http_json(
            "PATCH",
            f"/api/academy-events/{eid}",
            body={
                "status": "published",
                "venue": "Updated Hall",
                "seo_title": f"SEO {suffix}",
            },
            token=token,
            expect_status=200,
        )
        u = updated.get("event") or {}
        results.append(
            ok(
                "publish + venue/seo",
                u.get("status") == "published"
                and u.get("venue") == "Updated Hall"
                and u.get("seo_title") == f"SEO {suffix}"
                and u.get("published_at"),
            )
        )

        # Invalid end before start
        st, bad = http_json(
            "PATCH",
            f"/api/academy-events/{eid}",
            body={"end_at": "2030-01-01T00:00:00"},
            token=token,
        )
        results.append(ok("reject end before start", st == 400, f"status={st}"))

        # Soft archive
        st, arch = http_json(
            "DELETE",
            f"/api/academy-events/{eid}",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "archive event",
                (arch.get("event") or {}).get("status") == "archived",
            )
        )

        # Legacy calendar events require branch_id — 422 without it still proves route alive
        st, legacy = http_json("GET", "/api/events", token=token)
        results.append(
            ok(
                "legacy /api/events reachable",
                st in (200, 401, 403, 422),
                f"status={st}",
            )
        )

        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        results.append(
            ok(
                "camp + demo routes still in OpenAPI",
                "/api/camp-registrations" in raw and "/api/demo-schedules" in raw,
            )
        )

    except Exception as exc:
        results.append(ok("event CMS workflow", False, str(exc)))
    finally:
        _cleanup(event_ids)

    return all(results)


def main():
    print(f"M17-S01 QA against {BASE}")
    a = test_openapi_fe()
    b = test_workflow()
    print(f"\nResult: {sum([a, b])}/2 suites passed")
    sys.exit(0 if a and b else 1)


if __name__ == "__main__":
    main()
