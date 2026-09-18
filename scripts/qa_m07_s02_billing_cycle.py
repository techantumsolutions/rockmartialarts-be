"""
M07-S02 billing cycle date QA (month-end edge cases + optional OpenAPI smoke).

  python scripts/qa_m07_s02_billing_cycle.py
"""
import importlib.util
import os
import sys
import urllib.request
from datetime import datetime

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


def load_dates():
    path = os.path.join(ROOT, "utils", "billing_cycle_dates.py")
    spec = importlib.util.spec_from_file_location("billing_cycle_dates", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_month_end_cases(mod):
    cases = [
        (datetime(2026, 1, 31), 1, (2026, 2, 28)),
        (datetime(2024, 1, 31), 1, (2024, 2, 29)),  # leap year
        (datetime(2026, 1, 31), 3, (2026, 4, 30)),
        (datetime(2026, 3, 31), 1, (2026, 4, 30)),
        (datetime(2026, 8, 31), 1, (2026, 9, 30)),
        (datetime(2026, 1, 30), 1, (2026, 2, 28)),
        (datetime(2026, 1, 29), 1, (2026, 2, 28)),
        (datetime(2026, 1, 28), 1, (2026, 2, 28)),
        (datetime(2026, 5, 31), 1, (2026, 6, 30)),
        (datetime(2026, 10, 31), 1, (2026, 11, 30)),
        (datetime(2026, 12, 31), 1, (2027, 1, 31)),
        (datetime(2026, 2, 28), 1, (2026, 3, 28)),
    ]
    failed = []
    for start, months, expected in cases:
        out = mod.add_calendar_months(start, months, anchor_day=start.day)
        got = (out.year, out.month, out.day)
        if got != expected:
            failed.append(f"{start.date()}+{months}m => {got} expected {expected}")
    return ok("month-end calendar additions", not failed, "; ".join(failed) if failed else f"{len(cases)} cases")


def test_billing_period(mod):
    paid = datetime(2026, 1, 31, 10, 15, 0)
    start, end, next_due, anchor = mod.compute_billing_period(paid, 1)
    return ok(
        "paid-date period start/end/next_due",
        start == paid and end.date() == datetime(2026, 2, 28).date() and next_due == end and anchor == 31,
        f"end={end.date()} anchor={anchor}",
    )


def test_status(mod):
    end = datetime(2026, 9, 20)
    active = mod.derive_cycle_status(end, now=datetime(2026, 9, 1), due_soon_days=7)
    soon = mod.derive_cycle_status(end, now=datetime(2026, 9, 15), due_soon_days=7)
    overdue = mod.derive_cycle_status(end, now=datetime(2026, 9, 21), due_soon_days=7)
    return ok(
        "cycle status active/due_soon/overdue",
        active == "active" and soon == "due_soon" and overdue == "overdue",
        f"{active}/{soon}/{overdue}",
    )


def test_openapi():
    try:
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=5) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        return ok("OpenAPI exposes /api/billing-cycles", "/api/billing-cycles" in raw, BASE)
    except Exception as exc:
        return ok("OpenAPI exposes /api/billing-cycles", False, f"skipped/unreachable: {exc}")


def main():
    print(f"M07-S02 Billing Cycle QA  base={BASE}")
    mod = load_dates()
    results = [
        test_month_end_cases(mod),
        test_billing_period(mod),
        test_status(mod),
        test_openapi(),
    ]
    failed = sum(1 for r in results if not r)
    print(f"\nDone. {len(results) - failed}/{len(results)} passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
