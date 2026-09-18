"""
M13-S01 Demo Schedule Configuration QA.

  .venv\\Scripts\\python.exe scripts/qa_m13_s01_demo_schedules.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = (
    os.getenv("STUDENT_QA_BASE")
    or os.getenv("BILLING_QA_BASE")
    or os.getenv("INVOICE_QA_BASE")
    or "http://127.0.0.1:8003"
).rstrip("/")


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


def test_openapi():
    try:
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=8) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        needed = [
            "/api/demo-schedules",
            "/api/demo-schedules/{schedule_id}",
        ]
        missing = [p for p in needed if p not in raw]
        return ok("OpenAPI demo schedule endpoints", not missing, str(missing) or BASE)
    except Exception as exc:
        return ok("OpenAPI demo schedule endpoints", False, str(exc))


def test_auth_guard():
    st, _ = http_json("GET", "/api/demo-schedules?limit=5")
    return ok("list requires auth", st in (401, 403), f"status={st}")


def test_fe_files():
    fe = Path(ROOT).parent / "rockmartialarts-fe"
    page = fe / "components" / "demo-sessions" / "DemoSchedulesAdminPage.tsx"
    api = fe / "lib" / "demoScheduleAPI.ts"
    cfg = fe / "lib" / "dashboard-config.ts"
    route = fe / "app" / "[adminType]" / "dashboard" / "demo-schedules" / "page.tsx"
    raw_page = page.read_text(encoding="utf-8") if page.is_file() else ""
    raw_api = api.read_text(encoding="utf-8") if api.is_file() else ""
    raw_cfg = cfg.read_text(encoding="utf-8") if cfg.is_file() else ""
    checks = [
        page.is_file(),
        route.is_file(),
        "DEFAULT_DEMO_FEE_INR" in raw_api,
        "demo-schedules" in raw_api,
        "Demo Schedules" in raw_cfg,
        "recurrence" in raw_page,
        "weekdays" in raw_page,
        "capacity" in raw_page,
    ]
    return ok("FE demo schedule surfaces", all(checks), str(checks))


def _pick_branch_course(token: str):
    st_b, branches = http_json("GET", "/api/branches?skip=0&limit=5", token=token)
    st_c, courses = http_json("GET", "/api/courses?skip=0&limit=5", token=token)
    blist = (branches or {}).get("branches") or []
    clist = (courses or {}).get("courses") or []
    if not isinstance(blist, list):
        blist = []
    if not isinstance(clist, list):
        clist = []
    branch = blist[0] if blist else None
    course = clist[0] if clist else None
    bid = (branch or {}).get("id")
    cid = (course or {}).get("id")
    return bid, cid, st_b == 200 and st_c == 200 and bool(bid and cid)


def test_crud_and_rules(token: str):
    results = []
    bid, cid, ready = _pick_branch_course(token)
    results.append(ok("branch + course available", ready, f"branch={bid} course={cid}"))
    if not ready:
        return all(results)

    # Invalid timing
    st_bad, bad = http_json(
        "POST",
        "/api/demo-schedules",
        {
            "branch_id": bid,
            "course_id": cid,
            "recurrence": "weekly",
            "weekdays": ["monday"],
            "start_time": "11:00",
            "end_time": "10:00",
            "fee_inr": 300,
        },
        token=token,
    )
    results.append(
        ok(
            "reject end_time before start_time",
            st_bad == 422,
            f"status={st_bad} detail={bad.get('detail')}",
        )
    )

    # Weekly without weekdays
    st_wd, wd = http_json(
        "POST",
        "/api/demo-schedules",
        {
            "branch_id": bid,
            "course_id": cid,
            "recurrence": "weekly",
            "weekdays": [],
            "start_time": "09:00",
            "end_time": "10:00",
        },
        token=token,
    )
    results.append(
        ok(
            "reject weekly without weekdays",
            st_wd == 422,
            f"status={st_wd}",
        )
    )

    # Create weekly with default fee
    st1, created = http_json(
        "POST",
        "/api/demo-schedules",
        {
            "title": "S01 QA Weekly",
            "branch_id": bid,
            "course_id": cid,
            "recurrence": "weekly",
            "weekdays": ["saturday", "sunday"],
            "start_time": "10:00",
            "end_time": "11:00",
            "capacity": 8,
            "fee_inr": 300,
        },
        token=token,
        expect_status=201,
    )
    sched = (created or {}).get("schedule") or {}
    sid = sched.get("id")
    results.append(
        ok(
            "create weekly schedule fee 300",
            st1 == 201
            and sid
            and float(sched.get("fee_inr") or 0) == 300
            and sched.get("capacity") == 8
            and set(sched.get("weekdays") or []) == {"saturday", "sunday"},
            f"id={sid} fee={sched.get('fee_inr')}",
        )
    )

    # Overlap same branch/course/day/time
    st_ov, ov = http_json(
        "POST",
        "/api/demo-schedules",
        {
            "branch_id": bid,
            "course_id": cid,
            "recurrence": "weekly",
            "weekdays": ["saturday"],
            "start_time": "10:30",
            "end_time": "11:30",
            "fee_inr": 300,
        },
        token=token,
    )
    results.append(
        ok(
            "reject overlapping schedule",
            st_ov == 409,
            f"status={st_ov} detail={ov.get('detail')}",
        )
    )

    # Non-overlapping different day OK
    st2, created2 = http_json(
        "POST",
        "/api/demo-schedules",
        {
            "title": "S01 QA Mon",
            "branch_id": bid,
            "course_id": cid,
            "recurrence": "weekly",
            "weekdays": ["monday"],
            "start_time": "10:00",
            "end_time": "11:00",
            "capacity": 5,
        },
        token=token,
    )
    sid2 = ((created2 or {}).get("schedule") or {}).get("id")
    results.append(ok("create non-overlapping weekday", st2 == 201 and bool(sid2), sid2))

    # Daily recurrence expands weekdays
    st3, created3 = http_json(
        "POST",
        "/api/demo-schedules",
        {
            "title": "S01 QA Daily",
            "branch_id": bid,
            "course_id": cid,
            "recurrence": "daily",
            "start_time": "18:00",
            "end_time": "19:00",
            "fee_inr": 250,
        },
        token=token,
    )
    s3 = (created3 or {}).get("schedule") or {}
    sid3 = s3.get("id")
    results.append(
        ok(
            "create daily schedule",
            st3 == 201
            and sid3
            and s3.get("recurrence") == "daily"
            and len(s3.get("weekdays") or []) == 7
            and float(s3.get("fee_inr") or 0) == 250,
            f"id={sid3} days={len(s3.get('weekdays') or [])}",
        )
    )

    # List + filter
    st_l, listed = http_json(
        "GET",
        f"/api/demo-schedules?branch_id={bid}&course_id={cid}&search=S01&limit=50",
        token=token,
    )
    results.append(
        ok(
            "list filters by branch/course/search",
            st_l == 200 and int(listed.get("total") or 0) >= 2,
            f"total={listed.get('total')}",
        )
    )

    # Update
    if sid:
        st_u, updated = http_json(
            "PATCH",
            f"/api/demo-schedules/{sid}",
            {"capacity": 12, "fee_inr": 350},
            token=token,
        )
        us = (updated or {}).get("schedule") or {}
        results.append(
            ok(
                "update capacity and fee",
                st_u == 200 and us.get("capacity") == 12 and float(us.get("fee_inr") or 0) == 350,
                str(us.get("capacity")),
            )
        )

        # Soft delete
        st_d, deleted = http_json(
            "DELETE", f"/api/demo-schedules/{sid}", token=token
        )
        ds = (deleted or {}).get("schedule") or {}
        results.append(
            ok(
                "soft deactivate schedule",
                st_d == 200 and ds.get("is_active") is False,
                str(ds.get("is_active")),
            )
        )

    # Cleanup hard delete leftovers
    for cleanup_id in (sid, sid2, sid3):
        if cleanup_id:
            http_json(
                "DELETE",
                f"/api/demo-schedules/{cleanup_id}?hard=true",
                token=token,
            )

    return all(results)


def main():
    print(f"M13-S01 Demo Schedules QA  base={BASE}")
    results = [test_openapi(), test_auth_guard(), test_fe_files()]
    token = _mint_superadmin_token()
    if not token:
        results.append(ok("admin CRUD workflow", False, "no admin token"))
    else:
        results.append(test_crud_and_rules(token))
    failed = sum(1 for r in results if not r)
    print(f"\nDone. {len(results) - failed}/{len(results)} passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
