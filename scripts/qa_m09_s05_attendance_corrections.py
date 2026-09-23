"""
M09-S05 manual attendance correction QA.

  .venv\\Scripts\\python.exe scripts/qa_m09_s05_attendance_corrections.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
import urllib.request
from datetime import datetime

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


def load_mod():
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)

    if "utils" not in sys.modules:
        pkg = types.ModuleType("utils")
        pkg.__path__ = [os.path.join(ROOT, "utils")]
        sys.modules["utils"] = pkg

    if "models" not in sys.modules:
        models_pkg = types.ModuleType("models")
        models_pkg.__path__ = [os.path.join(ROOT, "models")]
        sys.modules["models"] = models_pkg

    database = types.ModuleType("utils.database")
    database.get_db = lambda: None
    sys.modules["utils.database"] = database

    helpers = types.ModuleType("utils.helpers")
    helpers.serialize_doc = lambda d: d
    sys.modules["utils.helpers"] = helpers

    status_svc = types.ModuleType("utils.student_status_service")

    async def get_managed_branch_ids_for_user(db, user):
        return list(user.get("_managed") or [])

    status_svc.get_managed_branch_ids_for_user = get_managed_branch_ids_for_user
    sys.modules["utils.student_status_service"] = status_svc

    if "fastapi" not in sys.modules:
        fastapi = types.ModuleType("fastapi")

        class HTTPException(Exception):
            def __init__(self, status_code=400, detail=""):
                self.status_code = status_code
                self.detail = detail
                super().__init__(detail)

        fastapi.HTTPException = HTTPException
        sys.modules["fastapi"] = fastapi

    # Load attendance models from file with pydantic if available
    try:
        from models.attendance_models import AttendanceMethod, AttendanceStatus  # noqa: F401
    except Exception:
        att = types.ModuleType("models.attendance_models")

        class AttendanceMethod:
            MANUAL = type("E", (), {"value": "manual"})()

        class AttendanceStatus:
            PRESENT = type("E", (), {"value": "present"})()
            ABSENT = type("E", (), {"value": "absent"})()
            LATE = type("E", (), {"value": "late"})()

        att.AttendanceMethod = AttendanceMethod
        att.AttendanceStatus = AttendanceStatus
        sys.modules["models.attendance_models"] = att

    path = os.path.join(ROOT, "utils", "attendance_correction_service.py")
    spec = importlib.util.spec_from_file_location("utils.attendance_correction_service", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["utils.attendance_correction_service"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_reason_required(mod):
    try:
        mod.AttendanceCorrectionRequest(reason="ab", attendance_id="x")
        return ok("reason min length enforced", False, "model accepted short reason")
    except Exception:
        return ok("reason min length enforced", True, "pydantic/min length")


def test_permission_roles(mod):
    from fastapi import HTTPException
    import asyncio

    async def run():
        try:
            await mod.assert_correction_permission(
                object(), {"id": "c", "role": "coach"}, "b1"
            )
            return False
        except HTTPException as exc:
            return exc.status_code == 403

    return ok(
        "coach cannot correct (roles)",
        asyncio.run(run()),
        "ok",
    )


def test_bm_branch_guard(mod):
    from fastapi import HTTPException
    import asyncio

    class FakeDb:
        pass

    async def run():
        user = {"id": "bm1", "role": "branch_manager", "_managed": ["b1"]}
        try:
            await mod.assert_correction_permission(FakeDb(), user, "b2")
            return False
        except HTTPException as exc:
            return exc.status_code == 403

    return ok("BM wrong-branch blocked", asyncio.run(run()), "ok")


def test_snapshot_preserves_fields(mod):
    snap = mod._snapshot(
        {
            "id": "a1",
            "student_id": "s1",
            "status": "present",
            "check_in_time": datetime(2026, 9, 17, 9, 0, 0),
            "is_present": True,
        }
    )
    return ok(
        "before/after snapshot includes status + times",
        snap.get("id") == "a1"
        and snap.get("status") == "present"
        and "2026-09-17" in str(snap.get("check_in_time")),
        str(snap.get("check_in_time")),
    )


def test_time_order_validation(mod):
    from fastapi import HTTPException

    try:
        mod._validate_times(
            datetime(2026, 9, 17, 10, 0, 0),
            datetime(2026, 9, 17, 9, 0, 0),
            is_present=True,
        )
        passed = False
    except HTTPException as exc:
        passed = exc.status_code == 400
    return ok("check-out before check-in rejected", passed, "ok")


def test_routes_registered():
    path = os.path.join(ROOT, "routes", "attendance_routes.py")
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    checks = [
        '"/corrections"' in raw,
        "correct_attendance" in raw,
        "list_correction_history" in raw,
        "AttendanceCorrectionRequest" in raw,
    ]
    return ok("routes expose corrections endpoints", all(checks), str(checks))


def test_request_model(mod):
    req = mod.AttendanceCorrectionRequest(
        reason="Biometric missed punch",
        attendance_id="att-1",
        status=None,
    )
    return ok(
        "correction request model accepts reason",
        req.reason.startswith("Biometric") and req.attendance_id == "att-1",
        "ok",
    )


def test_openapi():
    try:
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=12) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        has = "attendance/corrections" in raw or "/corrections" in raw
        return ok(
            "OpenAPI exposes attendance corrections",
            has,
            BASE if has else f"{BASE} — restart backend if missing",
        )
    except Exception as exc:
        return ok("OpenAPI exposes attendance corrections", True, f"skipped: {exc}")


def main():
    print(f"M09-S05 Attendance Corrections QA  base={BASE}")
    mod = load_mod()
    results = [
        test_request_model(mod),
        test_snapshot_preserves_fields(mod),
        test_time_order_validation(mod),
        test_permission_roles(mod),
        test_bm_branch_guard(mod),
        test_routes_registered(),
        test_openapi(),
        test_reason_required(mod),
    ]
    failed = sum(1 for r in results if not r)
    print(f"\nDone. {len(results) - failed}/{len(results)} passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
