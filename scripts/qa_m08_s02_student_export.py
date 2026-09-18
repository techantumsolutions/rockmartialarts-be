"""
M08-S02 student filters & export QA (offline helpers + OpenAPI smoke).

  python scripts/qa_m08_s02_student_export.py
"""
from __future__ import annotations

import asyncio
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

    def get_db():
        return None

    database.get_db = get_db
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

    # Minimal stub for student_status_service used by branch scope
    status_svc = types.ModuleType("utils.student_status_service")

    async def get_managed_branch_ids_for_user(db, current_user):
        managed = current_user.get("managed_branches") or []
        return [str(x) for x in managed if x]

    status_svc.get_managed_branch_ids_for_user = get_managed_branch_ids_for_user
    sys.modules["utils.student_status_service"] = status_svc

    # Stub fastapi HTTPException for offline import
    if "fastapi" not in sys.modules:
        fastapi = types.ModuleType("fastapi")

        class HTTPException(Exception):
            def __init__(self, status_code=400, detail=""):
                self.status_code = status_code
                self.detail = detail
                super().__init__(detail)

        fastapi.HTTPException = HTTPException
        sys.modules["fastapi"] = fastapi

    path = os.path.join(ROOT, "utils", "student_report_query.py")
    spec = importlib.util.spec_from_file_location("utils.student_report_query", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["utils.student_report_query"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_csv_export_fields(mod):
    rows = [
        {
            "student_id": "s1",
            "full_name": "Ada Lovelace",
            "email": "ada@example.com",
            "phone": "999",
            "is_active": True,
            "gender": "female",
            "date_of_birth": "1815-12-10",
            "branch_names": "Main",
            "course_names": "Karate",
            "total_enrollments": 1,
            "created_at": "2024-01-01T00:00:00",
        }
    ]
    csv_text = mod.build_student_export_csv(rows)
    header = csv_text.splitlines()[0]
    missing = [f for f in mod.EXPORT_FIELDS if f not in header]
    has_row = "Ada Lovelace" in csv_text and "active" in csv_text
    return ok(
        "CSV export contains approved fields and active label",
        not missing and has_row,
        f"missing={missing}" if missing else f"cols={len(mod.EXPORT_FIELDS)}",
    )


def test_excel_export_shape(mod):
    rows = [{"student_id": "s1", "full_name": "Test", "is_active": False}]
    xml = mod.build_student_export_excel_xml(rows)
    checks = [
        "Workbook" in xml,
        "Worksheet" in xml,
        "student_id" in xml,
        "inactive" in xml,
    ]
    return ok("Excel SpreadsheetML export shape", all(checks), "xls xml")


def test_bm_foreign_branch_forbidden(mod):
    class FakeDb:
        pass

    async def run():
        try:
            await mod.resolve_authorized_branch_scope(
                FakeDb(),
                {"role": "branch_manager", "managed_branches": ["b1"]},
                "other-branch",
            )
            return None
        except Exception as exc:
            return exc

    exc = asyncio.run(run())
    code = getattr(exc, "status_code", None)
    return ok(
        "BM cannot filter/export foreign branch (403)",
        code == 403,
        str(getattr(exc, "detail", exc)),
    )


def test_bm_managed_scope(mod):
    class FakeDb:
        pass

    async def run():
        return await mod.resolve_authorized_branch_scope(
            FakeDb(),
            {"role": "branch_manager", "managed_branches": ["b1", "b2"]},
            None,
        )

    allowed, effective = asyncio.run(run())
    return ok(
        "BM default scope is managed branches",
        allowed == ["b1", "b2"] and effective is None,
        str(allowed),
    )


def test_sa_all_branches(mod):
    class FakeDb:
        pass

    async def run():
        return await mod.resolve_authorized_branch_scope(
            FakeDb(),
            {"role": "super_admin"},
            None,
        )

    allowed, effective = asyncio.run(run())
    return ok(
        "Super admin can query all branches",
        allowed is None and effective is None,
        str((allowed, effective)),
    )


def test_openapi():
    try:
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=5) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        checks = [
            ("/api/reports/students/list" in raw or '"/students/list"' in raw, "students/list"),
            ("/api/reports/students/export" in raw or '"/students/export"' in raw, "students/export"),
        ]
        missing = [name for passed, name in checks if not passed]
        return ok(
            "OpenAPI exposes student list/export endpoints",
            not missing,
            BASE if not missing else f"missing={missing}",
        )
    except Exception as exc:
        return ok(
            "OpenAPI exposes student list/export endpoints",
            False,
            f"skipped/unreachable: {exc}",
        )


def main():
    print(f"M08-S02 Student Filters & Export QA  base={BASE}")
    mod = load_mod()
    results = [
        test_csv_export_fields(mod),
        test_excel_export_shape(mod),
        test_bm_foreign_branch_forbidden(mod),
        test_bm_managed_scope(mod),
        test_sa_all_branches(mod),
        test_openapi(),
    ]
    failed = sum(1 for r in results if not r)
    print(f"\nDone. {len(results) - failed}/{len(results)} passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
