#!/usr/bin/env python3
"""M23-S02/S03: Static checks that dashboard stats expose M23 metric keys."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CTRL = ROOT / "controllers" / "dashboard_controller.py"


def main() -> int:
    text = CTRL.read_text(encoding="utf-8")
    keys = [
        "inactive_students",
        "renewals_count",
        "leads_count",
        "demo_bookings_count",
        "training_requests_count",
        "events_count",
        "partners_count",
        "enrollments_count",
        "managed_branch_ids",
    ]
    failed = []
    for k in keys:
        if k not in text:
            failed.append(k)
            print(f"FAIL: missing {k}")
        else:
            print(f"OK: {k}")
    if "pay_student_ids" not in text and "branch_id" not in text:
        failed.append("recent_activities_scope")
    else:
        print("OK: recent activities scoping present")
    if failed:
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
