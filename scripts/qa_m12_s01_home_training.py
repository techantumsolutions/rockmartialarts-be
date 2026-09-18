"""
M12-S01 Home Training request QA.

  .venv\\Scripts\\python.exe scripts/qa_m12_s01_home_training.py
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


def test_openapi():
    try:
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=8) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        needed = [
            "/api/training-requests/home",
            "/api/training-requests",
            "/api/training-requests/{request_id}",
            "/api/training-requests/{request_id}/status",
            "/api/training-requests/{request_id}/coach",
            "/api/training-requests/{request_id}/status-history",
        ]
        missing = [p for p in needed if p not in raw]
        return ok("OpenAPI exposes training-request endpoints", not missing, str(missing) or BASE)
    except Exception as exc:
        return ok("OpenAPI exposes training-request endpoints", False, str(exc))


def test_model_transitions():
    sys.path.insert(0, ROOT)
    from models.training_request_models import ALLOWED_STATUS_TRANSITIONS, TrainingRequestStatus

    checks = [
        TrainingRequestStatus.UNDER_REVIEW.value
        in ALLOWED_STATUS_TRANSITIONS[TrainingRequestStatus.SUBMITTED.value],
        TrainingRequestStatus.COACH_ASSIGNED.value
        in ALLOWED_STATUS_TRANSITIONS[TrainingRequestStatus.SUBMITTED.value],
        TrainingRequestStatus.COMPLETED.value
        not in ALLOWED_STATUS_TRANSITIONS[TrainingRequestStatus.SUBMITTED.value],
        len(ALLOWED_STATUS_TRANSITIONS[TrainingRequestStatus.COMPLETED.value]) == 0,
    ]
    return ok("status transition map", all(checks), str(checks))


def test_public_create_and_auth_guard():
    status, payload = http_json(
        "POST",
        "/api/training-requests/home",
        {
            "contact_name": "QA Parent",
            "contact_phone": "9000012345",
            "contact_email": "qa.parent@example.com",
            "source": "qa_script",
            "details": {
                "participant_name": "QA Kid",
                "participant_phone": "9000012346",
                "number_of_participants": 1,
                "address_line1": "12 Test Street",
                "city": "Hyderabad",
                "state": "Telangana",
                "preferred_date": "2026-10-01",
                "preferred_time": "Morning",
                "training_type": "Karate",
            },
        },
        expect_status=201,
    )
    req = (payload or {}).get("request") or {}
    created = ok(
        "public home create",
        status == 201 and req.get("type") == "home" and req.get("status") == "submitted",
        req.get("id"),
    )

    list_status, _ = http_json("GET", "/api/training-requests?type=home&limit=5")
    guarded = ok(
        "admin list requires auth",
        list_status in (401, 403),
        f"status={list_status}",
    )

    detail_status, _ = http_json(
        "GET", f"/api/training-requests/{req.get('id') or 'missing'}"
    )
    detail_guard = ok(
        "admin detail requires auth",
        detail_status in (401, 403),
        f"status={detail_status}",
    )
    return created and guarded and detail_guard, req.get("id")


def test_admin_workflow(request_id: str | None):
    email = os.getenv("QA_SUPERADMIN_EMAIL") or os.getenv("SUPERADMIN_EMAIL")
    password = os.getenv("QA_SUPERADMIN_PASSWORD") or os.getenv("SUPERADMIN_PASSWORD")
    if not email or not password:
        # Try minting JWT from Mongo if available
        token = _mint_superadmin_token()
    else:
        try:
            _, login = http_json(
                "POST",
                "/api/superadmin/login",
                {"email": email, "password": password},
                expect_status=200,
            )
            token = login.get("access_token") or login.get("token")
        except Exception as exc:
            return ok("admin workflow (login)", False, str(exc))

    if not token:
        return ok(
            "admin workflow",
            False,
            "set QA_SUPERADMIN_EMAIL/PASSWORD or ensure Mongo has a superadmin",
        )

    if not request_id:
        return ok("admin workflow", False, "no request_id from create")

    st, listed = http_json(
        "GET",
        "/api/training-requests?type=home&search=QA%20Parent&limit=20",
        token=token,
    )
    list_ok = ok(
        "admin list home requests",
        st == 200 and isinstance(listed.get("requests"), list),
        f"total={listed.get('total')}",
    )

    st2, detail = http_json(
        "GET", f"/api/training-requests/{request_id}", token=token
    )
    detail_ok = ok(
        "admin get detail + history",
        st2 == 200
        and (detail.get("request") or {}).get("id") == request_id
        and isinstance(detail.get("status_history"), list),
        f"history={len(detail.get('status_history') or [])}",
    )

    st3, updated = http_json(
        "PATCH",
        f"/api/training-requests/{request_id}/status",
        {"status": "under_review", "note": "QA review"},
        token=token,
    )
    status_ok = ok(
        "status → under_review",
        st3 == 200 and (updated.get("request") or {}).get("status") == "under_review",
        str(st3),
    )

    # invalid transition from under_review to completed should fail
    st_bad, _ = http_json(
        "PATCH",
        f"/api/training-requests/{request_id}/status",
        {"status": "completed"},
        token=token,
    )
    bad_ok = ok("invalid transition rejected", st_bad == 400, f"status={st_bad}")

    coach_id = _find_coach_id(token)
    if coach_id:
        st4, assigned = http_json(
            "PATCH",
            f"/api/training-requests/{request_id}/coach",
            {"coach_id": coach_id, "note": "QA assign"},
            token=token,
        )
        coach_ok = ok(
            "coach assignment",
            st4 == 200
            and (assigned.get("request") or {}).get("assigned_coach_id") == coach_id
            and (assigned.get("request") or {}).get("status") == "coach_assigned",
            coach_id,
        )
    else:
        coach_ok = ok("coach assignment", False, "no coach found to assign")

    return list_ok and detail_ok and status_ok and bad_ok and coach_ok


def _find_coach_id(token: str):
    try:
        st, data = http_json(
            "GET", "/api/coaches?active_only=true&limit=5&skip=0", token=token
        )
        if st != 200:
            return None
        coaches = data.get("coaches") or []
        if coaches:
            return coaches[0].get("id")
    except Exception:
        return None
    return None


def _mint_superadmin_token():
    """Best-effort: read first superadmin from Mongo and sign JWT with SECRET_KEY."""
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(ROOT) / ".env")
        import jwt
        from motor.motor_asyncio import AsyncIOMotorClient
        import asyncio

        uri = (
            os.getenv("MONGO_URI")
            or os.getenv("MONGO_URL")
            or os.getenv("MONGODB_URL")
            or os.getenv("DATABASE_URL")
            or os.getenv("MONGODB_URI")
        )
        if not uri or "your_database" in uri:
            return None
        dbn = (
            os.getenv("DB_NAME")
            or os.getenv("MONGO_DB")
            or os.getenv("MONGODB_DB")
            or "marshalats"
        )
        secret = os.getenv("SECRET_KEY") or "student_management_secret_key_2025_secure"

        async def run():
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
            # Prefer configured DB; also probe common names
            names = [dbn, "rockmartialarts", "rock_martial_arts", "marshlats"]
            seen = set()
            for name in names:
                if name in seen:
                    continue
                seen.add(name)
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


def test_fe_files():
    fe = Path(ROOT).parent / "rockmartialarts-fe"
    files = [
        fe / "app" / "(website)" / "home-training" / "page.tsx",
        fe / "components" / "training-requests" / "HomeTrainingPublicForm.tsx",
        fe / "components" / "training-requests" / "TrainingRequestsAdminPage.tsx",
        fe / "app" / "[adminType]" / "dashboard" / "training-requests" / "page.tsx",
        fe / "lib" / "trainingRequestAPI.ts",
    ]
    missing = [str(p) for p in files if not p.is_file()]
    return ok("FE home-training + admin pages present", not missing, str(missing) or "ok")


def main():
    print(f"M12-S01 Home Training QA  base={BASE}")
    results = [
        test_openapi(),
        test_model_transitions(),
        test_fe_files(),
    ]
    created_ok, request_id = test_public_create_and_auth_guard()
    results.append(created_ok)
    results.append(test_admin_workflow(request_id))
    failed = sum(1 for r in results if not r)
    print(f"\nDone. {len(results) - failed}/{len(results)} passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
