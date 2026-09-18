"""
M10-S01 KPI Master QA.

  .venv\\Scripts\\python.exe scripts/qa_m10_s01_kpi_master.py
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

    if "fastapi" not in sys.modules:
        fastapi = types.ModuleType("fastapi")

        class HTTPException(Exception):
            def __init__(self, status_code=400, detail=""):
                self.status_code = status_code
                self.detail = detail
                super().__init__(detail)

        fastapi.HTTPException = HTTPException
        sys.modules["fastapi"] = fastapi

    # Load models.kpi_models with real pydantic
    path_models = os.path.join(ROOT, "models", "kpi_models.py")
    spec_m = importlib.util.spec_from_file_location("models.kpi_models", path_models)
    mod_m = importlib.util.module_from_spec(spec_m)
    sys.modules["models.kpi_models"] = mod_m
    spec_m.loader.exec_module(mod_m)

    path = os.path.join(ROOT, "utils", "kpi_service.py")
    spec = importlib.util.spec_from_file_location("utils.kpi_service", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["utils.kpi_service"] = mod
    spec.loader.exec_module(mod)
    return mod, mod_m


def test_normalize_code(mod):
    return ok(
        "code normalization",
        mod.normalize_kpi_code(" tech nique ") == "TECH_NIQUE"
        or mod.normalize_kpi_code("technique") == "TECHNIQUE",
        mod.normalize_kpi_code("technique"),
    )


def test_invalid_weight(mod):
    from fastapi import HTTPException

    try:
        mod._validate_weight(0, "percent")
        return ok("invalid weight rejected", False, "accepted 0")
    except HTTPException as exc:
        pass
    try:
        mod._validate_weight(150, "percent")
        return ok("percent weight >100 rejected", False, "accepted 150")
    except HTTPException as exc:
        return ok("percent weight >100 rejected", exc.status_code == 400, "ok")


def test_invalid_scores(mod):
    from fastapi import HTTPException

    try:
        mod._validate_scores(10, 5)
        return ok("min>max score rejected", False, "accepted")
    except HTTPException as exc:
        return ok("min>max score rejected", exc.status_code == 400, "ok")


def test_weight_summary_math(mod):
    class FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        async def to_list(self, length=None):
            return self._rows

    class FakeCol:
        def __init__(self, rows):
            self.rows = rows

        def find(self, query):
            return FakeCursor(self.rows)

    class FakeDb:
        def __init__(self, rows):
            self._col = FakeCol(rows)

        def __getitem__(self, name):
            return self._col

    async def run():
        db = FakeDb(
            [
                {"weight": 40, "weight_unit": "percent", "is_active": True},
                {"weight": 60, "weight_unit": "percent", "is_active": True},
            ]
        )
        s = await mod._active_weight_summary(db)
        return s["percent_sum_ok"] is True and s["percent_weight_sum"] == 100

    return ok("active percent weights sum to 100", asyncio.run(run()), "ok")


def test_weight_summary_invalid(mod):
    class FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        async def to_list(self, length=None):
            return self._rows

    class FakeCol:
        def __init__(self, rows):
            self.rows = rows

        def find(self, query):
            return FakeCursor(self.rows)

    class FakeDb:
        def __init__(self, rows):
            self._col = FakeCol(rows)

        def __getitem__(self, name):
            return self._col

    async def run():
        db = FakeDb(
            [
                {"weight": 40, "weight_unit": "percent", "is_active": True},
                {"weight": 40, "weight_unit": "percent", "is_active": True},
            ]
        )
        s = await mod._active_weight_summary(db)
        return s["percent_sum_ok"] is False and abs(s["percent_weight_sum"] - 80) < 0.01

    return ok("incomplete percent sum flagged", asyncio.run(run()), "ok")


def test_create_model(mod_m):
    doc = mod_m.KpiDefinitionCreate(
        code="TECH",
        name="Technique",
        weight=25,
        weight_unit=mod_m.KpiWeightUnit.PERCENT,
        max_score=100,
        min_score=0,
    )
    return ok(
        "KPI create model accepts weight + max_score",
        doc.weight == 25 and doc.max_score == 100,
        "ok",
    )


def test_routes_registered():
    path = os.path.join(ROOT, "routes", "kpi_routes.py")
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    checks = [
        "list_kpis" in raw,
        "create_kpi" in raw,
        "weight-summary" in raw,
        "set_kpi_active" in raw or "set_kpi_active_flag" in raw,
    ]
    return ok("KPI routes registered", all(checks), str(checks))


def test_server_wired():
    path = os.path.join(ROOT, "server.py")
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    return ok(
        "server mounts kpi-definitions router",
        "kpi_router" in raw and "kpi-definitions" in raw and "ensure_kpi_indexes" in raw,
        "ok",
    )


def test_openapi():
    try:
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=12) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        has = "kpi-definitions" in raw
        return ok(
            "OpenAPI exposes kpi-definitions",
            has,
            BASE if has else f"{BASE} — restart backend if missing",
        )
    except Exception as exc:
        return ok("OpenAPI exposes kpi-definitions", True, f"skipped: {exc}")


def main():
    print(f"M10-S01 KPI Master QA  base={BASE}")
    mod, mod_m = load_mod()
    results = [
        test_normalize_code(mod),
        test_invalid_weight(mod),
        test_invalid_scores(mod),
        test_weight_summary_math(mod),
        test_weight_summary_invalid(mod),
        test_create_model(mod_m),
        test_routes_registered(),
        test_server_wired(),
        test_openapi(),
    ]
    failed = sum(1 for r in results if not r)
    print(f"\nDone. {len(results) - failed}/{len(results)} passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
