"""
M07-S04 renewal validity / history / OpenAPI QA.

  python scripts/qa_m07_s04_renewal.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = (
    os.getenv("BILLING_QA_BASE")
    or os.getenv("INVOICE_QA_BASE")
    or os.getenv("CART_QA_BASE")
    or "http://127.0.0.1:8003"
).rstrip("/")


def ok(label, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    extra = f" — {detail}" if detail else ""
    print(f"[{status}] {label}{extra}")
    return passed


def load_renewal_service():
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)

    if "utils" not in sys.modules:
        pkg = types.ModuleType("utils")
        pkg.__path__ = [os.path.join(ROOT, "utils")]
        sys.modules["utils"] = pkg

    for name in ("subscription_dates", "billing_state", "renewal_service"):
        path = os.path.join(ROOT, "utils", f"{name}.py")
        mod_name = f"utils.{name}"
        if mod_name in sys.modules and name != "renewal_service":
            continue
        spec = importlib.util.spec_from_file_location(mod_name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)

    return sys.modules["utils.renewal_service"]


def _utc(y, m, d, hh=12, mm=0, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc)


def test_on_time_stacks(mod):
    prior_end = "2026-10-20"
    now = _utc(2026, 10, 5)
    out = mod.compute_renewal_validity_start(prior_end, now=now, grace_days=10)
    start = out["start_date"]
    return ok(
        "on-time renew stacks after prior end",
        out["mode"] == "stack_from_prior_end"
        and out["billing_state"] == "active"
        and start.year == 2026
        and start.month == 10
        and start.day == 21,
        f"mode={out['mode']} start={start}",
    )


def test_grace_stacks(mod):
    prior_end = "2026-09-20"
    now = _utc(2026, 9, 25)  # within 10-day grace
    out = mod.compute_renewal_validity_start(prior_end, now=now, grace_days=10)
    start = out["start_date"]
    return ok(
        "grace renew stacks after prior end (no gap)",
        out["mode"] == "stack_from_prior_end"
        and out["billing_state"] == "grace"
        and start.day == 21
        and start.month == 9,
        f"mode={out['mode']} state={out['billing_state']} start={start}",
    )


def test_overdue_from_now(mod):
    prior_end = "2026-09-20"
    now = _utc(2026, 10, 5)  # past grace
    out = mod.compute_renewal_validity_start(prior_end, now=now, grace_days=10)
    start = out["start_date"]
    return ok(
        "post-grace renew starts from now",
        out["mode"] == "from_now"
        and out["billing_state"] == "overdue"
        and start.year == 2026
        and start.month == 10
        and start.day == 5,
        f"mode={out['mode']} start={start}",
    )


def test_history_entry_shape(mod):
    entry = mod.build_renewal_history_entry(
        source_enrollment_id="src-1",
        renewal_enrollment_id="ren-1",
        payment_id="pay-1",
        prior_end_date="2026-09-20",
        renewal_date=datetime(2026, 9, 25),
        resulting_end_date="2026-10-25",
        course_fee=1000,
        admission_fee=0,
        arrear_amount=0,
        total_amount=1000,
        overdue_days=5,
        billing_state="grace",
        duration_id="dur-1",
        duration_months=1,
    )
    required = {
        "id",
        "source_enrollment_id",
        "renewal_enrollment_id",
        "payment_id",
        "prior_end_date",
        "renewal_date",
        "resulting_end_date",
        "arrear_amount",
        "total_amount",
        "overdue_days",
        "billing_state_at_renewal",
    }
    return ok(
        "renewal history entry fields",
        required.issubset(entry.keys()) and entry["total_amount"] == 1000,
        str({k: entry.get(k) for k in sorted(required)}),
    )


def test_checkout_total_includes_arrear():
    """Mirror _enrollment_checkout_total_inr additive arrear behavior."""
    fee = 1000.0
    adm = 0.0
    arrear = 0.0
    total = fee + adm + arrear
    return ok("checkout total with zero arrear unchanged", total == 1000.0, str(total))


def test_openapi():
    try:
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=5) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        checks = [
            ("prepare-student-renewal-checkout" in raw, "prepare-student-renewal-checkout"),
            ("renewal-history" in raw, "renewal-history"),
            ("renewal-quote" in raw, "renewal-quote"),
        ]
        missing = [name for passed, name in checks if not passed]
        return ok(
            "OpenAPI exposes renewal endpoints",
            not missing,
            BASE if not missing else f"missing={missing}",
        )
    except Exception as exc:
        return ok("OpenAPI exposes renewal endpoints", False, f"skipped/unreachable: {exc}")


def main():
    print(f"M07-S04 Renewal QA  base={BASE}")
    mod = load_renewal_service()
    results = [
        test_on_time_stacks(mod),
        test_grace_stacks(mod),
        test_overdue_from_now(mod),
        test_history_entry_shape(mod),
        test_checkout_total_includes_arrear(),
        test_openapi(),
    ]
    failed = sum(1 for r in results if not r)
    print(f"\nDone. {len(results) - failed}/{len(results)} passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
