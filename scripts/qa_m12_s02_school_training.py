"""
M12-S02 School Training request QA.

  .venv\\Scripts\\python.exe scripts/qa_m12_s02_school_training.py
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
            "/api/training-requests/school",
            "/api/training-requests/home",
            "/api/training-requests",
            "/api/training-requests/{request_id}/status",
            "/api/training-requests/{request_id}/coach",
        ]
        missing = [p for p in needed if p not in raw]
        return ok("OpenAPI exposes school + shared endpoints", not missing, str(missing) or BASE)
    except Exception as exc:
        return ok("OpenAPI exposes school + shared endpoints", False, str(exc))


def test_school_model_fields():
    path = os.path.join(ROOT, "models", "training_request_models.py")
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    checks = [
        "class SchoolTrainingDetails" in raw,
        "school_name" in raw,
        "number_of_students" in raw,
        "grade_levels" in raw,
        "schedule_notes" in raw,
        "class SchoolTrainingRequestCreate" in raw,
    ]
    return ok("school model fields", all(checks), str(checks))


def test_public_create_and_auth_guard():
    status, payload = http_json(
        "POST",
        "/api/training-requests/school",
        {
            "contact_name": "QA Principal",
            "contact_phone": "9000098765",
            "contact_email": "qa.school@example.com",
            "source": "qa_script",
            "details": {
                "school_name": "QA Public School",
                "school_type": "Secondary",
                "contact_designation": "Principal",
                "number_of_students": 40,
                "age_group": "10-14",
                "grade_levels": "Grade 6-8",
                "address_line1": "1 Education Road",
                "city": "Hyderabad",
                "state": "Telangana",
                "preferred_date": "2026-11-01",
                "preferred_time": "After school",
                "training_type": "Self-defense",
            },
        },
        expect_status=201,
    )
    req = (payload or {}).get("request") or {}
    created = ok(
        "public school create",
        status == 201
        and req.get("type") == "school"
        and req.get("status") == "submitted"
        and (req.get("details") or {}).get("school_name") == "QA Public School",
        req.get("id"),
    )

    list_status, _ = http_json("GET", "/api/training-requests?type=school&limit=5")
    guarded = ok(
        "admin list requires auth",
        list_status in (401, 403),
        f"status={list_status}",
    )
    return created and guarded, req.get("id")


def test_home_still_works():
    """Regression: home create must remain intact."""
    try:
        status, payload = http_json(
            "POST",
            "/api/training-requests/home",
            {
                "contact_name": "QA Home Parent",
                "contact_phone": "9000011111",
                "details": {
                    "participant_name": "QA Child",
                    "participant_phone": "9000011112",
                    "number_of_participants": 1,
                    "address_line1": "Home addr",
                    "city": "Hyderabad",
                    "state": "Telangana",
                },
            },
            expect_status=201,
        )
        req = (payload or {}).get("request") or {}
        return ok(
            "home create still works (regression)",
            status == 201 and req.get("type") == "home",
            req.get("id"),
        )
    except Exception as exc:
        return ok("home create still works (regression)", False, str(exc))


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


def test_admin_workflow(request_id: str | None):
    email = os.getenv("QA_SUPERADMIN_EMAIL") or os.getenv("SUPERADMIN_EMAIL")
    password = os.getenv("QA_SUPERADMIN_PASSWORD") or os.getenv("SUPERADMIN_PASSWORD")
    if email and password:
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
    else:
        token = _mint_superadmin_token()

    if not token:
        return ok(
            "admin workflow",
            False,
            "set QA_SUPERADMIN_EMAIL/PASSWORD or ensure Mongo has a superadmin",
        )
    if not request_id:
        return ok("admin workflow", False, "no request_id")

    st, listed = http_json(
        "GET",
        "/api/training-requests?type=school&search=QA%20Public%20School&limit=20",
        token=token,
    )
    list_ok = ok(
        "admin list school requests",
        st == 200 and isinstance(listed.get("requests"), list),
        f"total={listed.get('total')}",
    )

    st2, detail = http_json(
        "GET", f"/api/training-requests/{request_id}", token=token
    )
    detail_ok = ok(
        "admin school detail + history",
        st2 == 200
        and (detail.get("request") or {}).get("type") == "school"
        and isinstance(detail.get("status_history"), list),
        f"history={len(detail.get('status_history') or [])}",
    )

    st3, updated = http_json(
        "PATCH",
        f"/api/training-requests/{request_id}/status",
        {"status": "under_review", "note": "QA school review"},
        token=token,
    )
    status_ok = ok(
        "school status → under_review",
        st3 == 200 and (updated.get("request") or {}).get("status") == "under_review",
        str(st3),
    )

    st_bad, _ = http_json(
        "PATCH",
        f"/api/training-requests/{request_id}/status",
        {"status": "completed"},
        token=token,
    )
    bad_ok = ok("invalid school transition rejected", st_bad == 400, f"status={st_bad}")

    coach_id = None
    try:
        cst, cdata = http_json(
            "GET", "/api/coaches?active_only=true&limit=5&skip=0", token=token
        )
        if cst == 200 and (cdata.get("coaches") or []):
            coach_id = cdata["coaches"][0].get("id")
    except Exception:
        coach_id = None

    if coach_id:
        st4, assigned = http_json(
            "PATCH",
            f"/api/training-requests/{request_id}/coach",
            {"coach_id": coach_id, "note": "QA school assign"},
            token=token,
        )
        coach_ok = ok(
            "school coach assignment",
            st4 == 200
            and (assigned.get("request") or {}).get("assigned_coach_id") == coach_id
            and (assigned.get("request") or {}).get("status") == "coach_assigned",
            coach_id,
        )
    else:
        coach_ok = ok("school coach assignment", False, "no coach found")

    return list_ok and detail_ok and status_ok and bad_ok and coach_ok


def test_fe_files():
    fe = Path(ROOT).parent / "rockmartialarts-fe"
    files = [
        fe / "app" / "(website)" / "school-training" / "page.tsx",
        fe / "components" / "training-requests" / "SchoolTrainingPublicForm.tsx",
        fe / "lib" / "trainingRequestAPI.ts",
        fe / "components" / "training-requests" / "TrainingRequestsAdminPage.tsx",
    ]
    missing = [str(p) for p in files if not p.is_file()]
    api_ok = False
    try:
        raw = (fe / "lib" / "trainingRequestAPI.ts").read_text(encoding="utf-8")
        api_ok = "submitSchool" in raw and "SchoolTrainingDetails" in raw
    except Exception:
        pass
    return ok(
        "FE school training pages + API client",
        not missing and api_ok,
        str(missing) if missing else "ok",
    )


def main():
    print(f"M12-S02 School Training QA  base={BASE}")
    results = [
        test_openapi(),
        test_school_model_fields(),
        test_fe_files(),
        test_home_still_works(),
    ]
    created_ok, request_id = test_public_create_and_auth_guard()
    results.append(created_ok)
    results.append(test_admin_workflow(request_id))
    failed = sum(1 for r in results if not r)
    print(f"\nDone. {len(results) - failed}/{len(results)} passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
