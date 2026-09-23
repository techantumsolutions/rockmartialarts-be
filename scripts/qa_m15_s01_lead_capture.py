"""
M15-S01 Lead Capture QA.

  .venv\\Scripts\\python.exe scripts/qa_m15_s01_lead_capture.py
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


def _cleanup_phones(phones):
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
            for p in phones:
                await db.leads.delete_many({"phone": {"$regex": p[-10:]}})
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
        needed = ["/api/leads", "/api/leads/sources"]
        missing = [p for p in needed if p not in raw]
        results.append(ok("OpenAPI lead endpoints", not missing, str(missing) or BASE))
    except Exception as exc:
        results.append(ok("OpenAPI lead endpoints", False, str(exc)))

    fe_ok = all(
        [
            "source_type" in (FE_ROOT / "lib" / "submitLead.ts").read_text(encoding="utf-8"),
            "source_type" in (FE_ROOT / "components" / "website" / "LeadCaptureModal.tsx").read_text(
                encoding="utf-8"
            ),
            "sourceFilter"
            in (FE_ROOT / "app" / "[adminType]" / "dashboard" / "leads" / "page.tsx").read_text(
                encoding="utf-8"
            ),
            "Leads"
            in (FE_ROOT / "lib" / "dashboard-config.ts").read_text(encoding="utf-8"),
            "branch-admin" in (FE_ROOT / "lib" / "dashboard-config.ts").read_text(encoding="utf-8")
            and "/leads"
            in (FE_ROOT / "lib" / "dashboard-config.ts").read_text(encoding="utf-8"),
        ]
    )
    results.append(ok("FE lead surfaces", fe_ok, str(FE_ROOT)))
    return all(results)


def test_workflow():
    results = []
    token = _mint_superadmin_token()
    if not token:
        results.append(ok("superadmin token", False))
        return all(results)
    results.append(ok("superadmin token", True))

    suffix = uuid.uuid4().hex[:8]
    phone = f"+9198{suffix[:8]}"
    phones = [phone]

    try:
        # Visitor popup lead
        st, lead1 = http_json(
            "POST",
            "/api/leads",
            body={
                "name": "QA Visitor",
                "phone": phone,
                "source": "website_popup",
                "source_type": "website_popup",
            },
            expect_status=201,
        )
        lid1 = lead1.get("id")
        results.append(
            ok(
                "visitor lead create",
                lead1.get("source_type") == "website_popup" and bool(lid1),
                f"id={lid1} type={lead1.get('source_type')}",
            )
        )

        # Duplicate same phone+source within window → merge
        st, lead2 = http_json(
            "POST",
            "/api/leads",
            body={
                "name": "QA Visitor Updated",
                "phone": phone,
                "source": "website_popup",
                "source_type": "website_popup",
            },
            expect_status=201,
        )
        results.append(
            ok(
                "duplicate merge same source",
                lead2.get("id") == lid1
                and lead2.get("duplicate_merged") is True
                and int(lead2.get("capture_count") or 0) >= 2,
                f"id={lead2.get('id')} count={lead2.get('capture_count')} merged={lead2.get('duplicate_merged')}",
            )
        )

        # Different source_type → new lead (traceable)
        st, lead3 = http_json(
            "POST",
            "/api/leads",
            body={
                "name": "QA Reg",
                "email": f"qa.m15s01.{suffix}@example.com",
                "phone": phone,
                "source": "registration_step1",
                "source_type": "registration_step1",
            },
            expect_status=201,
        )
        results.append(
            ok(
                "different source creates new lead",
                lead3.get("id") != lid1
                and lead3.get("source_type") == "registration_step1",
                f"id={lead3.get('id')}",
            )
        )

        # Idempotent source_ref
        ref = f"tr-{suffix}"
        st, tr1 = http_json(
            "POST",
            "/api/leads",
            body={
                "name": "QA Training",
                "phone": f"+9197{suffix[:8]}",
                "source_type": "training_home",
                "source": "training_home",
                "source_ref_type": "training_request",
                "source_ref_id": ref,
                "course": "home",
            },
            expect_status=201,
        )
        phones.append(f"+9197{suffix[:8]}")
        st, tr2 = http_json(
            "POST",
            "/api/leads",
            body={
                "name": "QA Training Again",
                "phone": f"+9197{suffix[:8]}",
                "source_type": "training_home",
                "source_ref_type": "training_request",
                "source_ref_id": ref,
            },
            expect_status=201,
        )
        results.append(
            ok(
                "source_ref idempotent",
                tr1.get("id") == tr2.get("id") and tr2.get("duplicate_merged") is True,
                f"id={tr2.get('id')}",
            )
        )

        # Demo source via upsert path (public create with demo_booking type)
        st, demo = http_json(
            "POST",
            "/api/leads",
            body={
                "name": "QA Demo",
                "phone": f"+9196{suffix[:8]}",
                "source_type": "demo_booking",
                "source": "demo_booking",
                "source_ref_type": "demo_booking",
                "source_ref_id": f"demo-{suffix}",
                "course": "Karate Demo",
            },
            expect_status=201,
        )
        phones.append(f"+9196{suffix[:8]}")
        results.append(
            ok(
                "demo source lead",
                demo.get("source_type") == "demo_booking",
                demo.get("id"),
            )
        )

        st, sources = http_json(
            "GET", "/api/leads/sources", token=token, expect_status=200
        )
        results.append(
            ok(
                "admin sources list",
                isinstance(sources.get("sources"), list)
                and sources.get("duplicate_window_hours", 0) >= 1,
                f"n={len(sources.get('sources') or [])}",
            )
        )

        st, listing = http_json(
            "GET",
            "/api/leads?source_type=website_popup&search=" + suffix[:4],
            token=token,
            expect_status=200,
        )
        # search may not find suffix in name - use phone search
        st, listing = http_json(
            "GET",
            f"/api/leads?source_type=website_popup&search={phone[-10:]}",
            token=token,
            expect_status=200,
        )
        found = any(x.get("id") == lid1 for x in (listing.get("leads") or []))
        results.append(
            ok("admin list filter by source_type", found, f"total={listing.get('total')}")
        )

        st, patched = http_json(
            "PATCH",
            f"/api/leads/{lid1}",
            body={"status": "contacted"},
            token=token,
            expect_status=200,
        )
        results.append(ok("admin status update", patched.get("status") == "contacted"))

    finally:
        cleaned = _cleanup_phones(phones)
        results.append(ok("cleanup", cleaned is not False))

    return all(results)


def main():
    print(f"M15-S01 Lead Capture QA  base={BASE}")
    r1 = test_openapi_fe()
    r2 = test_workflow()
    print(f"\nDone. {int(r1) + int(r2)}/2 passed.")
    sys.exit(0 if (r1 and r2) else 1)


if __name__ == "__main__":
    main()
