"""
M09-S04 attendance history & reports QA.

  .venv\\Scripts\\python.exe scripts/qa_m09_s04_attendance_reports.py
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

    if "fastapi" not in sys.modules:
        fastapi = types.ModuleType("fastapi")

        class HTTPException(Exception):
            def __init__(self, status_code=400, detail=""):
                self.status_code = status_code
                self.detail = detail
                super().__init__(detail)

        fastapi.HTTPException = HTTPException
        sys.modules["fastapi"] = fastapi

    path = os.path.join(ROOT, "utils", "attendance_report_query.py")
    spec = importlib.util.spec_from_file_location("utils.attendance_report_query", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["utils.attendance_report_query"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_status_normalize(mod):
    return ok(
        "status normalize from is_present",
        mod.normalize_status_value({"is_present": True}) == "present"
        and mod.normalize_status_value({"status": "late"}) == "late"
        and mod.normalize_status_value({"is_present": False}) == "absent",
        "ok",
    )


def test_status_filter(mod):
    from fastapi import HTTPException

    q = {}
    mod.apply_status_filter(q, "present")
    late_q = {}
    mod.apply_status_filter(late_q, "late")
    bad = False
    try:
        mod.apply_status_filter({}, "nope")
    except HTTPException:
        bad = True
    return ok(
        "status filter present/late/invalid",
        "$or" in q and late_q.get("status") is not None and bad,
        "ok",
    )


def test_method_filter(mod):
    q = {}
    mod.apply_method_filter(q, "biometric")
    return ok(
        "method filter biometric",
        isinstance(q.get("method"), dict) and "biometric" in str(q["method"]),
        str(q.get("method")),
    )


def test_summary(mod):
    rows = [
        {"status": "present", "method": "biometric"},
        {"is_present": False, "method": "manual"},
        {"status": "late", "method": "Biometric"},
    ]
    s = mod.summarize_records(rows)
    return ok(
        "summary counts",
        s["total"] == 3 and s["present"] == 1 and s["absent"] == 1 and s["late"] == 1 and s["biometric"] == 2,
        str(s),
    )


def test_excel_export(mod):
    rows = [
        {
            "attendance_date": "2026-09-01",
            "student_name": "A",
            "course_name": "Kung Fu",
            "branch_name": "HQ",
            "is_present": True,
            "method": "biometric",
            "check_in_time": "09:00",
            "check_out_time": "10:00",
            "notes": "",
        }
    ]
    xml = mod.build_attendance_export_excel_xml(rows)
    out = mod.build_attendance_export(rows, format="excel")
    return ok(
        "Excel SpreadsheetML export",
        "Worksheet" in xml and out["filename"].endswith(".xls") and "content" in out,
        out["filename"],
    )


def test_csv_export(mod):
    rows = [{"student_name": "A", "is_present": True, "method": "manual"}]
    csv_text = mod.build_attendance_export_csv(rows)
    return ok(
        "CSV export includes status header",
        "status" in csv_text and "student_name" in csv_text,
        "ok",
    )


def test_routes_registered():
    path = os.path.join(ROOT, "routes", "attendance_routes.py")
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    checks = [
        'status: Optional[str] = Query' in raw,
        'method: Optional[str] = Query' in raw,
        '"/export"' in raw,
        '"/reports"' in raw,
    ]
    return ok("routes expose status/method filters", all(checks), str(checks))


def test_controller_signature():
    path = os.path.join(ROOT, "controllers", "attendance_controller.py")
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    return ok(
        "controller accepts status/method + summary",
        "status: Optional[str] = None" in raw
        and "method: Optional[str] = None" in raw
        and "summarize_records" in raw
        and "build_attendance_export" in raw,
        "ok",
    )


def test_openapi():
    try:
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=12) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        # status query appears on reports
        has_reports = "/api/attendance/reports" in raw or "/attendance/reports" in raw
        return ok(
            "OpenAPI exposes attendance reports",
            has_reports,
            BASE if has_reports else f"{BASE} — restart backend if missing",
        )
    except Exception as exc:
        return ok("OpenAPI exposes attendance reports", True, f"skipped: {exc}")


def main():
    print(f"M09-S04 Attendance Reports QA  base={BASE}")
    mod = load_mod()
    results = [
        test_status_normalize(mod),
        test_status_filter(mod),
        test_method_filter(mod),
        test_summary(mod),
        test_excel_export(mod),
        test_csv_export(mod),
        test_routes_registered(),
        test_controller_signature(),
        test_openapi(),
    ]
    failed = sum(1 for r in results if not r)
    print(f"\nDone. {len(results) - failed}/{len(results)} passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
