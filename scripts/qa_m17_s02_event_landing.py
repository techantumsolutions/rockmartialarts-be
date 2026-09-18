"""
M17-S02 Academy Event public landing QA.

  .venv\\Scripts\\python.exe scripts/qa_m17_s02_event_landing.py
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
                {"slug": {"$regex": "^qa-m17-s02-"}}
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
            "/api/academy-events/public",
            "/api/academy-events/public/{slug_or_id}",
            "/api/academy-events",
            "/api/events",
            "/api/camp-registrations",
            "/api/demo-schedules",
        ]
        missing = [p for p in needed if p not in raw]
        results.append(
            ok(
                "OpenAPI public academy-events + legacy intact",
                not missing,
                str(missing) or BASE,
            )
        )
    except Exception as exc:
        results.append(ok("OpenAPI public academy-events", False, str(exc)))

    fe_checks = [
        (FE_ROOT / "app" / "(website)" / "events" / "page.tsx").exists(),
        (FE_ROOT / "app" / "(website)" / "events" / "[slug]" / "page.tsx").exists(),
        (
            FE_ROOT
            / "app"
            / "(website)"
            / "events"
            / "[slug]"
            / "event-detail-client.tsx"
        ).exists(),
        (
            FE_ROOT
            / "app"
            / "(website)"
            / "events"
            / "[slug]"
            / "register"
            / "page.tsx"
        ).exists(),
        "listPublic"
        in (FE_ROOT / "lib" / "academyEventAPI.ts").read_text(encoding="utf-8"),
        'href: "/events"'
        in (FE_ROOT / "components" / "FixedTopNav.tsx").read_text(encoding="utf-8"),
        'href: "/events"'
        in (FE_ROOT / "components" / "website" / "WebsiteFooter.tsx").read_text(
            encoding="utf-8"
        ),
        "generateMetadata"
        in (
            FE_ROOT / "app" / "(website)" / "events" / "[slug]" / "page.tsx"
        ).read_text(encoding="utf-8"),
        "seo_title"
        in (
            FE_ROOT / "app" / "(website)" / "events" / "[slug]" / "page.tsx"
        ).read_text(encoding="utf-8"),
        "Register now"
        in (
            FE_ROOT
            / "app"
            / "(website)"
            / "events"
            / "[slug]"
            / "event-detail-client.tsx"
        ).read_text(encoding="utf-8"),
    ]
    results.append(ok("FE event landing surfaces", all(fe_checks), str(FE_ROOT)))
    return all(results)


def test_workflow():
    results = []
    token = _mint_superadmin_token()
    if not token:
        results.append(ok("superadmin token", False))
        return all(results)
    results.append(ok("superadmin token", True))

    suffix = uuid.uuid4().hex[:8]
    slug = f"qa-m17-s02-{suffix}"
    event_ids = []

    try:
        # Draft must stay hidden from public
        st, draft = http_json(
            "POST",
            "/api/academy-events",
            body={
                "title": f"QA Draft Hidden {suffix}",
                "slug": f"{slug}-draft",
                "event_type": "seminar",
                "start_at": "2031-01-10T10:00:00",
                "status": "draft",
                "fee_inr": 100,
            },
            token=token,
            expect_status=201,
        )
        did = (draft.get("event") or {}).get("id")
        if did:
            event_ids.append(did)

        # Published with SEO + capacity
        st, pub = http_json(
            "POST",
            "/api/academy-events",
            body={
                "title": f"QA Public Seminar {suffix}",
                "slug": slug,
                "event_type": "seminar",
                "short_description": "Public short",
                "description": "Full public description",
                "venue": "Dojo A",
                "venue_address": "10 Main Rd",
                "start_at": "2031-03-20T09:00:00",
                "end_at": "2031-03-20T12:00:00",
                "fee_inr": 299,
                "capacity": 25,
                "status": "published",
                "seo_title": f"SEO Title {suffix}",
                "seo_description": f"SEO Desc {suffix}",
                "registration_enabled": True,
            },
            token=token,
            expect_status=201,
        )
        pev = pub.get("event") or {}
        pid = pev.get("id")
        if pid:
            event_ids.append(pid)
        results.append(
            ok(
                "create published seminar",
                pev.get("status") == "published" and pev.get("slug") == slug,
                pid,
            )
        )

        # Free workshop published
        st, ws = http_json(
            "POST",
            "/api/academy-events",
            body={
                "title": f"QA Free Workshop {suffix}",
                "event_type": "workshop",
                "start_at": "2031-04-01T14:00:00",
                "fee_inr": 0,
                "status": "published",
                "capacity": 1,
                "registration_enabled": True,
            },
            token=token,
            expect_status=201,
        )
        wev = ws.get("event") or {}
        wid = wev.get("id")
        if wid:
            event_ids.append(wid)

        # Anonymous public list — draft excluded, published included
        st, listed = http_json(
            "GET",
            f"/api/academy-events/public?search={suffix}",
            expect_status=200,
        )
        events = listed.get("events") or []
        ids = {e.get("id") for e in events}
        results.append(
            ok(
                "public list hides draft",
                did not in ids and pid in ids,
                f"count={len(events)}",
            )
        )

        # Filter by type
        st, seminars = http_json(
            "GET",
            f"/api/academy-events/public?event_type=seminar&search={suffix}",
            expect_status=200,
        )
        results.append(
            ok(
                "public filter seminar",
                any(e.get("id") == pid for e in (seminars.get("events") or [])),
            )
        )

        # Detail by slug (anonymous)
        st, detail = http_json(
            "GET",
            f"/api/academy-events/public/{slug}",
            expect_status=200,
        )
        ev = detail.get("event") or {}
        results.append(
            ok(
                "public detail by slug + SEO fields",
                ev.get("id") == pid
                and ev.get("seo_title") == f"SEO Title {suffix}"
                and ev.get("seo_description") == f"SEO Desc {suffix}"
                and ev.get("venue") == "Dojo A"
                and float(ev.get("fee_inr") if ev.get("fee_inr") is not None else -1)
                == 299
                and ev.get("registration_open") is True
                and ev.get("seats_remaining") == 25
                and "created_by" not in ev,
                ev.get("slug"),
            )
        )

        # Detail by id
        st, by_id = http_json(
            "GET",
            f"/api/academy-events/public/{pid}",
            expect_status=200,
        )
        results.append(
            ok(
                "public detail by id",
                (by_id.get("event") or {}).get("slug") == slug,
            )
        )

        # Draft slug must 404 publicly
        st, hidden = http_json(
            "GET",
            f"/api/academy-events/public/{slug}-draft",
        )
        results.append(ok("draft slug public 404", st == 404, f"status={st}"))

        # Closed registration
        st, closed = http_json(
            "PATCH",
            f"/api/academy-events/{pid}",
            body={"registration_enabled": False},
            token=token,
            expect_status=200,
        )
        st, closed_pub = http_json(
            "GET",
            f"/api/academy-events/public/{slug}",
            expect_status=200,
        )
        cev = closed_pub.get("event") or {}
        results.append(
            ok(
                "registration closed reflected publicly",
                cev.get("registration_enabled") is False
                and cev.get("registration_open") is False,
            )
        )

        # Full capacity → not open
        # Mark workshop as full via direct count update if API has no registration yet
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

            async def fill():
                client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
                await client[dbn].academy_events.update_one(
                    {"id": wid}, {"$set": {"registrations_count": 1}}
                )

            try:
                asyncio.run(fill())
            except RuntimeError:
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(fill())
                finally:
                    loop.close()

            st, full = http_json(
                "GET",
                f"/api/academy-events/public/{wev.get('slug') or wid}",
                expect_status=200,
            )
            fev = full.get("event") or {}
            results.append(
                ok(
                    "full capacity closes registration_open",
                    fev.get("is_full") is True
                    and fev.get("seats_remaining") == 0
                    and fev.get("registration_open") is False,
                )
            )
        except Exception as exc:
            results.append(ok("full capacity closes registration_open", False, str(exc)))

        # Admin CMS still works
        st, admin_list = http_json(
            "GET",
            f"/api/academy-events?search={suffix}",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "admin list still includes draft + published",
                any(e.get("id") == did for e in (admin_list.get("events") or []))
                and any(e.get("id") == pid for e in (admin_list.get("events") or [])),
            )
        )

        # Legacy routes still present
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        results.append(
            ok(
                "camp + demo + legacy events intact",
                "/api/camp-registrations" in raw
                and "/api/demo-schedules" in raw
                and "/api/events" in raw,
            )
        )

    except Exception as exc:
        results.append(ok("event landing workflow", False, str(exc)))
    finally:
        _cleanup(event_ids)

    return all(results)


def main():
    print(f"M17-S02 QA against {BASE}")
    a = test_openapi_fe()
    b = test_workflow()
    print(f"\nResult: {sum([a, b])}/2 suites passed")
    sys.exit(0 if a and b else 1)


if __name__ == "__main__":
    main()
