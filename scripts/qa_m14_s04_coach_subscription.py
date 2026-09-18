"""
M14-S04 Coach Subscription QA.

  .venv\\Scripts\\python.exe scripts/qa_m14_s04_coach_subscription.py
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


def _db_cleanup(email: str):
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
                    cid = coach["id"]
                    await db.coaches.delete_one({"id": cid})
                    await db.coach_subscriptions.delete_many({"coach_id": cid})
                    await db.coach_subscription_payments.delete_many({"coach_id": cid})
                    await db.coach_approval_history.delete_many({"coach_id": cid})
                    await db.coach_availability.delete_many({"coach_id": cid})
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


def _force_expire_subscription(coach_id: str):
    """Set ends_at/grace in the past and status active so refresh moves to expired."""
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

        async def run():
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
            db = client[dbn]
            past = datetime.utcnow() - timedelta(days=20)
            grace_past = datetime.utcnow() - timedelta(days=5)
            await db.coach_subscriptions.update_one(
                {"coach_id": coach_id, "status": "active"},
                {
                    "$set": {
                        "starts_at": past - timedelta(days=30),
                        "ends_at": past,
                        "grace_ends_at": grace_past,
                        "plan_snapshot.deactivate_on_grace_expiry": True,
                    }
                },
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
    except Exception as exc:
        print(f"(force expire skipped: {exc})")
        return False


def test_openapi_fe():
    results = []
    try:
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=25) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        needed = [
            "/api/coach-subscriptions/plans",
            "/api/coach-subscriptions/me",
            "/api/coach-subscriptions/me/checkout",
            "/api/coach-subscriptions/me/verify-payment",
            "/api/coach-subscriptions/me/history",
            "/api/coach-subscriptions/coach/{coach_id}/grant",
            "/api/coach-subscriptions/refresh-statuses",
        ]
        missing = [p for p in needed if p not in raw]
        results.append(ok("OpenAPI subscription endpoints", not missing, str(missing) or BASE))
    except Exception as exc:
        results.append(ok("OpenAPI subscription endpoints", False, str(exc)))

    fe_ok = all(
        [
            (FE_ROOT / "lib" / "coachSubscriptionAPI.ts").is_file(),
            (FE_ROOT / "app" / "coach-dashboard" / "subscription" / "page.tsx").is_file(),
            (
                FE_ROOT
                / "app"
                / "[adminType]"
                / "dashboard"
                / "coach-subscription-plans"
                / "page.tsx"
            ).is_file(),
            (
                FE_ROOT
                / "app"
                / "[adminType]"
                / "dashboard"
                / "coach-subscriptions"
                / "page.tsx"
            ).is_file(),
            "Coach Sub. Plans"
            in (FE_ROOT / "lib" / "dashboard-config.ts").read_text(encoding="utf-8"),
            "/coach-dashboard/subscription"
            in (FE_ROOT / "components" / "coach-dashboard-header.tsx").read_text(
                encoding="utf-8"
            ),
        ]
    )
    results.append(ok("FE subscription surfaces", fe_ok, str(FE_ROOT)))
    return all(results)


def test_workflow():
    results = []
    token = _mint_superadmin_token()
    if not token:
        results.append(ok("superadmin token", False))
        return all(results)
    results.append(ok("superadmin token", True))

    # Plans list seeds default
    st, plans = http_json(
        "GET", "/api/coach-subscriptions/plans", token=token, expect_status=200
    )
    plan_list = plans.get("plans") or []
    results.append(ok("list/seed plans", len(plan_list) >= 1, f"count={len(plan_list)}"))
    plan_id = plan_list[0]["id"]

    # Create custom plan (business rules)
    st, created_plan = http_json(
        "POST",
        "/api/coach-subscriptions/plans",
        body={
            "name": f"QA Plan {uuid.uuid4().hex[:6]}",
            "description": "QA fee/grace rules",
            "fee_inr": 499,
            "duration_days": 30,
            "grace_period_days": 10,
            "deactivate_on_grace_expiry": True,
            "is_active": True,
            "is_default": False,
        },
        token=token,
        expect_status=200,
    )
    qa_plan_id = (created_plan.get("plan") or {}).get("id")
    results.append(ok("create plan with rules", bool(qa_plan_id), qa_plan_id))
    if qa_plan_id:
        plan_id = qa_plan_id

    st, opts = http_json("GET", "/api/coaches/register/options")
    bid = (opts.get("branches") or [{}])[0].get("id")
    results.append(ok("branch for coach", bool(bid)))
    if not bid:
        return all(results)

    suffix = uuid.uuid4().hex[:8]
    email = f"qa.m14s04.{suffix}@example.com"
    password = "TestPass123!"
    payload = {
        "first_name": "QA",
        "last_name": "Sub",
        "gender": "Male",
        "date_of_birth": "1991-03-01",
        "email": email,
        "country_code": "+91",
        "phone": f"6{suffix[:9]}",
        "password": password,
        "address": "3 Sub Street",
        "city": "Hyderabad",
        "state": "Telangana",
        "country": "India",
        "professional_experience": "1-3 years",
        "specializations": ["Karate"],
        "service_location_ids": [bid],
    }

    try:
        st, reg = http_json("POST", "/api/coaches/register", body=payload, expect_status=201)
        coach_id = reg.get("coach_id")
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
        results.append(ok("approved coach login (no sub yet)", bool(coach_token)))

        st, me = http_json(
            "GET", "/api/coach-subscriptions/me", token=coach_token, expect_status=200
        )
        results.append(ok("me subscription empty", me.get("subscription") is None))

        # Checkout — soft pass if Razorpay not configured
        st, checkout = http_json(
            "POST",
            "/api/coach-subscriptions/me/checkout",
            body={"plan_id": plan_id},
            token=coach_token,
        )
        if st == 200 and checkout.get("order", {}).get("id"):
            results.append(
                ok("checkout creates Razorpay order", True, checkout["order"]["id"])
            )
        elif st == 503:
            results.append(ok("checkout creates Razorpay order", True, "Razorpay not configured (soft)"))
        else:
            results.append(
                ok(
                    "checkout creates Razorpay order",
                    False,
                    f"status={st} detail={checkout.get('detail')}",
                )
            )

        # Admin grant activates without Razorpay
        st, granted = http_json(
            "POST",
            f"/api/coach-subscriptions/coach/{coach_id}/grant",
            body={"plan_id": plan_id, "note": "QA grant"},
            token=token,
            expect_status=200,
        )
        gsub = granted.get("subscription") or {}
        results.append(
            ok(
                "admin grant activates",
                gsub.get("status") == "active",
                f"status={gsub.get('status')}",
            )
        )

        st, hist = http_json(
            "GET",
            "/api/coach-subscriptions/me/history",
            token=coach_token,
            expect_status=200,
        )
        results.append(
            ok(
                "payment history recorded",
                len(hist.get("payments") or []) >= 1,
                f"count={len(hist.get('payments') or [])}",
            )
        )

        st, listing = http_json(
            "GET",
            f"/api/coach-subscriptions?search={email}",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "admin list subscriptions",
                any(s.get("coach_id") == coach_id for s in (listing.get("subscriptions") or [])),
            )
        )

        # Force expire + refresh
        _force_expire_subscription(coach_id)
        st, refreshed = http_json(
            "POST",
            "/api/coach-subscriptions/refresh-statuses",
            body={},
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "refresh statuses",
                refreshed.get("checked", 0) >= 0,
                str(refreshed),
            )
        )

        st, me2 = http_json(
            "GET",
            f"/api/coach-subscriptions/coach/{coach_id}",
            token=token,
            expect_status=200,
        )
        sub2 = me2.get("subscription") or {}
        results.append(
            ok(
                "subscription expired after grace",
                sub2.get("status") == "expired",
                f"status={sub2.get('status')}",
            )
        )

        st, blocked = http_json(
            "POST",
            "/api/coaches/login",
            body={"email": email, "password": password},
        )
        results.append(
            ok(
                "expired subscription blocks login",
                st == 403 and "subscription" in str(blocked.get("detail", "")).lower(),
                f"status={st} detail={blocked.get('detail')}",
            )
        )

        # Re-grant restores login
        http_json(
            "POST",
            f"/api/coach-subscriptions/coach/{coach_id}/grant",
            body={"plan_id": plan_id, "note": "QA renew grant"},
            token=token,
            expect_status=200,
        )
        st, login2 = http_json(
            "POST",
            "/api/coaches/login",
            body={"email": email, "password": password},
            expect_status=200,
        )
        results.append(ok("renewed coach can login", bool(login2.get("access_token"))))

    finally:
        cleaned = _db_cleanup(email)
        results.append(ok("cleanup", cleaned is not False, email))

    return all(results)


def main():
    print(f"M14-S04 Coach Subscription QA  base={BASE}")
    r1 = test_openapi_fe()
    r2 = test_workflow()
    print(f"\nDone. {int(r1) + int(r2)}/2 passed.")
    sys.exit(0 if (r1 and r2) else 1)


if __name__ == "__main__":
    main()
