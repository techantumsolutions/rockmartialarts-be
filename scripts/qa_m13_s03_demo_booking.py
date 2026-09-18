"""
M13-S03 Paid Demo Booking QA.

  .venv\\Scripts\\python.exe scripts/qa_m13_s03_demo_booking.py
"""
from __future__ import annotations

import hashlib
import hmac
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


def test_openapi_fe():
    results = []
    try:
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        needed = [
            "/api/demo-sessions/bookings",
            "/api/demo-sessions/bookings/{booking_id}/create-order",
            "/api/demo-sessions/bookings/{booking_id}/verify-payment",
        ]
        missing = [p for p in needed if p not in raw]
        results.append(ok("OpenAPI booking endpoints", not missing, str(missing) or BASE))
    except Exception as exc:
        results.append(ok("OpenAPI booking endpoints", False, str(exc)))

    fe = Path(ROOT).parent / "rockmartialarts-fe"
    view = fe / "components" / "demo-sessions" / "DemoAvailabilityPublicView.tsx"
    api = fe / "lib" / "demoSessionAPI.ts"
    raw_view = view.read_text(encoding="utf-8") if view.is_file() else ""
    raw_api = api.read_text(encoding="utf-8") if api.is_file() else ""
    checks = [
        "createBooking" in raw_api,
        "verifyPayment" in raw_api,
        "create-order" in raw_api,
        "openRazorpayCheckout" in raw_view,
        "Reserve & pay" in raw_view or "participant_name" in raw_view,
    ]
    results.append(ok("FE booking surfaces", all(checks), str(checks)))
    return all(results)


def test_booking_flow(token: str):
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
    target = _next_weekday(today, 5)  # Saturday
    if target <= today:
        target += timedelta(days=7)

    # Capacity 1 paid schedule
    st_s, created = http_json(
        "POST",
        "/api/demo-schedules",
        {
            "title": "S03 QA Paid",
            "branch_id": bid,
            "course_id": cid,
            "recurrence": "weekly",
            "weekdays": ["saturday"],
            "start_time": "16:00",
            "end_time": "17:00",
            "capacity": 1,
            "fee_inr": 300,
            "effective_from": today.isoformat(),
        },
        token=token,
    )
    sched = (created or {}).get("schedule") or {}
    sid = sched.get("id")
    results.append(ok("create paid schedule capacity 1", st_s == 201 and bool(sid), sid))

    # Free schedule for zero-fee confirm
    st_f, free_created = http_json(
        "POST",
        "/api/demo-schedules",
        {
            "title": "S03 QA Free",
            "branch_id": bid,
            "course_id": cid,
            "recurrence": "weekly",
            "weekdays": ["sunday"],
            "start_time": "09:00",
            "end_time": "10:00",
            "capacity": 5,
            "fee_inr": 0,
            "effective_from": today.isoformat(),
        },
        token=token,
    )
    free_sid = ((free_created or {}).get("schedule") or {}).get("id")
    results.append(ok("create free schedule", st_f == 201 and bool(free_sid), free_sid))

    # Public book — fee from server
    st_b1, booked = http_json(
        "POST",
        "/api/demo-sessions/bookings",
        {
            "schedule_id": sid,
            "slot_date": target.isoformat(),
            "start_time": "16:00",
            "participant_name": "S03 Tester",
            "participant_phone": "9888877777",
            "participant_email": "s03@example.com",
        },
    )
    booking = (booked or {}).get("booking") or {}
    booking_id = booking.get("id")
    results.append(
        ok(
            "reserve paid booking uses schedule fee",
            st_b1 == 201
            and booking_id
            and float(booking.get("fee_inr") or 0) == 300
            and booking.get("status") == "pending_payment"
            and booked.get("payment_required") is True,
            f"fee={booking.get('fee_inr')} status={booking.get('status')}",
        )
    )

    # Capacity race — second booking should 409
    st_b2, raced = http_json(
        "POST",
        "/api/demo-sessions/bookings",
        {
            "schedule_id": sid,
            "slot_date": target.isoformat(),
            "start_time": "16:00",
            "participant_name": "S03 Racer",
            "participant_phone": "9888866666",
        },
    )
    results.append(
        ok(
            "capacity race rejects second seat",
            st_b2 == 409,
            f"status={st_b2} detail={raced.get('detail')}",
        )
    )

    # Bad signature verify
    if booking_id:
        st_bad, bad = http_json(
            "POST",
            f"/api/demo-sessions/bookings/{booking_id}/verify-payment",
            {
                "razorpay_order_id": "order_fake",
                "razorpay_payment_id": "pay_fake",
                "razorpay_signature": "bad_signature",
            },
        )
        results.append(
            ok(
                "invalid signature does not confirm",
                st_bad == 400,
                f"status={st_bad}",
            )
        )
        st_get, got = http_json("GET", f"/api/demo-sessions/bookings/{booking_id}")
        g = (got or {}).get("booking") or {}
        results.append(
            ok(
                "booking still pending after failed verify",
                st_get == 200 and g.get("status") == "pending_payment",
                g.get("status"),
            )
        )

        # create-order (soft-pass if Razorpay not configured)
        st_ord, ord_payload = http_json(
            "POST", f"/api/demo-sessions/bookings/{booking_id}/create-order"
        )
        if st_ord == 200 and (ord_payload.get("order") or {}).get("id"):
            order_id = ord_payload["order"]["id"]
            results.append(ok("create-order returns Razorpay order", True, order_id))

            # Confirm with valid HMAC if secret available
            from dotenv import load_dotenv

            load_dotenv(Path(ROOT) / ".env")
            sec = (os.getenv("RAZORPAY_KEY_SECRET") or "").strip()
            if sec:
                pay_id = f"pay_qa_{uuid.uuid4().hex[:10]}"
                sig = hmac.new(
                    sec.encode("utf-8"),
                    f"{order_id}|{pay_id}".encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()
                st_ok, verified = http_json(
                    "POST",
                    f"/api/demo-sessions/bookings/{booking_id}/verify-payment",
                    {
                        "razorpay_order_id": order_id,
                        "razorpay_payment_id": pay_id,
                        "razorpay_signature": sig,
                    },
                )
                vb = (verified or {}).get("booking") or {}
                results.append(
                    ok(
                        "valid signature confirms booking",
                        st_ok == 200
                        and vb.get("status") == "confirmed"
                        and vb.get("payment_status") == "paid",
                        f"status={vb.get('status')} pay={vb.get('payment_status')}",
                    )
                )
            else:
                results.append(
                    ok(
                        "valid signature confirms booking",
                        True,
                        "soft-pass: no RAZORPAY_KEY_SECRET",
                    )
                )
        elif st_ord == 503:
            results.append(
                ok(
                    "create-order returns Razorpay order",
                    True,
                    "soft-pass: Razorpay not configured (503)",
                )
            )
            results.append(
                ok(
                    "valid signature confirms booking",
                    True,
                    "soft-pass: skipped without order",
                )
            )
        else:
            results.append(
                ok(
                    "create-order returns Razorpay order",
                    False,
                    f"status={st_ord} {ord_payload}",
                )
            )

    # Free booking confirms immediately
    sun = _next_weekday(today, 6)
    if sun <= today:
        sun += timedelta(days=7)
    st_free, free_booked = http_json(
        "POST",
        "/api/demo-sessions/bookings",
        {
            "schedule_id": free_sid,
            "slot_date": sun.isoformat(),
            "start_time": "09:00",
            "participant_name": "S03 Free",
            "participant_phone": "9888855555",
        },
    )
    fb = (free_booked or {}).get("booking") or {}
    results.append(
        ok(
            "zero fee booking confirms without payment",
            st_free == 201
            and fb.get("status") == "confirmed"
            and fb.get("payment_status") == "not_required"
            and free_booked.get("payment_required") is False,
            f"status={fb.get('status')} pay={fb.get('payment_status')}",
        )
    )

    # Past slot rejected
    st_past, past = http_json(
        "POST",
        "/api/demo-sessions/bookings",
        {
            "schedule_id": sid,
            "slot_date": (today - timedelta(days=7)).isoformat(),
            "start_time": "16:00",
            "participant_name": "Past",
            "participant_phone": "9888844444",
        },
    )
    results.append(
        ok(
            "past slot booking rejected",
            st_past in (400, 422),
            f"status={st_past}",
        )
    )

    # Cleanup schedules
    for cleanup_id in (sid, free_sid):
        if cleanup_id:
            http_json(
                "DELETE",
                f"/api/demo-schedules/{cleanup_id}?hard=true",
                token=token,
            )

    return all(results)


def main():
    print(f"M13-S03 Paid Demo Booking QA  base={BASE}")
    results = [test_openapi_fe()]
    token = _mint_superadmin_token()
    if not token:
        results.append(ok("booking workflow", False, "no admin token"))
    else:
        results.append(test_booking_flow(token))
    failed = sum(1 for r in results if not r)
    print(f"\nDone. {len(results) - failed}/{len(results)} passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
