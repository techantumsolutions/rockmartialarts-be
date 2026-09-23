"""
M15-S04 Lead Coach Assignment QA.

  .venv\\Scripts\\python.exe scripts/qa_m15_s04_lead_coach_assignment.py
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


def _mint_tokens():
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
            return None, None
        dbn = os.getenv("DB_NAME") or "marshalats"
        secret = os.getenv("SECRET_KEY") or "student_management_secret_key_2025_secure"

        async def run():
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
            sa = None
            coach = None
            for name in (dbn, "marshalats", "rockmartialarts", "rock_martial_arts"):
                if not sa:
                    sa = await client[name].superadmins.find_one({})
                if not coach:
                    coach = await client[name].coaches.find_one(
                        {
                            "$or": [
                                {"approval_status": "approved"},
                                {"approval_status": {"$exists": False}},
                                {"approval_status": None},
                            ],
                            "is_active": {"$ne": False},
                        }
                    )
                if sa and coach:
                    break
            return sa, coach

        try:
            sa, coach = asyncio.run(run())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                sa, coach = loop.run_until_complete(run())
            finally:
                loop.close()
        sa_tok = (
            jwt.encode({"sub": sa["id"], "role": "superadmin"}, secret, algorithm="HS256")
            if sa
            else None
        )
        coach_tok = (
            jwt.encode({"sub": coach["id"], "role": "coach"}, secret, algorithm="HS256")
            if coach
            else None
        )
        return sa_tok, (coach_tok, coach)
    except Exception as exc:
        print(f"(mint tokens skipped: {exc})")
        return None, None


def _cleanup(phone_suffix: str, lead_ids: list, assignment_ids: list):
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
            for aid in assignment_ids:
                await db.lead_coach_assignments.delete_many({"id": aid})
            for lid in lead_ids:
                await db.lead_coach_assignments.delete_many({"lead_id": lid})
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
            "/api/leads/{lead_id}/coach-assignment",
            "/api/leads/{lead_id}/eligible-coaches",
            "/api/lead-coach-assignments/me",
            "/api/lead-coach-assignments/{assignment_id}/accept",
            "/api/lead-coach-assignments/{assignment_id}/decline",
        ]
        missing = [p for p in needed if p not in raw]
        results.append(ok("OpenAPI assignment endpoints", not missing, str(missing) or BASE))
    except Exception as exc:
        results.append(ok("OpenAPI assignment endpoints", False, str(exc)))

    fe_checks = [
        (FE_ROOT / "lib" / "leadCoachAssignmentAPI.ts").exists(),
        (FE_ROOT / "components" / "leads" / "LeadCoachAssignmentSection.tsx").exists(),
        (FE_ROOT / "app" / "coach-dashboard" / "lead-assignments" / "page.tsx").exists(),
        "Lead Assignments"
        in (FE_ROOT / "components" / "coach-dashboard-header.tsx").read_text(
            encoding="utf-8"
        ),
        "LeadCoachAssignmentSection"
        in (FE_ROOT / "app" / "[adminType]" / "dashboard" / "leads" / "page.tsx").read_text(
            encoding="utf-8"
        ),
        "unassigned_coach" in (FE_ROOT / "lib" / "leadAPI.ts").read_text(encoding="utf-8"),
    ]
    results.append(ok("FE assignment surfaces", all(fe_checks), str(FE_ROOT)))
    return all(results)


def test_workflow():
    results = []
    sa_tok, coach_pack = _mint_tokens()
    if not sa_tok:
        results.append(ok("superadmin token", False))
        return all(results)
    results.append(ok("superadmin token", True))
    if not coach_pack or not coach_pack[0]:
        results.append(ok("coach token", False))
        return all(results)
    coach_tok, coach = coach_pack
    results.append(ok("coach token", True, coach.get("id")))

    suffix = uuid.uuid4().hex[:8]
    phone = f"+9192{suffix[:8]}"
    lead_ids = []
    assignment_ids = []

    try:
        st, lead = http_json(
            "POST",
            "/api/leads",
            body={
                "name": "QA Assign Lead",
                "phone": phone,
                "source_type": "website_popup",
                "source": "website_popup",
            },
            expect_status=201,
        )
        lid = lead.get("id")
        if lid:
            lead_ids.append(lid)
        results.append(ok("create lead", bool(lid), lid))

        st, eligible = http_json(
            "GET",
            f"/api/leads/{lid}/eligible-coaches",
            token=sa_tok,
            expect_status=200,
        )
        coaches = eligible.get("coaches") or []
        found_coach = any(c.get("id") == coach.get("id") for c in coaches)
        results.append(
            ok(
                "eligible coaches list",
                len(coaches) >= 1 and found_coach,
                f"n={len(coaches)} preferred={eligible.get('preferred_count')}",
            )
        )

        st, assigned = http_json(
            "POST",
            f"/api/leads/{lid}/coach-assignment",
            body={"coach_id": coach["id"], "note": "Please call this lead"},
            token=sa_tok,
            expect_status=201,
        )
        asn = assigned.get("assignment") or {}
        aid = asn.get("id")
        if aid:
            assignment_ids.append(aid)
        results.append(
            ok(
                "assign coach → pending",
                asn.get("status") == "pending"
                and asn.get("coach_id") == coach["id"]
                and (assigned.get("lead") or {}).get("coach_assignment_status")
                == "pending",
                aid,
            )
        )

        st, inbox = http_json(
            "GET",
            "/api/lead-coach-assignments/me?status=pending",
            token=coach_tok,
            expect_status=200,
        )
        found = any(x.get("id") == aid for x in (inbox.get("assignments") or []))
        results.append(ok("coach inbox pending", found, f"total={inbox.get('total')}"))

        st, declined = http_json(
            "POST",
            f"/api/lead-coach-assignments/{aid}/decline",
            body={"reason": "Schedule full"},
            token=coach_tok,
            expect_status=200,
        )
        results.append(
            ok(
                "coach decline",
                (declined.get("assignment") or {}).get("status") == "declined",
            )
        )

        # Reassign after decline
        st, assigned2 = http_json(
            "POST",
            f"/api/leads/{lid}/coach-assignment",
            body={"coach_id": coach["id"], "note": "Second try"},
            token=sa_tok,
            expect_status=201,
        )
        asn2 = assigned2.get("assignment") or {}
        aid2 = asn2.get("id")
        if aid2:
            assignment_ids.append(aid2)
        results.append(ok("reassign after decline", asn2.get("status") == "pending", aid2))

        st, accepted = http_json(
            "POST",
            f"/api/lead-coach-assignments/{aid2}/accept",
            body={"note": "Will call today"},
            token=coach_tok,
            expect_status=200,
        )
        lead_after = accepted.get("lead") or {}
        results.append(
            ok(
                "coach accept",
                (accepted.get("assignment") or {}).get("status") == "accepted"
                and lead_after.get("coach_assignment_status") == "accepted"
                and lead_after.get("assigned_coach_id") == coach["id"],
            )
        )

        st, listing = http_json(
            "GET",
            f"/api/leads?coach_assignment_status=accepted&search={phone[-8:]}",
            token=sa_tok,
            expect_status=200,
        )
        found_lead = any(x.get("id") == lid for x in (listing.get("leads") or []))
        results.append(ok("admin filter accepted", found_lead))

        # Converted lead cannot be assigned
        st, lead2 = http_json(
            "POST",
            "/api/leads",
            body={
                "name": "QA Converted",
                "phone": f"+9191{suffix[:8]}",
                "source_type": "website_popup",
            },
            expect_status=201,
        )
        lid2 = lead2.get("id")
        if lid2:
            lead_ids.append(lid2)
        http_json(
            "PATCH",
            f"/api/leads/{lid2}",
            body={"status": "converted"},
            token=sa_tok,
            expect_status=200,
        )
        st_bad, bad = http_json(
            "POST",
            f"/api/leads/{lid2}/coach-assignment",
            body={"coach_id": coach["id"]},
            token=sa_tok,
            expect_status=None,
        )
        results.append(
            ok(
                "block assign on converted",
                st_bad == 400,
                f"status={st_bad} detail={bad.get('detail')}",
            )
        )

        # Training-request assign path still present (regression smoke)
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        results.append(
            ok(
                "training-request coach assign intact",
                "/api/training-requests/{request_id}/coach" in raw,
            )
        )

    except AssertionError as exc:
        results.append(ok("workflow assertion", False, str(exc)))
    finally:
        cleaned = _cleanup(suffix[:8], lead_ids, assignment_ids)
        results.append(ok("cleanup", cleaned is not False))

    return all(results)


def main():
    print(f"M15-S04 Lead Coach Assignment QA  base={BASE}")
    r1 = test_openapi_fe()
    r2 = test_workflow()
    print(f"\nDone. {int(r1) + int(r2)}/2 passed.")
    sys.exit(0 if (r1 and r2) else 1)


if __name__ == "__main__":
    main()
