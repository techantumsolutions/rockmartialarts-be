#!/usr/bin/env python3
"""M23-S04/S05: Assert operational report routes + export job helpers exist."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    routes = (ROOT / "routes" / "reports_routes.py").read_text(encoding="utf-8")
    util = (ROOT / "utils" / "operational_reports.py").read_text(encoding="utf-8")
    checks = [
        (routes, '/enrollments/export', "enrollments export route"),
        (routes, '/renewals/export', "renewals export route"),
        (routes, '/leads/export', "leads export route"),
        (routes, '/events/export', "events export route"),
        (routes, '/exports/{job_id}/download', "export download route"),
        (util, "EXPORT_ROW_THRESHOLD", "async threshold"),
        (util, "export_audit_logs", "export audit"),
        (util, "export_jobs", "export jobs collection"),
        (util, "write_export_audit", "audit helper"),
    ]
    failed = 0
    for hay, needle, name in checks:
        if needle not in hay:
            print(f"FAIL: {name}")
            failed += 1
        else:
            print(f"OK: {name}")
    if failed:
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
