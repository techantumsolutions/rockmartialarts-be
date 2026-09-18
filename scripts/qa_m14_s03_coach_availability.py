"""
M14-S03 Coach Availability QA.

  .venv\\Scripts\\python.exe scripts/qa_m14_s03_coach_availability.py
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


def _cleanup(email: str):
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
            for name in (dbn, "marshalats", "rockmartialarts", "rock_martial_arts"):
                db = client[name]
                coach = await db.coaches.find_one({"email": email})
                if coach:
                    cid = coach.get("id")
                    await db.coaches.delete_one({"id": cid})
                    await db.coach_availability.delete_many({"coach_id": cid})
                    await db.coach_approval_history.delete_many({"coach_id": cid})
                    return True
            return False

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
            "/api/coaches/me/availability",
            "/api/coaches/me/availability/options",
            "/api/coaches/{coach_id}/availability",
            "/api/coaches/{coach_id}/availability/options",
        ]
        missing = [p for p in needed if p not in raw]
        results.append(ok("OpenAPI availability endpoints", not missing, str(missing) or BASE))
    except Exception as exc:
        results.append(ok("OpenAPI availability endpoints", False, str(exc)))

    fe_checks = [
        (FE_ROOT / "lib" / "coachAvailabilityAPI.ts").is_file(),
        (FE_ROOT / "components" / "coaches" / "CoachAvailabilityEditor.tsx").is_file(),
        (FE_ROOT / "app" / "coach-dashboard" / "availability" / "page.tsx").is_file(),
        (FE_ROOT / "app" / "[adminType]" / "dashboard" / "coach-availability" / "page.tsx").is_file(),
        "Coach Availability" in (FE_ROOT / "lib" / "dashboard-config.ts").read_text(encoding="utf-8"),
        "Availability" in (FE_ROOT / "components" / "coach-dashboard-header.tsx").read_text(
            encoding="utf-8"
        ),
        "/coach-dashboard/availability"
        in (FE_ROOT / "components" / "coach-dashboard-header.tsx").read_text(encoding="utf-8"),
    ]
    results.append(ok("FE availability surfaces", all(fe_checks), str(FE_ROOT)))
    return all(results)


def test_workflow():
    results = []
    token = _mint_superadmin_token()
    if not token:
        results.append(ok("superadmin token", False, "could not mint"))
        return all(results)
    results.append(ok("superadmin token", True))

    st, opts = http_json("GET", "/api/coaches/register/options")
    branches = opts.get("branches") or []
    if len(branches) < 1:
        results.append(ok("branch available", False))
        return all(results)
    bid = branches[0]["id"]
    bid2 = branches[1]["id"] if len(branches) > 1 else bid
    results.append(ok("branch available", True, bid))

    suffix = uuid.uuid4().hex[:8]
    email = f"qa.m14s03.{suffix}@example.com"
    password = "TestPass123!"
    payload = {
        "first_name": "QA",
        "last_name": "Avail",
        "gender": "Female",
        "date_of_birth": "1992-05-01",
        "email": email,
        "country_code": "+91",
        "phone": f"7{suffix[:9]}",
        "password": password,
        "address": "2 Avail Street",
        "area": "Area",
        "city": "Hyderabad",
        "state": "Telangana",
        "zip_code": "500001",
        "country": "India",
        "professional_experience": "1-3 years",
        "specializations": ["Karate"],
        "service_location_ids": [bid],
    }

    try:
        st, created = http_json("POST", "/api/coaches/register", body=payload, expect_status=201)
        coach_id = created.get("coach_id")
        results.append(ok("register coach", bool(coach_id), coach_id))

        # Approve so coach can set availability
        http_json(
            "POST",
            f"/api/coaches/{coach_id}/approve",
            body={"note": "QA"},
            token=token,
            expect_status=200,
        )

        st, login = http_json(
            "POST",
            "/api/coaches/login",
            body={"email": email, "password": password},
            expect_status=200,
        )
        coach_token = login.get("access_token")
        results.append(ok("coach login", bool(coach_token)))

        st, empty = http_json(
            "GET",
            "/api/coaches/me/availability",
            token=coach_token,
            expect_status=200,
        )
        results.append(
            ok(
                "get empty availability",
                empty.get("coach_id") == coach_id,
                f"slots={len(empty.get('weekly_slots') or [])}",
            )
        )

        st, loc_opts = http_json(
            "GET",
            "/api/coaches/me/availability/options",
            token=coach_token,
            expect_status=200,
        )
        results.append(
            ok(
                "availability options",
                bool(loc_opts.get("locations")) and bool(loc_opts.get("weekdays")),
                f"locs={len(loc_opts.get('locations') or [])}",
            )
        )

        # Reject invalid end before start
        st, bad = http_json(
            "PUT",
            "/api/coaches/me/availability",
            body={
                "service_location_ids": [bid],
                "weekly_slots": [
                    {
                        "weekday": "monday",
                        "start_time": "14:00",
                        "end_time": "10:00",
                        "service_location_id": bid,
                    }
                ],
            },
            token=coach_token,
        )
        results.append(ok("reject invalid time range", st == 422, f"status={st}"))

        # Reject overlap
        st, overlap = http_json(
            "PUT",
            "/api/coaches/me/availability",
            body={
                "service_location_ids": [bid],
                "weekly_slots": [
                    {
                        "weekday": "tuesday",
                        "start_time": "09:00",
                        "end_time": "12:00",
                        "service_location_id": bid,
                    },
                    {
                        "weekday": "tuesday",
                        "start_time": "11:00",
                        "end_time": "14:00",
                        "service_location_id": bid,
                    },
                ],
            },
            token=coach_token,
        )
        results.append(
            ok(
                "reject overlapping slots",
                st == 400 and "overlap" in str(overlap.get("detail", "")).lower(),
                f"status={st} detail={overlap.get('detail')}",
            )
        )

        # Valid update with weekly slots + locations
        body = {
            "timezone": "Asia/Kolkata",
            "service_location_ids": [bid] if bid == bid2 else [bid, bid2],
            "weekly_slots": [
                {
                    "weekday": "monday",
                    "start_time": "09:00",
                    "end_time": "12:00",
                    "service_location_id": bid,
                },
                {
                    "weekday": "wednesday",
                    "start_time": "16:00",
                    "end_time": "18:30",
                    "service_location_id": bid,
                },
                {
                    "weekday": "saturday",
                    "start_time": "08:00",
                    "end_time": "11:00",
                },
            ],
            "notes": "QA weekly availability",
        }
        # If coach only has bid in profile, using bid2 may fail — only use allowed
        body["service_location_ids"] = [bid]
        for s in body["weekly_slots"]:
            if s.get("service_location_id") and s["service_location_id"] != bid:
                s["service_location_id"] = bid

        st, updated = http_json(
            "PUT",
            "/api/coaches/me/availability",
            body=body,
            token=coach_token,
            expect_status=200,
        )
        avail = updated.get("availability") or {}
        results.append(
            ok(
                "update weekly availability",
                len(avail.get("weekly_slots") or []) == 3
                and avail.get("service_location_ids") == [bid],
                f"slots={len(avail.get('weekly_slots') or [])}",
            )
        )

        # Admin can read/update
        st, admin_get = http_json(
            "GET",
            f"/api/coaches/{coach_id}/availability",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "admin get availability",
                len(admin_get.get("weekly_slots") or []) == 3,
            )
        )

        st, admin_put = http_json(
            "PUT",
            f"/api/coaches/{coach_id}/availability",
            body={
                "service_location_ids": [bid],
                "weekly_slots": [
                    {
                        "weekday": "friday",
                        "start_time": "10:00",
                        "end_time": "13:00",
                        "service_location_id": bid,
                    }
                ],
                "notes": "Admin update",
            },
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "admin update availability",
                len((admin_put.get("availability") or {}).get("weekly_slots") or []) == 1,
            )
        )

        # Unauth blocked
        st, unauth = http_json("GET", "/api/coaches/me/availability")
        results.append(ok("unauth blocked", st in (401, 403), f"status={st}"))

    finally:
        cleaned = _cleanup(email)
        results.append(ok("cleanup", cleaned is not False, email))

    return all(results)


def main():
    print(f"M14-S03 Coach Availability QA  base={BASE}")
    r1 = test_openapi_fe()
    r2 = test_workflow()
    print(f"\nDone. {int(r1) + int(r2)}/2 passed.")
    sys.exit(0 if (r1 and r2) else 1)


if __name__ == "__main__":
    main()
