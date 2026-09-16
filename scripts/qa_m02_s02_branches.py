"""
Smoke checks for M02-S02 public branch discovery.

  python scripts/qa_m02_s02_branches.py

Optional:
  BRANCH_QA_BASE=http://127.0.0.1:8003
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = (os.getenv("BRANCH_QA_BASE") or os.getenv("GEOGRAPHY_QA_BASE") or "http://127.0.0.1:8003").rstrip("/")


def get(path: str):
    url = f"{BASE}{path}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return resp.status, body, None
    except urllib.error.HTTPError as exc:
        return exc.code, None, str(exc)
    except Exception as exc:
        return None, None, str(exc)


def ok(label: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    extra = f" — {detail}" if detail else ""
    print(f"[{status}] {label}{extra}")
    return passed


def main():
    failures = 0

    status, body, err = get("/health")
    if not ok("backend health", status == 200, err or f"status={status}"):
        print("Backend is not reachable. Start FastAPI and retry.")
        sys.exit(1)

    status, body, err = get("/api/branches/public/all?active_only=true")
    if not ok(
        "public branches active_only",
        status == 200 and isinstance((body or {}).get("branches"), list),
        err or f"status={status}",
    ):
        failures += 1
        print()
        print(f"{failures} failure(s)")
        sys.exit(1)

    branches = (body or {}).get("branches") or []
    print(f"        public branches: {len(branches)}")
    inactive = [b for b in branches if b.get("is_active") is False]
    if not ok("inactive branches hidden from public list", not inactive, f"leaked={len(inactive)}"):
        failures += 1

    location_id = None
    for branch in branches:
        if branch.get("location_id"):
            location_id = branch["location_id"]
            break

    if location_id:
        st2, body2, err2 = get(
            f"/api/branches/public/by-location/{location_id}?active_only=true"
        )
        if not ok(
            "public branches by location",
            st2 in (200, 404),
            err2 or f"status={st2}",
        ):
            failures += 1
        elif st2 == 200:
            loc_branches = (body2 or {}).get("branches") or []
            leaked = [b for b in loc_branches if b.get("is_active") is False]
            if not ok("by-location hides inactive", not leaked, f"leaked={len(leaked)}"):
                failures += 1

    print()
    if failures:
        print(f"{failures} failure(s)")
        sys.exit(1)
    print("S02 public branch smoke checks passed.")


if __name__ == "__main__":
    main()
