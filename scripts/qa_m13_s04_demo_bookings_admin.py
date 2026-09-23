"""
M13-S04 Demo Booking Administration QA.

  .venv\\Scripts\\python.exe scripts/qa_m13_s04_demo_bookings_admin.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
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


def _next_weekday(start: date, weekday: int) -> date:
    days_ahead = (weekday - start.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return start + timedelta(days=days_ahead)


def test_openapi_auth_fe():
    results = []
    try:
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        needed = [
            "/api/demo-bookings",
            "/api/demo-bookings/summary",
            "/api/demo-bookings/export",
            "/api/demo-bookings/{booking_id}",
            "/api/demo-bookings/{booking_id}/status",
        ]
        missing = [p for p in needed if p not in raw]
        results.append(ok("OpenAPI admin booking endpoints", not missing, str(missing) or BASE))
    except Exception as exc:
        results.append(ok("OpenAPI admin booking endpoints", False, str(exc)))

    st, _ = http_json("GET", "/api/demo-bookings?limit=5")
    results.append(ok("admin list requires auth", st in (401, 403), f"status={st}"))

    fe = Path(ROOT).parent / "rockmartialarts-fe"
    page = fe / "components" / "demo-sessions" / "DemoBookingsAdminPage.tsx"
    api = fe / "lib" / "demoBookingAdminAPI.ts"
    cfg = fe / "lib" / "dashboard-config.ts"
    route = fe / "app" / "[adminType]" / "dashboard" / "demo-bookings" / "page.tsx"
    raw_page = page.read_text(encoding="utf-8") if page.is_file() else ""
    raw_api = api.read_text(encoding="utf-8") if api.is_file() else ""
    raw_cfg = cfg.read_text(encoding="utf-8") if cfg.is_file() else ""
    checks = [
        page.is_file(),
        route.is_file(),
        "demo-bookings/export" in raw_api,
        "updateStatus" in raw_api,
        "Demo Bookings" in raw_cfg,
        "Export CSV" in raw_page,
        "branchFilter" in raw_page,
        "fromDate" in raw_page,
    ]
    results.append(ok("FE admin booking surfaces", all(checks), str(checks)))
    return all(results)


def test_admin_flow(token: str):
    results = []
    st_b, branches = http_json("GET", "/api/branches?skip=0&limit=5", token=token)
    st_c, courses = http_json("GET", "/api/courses?skip=0&limit=5", token=token)
    blist = (branches or {}).get("branches") or []
    clist = (courses or {}).get("courses") or []
    bid = (blist[0] or {}).get("id") if blist else None
    cid = (clist[0] or {}).get("id") if clist else None
    results.append(ok("branch/course ready", bool(bid and cid), f"{bid}/{cid}"))
    if not (bid and cid):
        return all(results)

    today = datetime.now(TZ).date()
    target = _next_weekday(today, 5)
    if target <= today:
        target += timedelta(days=7)

    st_s, created = http_json(
        "POST",
        "/api/demo-schedules",
        {
            "title": "S04 QA Admin",
            "branch_id": bid,
            "course_id": cid,
            "recurrence": "weekly",
            "weekdays": ["saturday"],
            "start_time": "17:00",
            "end_time": "18:00",
            "capacity": 5,
            "fee_inr": 0,
            "effective_from": today.isoformat(),
        },
        token=token,
    )
    sid = ((created or {}).get("schedule") or {}).get("id")
    results.append(ok("seed schedule", st_s == 201 and bool(sid), sid))

    st_book, booked = http_json(
        "POST",
        "/api/demo-sessions/bookings",
        {
            "schedule_id": sid,
            "slot_date": target.isoformat(),
            "start_time": "17:00",
            "participant_name": "S04 Admin Tester",
            "participant_phone": "9777766666",
        },
    )
    booking = (booked or {}).get("booking") or {}
    booking_id = booking.get("id")
    results.append(
        ok(
            "seed confirmed booking",
            st_book == 201 and booking.get("status") == "confirmed" and bool(booking_id),
            booking_id,
        )
    )

    st_list, listed = http_json(
        "GET",
        f"/api/demo-bookings?branch_id={bid}&course_id={cid}&from={target.isoformat()}&to={target.isoformat()}&search=S04&limit=20",
        token=token,
    )
    ids = {b.get("id") for b in (listed.get("bookings") or [])}
    results.append(
        ok(
            "admin list filters branch/date/course/search",
            st_list == 200 and booking_id in ids,
            f"total={listed.get('total')}",
        )
    )

    st_sum, summary = http_json(
        "GET", f"/api/demo-bookings/summary?branch_id={bid}", token=token
    )
    results.append(
        ok(
            "admin summary",
            st_sum == 200
            and isinstance(summary.get("by_status"), dict)
            and int(summary.get("total") or 0) >= 1,
            f"total={summary.get('total')}",
        )
    )

    st_exp, exported = http_json(
        "GET",
        f"/api/demo-bookings/export?branch_id={bid}&search=S04",
        token=token,
    )
    content = exported.get("content") or ""
    results.append(
        ok(
            "export csv includes booking",
            st_exp == 200
            and exported.get("content_type") == "text/csv"
            and "S04 Admin Tester" in content
            and "participant_name" in content,
            exported.get("filename"),
        )
    )

    if booking_id:
        st_st, updated = http_json(
            "PATCH",
            f"/api/demo-bookings/{booking_id}/status",
            {"status": "cancelled", "note": "S04 QA cancel"},
            token=token,
        )
        ub = (updated or {}).get("booking") or {}
        results.append(
            ok(
                "admin status cancel",
                st_st == 200 and ub.get("status") == "cancelled",
                ub.get("status"),
            )
        )

        st_bad, _ = http_json(
            "PATCH",
            f"/api/demo-bookings/{booking_id}/status",
            {"status": "confirmed"},
            token=token,
        )
        results.append(
            ok(
                "invalid status transition rejected",
                st_bad == 400,
                f"status={st_bad}",
            )
        )

    # BM outside branch should 403 when filtering unmanaged branch
    # (soft-check: unknown branch id as BM would need BM token — skip if only SA)
    st_out, out = http_json(
        "GET",
        "/api/demo-bookings?branch_id=not-a-managed-branch-id",
        token=token,
    )
    # Superadmin can query any branch id (may return empty)
    results.append(
        ok(
            "superadmin branch filter allowed",
            st_out == 200,
            f"status={st_out} total={out.get('total')}",
        )
    )

    if sid:
        http_json("DELETE", f"/api/demo-schedules/{sid}?hard=true", token=token)

    return all(results)


def main():
    print(f"M13-S04 Demo Bookings Admin QA  base={BASE}")
    results = [test_openapi_auth_fe()]
    token = _mint_superadmin_token()
    if not token:
        results.append(ok("admin workflow", False, "no admin token"))
    else:
        results.append(test_admin_flow(token))
    failed = sum(1 for r in results if not r)
    print(f"\nDone. {len(results) - failed}/{len(results)} passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
