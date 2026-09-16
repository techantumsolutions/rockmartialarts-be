"""
Smoke checks for M03-S04 public branch detail.

  python scripts/qa_m03_s04_branch_detail.py

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

SENSITIVE = ("manager_id", "bank_details", "bank_info", "bank_account")


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


def leaked_sensitive(payload):
    if not isinstance(payload, dict):
        return []
    return [key for key in SENSITIVE if key in payload and payload.get(key) not in (None, "", {}, [])]


def main():
    failures = 0

    status, body, err = get("/health")
    if not ok("backend health", status == 200, err or f"status={status}"):
        print("Backend is not reachable. Start FastAPI and retry.")
        sys.exit(1)

    st404, _, err404 = get("/api/branches/public/by-slug/this-branch-does-not-exist-xyz")
    if not ok("invalid slug returns 404", st404 == 404, err404 or f"status={st404}"):
        failures += 1

    st_search, search_body, err_search = get("/api/branches/public/search?active_only=true")
    if not ok(
        "public branch search",
        st_search == 200 and isinstance((search_body or {}).get("branches"), list),
        err_search or f"status={st_search}",
    ):
        failures += 1
        print()
        print(f"{failures} failure(s)")
        sys.exit(1)

    branches = (search_body or {}).get("branches") or []
    sample = next((b for b in branches if b.get("id") or b.get("slug")), None)
    if not sample:
        print("        no public branches; skipping detail checks")
        print()
        if failures:
            print(f"{failures} failure(s)")
            sys.exit(1)
        print("All S04 checks passed")
        return

    slug = (sample.get("slug") or sample.get("id") or "").strip()
    st, payload, err = get(f"/api/branches/public/by-slug/{urllib.parse.quote(slug)}")
    if not ok(
        "branch by-slug returns detail",
        st == 200 and isinstance(payload, dict) and payload.get("id"),
        err or f"status={st}",
    ):
        failures += 1
        print()
        print(f"{failures} failure(s)")
        sys.exit(1)

    if not ok("detail includes nested branch contact", bool((payload.get("branch") or {}).get("name") or payload.get("name"))):
        failures += 1
    if not ok("detail includes canonical slug", bool(payload.get("slug"))):
        failures += 1
    leaked = leaked_sensitive(payload)
    if not ok("detail omits manager/bank fields", not leaked, f"leaked={leaked}"):
        failures += 1
    if payload.get("is_active") is False:
        if not ok("inactive branch leaked via by-slug", False, payload.get("id")):
            failures += 1

    courses = payload.get("courses")
    if not ok("courses is a list", isinstance(courses, list)):
        failures += 1
        courses = []
    missing_ids = [c for c in courses if isinstance(c, dict) and not c.get("id")]
    if not ok("embedded courses have ids", not missing_ids, f"missing={len(missing_ids)}"):
        failures += 1

    bid = payload.get("id")
    st_id, by_id, err_id = get(f"/api/branches/public/{urllib.parse.quote(bid)}")
    if not ok(
        "branch by-id returns same public detail",
        st_id == 200 and isinstance(by_id, dict) and by_id.get("id") == bid,
        err_id or f"status={st_id}",
    ):
        failures += 1
    elif not ok(
        "by-id omits manager/bank fields",
        not leaked_sensitive(by_id),
        f"leaked={leaked_sensitive(by_id)}",
    ):
        failures += 1

    st_legacy, legacy, err_legacy = get(f"/api/public-branch-by-slug/{urllib.parse.quote(slug)}")
    if not ok(
        "legacy public-branch-by-slug matches",
        st_legacy == 200 and isinstance(legacy, dict) and legacy.get("id") == bid,
        err_legacy or f"status={st_legacy}",
    ):
        failures += 1

    st_courses, courses_body, err_courses = get(f"/api/courses/public/by-branch/{urllib.parse.quote(bid)}")
    if not ok(
        "public courses by-branch",
        st_courses == 200 and isinstance((courses_body or {}).get("courses"), list),
        err_courses or f"status={st_courses}",
    ):
        failures += 1
    else:
        by_branch_ids = {c.get("id") for c in (courses_body or {}).get("courses") or [] if c.get("id")}
        embedded_ids = {c.get("id") for c in courses if isinstance(c, dict) and c.get("id")}
        extra = embedded_ids - by_branch_ids
        if not ok(
            "embedded courses are available at this branch",
            not extra,
            f"extra={extra}",
        ):
            failures += 1
        print(f"        slug={slug} courses={len(embedded_ids)} by_branch={len(by_branch_ids)}")

    st_inact, inact_body, err_inact = get("/api/branches/public/search?active_only=false")
    if st_inact == 200:
        inactive = [b for b in (inact_body or {}).get("branches") or [] if b.get("is_active") is False]
        if inactive:
            inactive_slug = (inactive[0].get("slug") or inactive[0].get("id") or "").strip()
            st_hidden, _, err_hidden = get(f"/api/branches/public/by-slug/{urllib.parse.quote(inactive_slug)}")
            if not ok("inactive branch by-slug is 404", st_hidden == 404, err_hidden or f"status={st_hidden}"):
                failures += 1
        else:
            print("        no inactive branches in data; skipped inactive 404 check")
    else:
        if not ok("search active_only=false", False, err_inact or f"status={st_inact}"):
            failures += 1

    print()
    if failures:
        print(f"{failures} failure(s)")
        sys.exit(1)
    print("All S04 checks passed")


if __name__ == "__main__":
    main()
