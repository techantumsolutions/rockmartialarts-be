"""
M15-S03 Lead Follow-Up Pipeline QA.

  .venv\\Scripts\\python.exe scripts/qa_m15_s03_lead_follow_up.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta
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


def _cleanup(phone_suffix: str, lead_ids: list):
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
            for lid in lead_ids:
                await db.lead_follow_up_events.delete_many({"lead_id": lid})
                await db.leads.delete_many({"id": lid})
            if phone_suffix:
                await db.leads.delete_many({"phone": {"$regex": phone_suffix}})
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
            "/api/leads/summary",
            "/api/leads/{lead_id}",
            "/api/leads/{lead_id}/follow-ups",
        ]
        missing = [p for p in needed if p not in raw]
        results.append(ok("OpenAPI follow-up endpoints", not missing, str(missing) or BASE))
    except Exception as exc:
        results.append(ok("OpenAPI follow-up endpoints", False, str(exc)))

    fe_checks = [
        (FE_ROOT / "lib" / "leadAPI.ts").exists(),
        "follow_up_due"
        in (FE_ROOT / "lib" / "leadAPI.ts").read_text(encoding="utf-8"),
        "Log follow-up"
        in (FE_ROOT / "app" / "[adminType]" / "dashboard" / "leads" / "page.tsx").read_text(
            encoding="utf-8"
        ),
        "overdue"
        in (FE_ROOT / "app" / "[adminType]" / "dashboard" / "leads" / "page.tsx").read_text(
            encoding="utf-8"
        ),
        "createFollowUp" in (FE_ROOT / "lib" / "leadAPI.ts").read_text(encoding="utf-8"),
    ]
    results.append(ok("FE pipeline surfaces", all(fe_checks), str(FE_ROOT)))
    return all(results)


def test_workflow():
    results = []
    token = _mint_superadmin_token()
    if not token:
        results.append(ok("superadmin token", False))
        return all(results)
    results.append(ok("superadmin token", True))

    suffix = uuid.uuid4().hex[:8]
    phone = f"+9193{suffix[:8]}"
    lead_ids = []

    try:
        st, lead = http_json(
            "POST",
            "/api/leads",
            body={
                "name": "QA FollowUp Lead",
                "phone": phone,
                "source": "website_popup",
                "source_type": "website_popup",
            },
            expect_status=201,
        )
        lid = lead.get("id")
        if lid:
            lead_ids.append(lid)
        results.append(ok("create lead", bool(lid), lid))

        past = (datetime.utcnow() - timedelta(days=2)).isoformat() + "Z"
        st, fu = http_json(
            "POST",
            f"/api/leads/{lid}/follow-ups",
            body={
                "action": "call",
                "note": "Left voicemail",
                "status": "contacted",
                "next_follow_up_at": past,
            },
            token=token,
            expect_status=201,
        )
        updated = fu.get("lead") or {}
        event = fu.get("follow_up") or {}
        results.append(
            ok(
                "log follow-up + status + next date",
                updated.get("status") == "contacted"
                and bool(updated.get("next_follow_up_at"))
                and bool(event.get("id"))
                and event.get("note") == "Left voicemail",
                f"status={updated.get('status')} event={event.get('id')}",
            )
        )

        st, detail = http_json(
            "GET", f"/api/leads/{lid}", token=token, expect_status=200
        )
        hist = detail.get("follow_ups") or []
        results.append(
            ok(
                "get lead embeds history",
                len(hist) >= 1 and (detail.get("lead") or {}).get("id") == lid,
                f"n={len(hist)}",
            )
        )

        st, hist_page = http_json(
            "GET",
            f"/api/leads/{lid}/follow-ups",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "list follow-ups",
                int(hist_page.get("total") or 0) >= 1,
                f"total={hist_page.get('total')}",
            )
        )

        # Status PATCH also writes history
        st, patched = http_json(
            "PATCH",
            f"/api/leads/{lid}",
            body={"status": "qualified", "note": "Interested in kids class"},
            token=token,
            expect_status=200,
        )
        results.append(ok("PATCH status → qualified", patched.get("status") == "qualified"))

        st, hist2 = http_json(
            "GET",
            f"/api/leads/{lid}/follow-ups",
            token=token,
            expect_status=200,
        )
        actions = [x.get("action") for x in (hist2.get("follow_ups") or [])]
        results.append(
            ok(
                "status change in history",
                int(hist2.get("total") or 0) >= 2,
                f"actions={actions}",
            )
        )

        st, overdue_list = http_json(
            "GET",
            f"/api/leads?follow_up_due=overdue&search={phone[-8:]}",
            token=token,
            expect_status=200,
        )
        found = any(x.get("id") == lid for x in (overdue_list.get("leads") or []))
        results.append(
            ok("list filter overdue", found, f"total={overdue_list.get('total')}")
        )

        st, summary = http_json(
            "GET", "/api/leads/summary", token=token, expect_status=200
        )
        results.append(
            ok(
                "pipeline summary",
                isinstance(summary.get("by_status"), dict)
                and isinstance(summary.get("overdue"), int),
                f"overdue={summary.get('overdue')} open={summary.get('open')}",
            )
        )

        st_unauth, _ = http_json("GET", "/api/leads/summary", expect_status=None)
        results.append(
            ok("summary requires auth", st_unauth in (401, 403), f"status={st_unauth}")
        )

    except AssertionError as exc:
        results.append(ok("workflow assertion", False, str(exc)))
    finally:
        cleaned = _cleanup(suffix[:8], lead_ids)
        results.append(ok("cleanup", cleaned is not False))

    return all(results)


def main():
    print(f"M15-S03 Lead Follow-Up Pipeline QA  base={BASE}")
    r1 = test_openapi_fe()
    r2 = test_workflow()
    print(f"\nDone. {int(r1) + int(r2)}/2 passed.")
    sys.exit(0 if (r1 and r2) else 1)


if __name__ == "__main__":
    main()
