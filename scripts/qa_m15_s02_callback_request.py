"""
M15-S02 Callback Request QA.

  .venv\\Scripts\\python.exe scripts/qa_m15_s02_callback_request.py
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


def _cleanup(phone_suffix: str, callback_ids: list):
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
            for cid in callback_ids:
                await db.lead_callbacks.delete_many({"id": cid})
            if phone_suffix:
                await db.lead_callbacks.delete_many(
                    {"phone": {"$regex": phone_suffix}}
                )
                await db.leads.delete_many(
                    {
                        "source_type": "callback",
                        "phone": {"$regex": phone_suffix},
                    }
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
            "/api/callbacks",
            "/api/callbacks/summary",
            "/api/callbacks/{callback_id}",
            "/api/callbacks/{callback_id}/status",
        ]
        missing = [p for p in needed if p not in raw]
        results.append(ok("OpenAPI callback endpoints", not missing, str(missing) or BASE))
    except Exception as exc:
        results.append(ok("OpenAPI callback endpoints", False, str(exc)))

    fe_checks = [
        (FE_ROOT / "lib" / "callbackAPI.ts").exists(),
        (FE_ROOT / "components" / "callbacks" / "CallbackRequestPublicForm.tsx").exists(),
        (FE_ROOT / "app" / "(website)" / "request-callback" / "page.tsx").exists(),
        (FE_ROOT / "app" / "[adminType]" / "dashboard" / "callbacks" / "page.tsx").exists(),
        "Request Callback"
        in (FE_ROOT / "components" / "FixedTopNav.tsx").read_text(encoding="utf-8"),
        "/callbacks"
        in (FE_ROOT / "lib" / "dashboard-config.ts").read_text(encoding="utf-8"),
        "request-callback"
        in (FE_ROOT / "app" / "(website)" / "contact" / "page.tsx").read_text(
            encoding="utf-8"
        ),
    ]
    results.append(ok("FE callback surfaces", all(fe_checks), str(FE_ROOT)))
    return all(results)


def test_workflow():
    results = []
    token = _mint_superadmin_token()
    if not token:
        results.append(ok("superadmin token", False))
        return all(results)
    results.append(ok("superadmin token", True))

    suffix = uuid.uuid4().hex[:8]
    phone = f"+9195{suffix[:8]}"
    callback_ids = []

    try:
        # Public create — normal priority (no highlight)
        st, created = http_json(
            "POST",
            "/api/callbacks",
            body={
                "name": "QA Callback Normal",
                "phone": phone,
                "email": f"qa.m15s02.{suffix}@example.com",
                "preferred_time": "Morning (9 AM – 12 PM)",
                "message": "Please call about kids karate",
                "course_interest": "Karate",
                "priority": "normal",
            },
            expect_status=201,
        )
        cb = created.get("callback") or {}
        cid = cb.get("id")
        if cid:
            callback_ids.append(cid)
        results.append(
            ok(
                "public callback create",
                bool(cid) and cb.get("status") == "new" and cb.get("highlight") is False,
                f"id={cid} highlight={cb.get('highlight')}",
            )
        )
        results.append(
            ok(
                "callback linked lead",
                bool(cb.get("lead_id")),
                f"lead_id={cb.get('lead_id')}",
            )
        )

        # High priority → highlight
        phone2 = f"+9194{suffix[:8]}"
        st, high = http_json(
            "POST",
            "/api/callbacks",
            body={
                "name": "QA Callback High",
                "phone": phone2,
                "priority": "high",
                "message": "Need urgent slot info",
            },
            expect_status=201,
        )
        hcb = high.get("callback") or {}
        hid = hcb.get("id")
        if hid:
            callback_ids.append(hid)
        results.append(
            ok(
                "high priority auto-highlight",
                hcb.get("priority") == "high" and hcb.get("highlight") is True,
                f"id={hid}",
            )
        )

        # Admin list + highlight filter
        st, listing = http_json(
            "GET",
            f"/api/callbacks?search={suffix[:4]}&highlight_only=true",
            token=token,
            expect_status=200,
        )
        found_high = any(x.get("id") == hid for x in (listing.get("callbacks") or []))
        results.append(
            ok(
                "admin list highlight_only",
                found_high,
                f"total={listing.get('total')}",
            )
        )

        st, summary = http_json(
            "GET", "/api/callbacks/summary", token=token, expect_status=200
        )
        results.append(
            ok(
                "admin summary",
                isinstance(summary.get("total"), int)
                and isinstance(summary.get("highlighted"), int),
                f"total={summary.get('total')} highlighted={summary.get('highlighted')}",
            )
        )

        # Status transition
        st, patched = http_json(
            "PATCH",
            f"/api/callbacks/{cid}/status",
            body={"status": "contacted", "admin_note": "Called once"},
            token=token,
            expect_status=200,
        )
        pcb = patched.get("callback") or {}
        results.append(
            ok(
                "status → contacted",
                pcb.get("status") == "contacted" and bool(pcb.get("contacted_at")),
                pcb.get("status"),
            )
        )

        st, done = http_json(
            "PATCH",
            f"/api/callbacks/{cid}/status",
            body={"status": "completed"},
            token=token,
            expect_status=200,
        )
        dcb = done.get("callback") or {}
        results.append(
            ok(
                "status → completed",
                dcb.get("status") == "completed" and bool(dcb.get("completed_at")),
                dcb.get("status"),
            )
        )

        # Priority update derives highlight
        st, pri = http_json(
            "PATCH",
            f"/api/callbacks/{cid}",
            body={"priority": "urgent"},
            token=token,
            expect_status=200,
        )
        pcb2 = pri.get("callback") or {}
        results.append(
            ok(
                "priority update sets highlight",
                pcb2.get("priority") == "urgent" and pcb2.get("highlight") is True,
                f"priority={pcb2.get('priority')}",
            )
        )

        # Explicit highlight off
        st, hl = http_json(
            "PATCH",
            f"/api/callbacks/{cid}",
            body={"highlight": False},
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "highlight override off",
                (hl.get("callback") or {}).get("highlight") is False,
            )
        )

        # Auth required for list
        st_unauth, _ = http_json("GET", "/api/callbacks", expect_status=None)
        results.append(
            ok("list requires auth", st_unauth in (401, 403), f"status={st_unauth}")
        )

    except AssertionError as exc:
        results.append(ok("workflow assertion", False, str(exc)))
    finally:
        cleaned = _cleanup(suffix[:8], callback_ids)
        # also clean phone2 suffix pattern
        results.append(ok("cleanup", cleaned is not False))

    return all(results)


def main():
    print(f"M15-S02 Callback Request QA  base={BASE}")
    r1 = test_openapi_fe()
    r2 = test_workflow()
    print(f"\nDone. {int(r1) + int(r2)}/2 passed.")
    sys.exit(0 if (r1 and r2) else 1)


if __name__ == "__main__":
    main()
