"""
Smoke checks for M02-S01 public geography APIs and legacy location compatibility.

  python scripts/qa_m02_s01_geography.py

Optional:
  GEOGRAPHY_QA_BASE=http://127.0.0.1:8003
"""
import os
import sys
import urllib.error
import urllib.request
import json

BASE = (os.getenv("GEOGRAPHY_QA_BASE") or "http://127.0.0.1:8003").rstrip("/")


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
        failures += 1
        print("Backend is not reachable. Start FastAPI and retry.")
        sys.exit(1)

    status, body, err = get("/api/locations")
    # Authenticated in some deployments; 401 is still "route exists"
    if not ok(
        "legacy /api/locations route exists",
        status in (200, 401, 403),
        err or f"status={status}",
    ):
        failures += 1

    status, body, err = get("/api/locations/public/details?active_only=true")
    if not ok("legacy public locations", status == 200, err or f"status={status}"):
        failures += 1

    status, body, err = get("/api/states/public?active_only=true")
    if not ok("public states", status == 200 and isinstance((body or {}).get("states"), list), err or f"status={status}"):
        failures += 1
    else:
        states = (body or {}).get("states") or []
        print(f"        active states: {len(states)}")
        for s in states:
            if s.get("is_active") is False:
                failures += 1
                print("[FAIL] inactive state leaked in public list")
                break

    status, body, err = get("/api/cities/public?active_only=true")
    if not ok("public cities", status == 200 and isinstance((body or {}).get("cities"), list), err or f"status={status}"):
        failures += 1
    else:
        cities = (body or {}).get("cities") or []
        print(f"        active cities: {len(cities)}")
        if cities:
            sid = cities[0].get("state_id")
            if sid:
                st2, body2, err2 = get(f"/api/cities/public?state_id={sid}&active_only=true")
                if not ok("public cities filter by state_id", st2 == 200, err2 or f"status={st2}"):
                    failures += 1
                else:
                    filtered = (body2 or {}).get("cities") or []
                    bad = [c for c in filtered if c.get("state_id") and c.get("state_id") != sid]
                    if not ok("filtered cities belong to requested state", not bad, f"mismatched={len(bad)}"):
                        failures += 1

    print()
    if failures:
        print(f"QA finished with {failures} failure(s).")
        sys.exit(1)
    print("QA finished: all checks passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
