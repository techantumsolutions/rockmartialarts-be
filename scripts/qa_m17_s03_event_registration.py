"""
M17-S03 Academy Event Registration QA.

  .venv\\Scripts\\python.exe scripts/qa_m17_s03_event_registration.py
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
        raise AssertionError(
            f"{method} {path} expected {expect_status} got {status}: {payload}"
        )
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


def _cleanup(event_ids, reg_ids):
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
            for rid in reg_ids or []:
                await db.academy_event_registrations.delete_many({"id": rid})
            await db.academy_event_registrations.delete_many(
                {"event_slug": {"$regex": "^qa-m17-s03-"}}
            )
            for eid in event_ids or []:
                await db.academy_events.delete_many({"id": eid})
            await db.academy_events.delete_many(
                {"slug": {"$regex": "^qa-m17-s03-"}}
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
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        needed = [
            "/api/academy-event-registrations",
            "/api/academy-event-registrations/{registration_id}",
            "/api/academy-event-registrations/{registration_id}/create-order",
            "/api/academy-event-registrations/{registration_id}/verify-payment",
            "/api/academy-events/public",
            "/api/demo-sessions/bookings",
            "/api/camp-registrations",
        ]
        missing = [p for p in needed if p not in raw]
        results.append(
            ok(
                "OpenAPI registration + legacy intact",
                not missing,
                str(missing) or BASE,
            )
        )
    except Exception as exc:
        results.append(ok("OpenAPI registration", False, str(exc)))

    fe_checks = [
        (FE_ROOT / "lib" / "academyEventRegistrationAPI.ts").exists(),
        (
            FE_ROOT
            / "app"
            / "(website)"
            / "events"
            / "[slug]"
            / "register"
            / "page.tsx"
        ).exists(),
        "academyEventRegistrationAPI"
        in (
            FE_ROOT
            / "app"
            / "(website)"
            / "events"
            / "[slug]"
            / "register"
            / "page.tsx"
        ).read_text(encoding="utf-8"),
        "Registration confirmed"
        in (
            FE_ROOT
            / "app"
            / "(website)"
            / "events"
            / "[slug]"
            / "register"
            / "page.tsx"
        ).read_text(encoding="utf-8"),
        "registration_fields"
        in (FE_ROOT / "components" / "events" / "AcademyEventsAdminPage.tsx").read_text(
            encoding="utf-8"
        ),
        "openRazorpayCheckout"
        in (
            FE_ROOT
            / "app"
            / "(website)"
            / "events"
            / "[slug]"
            / "register"
            / "page.tsx"
        ).read_text(encoding="utf-8"),
    ]
    results.append(ok("FE registration surfaces", all(fe_checks), str(FE_ROOT)))
    return all(results)


def test_workflow():
    results = []
    token = _mint_superadmin_token()
    if not token:
        results.append(ok("superadmin token", False))
        return all(results)
    results.append(ok("superadmin token", True))

    suffix = uuid.uuid4().hex[:8]
    free_slug = f"qa-m17-s03-free-{suffix}"
    paid_slug = f"qa-m17-s03-paid-{suffix}"
    full_slug = f"qa-m17-s03-full-{suffix}"
    event_ids = []
    reg_ids = []

    try:
        # Free published event capacity 2
        st, free_ev = http_json(
            "POST",
            "/api/academy-events",
            body={
                "title": f"QA Free Reg {suffix}",
                "slug": free_slug,
                "event_type": "workshop",
                "start_at": "2031-05-01T10:00:00",
                "fee_inr": 0,
                "capacity": 2,
                "status": "published",
                "registration_enabled": True,
                "registration_fields": [
                    {
                        "key": "participant_email",
                        "enabled": True,
                        "required": True,
                    },
                    {"key": "participant_age", "enabled": False},
                    {"key": "notes", "enabled": True, "required": False},
                ],
            },
            token=token,
            expect_status=201,
        )
        fev = free_ev.get("event") or {}
        fid = fev.get("id")
        if fid:
            event_ids.append(fid)
        results.append(
            ok(
                "create free event with field config",
                fev.get("status") == "published"
                and any(
                    f.get("key") == "participant_email" and f.get("required")
                    for f in (fev.get("registration_fields") or [])
                )
                and any(
                    f.get("key") == "participant_age" and f.get("enabled") is False
                    for f in (fev.get("registration_fields") or [])
                ),
                fid,
            )
        )

        # Public detail includes fields
        st, pub = http_json(
            "GET", f"/api/academy-events/public/{free_slug}", expect_status=200
        )
        pev = pub.get("event") or {}
        results.append(
            ok(
                "public event exposes registration_fields",
                isinstance(pev.get("registration_fields"), list)
                and len(pev.get("registration_fields") or []) >= 2,
            )
        )

        # Missing required email → 400
        st, bad = http_json(
            "POST",
            "/api/academy-event-registrations",
            body={
                "event_slug": free_slug,
                "participant_name": "QA User",
                "participant_phone": "9999900001",
            },
        )
        results.append(ok("required email enforced", st == 400, f"status={st}"))

        # Free confirm
        st, reg = http_json(
            "POST",
            "/api/academy-event-registrations",
            body={
                "event_slug": free_slug,
                "participant_name": "QA Free User",
                "participant_phone": "9999900002",
                "participant_email": "qa-free@example.com",
                "notes": "front row",
            },
            expect_status=201,
        )
        r = reg.get("registration") or {}
        rid = r.get("id")
        if rid:
            reg_ids.append(rid)
        results.append(
            ok(
                "free registration confirms immediately",
                reg.get("payment_required") is False
                and r.get("status") == "confirmed"
                and r.get("payment_status") == "not_required"
                and float(r.get("fee_inr") if r.get("fee_inr") is not None else -1) == 0,
                rid,
            )
        )

        # Seats decremented
        st, after = http_json(
            "GET", f"/api/academy-events/public/{free_slug}", expect_status=200
        )
        results.append(
            ok(
                "capacity decremented after free reg",
                (after.get("event") or {}).get("seats_remaining") == 1
                and (after.get("event") or {}).get("registrations_count") == 1,
            )
        )

        # Paid event
        st, paid_ev = http_json(
            "POST",
            "/api/academy-events",
            body={
                "title": f"QA Paid Reg {suffix}",
                "slug": paid_slug,
                "event_type": "seminar",
                "start_at": "2031-06-01T11:00:00",
                "fee_inr": 199,
                "capacity": 5,
                "status": "published",
                "registration_enabled": True,
            },
            token=token,
            expect_status=201,
        )
        pid = (paid_ev.get("event") or {}).get("id")
        if pid:
            event_ids.append(pid)

        st, preg = http_json(
            "POST",
            "/api/academy-event-registrations",
            body={
                "event_id": pid,
                "participant_name": "QA Paid User",
                "participant_phone": "9999900003",
                "participant_email": "qa-paid@example.com",
            },
            expect_status=201,
        )
        pr = preg.get("registration") or {}
        prid = pr.get("id")
        if prid:
            reg_ids.append(prid)
        results.append(
            ok(
                "paid registration pending_payment",
                preg.get("payment_required") is True
                and pr.get("status") == "pending_payment"
                and float(pr.get("fee_inr") if pr.get("fee_inr") is not None else -1)
                == 199
                and int(pr.get("amount_paise") or 0) == 19900,
                prid,
            )
        )

        # Get registration
        st, got = http_json(
            "GET",
            f"/api/academy-event-registrations/{prid}",
            expect_status=200,
        )
        results.append(
            ok(
                "get registration by id",
                (got.get("registration") or {}).get("id") == prid
                and "razorpay_signature" not in (got.get("registration") or {}),
            )
        )

        # Paid holds a seat
        st, paid_pub = http_json(
            "GET", f"/api/academy-events/public/{paid_slug}", expect_status=200
        )
        results.append(
            ok(
                "pending payment holds seat",
                (paid_pub.get("event") or {}).get("registrations_count") == 1
                and (paid_pub.get("event") or {}).get("seats_remaining") == 4,
            )
        )

        # Full capacity event
        st, full_ev = http_json(
            "POST",
            "/api/academy-events",
            body={
                "title": f"QA Full {suffix}",
                "slug": full_slug,
                "event_type": "event",
                "start_at": "2031-07-01T09:00:00",
                "fee_inr": 0,
                "capacity": 1,
                "status": "published",
            },
            token=token,
            expect_status=201,
        )
        full_id = (full_ev.get("event") or {}).get("id")
        if full_id:
            event_ids.append(full_id)

        st, r1 = http_json(
            "POST",
            "/api/academy-event-registrations",
            body={
                "event_slug": full_slug,
                "participant_name": "First",
                "participant_phone": "9999900004",
            },
            expect_status=201,
        )
        if (r1.get("registration") or {}).get("id"):
            reg_ids.append(r1["registration"]["id"])

        st, r2 = http_json(
            "POST",
            "/api/academy-event-registrations",
            body={
                "event_slug": full_slug,
                "participant_name": "Second",
                "participant_phone": "9999900005",
            },
        )
        results.append(ok("capacity rejection 409", st == 409, f"status={st}"))

        # Closed registration
        st, _ = http_json(
            "PATCH",
            f"/api/academy-events/{fid}",
            body={"registration_enabled": False},
            token=token,
            expect_status=200,
        )
        st, closed = http_json(
            "POST",
            "/api/academy-event-registrations",
            body={
                "event_slug": free_slug,
                "participant_name": "Blocked",
                "participant_phone": "9999900006",
                "participant_email": "x@y.com",
            },
        )
        results.append(ok("closed registration rejected", st == 400, f"status={st}"))

        # Draft not registerable
        st, draft = http_json(
            "POST",
            "/api/academy-events",
            body={
                "title": f"QA Draft {suffix}",
                "slug": f"qa-m17-s03-draft-{suffix}",
                "event_type": "event",
                "start_at": "2031-08-01T10:00:00",
                "status": "draft",
                "fee_inr": 0,
            },
            token=token,
            expect_status=201,
        )
        did = (draft.get("event") or {}).get("id")
        if did:
            event_ids.append(did)
        st, draft_reg = http_json(
            "POST",
            "/api/academy-event-registrations",
            body={
                "event_slug": f"qa-m17-s03-draft-{suffix}",
                "participant_name": "No",
                "participant_phone": "9999900007",
            },
        )
        results.append(ok("draft event not registerable", st == 404, f"status={st}"))

        # Legacy intact
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        results.append(
            ok(
                "demo + camp routes intact",
                "/api/demo-sessions" in raw and "/api/camp-registrations" in raw,
            )
        )

    except Exception as exc:
        results.append(ok("registration workflow", False, str(exc)))
    finally:
        _cleanup(event_ids, reg_ids)

    return all(results)


def main():
    print(f"M17-S03 QA against {BASE}")
    a = test_openapi_fe()
    b = test_workflow()
    print(f"\nResult: {sum([a, b])}/2 suites passed")
    sys.exit(0 if a and b else 1)


if __name__ == "__main__":
    main()
