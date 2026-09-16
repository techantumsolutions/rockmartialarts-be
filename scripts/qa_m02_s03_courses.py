"""
Smoke checks for M02-S03 category / course hierarchy.

  python scripts/qa_m02_s03_courses.py

Optional:
  COURSE_QA_BASE=http://127.0.0.1:8003
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

BASE = (
    os.getenv("COURSE_QA_BASE")
    or os.getenv("BRANCH_QA_BASE")
    or os.getenv("GEOGRAPHY_QA_BASE")
    or "http://127.0.0.1:8003"
).rstrip("/")

_slug_strip_re = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    text = (value or "").strip().lower()
    text = _slug_strip_re.sub("-", text).strip("-")
    return text or "item"


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

    status, body, err = get("/api/categories/public/all?active_only=true")
    if not ok(
        "public categories active_only",
        status == 200 and isinstance((body or {}).get("categories"), list),
        err or f"status={status}",
    ):
        failures += 1
        print()
        print(f"{failures} failure(s)")
        sys.exit(1)

    categories = (body or {}).get("categories") or []
    print(f"        public categories: {len(categories)}")
    inactive = [c for c in categories if c.get("is_active") is False]
    if not ok("inactive categories hidden from public list", not inactive, f"leaked={len(inactive)}"):
        failures += 1

    nested_inactive = []
    for category in categories:
        for sub in category.get("subcategories") or []:
            if sub.get("is_active") is False:
                nested_inactive.append(sub.get("id"))
    if not ok("inactive subcategories hidden from public list", not nested_inactive, f"leaked={len(nested_inactive)}"):
        failures += 1

    status, body, err = get("/api/courses/public/all?active_only=true")
    if not ok(
        "public courses active_only",
        status == 200 and isinstance((body or {}).get("courses"), list),
        err or f"status={status}",
    ):
        failures += 1
    else:
        courses = (body or {}).get("courses") or []
        print(f"        public courses: {len(courses)}")
        inactive_courses = [c for c in courses if (c.get("settings") or {}).get("active") is False or c.get("enabled") is False]
        if not ok("inactive courses hidden from public list", not inactive_courses, f"leaked={len(inactive_courses)}"):
            failures += 1

        sample = next((c for c in courses if c.get("id") or c.get("slug") or c.get("title")), None)
        if sample:
            slug = sample.get("slug") or slugify(sample.get("title") or sample.get("code") or "")
            st2, body2, err2 = get(f"/api/courses/public/by-slug/{slug}")
            if not ok(
                "public course by-slug",
                st2 == 200 and isinstance(body2, dict) and (body2.get("id") or (body2.get("course") or {}).get("id")),
                err2 or f"status={st2}",
            ):
                failures += 1
            st3, _, err3 = get("/api/courses/public/by-slug/this-course-does-not-exist-xyz")
            if not ok("missing slug returns 404", st3 == 404, err3 or f"status={st3}"):
                failures += 1

            category_id = sample.get("category_id")
            if category_id:
                st4, body4, err4 = get(f"/api/courses/public/by-category/{category_id}?active_only=true")
                if not ok(
                    "public courses by-category",
                    st4 == 200 and isinstance((body4 or {}).get("courses"), list),
                    err4 or f"status={st4}",
                ):
                    failures += 1

    if categories:
        first = categories[0]
        cat_id = first.get("id")
        if cat_id:
            st5, body5, err5 = get(f"/api/categories/public/details?category_id={cat_id}&active_only=true")
            if not ok(
                "public category details",
                st5 == 200,
                err5 or f"status={st5}",
            ):
                failures += 1

    print()
    if failures:
        print(f"{failures} failure(s)")
        sys.exit(1)
    print("All S03 checks passed")


if __name__ == "__main__":
    main()
