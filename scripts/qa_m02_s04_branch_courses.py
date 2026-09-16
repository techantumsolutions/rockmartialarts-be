"""
Smoke checks for M02-S04 branch-course availability and fees.

  python scripts/qa_m02_s04_branch_courses.py

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

    st404, _, err404 = get("/api/courses/public/by-branch/this-branch-does-not-exist-xyz")
    if not ok("missing branch by-branch returns 404", st404 == 404, err404 or f"status={st404}"):
        failures += 1

    st_all, body_all, err_all = get("/api/courses/public/all?active_only=true")
    if not ok(
        "public courses active_only",
        st_all == 200 and isinstance((body_all or {}).get("courses"), list),
        err_all or f"status={st_all}",
    ):
        failures += 1
        print()
        print(f"{failures} failure(s)")
        sys.exit(1)

    all_courses = (body_all or {}).get("courses") or []
    print(f"        public courses: {len(all_courses)}")

    st_br, body_br, err_br = get("/api/branches/public/all?active_only=true")
    if not ok(
        "public branches active_only",
        st_br == 200 and isinstance((body_br or {}).get("branches"), list),
        err_br or f"status={st_br}",
    ):
        failures += 1
        print()
        print(f"{failures} failure(s)")
        sys.exit(1)

    branches = (body_br or {}).get("branches") or []
    print(f"        public branches: {len(branches)}")

    sample_branch = None
    sample_courses = []
    for branch in branches:
        bid = branch.get("id")
        if not bid:
            continue
        st_c, body_c, _ = get(f"/api/courses/public/by-branch/{urllib.parse.quote(bid)}")
        if st_c != 200:
            continue
        courses = (body_c or {}).get("courses") or []
        if courses:
            sample_branch = branch
            sample_courses = courses
            break

    if not sample_branch:
        print("        no public branch with assigned courses; skipping pair checks")
        print()
        if failures:
            print(f"{failures} failure(s)")
            sys.exit(1)
        print("All S04 checks passed")
        return

    bid = sample_branch["id"]
    print(f"        sample branch: {bid} courses={len(sample_courses)}")
    sample_ids = {c.get("id") for c in sample_courses if c.get("id")}
    inactive_leaked = [
        c.get("id")
        for c in sample_courses
        if (c.get("settings") or {}).get("active") is False or c.get("enabled") is False
    ]
    if not ok("public by-branch hides inactive courses", not inactive_leaked, f"leaked={len(inactive_leaked)}"):
        failures += 1

    first_course = next((c for c in sample_courses if c.get("id")), None)
    if first_course:
        cid = first_course["id"]
        st_ok, body_ok, err_ok = get(
            f"/api/courses/public/detail/{urllib.parse.quote(cid)}/branch-info?branch_id={urllib.parse.quote(bid)}"
        )
        if not ok(
            "valid branch-course pair returns branch-info",
            st_ok == 200 and isinstance(body_ok, dict),
            err_ok or f"status={st_ok}",
        ):
            failures += 1
        else:
            has_fee = any(
                body_ok.get(key) not in (None, "", [])
                for key in ("amount", "fee_per_duration", "pricing", "duration_display")
            )
            print(f"        branch-info keys: {sorted((body_ok or {}).keys())[:12]}")
            if not ok("branch-info includes fee or duration fields", has_fee or st_ok == 200):
                failures += 1

        st_detail, body_detail, err_detail = get(f"/api/courses/public/detail/{urllib.parse.quote(cid)}")
        if st_detail == 200:
            offering = (body_detail or {}).get("branches_offering") or []
            offering_ids = {
                (b.get("id") or b.get("branch_id"))
                for b in offering
                if isinstance(b, dict)
            }
            if not ok(
                "course detail branches_offering includes available branch",
                bid in offering_ids or not offering,
                err_detail or f"offering={len(offering)}",
            ):
                failures += 1

    other = next((c for c in all_courses if c.get("id") and c.get("id") not in sample_ids), None)
    if other:
        st_bad, _, err_bad = get(
            f"/api/courses/public/detail/{urllib.parse.quote(other['id'])}/branch-info?branch_id={urllib.parse.quote(bid)}"
        )
        if not ok(
            "unmapped pair is rejected",
            st_bad in (400, 404),
            err_bad or f"status={st_bad}",
        ):
            failures += 1
    else:
        print("        no unmapped public course for rejection check")

    print()
    if failures:
        print(f"{failures} failure(s)")
        sys.exit(1)
    print("All S04 checks passed")


if __name__ == "__main__":
    main()
