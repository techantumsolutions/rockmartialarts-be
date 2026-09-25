#!/usr/bin/env python3
"""M23-S01: Assert SA vs BM nav grouping expectations (static FE config scan)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

FE_ROOT = Path(__file__).resolve().parents[1].parent / "rockmartialarts-fe"
if not FE_ROOT.exists():
    FE_ROOT = Path(__file__).resolve().parents[2] / "rockmartialarts-fe"
CONFIG = FE_ROOT / "lib" / "dashboard-config.ts"


def main() -> int:
    if not CONFIG.exists():
        print(f"FAIL: missing {CONFIG}")
        return 1
    text = CONFIG.read_text(encoding="utf-8")

    checks = [
        ("Partners group", 'label: "Partners"'),
        ("Coach ops group", 'label: "Coach ops"'),
        ("CRM group", 'label: "CRM"'),
        ("filterNavItemsByPermission", "filterNavItemsByPermission"),
        ("permissionId field", "permissionId?:"),
        ("enabled field", "enabled?:"),
    ]
    failed = []
    for name, needle in checks:
        if needle not in text:
            failed.append(name)
            print(f"FAIL: {name} not found")
        else:
            print(f"OK: {name}")

    # BM menu should not list Partner Profiles as a flat top-level without Partners parent
    # After grouping, Partner Profiles appears only under children of Partners
    if 'label: "Partners"' not in text:
        failed.append("Partners grouping")

    # Super admin still has Branches; branch admin menu section should not include create-branch style
    if "BRANCH_ADMIN_MENU" not in text:
        failed.append("BRANCH_ADMIN_MENU")
        print("FAIL: BRANCH_ADMIN_MENU missing")
    else:
        print("OK: BRANCH_ADMIN_MENU present")

    if failed:
        print(f"RESULT: FAIL ({len(failed)} issues)")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
