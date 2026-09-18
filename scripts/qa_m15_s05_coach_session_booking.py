"""
M15-S05 Coach Session Booking & Schedule QA.

  .venv\\Scripts\\python.exe scripts/qa_m15_s05_coach_session_booking.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import date, timedelta
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


def _cleanup(phone_suffix, lead_ids, booking_ids, coach_id, restore_avail=None):
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
            for bid in booking_ids:
                await db.coach_session_bookings.delete_many({"id": bid})
            for lid in lead_ids:
                await db.lead_coach_assignments.delete_many({"lead_id": lid})
                await db.coach_session_bookings.delete_many({"lead_id": lid})
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


def _next_weekday(target_weekday: int) -> date:
    """target_weekday: Mon=0 … Sun=6"""
    d = date.today() + timedelta(days=1)
    for _ in range(14):
        if d.weekday() == target_weekday:
            return d
        d += timedelta(days=1)
    return date.today() + timedelta(days=1)


def test_openapi_fe():
    results = []
    try:
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        needed = [
            "/api/coach-session-bookings",
            "/api/coach-session-bookings/slots",
            "/api/leads/{lead_id}/coach-session-bookings",
            "/api/coaches/me/schedule",
            "/api/demo-sessions/availability",
            "/api/coaches/me/availability",
        ]
        missing = [p for p in needed if p not in raw]
        results.append(ok("OpenAPI session + demo/avail intact", not missing, str(missing) or BASE))
    except Exception as exc:
        results.append(ok("OpenAPI session endpoints", False, str(exc)))

    fe_checks = [
        (FE_ROOT / "lib" / "coachSessionBookingAPI.ts").exists(),
        (FE_ROOT / "components" / "leads" / "LeadCoachSessionBookingSection.tsx").exists(),
        (FE_ROOT / "app" / "coach-dashboard" / "schedule" / "page.tsx").exists(),
        (FE_ROOT / "app" / "[adminType]" / "dashboard" / "coach-session-bookings" / "page.tsx").exists(),
        "Schedule"
        in (FE_ROOT / "components" / "coach-dashboard-header.tsx").read_text(encoding="utf-8"),
        "Coach Sessions"
        in (FE_ROOT / "lib" / "dashboard-config.ts").read_text(encoding="utf-8"),
        "mySchedule"
        in (FE_ROOT / "app" / "coach-dashboard" / "schedule" / "page.tsx").read_text(
            encoding="utf-8"
        ),
        "mockEvents"
        not in (FE_ROOT / "app" / "coach-dashboard" / "schedule" / "page.tsx").read_text(
            encoding="utf-8"
        ),
    ]
    results.append(ok("FE session surfaces", all(fe_checks), str(FE_ROOT)))
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
    cid = coach["id"]
    results.append(ok("coach token", True, cid))

    suffix = uuid.uuid4().hex[:8]
    phone = f"+9190{suffix[:8]}"
    lead_ids = []
    booking_ids = []

    try:
        # Ensure weekly availability (Monday 10:00-11:00)
        http_json(
            "PUT",
            f"/api/coaches/{cid}/availability",
            body={
                "weekly_slots": [
                    {
                        "weekday": "monday",
                        "start_time": "10:00",
                        "end_time": "11:00",
                    }
                ],
                "timezone": "Asia/Kolkata",
                "notes": "QA M15-S05",
            },
            token=sa_tok,
            expect_status=200,
        )
        results.append(ok("set coach availability", True))

        # Lead → assign → accept
        st, lead = http_json(
            "POST",
            "/api/leads",
            body={
                "name": "QA Session Lead",
                "phone": phone,
                "source_type": "website_popup",
            },
            expect_status=201,
        )
        lid = lead.get("id")
        if lid:
            lead_ids.append(lid)

        st, assigned = http_json(
            "POST",
            f"/api/leads/{lid}/coach-assignment",
            body={"coach_id": cid, "note": "Book a consult"},
            token=sa_tok,
            expect_status=201,
        )
        aid = (assigned.get("assignment") or {}).get("id")
        http_json(
            "POST",
            f"/api/lead-coach-assignments/{aid}/accept",
            body={},
            token=coach_tok,
            expect_status=200,
        )
        results.append(ok("lead assigned + accepted", True, aid))

        # Slots
        mon = _next_weekday(0)  # Monday
        st, slots = http_json(
            "GET",
            f"/api/leads/{lid}/coach-session-slots?from={mon.isoformat()}&to={(mon + timedelta(days=7)).isoformat()}",
            token=sa_tok,
            expect_status=200,
        )
        slot_list = slots.get("slots") or []
        mon_slot = next(
            (
                s
                for s in slot_list
                if s.get("session_date") == mon.isoformat()
                and s.get("start_time") == "10:00"
            ),
            None,
        )
        results.append(
            ok(
                "session slots from availability",
                bool(mon_slot),
                f"n={len(slot_list)} date={mon.isoformat()}",
            )
        )

        # Book
        st, booked = http_json(
            "POST",
            f"/api/leads/{lid}/coach-session-bookings",
            body={
                "coach_id": cid,
                "session_date": mon.isoformat(),
                "start_time": "10:00",
                "end_time": "11:00",
                "notes": "Intro call",
                "source_type": "lead",
            },
            token=sa_tok,
            expect_status=201,
        )
        booking = booked.get("booking") or {}
        bid = booking.get("id")
        if bid:
            booking_ids.append(bid)
        results.append(
            ok(
                "book session",
                booking.get("status") == "scheduled" and booking.get("lead_id") == lid,
                bid,
            )
        )

        # Double-book blocked
        st_dup, dup = http_json(
            "POST",
            f"/api/leads/{lid}/coach-session-bookings",
            body={
                "coach_id": cid,
                "session_date": mon.isoformat(),
                "start_time": "10:00",
                "end_time": "11:00",
            },
            token=sa_tok,
            expect_status=None,
        )
        results.append(ok("double-book blocked", st_dup in (400, 409), f"status={st_dup}"))

        # Coach schedule
        st, sched = http_json(
            "GET",
            f"/api/coaches/me/schedule?from={mon.isoformat()}&to={mon.isoformat()}",
            token=coach_tok,
            expect_status=200,
        )
        found = any(x.get("id") == bid for x in (sched.get("bookings") or []))
        results.append(ok("coach schedule includes booking", found))

        # Confirm
        st, conf = http_json(
            "PATCH",
            f"/api/coach-session-bookings/{bid}/status",
            body={"status": "confirmed"},
            token=coach_tok,
            expect_status=200,
        )
        results.append(
            ok(
                "coach confirm",
                (conf.get("booking") or {}).get("status") == "confirmed",
            )
        )

        # Admin list
        st, listing = http_json(
            "GET",
            f"/api/coach-session-bookings?lead_id={lid}",
            token=sa_tok,
            expect_status=200,
        )
        results.append(
            ok(
                "admin list by lead",
                any(x.get("id") == bid for x in (listing.get("bookings") or [])),
            )
        )

        # Complete
        st, done = http_json(
            "PATCH",
            f"/api/coach-session-bookings/{bid}/status",
            body={"status": "completed"},
            token=coach_tok,
            expect_status=200,
        )
        results.append(
            ok("complete session", (done.get("booking") or {}).get("status") == "completed")
        )

        # Demo availability still present (regression)
        st_demo, _ = http_json(
            "GET", "/api/demo-sessions/options", expect_status=None
        )
        results.append(
            ok("demo options still reachable", st_demo in (200, 401, 403), f"status={st_demo}")
        )

    except AssertionError as exc:
        results.append(ok("workflow assertion", False, str(exc)))
    finally:
        cleaned = _cleanup(suffix[:8], lead_ids, booking_ids, cid)
        results.append(ok("cleanup", cleaned is not False))

    return all(results)


def main():
    print(f"M15-S05 Coach Session Booking QA  base={BASE}")
    r1 = test_openapi_fe()
    r2 = test_workflow()
    print(f"\nDone. {int(r1) + int(r2)}/2 passed.")
    sys.exit(0 if (r1 and r2) else 1)


if __name__ == "__main__":
    main()
