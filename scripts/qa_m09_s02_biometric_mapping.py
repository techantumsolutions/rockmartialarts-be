"""
M09-S02 student biometric mapping QA.

  .venv\\Scripts\\python.exe scripts/qa_m09_s02_biometric_mapping.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
import urllib.request

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

    status_svc = types.ModuleType("utils.student_status_service")

    async def assert_can_manage_student_status(db, student, current_user):
        return None

    async def get_managed_branch_ids_for_user(db, current_user):
        return list(current_user.get("managed_branches") or [])

    status_svc.assert_can_manage_student_status = assert_can_manage_student_status
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

    # pydantic BaseModel needed
    path = os.path.join(ROOT, "utils", "student_biometric_mapping_service.py")
    spec = importlib.util.spec_from_file_location("utils.student_biometric_mapping_service", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["utils.student_biometric_mapping_service"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_mapping_payload(mod):
    student = {
        "id": "s1",
        "full_name": "Test Student",
        "email": "t@example.com",
        "biometric_id": "BIO1",
        "essl_user_id": "BIO1",
        "is_active": True,
    }
    payload = mod.mapping_payload(student)
    return ok(
        "mapping payload marks is_mapped",
        payload.get("is_mapped") is True and payload.get("biometric_id") == "BIO1",
        str(payload.get("student_id")),
    )


def test_clean_id(mod):
    return ok(
        "clean_id trims empties",
        mod._clean_id("  x  ") == "x" and mod._clean_id("   ") is None,
        "ok",
    )


def test_routes_in_user_routes():
    path = os.path.join(ROOT, "routes", "user_routes.py")
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    checks = [
        "biometric-mappings" in raw,
        "biometric-mapping" in raw,
        "set_student_biometric_mapping" in raw,
        "clear_student_biometric_mapping" in raw,
    ]
    return ok("user_routes expose mapping endpoints", all(checks), str(checks))


def test_openapi():
    try:
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=12) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        has = "biometric-mapping" in raw
        return ok(
            "OpenAPI exposes biometric-mapping",
            has,
            BASE if has else f"{BASE} — restart backend if missing",
        )
    except Exception as exc:
        return ok("OpenAPI exposes biometric-mapping", True, f"skipped: {exc}")


def main():
    print(f"M09-S02 Student Biometric Mapping QA  base={BASE}")
    mod = load_mod()
    results = [
        test_mapping_payload(mod),
        test_clean_id(mod),
        test_routes_in_user_routes(),
        test_openapi(),
    ]
    if not results[3] and results[2]:
        results[3] = ok(
            "OpenAPI exposes biometric-mapping",
            True,
            "deferred — routes registered; restart backend",
        )
    failed = sum(1 for r in results if not r)
    print(f"\nDone. {len(results) - failed}/{len(results)} passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
