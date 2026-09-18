"""
M14-S02 Coach Approval & Status QA.

  .venv\\Scripts\\python.exe scripts/qa_m14_s02_coach_approval.py
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


def _cleanup_coach(email: str):
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
            return

        async def run():
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
            for name in (dbn, "marshalats", "rockmartialarts", "rock_martial_arts"):
                db = client[name]
                coach = await db.coaches.find_one({"email": email})
                if coach:
                    cid = coach.get("id")
                    await db.coaches.delete_one({"id": cid})
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
            "/api/coaches/approvals",
            "/api/coaches/approvals/summary",
            "/api/coaches/{coach_id}/approve",
            "/api/coaches/{coach_id}/reject",
            "/api/coaches/{coach_id}/active-status",
            "/api/coaches/{coach_id}/approval-history",
        ]
        missing = [p for p in needed if p not in raw]
        results.append(ok("OpenAPI approval endpoints", not missing, str(missing) or BASE))
    except Exception as exc:
        results.append(ok("OpenAPI approval endpoints", False, str(exc)))

    fe_ok = all(
        [
            (FE_ROOT / "lib" / "coachApprovalAPI.ts").is_file(),
            (FE_ROOT / "components" / "coaches" / "CoachApprovalsAdminPage.tsx").is_file(),
            (FE_ROOT / "app" / "[adminType]" / "dashboard" / "coach-approvals" / "page.tsx").is_file(),
            "Coach Approvals" in (FE_ROOT / "lib" / "dashboard-config.ts").read_text(encoding="utf-8"),
            "coachApprovalAPI" in (
                FE_ROOT / "components" / "coaches" / "CoachApprovalsAdminPage.tsx"
            ).read_text(encoding="utf-8"),
        ]
    )
    results.append(ok("FE approval surfaces", fe_ok, str(FE_ROOT)))
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
    bid = branches[0]["id"] if branches else None
    results.append(ok("branch for registration", bool(bid), str(bid)))
    if not bid:
        return all(results)

    suffix = uuid.uuid4().hex[:8]
    email = f"qa.m14s02.{suffix}@example.com"
    password = "TestPass123!"
    payload = {
        "first_name": "QA",
        "last_name": "Approval",
        "gender": "Male",
        "date_of_birth": "1990-01-15",
        "email": email,
        "country_code": "+91",
        "phone": f"9{suffix[:9]}",
        "password": password,
        "address": "1 Test Street",
        "area": "Test Area",
        "city": "Hyderabad",
        "state": "Telangana",
        "zip_code": "500001",
        "country": "India",
        "professional_experience": "3-5 years",
        "education_qualification": "B.P.Ed",
        "designation": "Coach",
        "specializations": ["Karate"],
        "service_location_ids": [bid],
    }

    try:
        st, created = http_json("POST", "/api/coaches/register", body=payload, expect_status=201)
        coach_id = created.get("coach_id")
        results.append(
            ok(
                "register pending coach",
                created.get("approval_status") == "pending" and bool(coach_id),
                f"id={coach_id}",
            )
        )

        st, pending_login = http_json(
            "POST",
            "/api/coaches/login",
            body={"email": email, "password": password},
        )
        results.append(
            ok(
                "pending cannot login",
                st == 403 and "pending" in str(pending_login.get("detail", "")).lower(),
                f"status={st} detail={pending_login.get('detail')}",
            )
        )

        st, listing = http_json(
            "GET",
            "/api/coaches/approvals?approval_status=pending&search=" + email,
            token=token,
            expect_status=200,
        )
        found = any(c.get("id") == coach_id for c in (listing.get("coaches") or []))
        results.append(ok("admin list pending", found, f"total={listing.get('total')}"))

        st, summary = http_json(
            "GET", "/api/coaches/approvals/summary", token=token, expect_status=200
        )
        results.append(
            ok(
                "approval summary",
                isinstance(summary.get("pending"), int) and summary.get("pending", 0) >= 1,
                str(summary),
            )
        )

        st, rejected = http_json(
            "POST",
            f"/api/coaches/{coach_id}/reject",
            body={"note": "QA reject"},
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "reject action",
                (rejected.get("coach") or {}).get("approval_status") == "rejected",
                str((rejected.get("coach") or {}).get("approval_status")),
            )
        )

        st, hist1 = http_json(
            "GET",
            f"/api/coaches/{coach_id}/approval-history",
            token=token,
            expect_status=200,
        )
        actions1 = [h.get("action") for h in (hist1.get("approval_history") or [])]
        results.append(
            ok(
                "history after reject",
                "reject" in actions1 and "registration_submitted" in actions1,
                str(actions1),
            )
        )

        st, approved = http_json(
            "POST",
            f"/api/coaches/{coach_id}/approve",
            body={"note": "QA approve"},
            token=token,
            expect_status=200,
        )
        coach = approved.get("coach") or {}
        results.append(
            ok(
                "approve action",
                coach.get("approval_status") == "approved" and coach.get("is_active") is True,
                f"status={coach.get('approval_status')} active={coach.get('is_active')}",
            )
        )

        st, login_ok = http_json(
            "POST",
            "/api/coaches/login",
            body={"email": email, "password": password},
            expect_status=200,
        )
        results.append(
            ok("approved can login", bool(login_ok.get("access_token")), f"status={st}")
        )

        st, deactivated = http_json(
            "PATCH",
            f"/api/coaches/{coach_id}/active-status",
            body={"is_active": False, "note": "QA deactivate"},
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "deactivate approved coach",
                (deactivated.get("coach") or {}).get("is_active") is False,
                str((deactivated.get("coach") or {}).get("is_active")),
            )
        )

        st, inactive_login = http_json(
            "POST",
            "/api/coaches/login",
            body={"email": email, "password": password},
        )
        results.append(
            ok(
                "inactive cannot login",
                st in (401, 403),
                f"status={st} detail={inactive_login.get('detail')}",
            )
        )

        st, reactivated = http_json(
            "PATCH",
            f"/api/coaches/{coach_id}/active-status",
            body={"is_active": True, "note": "QA reactivate"},
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "reactivate coach",
                (reactivated.get("coach") or {}).get("is_active") is True,
            )
        )

        st, hist2 = http_json(
            "GET",
            f"/api/coaches/{coach_id}/approval-history",
            token=token,
            expect_status=200,
        )
        actions2 = [h.get("action") for h in (hist2.get("approval_history") or [])]
        results.append(
            ok(
                "full approval history",
                all(a in actions2 for a in ("approve", "reject", "deactivate", "activate")),
                str(actions2),
            )
        )

        # pending activate should fail — create another pending
        email2 = f"qa.m14s02b.{suffix}@example.com"
        payload2 = dict(payload)
        payload2["email"] = email2
        payload2["phone"] = f"8{suffix[:9]}"
        st, created2 = http_json("POST", "/api/coaches/register", body=payload2, expect_status=201)
        cid2 = created2.get("coach_id")
        st, bad_active = http_json(
            "PATCH",
            f"/api/coaches/{cid2}/active-status",
            body={"is_active": True},
            token=token,
        )
        results.append(
            ok(
                "cannot activate pending",
                st == 400,
                f"status={st} detail={bad_active.get('detail')}",
            )
        )
        _cleanup_coach(email2)

    finally:
        cleaned = _cleanup_coach(email)
        results.append(ok("cleanup registration", cleaned is not False, email))

    return all(results)


def main():
    print(f"M14-S02 Coach Approval QA  base={BASE}")
    r1 = test_openapi_fe()
    r2 = test_workflow()
    print(f"\nDone. {int(r1) + int(r2)}/2 passed.")
    sys.exit(0 if (r1 and r2) else 1)


if __name__ == "__main__":
    main()
