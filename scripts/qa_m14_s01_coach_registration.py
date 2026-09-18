"""
M14-S01 Coach Registration QA.

  .venv\\Scripts\\python.exe scripts/qa_m14_s01_coach_registration.py
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


def ok(label, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    extra = f" — {detail}" if detail else ""
    line = f"[{status}] {label}{extra}"
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))
    return passed


def http_json(method, path, body=None, token=None, expect_status=None, data=None, headers_extra=None):
    url = f"{BASE}{path}"
    headers = {"Accept": "application/json"}
    if headers_extra:
        headers.update(headers_extra)
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


def test_openapi_fe():
    results = []
    try:
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        needed = [
            "/api/coaches/register",
            "/api/coaches/register/options",
            "/api/coaches/register/photo",
        ]
        missing = [p for p in needed if p not in raw]
        results.append(ok("OpenAPI registration endpoints", not missing, str(missing) or BASE))
    except Exception as exc:
        results.append(ok("OpenAPI registration endpoints", False, str(exc)))

    st, opts = http_json("GET", "/api/coaches/register/options")
    results.append(
        ok(
            "register options public",
            st == 200
            and isinstance(opts.get("branches"), list)
            and isinstance(opts.get("specializations"), list),
            f"status={st} branches={len(opts.get('branches') or [])}",
        )
    )

    fe = Path(ROOT).parent / "rockmartialarts-fe"
    page = fe / "app" / "(website)" / "coach-register" / "page.tsx"
    form = fe / "components" / "coaches" / "CoachRegistrationPublicForm.tsx"
    api = fe / "lib" / "coachRegistrationAPI.ts"
    nav = fe / "components" / "FixedTopNav.tsx"
    checks = [
        page.is_file(),
        form.is_file(),
        "coaches/register" in (api.read_text(encoding="utf-8") if api.is_file() else ""),
        "/coach-register" in (nav.read_text(encoding="utf-8") if nav.is_file() else ""),
        "service_location" in (form.read_text(encoding="utf-8") if form.is_file() else ""),
        "specializations" in (form.read_text(encoding="utf-8") if form.is_file() else ""),
    ]
    results.append(ok("FE registration surfaces", all(checks), str(checks)))
    return all(results)


def test_register_flow():
    results = []
    st_o, opts = http_json("GET", "/api/coaches/register/options")
    branches = opts.get("branches") or []
    bid = (branches[0] or {}).get("id") if branches else None
    results.append(ok("branch available for locations", bool(bid), str(bid)))
    if not bid:
        return all(results)

    suffix = uuid.uuid4().hex[:8]
    email = f"m14s01_{suffix}@example.com"
    phone = f"9{suffix[:9].ljust(9, '0')}"

    # Missing specialization
    st_bad, _ = http_json(
        "POST",
        "/api/coaches/register",
        {
            "first_name": "M14",
            "last_name": "Coach",
            "gender": "male",
            "date_of_birth": "1990-01-01",
            "email": email,
            "phone": phone,
            "password": "TestPass1",
            "address": "Line 1",
            "city": "Hyderabad",
            "state": "Telangana",
            "professional_experience": "3-5 years",
            "specializations": [],
            "service_location_ids": [bid],
        },
    )
    results.append(ok("reject empty specializations", st_bad == 422, f"status={st_bad}"))

    st_ok, created = http_json(
        "POST",
        "/api/coaches/register",
        {
            "first_name": "M14",
            "last_name": "SelfReg",
            "gender": "male",
            "date_of_birth": "1990-05-15",
            "email": email,
            "country_code": "+91",
            "phone": phone,
            "password": "TestPass1",
            "address": "12 Demo Street",
            "area": "Jubilee Hills",
            "city": "Hyderabad",
            "state": "Telangana",
            "zip_code": "500033",
            "country": "India",
            "professional_experience": "5-10 years",
            "education_qualification": "Black Belt",
            "designation": "Coach",
            "specializations": ["Taekwondo", "Self Defense"],
            "service_location_ids": [bid],
            "about_short": "S01 QA coach",
        },
    )
    coach = (created or {}).get("coach") or {}
    coach_id = created.get("coach_id") or coach.get("id")
    results.append(
        ok(
            "register creates pending coach",
            st_ok == 201
            and bool(coach_id)
            and created.get("approval_status") == "pending"
            and coach.get("is_active") is False
            and coach.get("service_location_ids") == [bid]
            and "Taekwondo" in (coach.get("areas_of_expertise") or []),
            f"id={coach_id} status={created.get('approval_status')}",
        )
    )

    # Duplicate email
    st_dup, _ = http_json(
        "POST",
        "/api/coaches/register",
        {
            "first_name": "Dup",
            "last_name": "Coach",
            "gender": "male",
            "date_of_birth": "1991-01-01",
            "email": email,
            "phone": f"8{phone[1:]}",
            "password": "TestPass1",
            "address": "X",
            "city": "Hyderabad",
            "state": "Telangana",
            "professional_experience": "1-3 years",
            "specializations": ["Karate"],
            "service_location_ids": [bid],
        },
    )
    results.append(ok("duplicate email rejected", st_dup == 400, f"status={st_dup}"))

    # Pending cannot login
    st_login, login = http_json(
        "POST",
        "/api/coaches/login",
        {"email": email, "password": "TestPass1"},
    )
    results.append(
        ok(
            "pending coach cannot login",
            st_login == 403,
            f"status={st_login} detail={login.get('detail')}",
        )
    )

    # Photo upload (tiny png)
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    boundary = f"----Boundary{uuid.uuid4().hex}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="t.png"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode() + png + f"\r\n--{boundary}--\r\n".encode()
    st_ph, photo = http_json(
        "POST",
        "/api/coaches/register/photo",
        data=body,
        headers_extra={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    results.append(
        ok(
            "public photo upload",
            st_ph == 200 and bool(photo.get("file_url")),
            photo.get("file_url"),
        )
    )

    # Cleanup registered coach
    if coach_id:
        try:
            from dotenv import load_dotenv
            import asyncio
            from motor.motor_asyncio import AsyncIOMotorClient

            load_dotenv(Path(ROOT) / ".env")
            uri = (
                os.getenv("MONGO_URI")
                or os.getenv("MONGO_URL")
                or os.getenv("MONGODB_URL")
                or os.getenv("DATABASE_URL")
            )
            dbn = os.getenv("DB_NAME") or "marshalats"

            async def cleanup():
                client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
                for name in (dbn, "marshalats", "rockmartialarts", "rock_martial_arts"):
                    r = await client[name].coaches.delete_one({"id": coach_id})
                    if r.deleted_count:
                        return name
                return None

            used = asyncio.run(cleanup())
            results.append(ok("cleanup registration", True, str(used)))
        except Exception as exc:
            results.append(ok("cleanup registration", False, str(exc)))

    return all(results)


def main():
    print(f"M14-S01 Coach Registration QA  base={BASE}")
    results = [test_openapi_fe(), test_register_flow()]
    failed = sum(1 for r in results if not r)
    print(f"\nDone. {len(results) - failed}/{len(results)} passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
