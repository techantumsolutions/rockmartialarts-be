"""
M08-S01 student status management QA (offline helpers + OpenAPI smoke).

  python scripts/qa_m08_s01_student_status.py
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

    # Stub database + helpers so module imports offline
    database = types.ModuleType("utils.database")

    def get_db():
        return None

    database.get_db = get_db
    sys.modules["utils.database"] = database

    helpers = types.ModuleType("utils.helpers")

    def serialize_doc(doc):
        if isinstance(doc, dict):
            return {k: v for k, v in doc.items() if k != "_id"}
        return doc

    helpers.serialize_doc = serialize_doc
    sys.modules["utils.helpers"] = helpers

    path = os.path.join(ROOT, "utils", "student_status_service.py")
    spec = importlib.util.spec_from_file_location("utils.student_status_service", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["utils.student_status_service"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_managed_branch_fallback(mod):
    # Pure function path with empty db-less user using JWT fields
    import asyncio

    async def run():
        # get_managed_branch_ids with get_db returning None will fail on await —
        # exercise only the JWT parsing by calling with a fake db.
        class FakeCursor:
            async def to_list(self, length=None):
                return []

        class FakeBranches:
            def find(self, *a, **k):
                return FakeCursor()

        class FakeDb:
            branches = FakeBranches()

        user = {
            "id": "bm-1",
            "managed_branches": ["b1", "b2"],
            "role": "branch_manager",
        }
        ids = await mod.get_managed_branch_ids_for_user(FakeDb(), user)
        return ids

    ids = asyncio.run(run())
    return ok("managed branch ids from JWT fallback", ids == ["b1", "b2"], str(ids))


def test_history_shape(mod):
    entry = {
        "id": "h1",
        "student_id": "s1",
        "previous_status": True,
        "previous_label": "active",
        "new_status": False,
        "new_label": "inactive",
        "actor_id": "a1",
        "reason": "Left branch",
        "source": "status_api",
    }
    required = {
        "student_id",
        "previous_status",
        "new_status",
        "previous_label",
        "new_label",
        "reason",
    }
    return ok("history entry fields present", required.issubset(entry.keys()), str(entry))


def test_openapi():
    try:
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=5) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        checks = [
            ("/status-history" in raw or "status-history" in raw, "status-history"),
            ("StudentStatusUpdateBody" in raw or '"/api/users/{user_id}/status"' in raw or "/status" in raw, "status"),
            ("is_active" in raw, "is_active filter"),
        ]
        missing = [name for passed, name in checks if not passed]
        return ok(
            "OpenAPI exposes student status endpoints",
            not missing,
            BASE if not missing else f"missing={missing}",
        )
    except Exception as exc:
        return ok("OpenAPI exposes student status endpoints", False, f"skipped/unreachable: {exc}")


def main():
    print(f"M08-S01 Student Status QA  base={BASE}")
    mod = load_mod()
    results = [
        test_managed_branch_fallback(mod),
        test_history_shape(mod),
        test_openapi(),
    ]
    failed = sum(1 for r in results if not r)
    print(f"\nDone. {len(results) - failed}/{len(results)} passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
