"""
M17-S04 Academy Event OTP + My Registrations QA.

  .venv\\Scripts\\python.exe scripts/qa_m17_s04_event_otp_mine.py
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


def http_json(method, path, body=None, token=None, headers=None, expect_status=None):
    url = f"{BASE}{path}"
    data = None
    hdrs = {"Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    if body is not None:
        hdrs["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
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


def _seed_otp(phone: str, code: str):
    from dotenv import load_dotenv

    load_dotenv(Path(ROOT) / ".env")
    import asyncio
    from motor.motor_asyncio import AsyncIOMotorClient
    from passlib.context import CryptContext

    uri = (
        os.getenv("MONGO_URI")
        or os.getenv("MONGO_URL")
        or os.getenv("MONGODB_URL")
        or os.getenv("DATABASE_URL")
    )
    dbn = os.getenv("DB_NAME") or "marshalats"
    pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

    async def run():
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
        db = client[dbn]
        await db.academy_event_registration_otp.delete_many(
            {"phone": {"$in": [phone, phone.replace("+91", "")]}}
        )
        await db.academy_event_registration_otp.insert_one(
            {
                "phone": phone,
                "code_hash": pwd.hash(code),
                "expires_at": datetime.utcnow() + timedelta(minutes=10),
                "last_sent_at": datetime.utcnow(),
            }
        )

    try:
        asyncio.run(run())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(run())
        finally:
            loop.close()


def _cleanup(event_ids, reg_ids, phones):
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
            for eid in event_ids or []:
                await db.academy_events.delete_many({"id": eid})
            await db.academy_events.delete_many(
                {"slug": {"$regex": "^qa-m17-s04-"}}
            )
            for ph in phones or []:
                await db.academy_event_registration_otp.delete_many(
                    {"phone": {"$in": [ph, ph.replace("+91", "")]}}
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
            "/api/academy-event-registrations/send-otp",
            "/api/academy-event-registrations/verify-otp",
            "/api/academy-event-registrations/mine",
            "/api/academy-event-registrations",
            "/api/leads/send-otp",
            "/api/reg-checkout/send-otp",
            "/api/demo-sessions/bookings",
        ]
        missing = [p for p in needed if p not in raw]
        results.append(
            ok("OpenAPI OTP/mine + legacy intact", not missing, str(missing) or BASE)
        )
    except Exception as exc:
        results.append(ok("OpenAPI OTP/mine", False, str(exc)))

    fe_checks = [
        (FE_ROOT / "app" / "(website)" / "events" / "my-registrations" / "page.tsx").exists(),
        "sendOtp"
        in (FE_ROOT / "lib" / "academyEventRegistrationAPI.ts").read_text(encoding="utf-8"),
        "listMine"
        in (FE_ROOT / "lib" / "academyEventRegistrationAPI.ts").read_text(encoding="utf-8"),
        'href: "/events/my-registrations"'
        in (FE_ROOT / "components" / "FixedTopNav.tsx").read_text(encoding="utf-8"),
        "Event Registrations"
        in (FE_ROOT / "lib" / "dashboard-config.ts").read_text(encoding="utf-8"),
        "My Event Registrations"
        in (
            FE_ROOT / "app" / "(website)" / "events" / "my-registrations" / "page.tsx"
        ).read_text(encoding="utf-8"),
    ]
    results.append(ok("FE my-registrations surfaces", all(fe_checks), str(FE_ROOT)))
    return all(results)


def test_workflow():
    results = []
    token = _mint_superadmin_token()
    if not token:
        results.append(ok("superadmin token", False))
        return all(results)
    results.append(ok("superadmin token", True))

    suffix = uuid.uuid4().hex[:8]
    n = int(suffix, 16) % 100000000
    phone_a = f"+9198{n:08d}"
    phone_b = f"+9199{n:08d}"
    code = "424242"
    event_ids = []
    reg_ids = []
    phones = [phone_a, phone_b]

    try:
        st, ev = http_json(
            "POST",
            "/api/academy-events",
            body={
                "title": f"QA OTP Event {suffix}",
                "slug": f"qa-m17-s04-{suffix}",
                "event_type": "seminar",
                "start_at": "2031-09-01T10:00:00",
                "fee_inr": 0,
                "capacity": 10,
                "status": "published",
            },
            token=token,
            expect_status=201,
        )
        eid = (ev.get("event") or {}).get("id")
        if eid:
            event_ids.append(eid)

        st, reg = http_json(
            "POST",
            "/api/academy-event-registrations",
            body={
                "event_id": eid,
                "participant_name": "OTP Owner",
                "participant_phone": phone_a,
                "participant_email": "otp-owner@example.com",
            },
            expect_status=201,
        )
        rid = (reg.get("registration") or {}).get("id")
        if rid:
            reg_ids.append(rid)
        stored_phone = (reg.get("registration") or {}).get("participant_phone")
        results.append(
            ok(
                "registration stores canonical phone",
                str(stored_phone).startswith("+91"),
                stored_phone,
            )
        )

        # Second reg other phone
        st, reg2 = http_json(
            "POST",
            "/api/academy-event-registrations",
            body={
                "event_id": eid,
                "participant_name": "Other",
                "participant_phone": phone_b,
            },
            expect_status=201,
        )
        rid2 = (reg2.get("registration") or {}).get("id")
        if rid2:
            reg_ids.append(rid2)

        # Unscoped get denied
        st, _ = http_json("GET", f"/api/academy-event-registrations/{rid}")
        results.append(ok("get without token denied", st in (401, 403), f"status={st}"))

        # Mine without auth denied
        st, _ = http_json("GET", "/api/academy-event-registrations/mine")
        results.append(ok("mine without auth denied", st == 401, f"status={st}"))

        # Seed OTP + verify
        canonical = stored_phone or phone_a
        _seed_otp(canonical, code)

        st, verified = http_json(
            "POST",
            "/api/academy-event-registrations/verify-otp",
            body={"phone": canonical, "otp": code},
            expect_status=200,
        )
        vtoken = verified.get("verification_token")
        results.append(
            ok(
                "verify-otp returns token",
                verified.get("verified") is True and bool(vtoken),
            )
        )

        # Mine with token
        st, mine = http_json(
            "GET",
            "/api/academy-event-registrations/mine",
            headers={"X-Event-Registration-Token": vtoken},
            expect_status=200,
        )
        ids = {r.get("id") for r in (mine.get("registrations") or [])}
        results.append(
            ok(
                "mine lists only own registrations",
                rid in ids and rid2 not in ids,
                f"count={len(ids)}",
            )
        )

        # Owned get
        st, owned = http_json(
            "GET",
            f"/api/academy-event-registrations/{rid}",
            headers={"X-Event-Registration-Token": vtoken},
            expect_status=200,
        )
        results.append(
            ok(
                "owned get succeeds",
                (owned.get("registration") or {}).get("id") == rid,
            )
        )

        # Other registration forbidden
        st, _ = http_json(
            "GET",
            f"/api/academy-event-registrations/{rid2}",
            headers={"X-Event-Registration-Token": vtoken},
        )
        results.append(ok("other registration forbidden", st == 403, f"status={st}"))

        # Wrong OTP
        _seed_otp(canonical, "111111")
        st, _ = http_json(
            "POST",
            "/api/academy-event-registrations/verify-otp",
            body={"phone": canonical, "otp": "000000"},
        )
        results.append(ok("wrong OTP rejected", st == 400, f"status={st}"))

        # Lead / reg-checkout OTP still present
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        results.append(
            ok(
                "lead + reg-checkout OTP intact",
                "/api/leads/send-otp" in raw and "/api/reg-checkout/send-otp" in raw,
            )
        )

    except Exception as exc:
        results.append(ok("otp/mine workflow", False, str(exc)))
    finally:
        _cleanup(event_ids, reg_ids, phones)

    return all(results)


def main():
    print(f"M17-S04 QA against {BASE}")
    a = test_openapi_fe()
    b = test_workflow()
    print(f"\nResult: {sum([a, b])}/2 suites passed")
    sys.exit(0 if a and b else 1)


if __name__ == "__main__":
    main()
