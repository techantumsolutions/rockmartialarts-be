"""
M12-S06 Unified Training Request Administration QA.

  .venv\\Scripts\\python.exe scripts/qa_m12_s06_unified_admin.py
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


def test_openapi():
    try:
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=8) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        needed = [
            "/api/training-requests/summary",
            "/api/training-requests",
            "/api/training-requests/home",
            "/api/training-requests/school",
            "/api/training-requests/college",
            "/api/training-requests/corporate",
            "/api/training-requests/residential",
            "/api/training-requests/{request_id}/status",
            "/api/training-requests/{request_id}/coach",
            "/api/training-requests/{request_id}/status-history",
        ]
        missing = [p for p in needed if p not in raw]
        return ok("OpenAPI unified admin endpoints", not missing, str(missing) or BASE)
    except Exception as exc:
        return ok("OpenAPI unified admin endpoints", False, str(exc))


def test_auth_guards():
    st1, _ = http_json("GET", "/api/training-requests?limit=5")
    st2, _ = http_json("GET", "/api/training-requests/summary")
    return ok(
        "list + summary require auth",
        st1 in (401, 403) and st2 in (401, 403),
        f"list={st1} summary={st2}",
    )


def seed_all_types():
    seeds = []
    cases = [
        (
            "home",
            {
                "contact_name": "S06 Home",
                "contact_phone": "9111100001",
                "details": {
                    "participant_name": "S06 Home P",
                    "participant_phone": "9111100002",
                    "number_of_participants": 1,
                    "address_line1": "A",
                    "city": "Hyderabad",
                    "state": "Telangana",
                },
            },
        ),
        (
            "school",
            {
                "contact_name": "S06 School",
                "contact_phone": "9111100003",
                "details": {
                    "school_name": "S06 School Name",
                    "number_of_students": 10,
                    "address_line1": "B",
                    "city": "Hyderabad",
                    "state": "Telangana",
                },
            },
        ),
        (
            "college",
            {
                "contact_name": "S06 College",
                "contact_phone": "9111100004",
                "details": {
                    "college_name": "S06 College Name",
                    "number_of_participants": 20,
                    "address_line1": "C",
                    "city": "Hyderabad",
                    "state": "Telangana",
                },
            },
        ),
        (
            "corporate",
            {
                "contact_name": "S06 Corp",
                "contact_phone": "9111100005",
                "details": {
                    "organization_name": "S06 Org",
                    "employee_count": 12,
                    "address_line1": "D",
                    "city": "Hyderabad",
                    "state": "Telangana",
                },
            },
        ),
        (
            "residential",
            {
                "contact_name": "S06 Res",
                "contact_phone": "9111100006",
                "details": {
                    "participant_name": "S06 Res P",
                    "participant_phone": "9111100007",
                    "package_id": "enquiry-custom",
                    "accommodation": "shared_dorm",
                    "food_preference": "veg",
                },
            },
        ),
    ]
    for kind, body in cases:
        st, p = http_json(
            "POST", f"/api/training-requests/{kind}", body, expect_status=201
        )
        req = (p or {}).get("request") or {}
        seeds.append((kind, req.get("id"), st == 201 and req.get("type") == kind))
    return seeds


def test_unified_admin(token: str, seeds):
    results = []
    created = ok(
        "seed all 5 training types",
        all(flag for _, _, flag in seeds),
        str([k for k, _, flag in seeds if flag]),
    )
    results.append(created)

    st, listed = http_json(
        "GET", "/api/training-requests?search=S06&limit=50", token=token
    )
    types_found = {r.get("type") for r in (listed.get("requests") or [])}
    results.append(
        ok(
            "unified list returns mixed types",
            st == 200 and {"home", "school", "college", "corporate", "residential"} <= types_found,
            f"types={sorted(types_found)} total={listed.get('total')}",
        )
    )

    for kind, _, _ in seeds:
        st_k, data = http_json(
            "GET", f"/api/training-requests?type={kind}&search=S06&limit=20", token=token
        )
        rows = data.get("requests") or []
        results.append(
            ok(
                f"type filter {kind}",
                st_k == 200 and rows and all(r.get("type") == kind for r in rows),
                f"count={len(rows)}",
            )
        )

    st_s, data_s = http_json(
        "GET", "/api/training-requests?status=submitted&search=S06&limit=50", token=token
    )
    results.append(
        ok(
            "status filter submitted",
            st_s == 200
            and (data_s.get("requests") or [])
            and all(r.get("status") == "submitted" for r in data_s.get("requests") or []),
            f"count={len(data_s.get('requests') or [])}",
        )
    )

    st_u, data_u = http_json(
        "GET",
        "/api/training-requests?unassigned_only=true&search=S06&limit=50",
        token=token,
    )
    results.append(
        ok(
            "unassigned_only filter",
            st_u == 200
            and all(not r.get("assigned_coach_id") for r in (data_u.get("requests") or [])),
            f"count={len(data_u.get('requests') or [])}",
        )
    )

    st_sum, summary = http_json("GET", "/api/training-requests/summary", token=token)
    results.append(
        ok(
            "summary endpoint",
            st_sum == 200
            and isinstance(summary.get("by_type"), dict)
            and isinstance(summary.get("by_status"), dict)
            and "unassigned_coach" in summary
            and int(summary.get("total") or 0) >= 5,
            f"total={summary.get('total')} by_type={summary.get('by_type')}",
        )
    )

    # Status history + coach assign on one seeded request
    home_id = next((rid for k, rid, ok in seeds if k == "home" and ok), None)
    if home_id:
        st_d, detail = http_json(
            "GET", f"/api/training-requests/{home_id}", token=token
        )
        results.append(
            ok(
                "detail includes status history",
                st_d == 200 and isinstance(detail.get("status_history"), list),
                f"history={len(detail.get('status_history') or [])}",
            )
        )
        st_st, _ = http_json(
            "PATCH",
            f"/api/training-requests/{home_id}/status",
            {"status": "under_review", "note": "S06 QA"},
            token=token,
        )
        results.append(ok("status workflow from unified admin", st_st == 200, str(st_st)))

        coach_id = None
        cst, cdata = http_json(
            "GET", "/api/coaches?active_only=true&limit=5&skip=0", token=token
        )
        if cst == 200 and (cdata.get("coaches") or []):
            coach_id = cdata["coaches"][0].get("id")
        if coach_id:
            st_c, assigned = http_json(
                "PATCH",
                f"/api/training-requests/{home_id}/coach",
                {"coach_id": coach_id, "note": "S06 assign"},
                token=token,
            )
            results.append(
                ok(
                    "coach assignment from unified admin",
                    st_c == 200
                    and (assigned.get("request") or {}).get("assigned_coach_id") == coach_id,
                    coach_id,
                )
            )
            st_h, hist = http_json(
                "GET", f"/api/training-requests/{home_id}/status-history", token=token
            )
            actions = {h.get("action") for h in (hist.get("history") or [])}
            results.append(
                ok(
                    "status history retains workflow changes",
                    st_h == 200 and "coach_assigned" in actions,
                    f"actions={sorted(a for a in actions if a)}",
                )
            )
        else:
            results.append(ok("coach assignment from unified admin", False, "no coach"))
    else:
        results.append(ok("detail includes status history", False, "no home id"))

    return all(results)


def test_fe_files():
    fe = Path(ROOT).parent / "rockmartialarts-fe"
    page = (
        fe
        / "components"
        / "training-requests"
        / "TrainingRequestsAdminPage.tsx"
    )
    api = fe / "lib" / "trainingRequestAPI.ts"
    cfg = fe / "lib" / "dashboard-config.ts"
    raw_page = page.read_text(encoding="utf-8") if page.is_file() else ""
    raw_api = api.read_text(encoding="utf-8") if api.is_file() else ""
    raw_cfg = cfg.read_text(encoding="utf-8") if cfg.is_file() else ""
    checks = [
        page.is_file(),
        "summary" in raw_api,
        "TRAINING_REQUEST_TYPES" in raw_api,
        "unassigned_only" in raw_api,
        "Training Requests" in raw_cfg,
        "SummaryCard" in raw_page or "by_type" in raw_page,
        "paymentFilter" in raw_page,
        "branchFilter" in raw_page,
    ]
    return ok("FE unified admin surfaces", all(checks), str(checks))


def main():
    print(f"M12-S06 Unified Admin QA  base={BASE}")
    results = [test_openapi(), test_auth_guards(), test_fe_files()]
    token = _mint_superadmin_token()
    if not token:
        results.append(ok("admin workflow", False, "no admin token"))
    else:
        seeds = seed_all_types()
        results.append(test_unified_admin(token, seeds))
    failed = sum(1 for r in results if not r)
    print(f"\nDone. {len(results) - failed}/{len(results)} passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
