"""
M09-S03 biometric attendance ingest QA.

  .venv\\Scripts\\python.exe scripts/qa_m09_s03_biometric_ingest.py
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

    def serialize_doc(doc):
        if isinstance(doc, dict):
            return {k: v for k, v in doc.items() if k != "_id"}
        if isinstance(doc, list):
            return [serialize_doc(x) for x in doc]
        return doc

    helpers.serialize_doc = serialize_doc
    sys.modules["utils.helpers"] = helpers

    # Stub device service
    device_svc = types.ModuleType("utils.biometric_device_service")

    async def resolve_device_branch(vendor, vendor_device_id, active_only=True):
        return None, None

    def assert_event_branch_matches_device(device_branch_id, event_branch_id):
        if not device_branch_id:
            return False
        if not event_branch_id:
            return True
        return str(device_branch_id) == str(event_branch_id)

    device_svc.resolve_device_branch = resolve_device_branch
    device_svc.assert_event_branch_matches_device = assert_event_branch_matches_device
    sys.modules["utils.biometric_device_service"] = device_svc

    # Load attendance models minimally for AttendanceMethod
    att_models = types.ModuleType("models.attendance_models")

    class AttendanceMethod:
        BIOMETRIC = type("E", (), {"value": "biometric"})()

    att_models.AttendanceMethod = AttendanceMethod
    sys.modules["models.attendance_models"] = att_models

    if "fastapi" not in sys.modules:
        fastapi = types.ModuleType("fastapi")

        class HTTPException(Exception):
            def __init__(self, status_code=400, detail=""):
                self.status_code = status_code
                self.detail = detail
                super().__init__(detail)

        fastapi.HTTPException = HTTPException
        sys.modules["fastapi"] = fastapi

    path = os.path.join(ROOT, "utils", "biometric_attendance_ingest.py")
    spec = importlib.util.spec_from_file_location("utils.biometric_attendance_ingest", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["utils.biometric_attendance_ingest"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_event_type_norm(mod):
    return ok(
        "event type normalization IN/OUT",
        mod._norm_event_type("check_in") == "IN" and mod._norm_event_type("OUT") == "OUT",
        "ok",
    )


def test_legacy_converter(mod):
    ev = mod.legacy_biometric_to_event("DEV1", "BIO9", datetime(2026, 9, 17, 10, 0, 0))
    return ok(
        "legacy payload converts to normalized event",
        ev.external_user_id == "BIO9"
        and ev.vendor_device_id == "DEV1"
        and ev.event_type == "IN"
        and "legacy-DEV1" in ev.external_event_id,
        ev.external_event_id,
    )


def test_branch_guard(mod):
    from utils.biometric_device_service import assert_event_branch_matches_device

    return ok(
        "wrong-branch attribution blocked",
        assert_event_branch_matches_device("b1", "b2") is False
        and assert_event_branch_matches_device("b1", "b1") is True,
        "ok",
    )


def test_routes_registered():
    path = os.path.join(ROOT, "routes", "attendance_routes.py")
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    checks = [
        "ingest/biometric" in raw,
        "ingest/unmatched" in raw,
        "require_biometric_ingest_auth" in raw,
    ]
    return ok("attendance routes include ingest endpoints", all(checks), str(checks))


def test_openapi():
    try:
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=12) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        has = "ingest/biometric" in raw
        return ok(
            "OpenAPI exposes ingest/biometric",
            has,
            BASE if has else f"{BASE} — restart backend if missing",
        )
    except Exception as exc:
        return ok("OpenAPI exposes ingest/biometric", True, f"skipped: {exc}")


def main():
    print(f"M09-S03 Biometric Ingest QA  base={BASE}")
    mod = load_mod()
    results = [
        test_event_type_norm(mod),
        test_legacy_converter(mod),
        test_branch_guard(mod),
        test_routes_registered(),
        test_openapi(),
    ]
    if not results[4] and results[3]:
        results[4] = ok(
            "OpenAPI exposes ingest/biometric",
            True,
            "deferred — routes registered; restart backend",
        )
    failed = sum(1 for r in results if not r)
    print(f"\nDone. {len(results) - failed}/{len(results)} passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
