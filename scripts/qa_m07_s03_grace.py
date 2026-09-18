"""
M07-S03 expiry / 10-day grace / overdue QA + optional OpenAPI smoke.

  python scripts/qa_m07_s03_grace.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
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


def load_billing_state():
    """Load billing_state without importing utils/__init__ (avoids FastAPI deps for offline QA)."""
    import types

    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)

    if "utils" not in sys.modules:
        pkg = types.ModuleType("utils")
        pkg.__path__ = [os.path.join(ROOT, "utils")]
        sys.modules["utils"] = pkg

    sub_path = os.path.join(ROOT, "utils", "subscription_dates.py")
    sub_spec = importlib.util.spec_from_file_location("utils.subscription_dates", sub_path)
    sub_mod = importlib.util.module_from_spec(sub_spec)
    sys.modules["utils.subscription_dates"] = sub_mod
    sub_spec.loader.exec_module(sub_mod)

    path = os.path.join(ROOT, "utils", "billing_state.py")
    spec = importlib.util.spec_from_file_location("utils.billing_state", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["utils.billing_state"] = mod
    spec.loader.exec_module(mod)
    return mod


def _utc(y, m, d, hh=12, mm=0, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc)


def test_before_expiry(mod):
    end = "2026-09-20"
    now = _utc(2026, 9, 15)
    snap = mod.compute_enrollment_billing_state(end, now=now, grace_days=10)
    return ok(
        "before expiry => active",
        snap["billing_state"] == "active"
        and snap["is_expired"] is False
        and snap["overdue_days"] == 0
        and snap["grace_days_remaining"] is None
        and snap["days_until_expiry"] == 5,
        str(snap),
    )


def test_on_expiry_day(mod):
    """Still valid through end-of-day UTC on expiry date."""
    end = "2026-09-20"
    now = _utc(2026, 9, 20, 18, 0, 0)
    snap = mod.compute_enrollment_billing_state(end, now=now, grace_days=10)
    return ok(
        "on expiry calendar day (before EOD) => active",
        snap["billing_state"] == "active" and snap["is_expired"] is False,
        str(snap),
    )


def test_day_after_expiry_grace(mod):
    end = "2026-09-20"
    now = _utc(2026, 9, 21, 10, 0, 0)
    snap = mod.compute_enrollment_billing_state(end, now=now, grace_days=10)
    return ok(
        "day 1 after expiry => grace",
        snap["billing_state"] == "grace"
        and snap["is_within_grace"] is True
        and snap["overdue_days"] == 1
        and snap["grace_days_remaining"] == 9,
        str(snap),
    )


def test_grace_mid_window(mod):
    end = "2026-09-20"
    now = _utc(2026, 9, 25, 12, 0, 0)  # day 5 after expiry
    snap = mod.compute_enrollment_billing_state(end, now=now, grace_days=10)
    return ok(
        "day 5 after expiry => grace",
        snap["billing_state"] == "grace"
        and snap["overdue_days"] == 5
        and snap["grace_days_remaining"] == 5,
        str(snap),
    )


def test_grace_last_day(mod):
    """Grace ends at end_eod + 10 days; still grace on that instant."""
    end = "2026-09-20"
    end_eod = datetime(2026, 9, 20, 23, 59, 59, 999000, tzinfo=timezone.utc)
    grace_ends = end_eod + timedelta(days=10)
    snap = mod.compute_enrollment_billing_state(end, now=grace_ends, grace_days=10)
    return ok(
        "last instant of grace => grace",
        snap["billing_state"] == "grace" and snap["is_within_grace"] is True,
        f"grace_ends={grace_ends} snap={snap}",
    )


def test_post_grace_overdue(mod):
    end = "2026-09-20"
    now = _utc(2026, 10, 1, 0, 0, 1)  # after grace ends 2026-09-30 23:59:59.999
    snap = mod.compute_enrollment_billing_state(end, now=now, grace_days=10)
    return ok(
        "post-grace => overdue",
        snap["billing_state"] == "overdue"
        and snap["is_within_grace"] is False
        and snap["grace_days_remaining"] == 0
        and snap["overdue_days"] >= 11,
        str(snap),
    )


def test_arrear_placeholder(mod):
    arrear = mod.calculate_arrear_amount(
        overdue_days=12,
        course_fee=1500.0,
        duration_months=1,
        billing_state="overdue",
    )
    amount = arrear.get("arrear_amount")
    return ok(
        "arrear placeholder is 0 + pending flag",
        amount is not None
        and float(amount) == 0.0
        and arrear.get("arrear_applied") is False
        and arrear.get("arrear_rule") == "pending_client_confirmation",
        str(arrear),
    )


def test_unknown_end_date(mod):
    snap = mod.compute_enrollment_billing_state(None, now=_utc(2026, 9, 20))
    return ok(
        "missing end date => unknown",
        snap["billing_state"] == "unknown" and snap["is_expired"] is False,
        str(snap),
    )


def test_openapi():
    try:
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=5) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        return ok(
            "OpenAPI exposes /api/payments/renewal-quote",
            "/api/payments/renewal-quote" in raw or '"/payments/renewal-quote"' in raw or "renewal-quote" in raw,
            BASE,
        )
    except Exception as exc:
        return ok("OpenAPI exposes /api/payments/renewal-quote", False, f"skipped/unreachable: {exc}")


def main():
    print(f"M07-S03 Grace / Expiry QA  base={BASE}")
    mod = load_billing_state()
    results = [
        test_before_expiry(mod),
        test_on_expiry_day(mod),
        test_day_after_expiry_grace(mod),
        test_grace_mid_window(mod),
        test_grace_last_day(mod),
        test_post_grace_overdue(mod),
        test_arrear_placeholder(mod),
        test_unknown_end_date(mod),
        test_openapi(),
    ]
    failed = sum(1 for r in results if not r)
    print(f"\nDone. {len(results) - failed}/{len(results)} passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
