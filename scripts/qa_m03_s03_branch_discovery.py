"""
Smoke checks for M03-S03 public branch discovery.

  python scripts/qa_m03_s03_branch_discovery.py

Optional:
  BRANCH_QA_BASE=http://127.0.0.1:8003
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = (
    os.getenv("BRANCH_QA_BASE")
    or os.getenv("COURSE_QA_BASE")
    or os.getenv("GEOGRAPHY_QA_BASE")
    or "http://127.0.0.1:8003"
).rstrip("/")


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


def ids(body):
    return {b.get("id") for b in (body or {}).get("branches") or [] if b.get("id")}


def main():
    failures = 0

    status, body, err = get("/health")
    if not ok("backend health", status == 200, err or f"status={status}"):
        print("Backend is not reachable. Start FastAPI and retry.")
        sys.exit(1)

    st, all_body, err = get("/api/branches/public/search?active_only=true")
    if not ok(
        "search with no filters",
        st == 200 and isinstance((all_body or {}).get("branches"), list),
        err or f"status={st}",
    ):
        failures += 1
        print()
        print(f"{failures} failure(s)")
        sys.exit(1)

    branches = (all_body or {}).get("branches") or []
    print(f"        public branches: {len(branches)} total={all_body.get('total')}")
    inactive = [b for b in branches if b.get("is_active") is False]
    if not ok("search hides inactive branches", not inactive, f"leaked={len(inactive)}"):
        failures += 1

    st_miss, miss_body, err_miss = get("/api/branches/public/search?q=zzznomatchbranchxyz")
    if not ok(
        "unknown search returns empty list",
        st_miss == 200 and (miss_body or {}).get("branches") == [],
        err_miss or f"status={st_miss}",
    ):
        failures += 1

    st_cities, cities_body, err_cities = get("/api/cities/public?active_only=true")
    if not ok(
        "public cities",
        st_cities == 200 and isinstance((cities_body or {}).get("cities"), list),
        err_cities or f"status={st_cities}",
    ):
        failures += 1
        print()
        print(f"{failures} failure(s)")
        sys.exit(1)

    cities = (cities_body or {}).get("cities") or []
    missing_counts = [c.get("id") for c in cities if "branch_count" not in c]
    if not ok("cities include branch_count", not missing_counts, f"missing={len(missing_counts)}"):
        failures += 1

    sample_city = next((c for c in cities if (c.get("branch_count") or 0) > 0 and c.get("id")), None)
    if sample_city:
        cid = sample_city["id"]
        st_city, city_body, err_city = get(f"/api/branches/public/search?city_id={urllib.parse.quote(cid)}")
        city_total = (city_body or {}).get("total")
        if not ok(
            "city filter returns 200",
            st_city == 200 and isinstance((city_body or {}).get("branches"), list),
            err_city or f"status={st_city}",
        ):
            failures += 1
        elif not ok(
            "city branch_count matches listing total",
            city_total == sample_city.get("branch_count"),
            f"count={sample_city.get('branch_count')} total={city_total}",
        ):
            failures += 1
        print(f"        city '{sample_city.get('name')}' count={sample_city.get('branch_count')} listing={city_total}")

        sid = sample_city.get("state_id")
        if sid:
            st_state, state_body, err_state = get(f"/api/branches/public/search?state_id={urllib.parse.quote(sid)}")
            if not ok(
                "state filter returns 200",
                st_state == 200 and isinstance((state_body or {}).get("branches"), list),
                err_state or f"status={st_state}",
            ):
                failures += 1
            else:
                city_ids = ids(city_body)
                state_ids = ids(state_body)
                leaked = city_ids - state_ids
                if not ok("city results are within state results", not leaked, f"leaked={len(leaked)}"):
                    failures += 1

            st_both, both_body, err_both = get(
                f"/api/branches/public/search?state_id={urllib.parse.quote(sid)}&city_id={urllib.parse.quote(cid)}"
            )
            if not ok(
                "state+city combination",
                st_both == 200 and ids(both_body) == ids(city_body),
                err_both or f"status={st_both}",
            ):
                failures += 1

    if branches:
        sample = branches[0]
        name = (sample.get("name") or ((sample.get("branch") or {}).get("name")) or "")[:4]
        if name:
            st_q, q_body, err_q = get(f"/api/branches/public/search?q={urllib.parse.quote(name)}")
            if not ok(
                "text search returns 200",
                st_q == 200 and isinstance((q_body or {}).get("branches"), list) and (q_body or {}).get("total", 0) >= 1,
                err_q or f"status={st_q}",
            ):
                failures += 1

    print()
    if failures:
        print(f"{failures} failure(s)")
        sys.exit(1)
    print("All S03 checks passed")


if __name__ == "__main__":
    main()
