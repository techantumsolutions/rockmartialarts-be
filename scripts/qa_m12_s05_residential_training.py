"""
M12-S05 Residential Training request QA.

  .venv\\Scripts\\python.exe scripts/qa_m12_s05_residential_training.py
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
            "/api/training-requests/residential",
            "/api/training-requests/residential/packages",
            "/api/training-requests/{request_id}/create-order",
            "/api/training-requests/{request_id}/verify-payment",
            "/api/training-requests/home",
            "/api/camp-registrations",
        ]
        missing = [p for p in needed if p not in raw]
        return ok(
            "OpenAPI exposes residential + camp untouched",
            not missing,
            str(missing) or BASE,
        )
    except Exception as exc:
        return ok("OpenAPI exposes residential + camp untouched", False, str(exc))


def test_model_and_packages():
    path = os.path.join(ROOT, "models", "training_request_models.py")
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    checks = [
        "class ResidentialTrainingDetails" in raw,
        "DEFAULT_RESIDENTIAL_PACKAGES" in raw,
        "accommodation" in raw,
        "food_preference" in raw,
        "package_id" in raw,
        "class TrainingRequestPaymentVerify" in raw,
    ]
    return ok("residential model + packages", all(checks), str(checks))


def test_public_packages_catalog():
    st, data = http_json("GET", "/api/training-requests/residential/packages")
    pkgs = data.get("packages") or []
    ids = {p.get("id") for p in pkgs}
    return ok(
        "public packages catalog",
        st == 200 and "weekend-3d" in ids and "enquiry-custom" in ids,
        f"count={len(pkgs)}",
    )


def test_create_paid_and_enquiry():
    st1, p1 = http_json(
        "POST",
        "/api/training-requests/residential",
        {
            "contact_name": "QA Parent Res",
            "contact_phone": "9000077001",
            "pay_now": True,
            "details": {
                "participant_name": "QA Participant Res",
                "participant_phone": "9000077002",
                "package_id": "weekend-3d",
                "accommodation": "twin_sharing",
                "food_preference": "veg",
                "preferred_start_date": "2026-12-20",
            },
        },
        expect_status=201,
    )
    req1 = (p1 or {}).get("request") or {}
    paid_pkg = ok(
        "residential create (paid package)",
        st1 == 201
        and req1.get("type") == "residential"
        and req1.get("payment_status") == "pending"
        and int(req1.get("fee_pay_now_inr") or 0) == 7500
        and int(req1.get("amount_paise") or 0) == 750000,
        req1.get("id"),
    )

    st2, p2 = http_json(
        "POST",
        "/api/training-requests/residential",
        {
            "contact_name": "QA Enquiry",
            "contact_phone": "9000077003",
            "details": {
                "participant_name": "QA Enquiry Part",
                "participant_phone": "9000077004",
                "package_id": "enquiry-custom",
                "accommodation": "shared_dorm",
                "food_preference": "both",
            },
        },
        expect_status=201,
    )
    req2 = (p2 or {}).get("request") or {}
    enquiry = ok(
        "residential create (enquiry/no payment)",
        st2 == 201
        and req2.get("type") == "residential"
        and req2.get("payment_status") == "not_required",
        req2.get("id"),
    )

    # Invalid package
    st_bad, _ = http_json(
        "POST",
        "/api/training-requests/residential",
        {
            "contact_name": "X",
            "contact_phone": "9000077005",
            "details": {
                "participant_name": "Y",
                "participant_phone": "9000077006",
                "package_id": "does-not-exist",
                "accommodation": "shared_dorm",
                "food_preference": "veg",
            },
        },
    )
    bad = ok("invalid package rejected", st_bad == 400, f"status={st_bad}")

    # create-order for paid request (503 if Razorpay not configured is acceptable soft pass detail)
    order_st, order_payload = http_json(
        "POST", f"/api/training-requests/{req1.get('id')}/create-order"
    )
    if order_st == 200 and (order_payload.get("order") or {}).get("id"):
        order_ok = ok("create-order returns Razorpay order", True, order_payload["order"]["id"])
    elif order_st == 503:
        order_ok = ok(
            "create-order returns Razorpay order",
            True,
            "soft-pass: Razorpay not configured (503)",
        )
    else:
        order_ok = ok(
            "create-order returns Razorpay order",
            False,
            f"status={order_st} payload={order_payload}",
        )

    # enquiry should not allow order
    enq_st, _ = http_json(
        "POST", f"/api/training-requests/{req2.get('id')}/create-order"
    )
    enq_block = ok(
        "enquiry package cannot create-order",
        enq_st == 400,
        f"status={enq_st}",
    )

    list_st, _ = http_json("GET", "/api/training-requests?type=residential&limit=5")
    auth = ok("admin list requires auth", list_st in (401, 403), f"status={list_st}")

    return all([paid_pkg, enquiry, bad, order_ok, enq_block, auth]), req1.get("id")


def test_regressions():
    results = []
    for kind, body in [
        (
            "home",
            {
                "contact_name": "QA H",
                "contact_phone": "9000010101",
                "details": {
                    "participant_name": "QA HP",
                    "participant_phone": "9000010102",
                    "number_of_participants": 1,
                    "address_line1": "A",
                    "city": "Hyderabad",
                    "state": "Telangana",
                },
            },
        ),
        (
            "corporate",
            {
                "contact_name": "QA C",
                "contact_phone": "9000010103",
                "details": {
                    "organization_name": "QA Org",
                    "employee_count": 10,
                    "address_line1": "B",
                    "city": "Hyderabad",
                    "state": "Telangana",
                },
            },
        ),
    ]:
        try:
            st, p = http_json(
                "POST",
                f"/api/training-requests/{kind}",
                body,
                expect_status=201,
            )
            results.append(
                ok(
                    f"{kind} regression",
                    st == 201 and (p.get("request") or {}).get("type") == kind,
                    (p.get("request") or {}).get("id"),
                )
            )
        except Exception as exc:
            results.append(ok(f"{kind} regression", False, str(exc)))
    return all(results)


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
    token = _mint_superadmin_token()
    if not token or not request_id:
        return ok("admin residential workflow", False, "no token/request")

    st, listed = http_json(
        "GET",
        "/api/training-requests?type=residential&search=QA%20Participant%20Res&limit=20",
        token=token,
    )
    list_ok = ok(
        "admin list residential",
        st == 200 and isinstance(listed.get("requests"), list),
        f"total={listed.get('total')}",
    )

    st2, detail = http_json(
        "GET", f"/api/training-requests/{request_id}", token=token
    )
    detail_ok = ok(
        "admin residential detail",
        st2 == 200
        and (detail.get("request") or {}).get("type") == "residential"
        and (detail.get("request") or {}).get("payment_status") in ("pending", "paid"),
        f"payment={(detail.get('request') or {}).get('payment_status')}",
    )

    st3, updated = http_json(
        "PATCH",
        f"/api/training-requests/{request_id}/status",
        {"status": "under_review", "note": "QA residential review"},
        token=token,
    )
    status_ok = ok(
        "residential status workflow",
        st3 == 200 and (updated.get("request") or {}).get("status") == "under_review",
        str(st3),
    )

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
            {"coach_id": coach_id},
            token=token,
        )
        coach_ok = ok(
            "residential coach assignment",
            st4 == 200
            and (assigned.get("request") or {}).get("assigned_coach_id") == coach_id,
            coach_id,
        )
    else:
        coach_ok = ok("residential coach assignment", False, "no coach")

    return list_ok and detail_ok and status_ok and coach_ok


def test_fe_files():
    fe = Path(ROOT).parent / "rockmartialarts-fe"
    files = [
        fe / "app" / "(website)" / "residential-training" / "page.tsx",
        fe / "components" / "training-requests" / "ResidentialTrainingPublicForm.tsx",
        fe / "lib" / "trainingRequestAPI.ts",
    ]
    missing = [str(p) for p in files if not p.is_file()]
    raw = (fe / "lib" / "trainingRequestAPI.ts").read_text(encoding="utf-8")
    api_ok = "submitResidential" in raw and "createResidentialOrder" in raw
    return ok("FE residential pages + API", not missing and api_ok, str(missing) or "ok")


def main():
    print(f"M12-S05 Residential Training QA  base={BASE}")
    results = [
        test_openapi(),
        test_model_and_packages(),
        test_public_packages_catalog(),
        test_fe_files(),
        test_regressions(),
    ]
    created_ok, request_id = test_create_paid_and_enquiry()
    results.append(created_ok)
    results.append(test_admin_workflow(request_id))
    failed = sum(1 for r in results if not r)
    print(f"\nDone. {len(results) - failed}/{len(results)} passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
