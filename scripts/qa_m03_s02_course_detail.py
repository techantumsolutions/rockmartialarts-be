"""
Smoke checks for M03-S02 public course detail.

  python scripts/qa_m03_s02_course_detail.py

Optional:
  COURSE_QA_BASE=http://127.0.0.1:8003
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = (
    os.getenv("COURSE_QA_BASE")
    or os.getenv("BRANCH_QA_BASE")
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


def main():
    failures = 0

    status, body, err = get("/health")
    if not ok("backend health", status == 200, err or f"status={status}"):
        print("Backend is not reachable. Start FastAPI and retry.")
        sys.exit(1)

    st404, _, err404 = get("/api/courses/public/by-slug/this-course-does-not-exist-xyz")
    if not ok("invalid slug returns 404", st404 == 404, err404 or f"status={st404}"):
        failures += 1

    st_all, body_all, err_all = get("/api/courses/public/all?active_only=true")
    if not ok(
        "public courses list",
        st_all == 200 and isinstance((body_all or {}).get("courses"), list),
        err_all or f"status={st_all}",
    ):
        failures += 1
        print()
        print(f"{failures} failure(s)")
        sys.exit(1)

    courses = (body_all or {}).get("courses") or []
    sample = next((c for c in courses if c.get("id") or c.get("slug") or c.get("title")), None)
    if not sample:
        print("        no public courses; skipping detail checks")
        print()
        if failures:
            print(f"{failures} failure(s)")
            sys.exit(1)
        print("All S02 checks passed")
        return

    slug = (sample.get("slug") or sample.get("id") or "").strip()
    st, payload, err = get(f"/api/courses/public/by-slug/{urllib.parse.quote(slug)}")
    if not ok(
        "course by-slug returns detail",
        st == 200 and isinstance(payload, dict) and isinstance((payload or {}).get("course"), dict),
        err or f"status={st}",
    ):
        failures += 1
        print()
        print(f"{failures} failure(s)")
        sys.exit(1)

    course = (payload or {}).get("course") or {}
    if not ok("active course document present", bool(course.get("id")), "missing id"):
        failures += 1
    if course.get("settings", {}).get("active") is False:
        if not ok("inactive course leaked via by-slug", False, course.get("id")):
            failures += 1

    offering = (payload or {}).get("branches_offering") or course.get("branches_offering") or []
    assignments = course.get("branch_assignments") or []
    print(f"        slug={slug} branches={len(offering)} assignments={len(assignments)}")
    if not ok("branches_offering is a list", isinstance(offering, list)):
        failures += 1
    missing_name = [b.get("id") or b.get("branch_id") for b in offering if isinstance(b, dict) and not (b.get("name") or b.get("branch_name"))]
    if not ok("offering branches include names", not missing_name, f"missing={len(missing_name)}"):
        failures += 1
    if assignments:
        assign_ids = {a.get("branch_id") for a in assignments if a.get("branch_id")}
        offer_ids = {b.get("id") or b.get("branch_id") for b in offering if isinstance(b, dict)}
        extra = assign_ids - offer_ids
        if not ok("branch_assignments match available offering", not extra, f"extra={extra}"):
            failures += 1

    category = (payload or {}).get("category") or course.get("category")
    if category:
        if not ok(
            "category is public nav-shaped",
            isinstance(category, dict) and category.get("id") and category.get("name"),
            str(category)[:80],
        ):
            failures += 1

    if not ok(
        "canonical slug on course",
        bool(course.get("slug")),
        "missing slug",
    ):
        failures += 1

    durations = course.get("available_durations")
    if not ok("available_durations present", isinstance(durations, list)):
        failures += 1

    cid = course.get("id")
    st_id, payload_id, err_id = get(f"/api/courses/public/by-slug/{urllib.parse.quote(cid)}")
    if not ok(
        "by-slug accepts course id",
        st_id == 200 and ((payload_id or {}).get("course") or {}).get("id") == cid,
        err_id or f"status={st_id}",
    ):
        failures += 1

    st_br, body_br, _ = get("/api/branches/public/all?active_only=true")
    if st_br == 200 and offering:
        offer_ids = {b.get("id") or b.get("branch_id") for b in offering if isinstance(b, dict)}
        other = next(
            (b for b in (body_br or {}).get("branches") or [] if b.get("id") and b.get("id") not in offer_ids),
            None,
        )
        if other:
            st_bad, _, err_bad = get(
                f"/api/courses/public/detail/{urllib.parse.quote(cid)}/branch-info?branch_id={urllib.parse.quote(other['id'])}"
            )
            if not ok(
                "unavailable branch-info rejected",
                st_bad in (400, 404),
                err_bad or f"status={st_bad}",
            ):
                failures += 1
        if offer_ids:
            bid = next(iter(offer_ids))
            st_ok, body_ok, err_ok = get(
                f"/api/courses/public/detail/{urllib.parse.quote(cid)}/branch-info?branch_id={urllib.parse.quote(bid)}"
            )
            if not ok(
                "available branch-info returns 200",
                st_ok == 200 and isinstance(body_ok, dict),
                err_ok or f"status={st_ok}",
            ):
                failures += 1

    print()
    if failures:
        print(f"{failures} failure(s)")
        sys.exit(1)
    print("All S02 checks passed")


if __name__ == "__main__":
    main()
