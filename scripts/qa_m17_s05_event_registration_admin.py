"""
M17-S05 Academy Event Registration Admin QA.

  .venv\\Scripts\\python.exe scripts/qa_m17_s05_event_registration_admin.py
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


def _cleanup(event_ids, reg_ids):
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
            for rid in reg_ids or []:
                await db.academy_event_registrations.delete_many({"id": rid})
            for eid in event_ids or []:
                await db.academy_events.delete_many({"id": eid})
            await db.academy_events.delete_many(
                {"slug": {"$regex": "^qa-m17-s05-"}}
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
            "/api/academy-event-registration-admin",
            "/api/academy-event-registration-admin/summary",
            "/api/academy-event-registration-admin/export",
            "/api/academy-event-registrations/mine",
            "/api/academy-event-registrations/send-otp",
            "/api/demo-bookings",
            "/api/demo-bookings/export",
        ]
        missing = [p for p in needed if p not in raw]
        results.append(
            ok(
                "OpenAPI admin regs + public/legacy intact",
                not missing,
                str(missing) or BASE,
            )
        )
    except Exception as exc:
        results.append(ok("OpenAPI admin regs", False, str(exc)))

    fe_checks = [
        (FE_ROOT / "lib" / "academyEventRegistrationAdminAPI.ts").exists(),
        (
            FE_ROOT / "components" / "events" / "EventRegistrationsAdminPage.tsx"
        ).exists(),
        (
            FE_ROOT
            / "app"
            / "[adminType]"
            / "dashboard"
            / "event-registrations"
            / "page.tsx"
        ).exists(),
        "/event-registrations"
        in (FE_ROOT / "lib" / "dashboard-config.ts").read_text(encoding="utf-8"),
        "Export CSV"
        in (
            FE_ROOT / "components" / "events" / "EventRegistrationsAdminPage.tsx"
        ).read_text(encoding="utf-8"),
        "academy_event_registration"
        in (
            FE_ROOT / "components" / "events" / "EventRegistrationsAdminPage.tsx"
        ).read_text(encoding="utf-8"),
    ]
    results.append(ok("FE admin registrations surfaces", all(fe_checks), str(FE_ROOT)))
    return all(results)


def test_workflow():
    results = []
    token = _mint_superadmin_token()
    if not token:
        results.append(ok("superadmin token", False))
        return all(results)
    results.append(ok("superadmin token", True))

    suffix = uuid.uuid4().hex[:8]
    n = int(suffix, 16) % 100000000
    phone = f"+9197{n:08d}"
    event_ids = []
    reg_ids = []

    try:
        # Unauthenticated admin denied
        st, _ = http_json("GET", "/api/academy-event-registration-admin")
        results.append(ok("admin list requires auth", st in (401, 403), f"status={st}"))

        st, ev = http_json(
            "POST",
            "/api/academy-events",
            body={
                "title": f"QA Admin Event {suffix}",
                "slug": f"qa-m17-s05-{suffix}",
                "event_type": "workshop",
                "start_at": "2031-10-01T10:00:00",
                "fee_inr": 0,
                "capacity": 20,
                "status": "published",
            },
            token=token,
            expect_status=201,
        )
        eid = (ev.get("event") or {}).get("id")
        if eid:
            event_ids.append(eid)

        st, reg = http_json(
            "POST",
            "/api/academy-event-registrations",
            body={
                "event_id": eid,
                "participant_name": f"Admin QA {suffix}",
                "participant_phone": phone,
                "participant_email": f"qa-s05-{suffix}@example.com",
            },
            expect_status=201,
        )
        rid = (reg.get("registration") or {}).get("id")
        if rid:
            reg_ids.append(rid)

        # List + filter
        st, listed = http_json(
            "GET",
            f"/api/academy-event-registration-admin?event_id={eid}&search={suffix}",
            token=token,
            expect_status=200,
        )
        ids = {r.get("id") for r in (listed.get("registrations") or [])}
        results.append(
            ok(
                "admin list filters by event + search",
                rid in ids and (listed.get("total") or 0) >= 1,
                f"total={listed.get('total')}",
            )
        )

        # Summary
        st, summary = http_json(
            "GET",
            f"/api/academy-event-registration-admin/summary?event_id={eid}",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "summary counts",
                (summary.get("total") or 0) >= 1
                and (summary.get("by_status") or {}).get("confirmed", 0) >= 1,
                str(summary.get("by_status")),
            )
        )

        # Get detail
        st, detail = http_json(
            "GET",
            f"/api/academy-event-registration-admin/{rid}",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "admin get detail",
                (detail.get("registration") or {}).get("id") == rid,
            )
        )

        # Export CSV
        st, exported = http_json(
            "GET",
            f"/api/academy-event-registration-admin/export?event_id={eid}",
            token=token,
            expect_status=200,
        )
        content = exported.get("content") or ""
        results.append(
            ok(
                "CSV export",
                "participant_name" in content
                and rid in content
                and (exported.get("total") or 0) >= 1
                and "text/csv" in str(exported.get("content_type") or ""),
                exported.get("filename"),
            )
        )

        # Status cancel
        st, updated = http_json(
            "PATCH",
            f"/api/academy-event-registration-admin/{rid}/status",
            body={"status": "cancelled", "note": "QA cancel"},
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "admin cancel status",
                (updated.get("registration") or {}).get("status") == "cancelled",
            )
        )

        # Public mine/OTP routes still present; create still works on another event
        st, _ = http_json(
            "GET", "/api/academy-event-registrations/mine"
        )
        results.append(
            ok("public mine still reachable", st in (401, 403), f"status={st}")
        )

        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        results.append(
            ok(
                "demo bookings + public OTP intact",
                "/api/demo-bookings/export" in raw
                and "/api/academy-event-registrations/send-otp" in raw,
            )
        )

    except Exception as exc:
        results.append(ok("admin workflow", False, str(exc)))
    finally:
        _cleanup(event_ids, reg_ids)

    return all(results)


def main():
    print(f"M17-S05 QA against {BASE}")
    a = test_openapi_fe()
    b = test_workflow()
    print(f"\nResult: {sum([a, b])}/2 suites passed")
    sys.exit(0 if a and b else 1)


if __name__ == "__main__":
    main()
