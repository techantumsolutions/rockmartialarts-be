"""
M13-S02 Demo Slot Availability QA.

  .venv\\Scripts\\python.exe scripts/qa_m13_s02_demo_availability.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = (
    os.getenv("STUDENT_QA_BASE")
    or os.getenv("BILLING_QA_BASE")
    or os.getenv("INVOICE_QA_BASE")
    or "http://127.0.0.1:8003"
).rstrip("/")
TZ = ZoneInfo("Asia/Kolkata")


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
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
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
                    return sa, name
            return None, None

        try:
            sa, _ = asyncio.run(run())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                sa, _ = loop.run_until_complete(run())
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


def _next_weekday(start: date, weekday: int) -> date:
    """weekday: Mon=0 … Sun=6"""
    days_ahead = (weekday - start.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return start + timedelta(days=days_ahead)


def test_openapi_and_public():
    results = []
    try:
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        needed = [
            "/api/demo-sessions/availability",
            "/api/demo-sessions/options",
        ]
        missing = [p for p in needed if p not in raw]
        results.append(
            ok("OpenAPI availability endpoints", not missing, str(missing) or BASE)
        )
    except Exception as exc:
        results.append(ok("OpenAPI availability endpoints", False, str(exc)))

    st, _ = http_json("GET", "/api/demo-sessions/availability")
    results.append(ok("availability is public", st == 200, f"status={st}"))

    st2, opts = http_json("GET", "/api/demo-sessions/options")
    results.append(
        ok(
            "options is public",
            st2 == 200
            and isinstance(opts.get("branches"), list)
            and isinstance(opts.get("courses"), list),
            f"status={st2}",
        )
    )
    return all(results)


def test_fe_files():
    fe = Path(ROOT).parent / "rockmartialarts-fe"
    page = fe / "app" / "(website)" / "book-demo" / "page.tsx"
    view = fe / "components" / "demo-sessions" / "DemoAvailabilityPublicView.tsx"
    api = fe / "lib" / "demoSessionAPI.ts"
    nav = fe / "components" / "FixedTopNav.tsx"
    checks = [
        page.is_file(),
        view.is_file(),
        "demo-sessions/availability" in (api.read_text(encoding="utf-8") if api.is_file() else ""),
        "/book-demo" in (nav.read_text(encoding="utf-8") if nav.is_file() else ""),
        "Find a demo slot" in (view.read_text(encoding="utf-8") if view.is_file() else ""),
    ]
    return ok("FE availability surfaces", all(checks), str(checks))


def test_unit_generation():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from utils.demo_availability_service import generate_slots_from_schedules

    today = datetime.now(TZ).date()
    # Pick a weekday 3+ days ahead so it isn't "past" for morning slots
    target = today + timedelta(days=3)
    day_name = (
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    )[target.weekday()]

    sched = {
        "id": "sched-unit-1",
        "is_active": True,
        "weekdays": [day_name],
        "start_time": "10:00",
        "end_time": "11:00",
        "branch_id": "b1",
        "branch_name": "B1",
        "course_id": "c1",
        "course_name": "C1",
        "fee_inr": 300,
        "capacity": 2,
        "recurrence": "weekly",
    }
    slots = generate_slots_from_schedules(
        [sched],
        date_from=today,
        date_to=today + timedelta(days=10),
        booking_counts={},
        now=datetime.now(TZ),
    )
    matching = [s for s in slots if s["slot_date"] == target.isoformat()]
    r1 = ok("unit generates future weekday slot", len(matching) == 1, str(len(matching)))

    # Past slot excluded
    past_day = today
    past_name = (
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    )[past_day.weekday()]
    past_sched = {
        **sched,
        "id": "sched-past",
        "weekdays": [past_name],
        "start_time": "00:05",
        "end_time": "00:30",
    }
    past_slots = generate_slots_from_schedules(
        [past_sched],
        date_from=today,
        date_to=today,
        booking_counts={},
        now=datetime.now(TZ).replace(hour=12, minute=0, second=0, microsecond=0),
    )
    r2 = ok("unit excludes past-time slot today", len(past_slots) == 0, str(past_slots))

    # Full capacity excluded
    full_slots = generate_slots_from_schedules(
        [sched],
        date_from=today,
        date_to=today + timedelta(days=10),
        booking_counts={(sched["id"], target.isoformat()): 2},
        now=datetime.now(TZ),
        include_full=False,
    )
    still = [s for s in full_slots if s["slot_date"] == target.isoformat()]
    r3 = ok("unit excludes fully booked slot", len(still) == 0, str(len(still)))

    return r1 and r2 and r3


def test_live_flow(token: str):
    results = []
    st_b, branches = http_json("GET", "/api/branches?skip=0&limit=5", token=token)
    st_c, courses = http_json("GET", "/api/courses?skip=0&limit=5", token=token)
    blist = (branches or {}).get("branches") or []
    clist = (courses or {}).get("courses") or []
    bid = (blist[0] or {}).get("id") if blist else None
    cid = (clist[0] or {}).get("id") if clist else None
    results.append(ok("seed branch/course", bool(bid and cid), f"{bid}/{cid}"))
    if not (bid and cid):
        return all(results)

    today = datetime.now(TZ).date()
    # Use Saturday slots far enough ahead
    target = _next_weekday(today, 5)  # Saturday
    if target <= today:
        target = target + timedelta(days=7)

    st_create, created = http_json(
        "POST",
        "/api/demo-schedules",
        {
            "title": "S02 QA Avail",
            "branch_id": bid,
            "course_id": cid,
            "recurrence": "weekly",
            "weekdays": ["saturday"],
            "start_time": "15:00",
            "end_time": "16:00",
            "capacity": 1,
            "fee_inr": 300,
            "effective_from": today.isoformat(),
        },
        token=token,
    )
    sched = (created or {}).get("schedule") or {}
    sid = sched.get("id")
    results.append(ok("create schedule for availability", st_create == 201 and bool(sid), sid))

    # Wrong course filter → empty
    st_f, filtered = http_json(
        "GET",
        f"/api/demo-sessions/availability?branch_id={bid}&course_id=not-a-real-course&from={today.isoformat()}&to={(today + timedelta(days=20)).isoformat()}",
    )
    results.append(
        ok(
            "branch/course filter excludes mismatch",
            st_f == 200 and int(filtered.get("count") or 0) == 0,
            f"count={filtered.get('count')}",
        )
    )

    # Matching filter includes our Saturday
    st_ok, avail = http_json(
        "GET",
        f"/api/demo-sessions/availability?branch_id={bid}&course_id={cid}&from={today.isoformat()}&to={(today + timedelta(days=20)).isoformat()}",
    )
    slots = avail.get("slots") or []
    ours = [
        s
        for s in slots
        if s.get("schedule_id") == sid and s.get("slot_date") == target.isoformat()
    ]
    results.append(
        ok(
            "availability returns generated Saturday slot",
            st_ok == 200 and len(ours) == 1 and float(ours[0].get("fee_inr") or 0) == 300,
            f"found={len(ours)} total={avail.get('count')}",
        )
    )

    # Insert held booking to fill capacity=1
    if sid:
        try:
            from dotenv import load_dotenv
            import asyncio
            from motor.motor_asyncio import AsyncIOMotorClient

            load_dotenv(Path(ROOT) / ".env")
            uri = (
                os.getenv("MONGO_URI")
                or os.getenv("MONGO_URL")
                or os.getenv("MONGODB_URL")
                or os.getenv("DATABASE_URL")
            )
            dbn = os.getenv("DB_NAME") or "marshalats"

            async def insert_booking():
                client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
                booking_id = str(uuid.uuid4())
                doc = {
                    "id": booking_id,
                    "schedule_id": sid,
                    "slot_date": target.isoformat(),
                    "start_time": "15:00",
                    "end_time": "16:00",
                    "status": "pending_payment",
                    "branch_id": bid,
                    "course_id": cid,
                }
                used_db = None
                for name in (dbn, "marshalats", "rockmartialarts", "rock_martial_arts"):
                    found = await client[name][ "demo_schedules"].find_one({"id": sid})
                    if found:
                        await client[name].demo_bookings.insert_one(doc)
                        used_db = name
                        break
                if not used_db:
                    # fallback
                    await client[dbn].demo_bookings.insert_one(doc)
                    used_db = dbn
                return booking_id, used_db

            booking_id, used_db = asyncio.run(insert_booking())
            st_full, full = http_json(
                "GET",
                f"/api/demo-sessions/availability?branch_id={bid}&course_id={cid}&from={today.isoformat()}&to={(today + timedelta(days=20)).isoformat()}",
            )
            ours_full = [
                s
                for s in (full.get("slots") or [])
                if s.get("schedule_id") == sid and s.get("slot_date") == target.isoformat()
            ]
            results.append(
                ok(
                    "capacity excludes fully booked slot",
                    st_full == 200 and len(ours_full) == 0,
                    f"found={len(ours_full)} db={used_db}",
                )
            )

            st_inc, inc = http_json(
                "GET",
                f"/api/demo-sessions/availability?branch_id={bid}&course_id={cid}&from={today.isoformat()}&to={(today + timedelta(days=20)).isoformat()}&include_full=true",
            )
            ours_inc = [
                s
                for s in (inc.get("slots") or [])
                if s.get("schedule_id") == sid and s.get("slot_date") == target.isoformat()
            ]
            results.append(
                ok(
                    "include_full returns full slot",
                    st_inc == 200
                    and len(ours_inc) == 1
                    and ours_inc[0].get("is_full") is True,
                    str(ours_inc[0].get("remaining") if ours_inc else None),
                )
            )

            async def cleanup_booking():
                client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
                await client[used_db].demo_bookings.delete_one({"id": booking_id})

            asyncio.run(cleanup_booking())
        except Exception as exc:
            results.append(ok("capacity excludes fully booked slot", False, str(exc)))

    # Past date clamp: from in the past should still return 200 and from>=today
    past = (today - timedelta(days=5)).isoformat()
    st_p, past_resp = http_json(
        "GET",
        f"/api/demo-sessions/availability?from={past}&to={(today + timedelta(days=3)).isoformat()}",
    )
    results.append(
        ok(
            "past from-date clamped to today",
            st_p == 200 and past_resp.get("from") == today.isoformat(),
            f"from={past_resp.get('from')}",
        )
    )

    if sid:
        http_json("DELETE", f"/api/demo-schedules/{sid}?hard=true", token=token)

    return all(results)


def main():
    print(f"M13-S02 Demo Availability QA  base={BASE}")
    results = [
        test_openapi_and_public(),
        test_fe_files(),
        test_unit_generation(),
    ]
    token = _mint_superadmin_token()
    if not token:
        results.append(ok("live availability flow", False, "no admin token"))
    else:
        results.append(test_live_flow(token))
    failed = sum(1 for r in results if not r)
    print(f"\nDone. {len(results) - failed}/{len(results)} passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
