#!/usr/bin/env python3
"""M09 API smoke (no physical device required).

Validates OpenAPI presence of biometric device, mapping, ingest, reports, corrections.
Exit 0 if routes exist; exit 1 if any missing.
"""
from __future__ import annotations

import json
import sys
import urllib.request

REQUIRED = [
    "/api/biometric-devices",
    "/api/users/students/biometric-mappings",
    "/api/attendance/ingest/biometric",
    "/api/attendance/reports",
    "/api/attendance/corrections",
]


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8003/openapi.json"
    with urllib.request.urlopen(url, timeout=20) as resp:
        data = json.load(resp)
    paths = set(data.get("paths") or {})
    missing = [p for p in REQUIRED if p not in paths]
    if missing:
        print("MISSING", missing)
        return 1
    print("OK M09 biometric API surface present:", len(REQUIRED), "routes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
