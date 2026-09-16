"""
Smoke checks for M03-S01 category navigation.

  python scripts/qa_m03_s01_category_nav.py

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

NAV_FIELDS = {"id", "name", "slug"}


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


def extra_keys(item: dict) -> set:
    return set(item.keys()) - NAV_FIELDS


def main():
    failures = 0

    status, body, err = get("/health")
    if not ok("backend health", status == 200, err or f"status={status}"):
        print("Backend is not reachable. Start FastAPI and retry.")
        sys.exit(1)

    st404, _, err404 = get("/api/categories/public/by-slug/this-category-does-not-exist-xyz")
    if not ok("missing category slug returns 404", st404 == 404, err404 or f"status={st404}"):
        failures += 1

    st_nav, body_nav, err_nav = get("/api/categories/public/nav")
    if not ok(
        "public category nav",
        st_nav == 200 and isinstance((body_nav or {}).get("categories"), list),
        err_nav or f"status={st_nav}",
    ):
        failures += 1
        print()
        print(f"{failures} failure(s)")
        sys.exit(1)

    categories = (body_nav or {}).get("categories") or []
    print(f"        nav categories: {len(categories)}")

    bloated = [c.get("id") for c in categories if extra_keys(c) - set()]
    unexpected = []
    nested = []
    missing_core = []
    for item in categories:
        if not isinstance(item, dict):
            unexpected.append(item)
            continue
        extra = extra_keys(item)
        if extra:
            bloated.append(item.get("id") or extra)
        if item.get("subcategories"):
            nested.append(item.get("id"))
        if not item.get("id") or not item.get("name") or not item.get("slug"):
            missing_core.append(item.get("id"))

    if not ok("nav items are id/name/slug only", not bloated, f"extra={bloated[:5]}"):
        failures += 1
    if not ok("nav has no nested subcategory tree", not nested, f"nested={len(nested)}"):
        failures += 1
    if not ok("nav items include id, name, slug", not missing_core, f"missing={len(missing_core)}"):
        failures += 1

    st_all, body_all, err_all = get("/api/categories/public/all?active_only=true&include_subcategories=true")
    if st_all == 200:
        public_all = (body_all or {}).get("categories") or []
        inactive = [c for c in public_all if c.get("is_active") is False]
        if not ok("public all hides inactive flag leakage", not inactive, f"leaked={len(inactive)}"):
            failures += 1
        nav_ids = {c.get("id") for c in categories}
        all_ids = {c.get("id") for c in public_all}
        extra_nav = nav_ids - all_ids
        if not ok("nav ids are a subset of public top-level categories", not extra_nav, f"extra={extra_nav}"):
            failures += 1
        if len(public_all) != len(categories):
            print(f"        public/all top-level={len(public_all)} nav={len(categories)}")

    if not categories:
        print("        no public categories; skipping by-slug checks")
        print()
        if failures:
            print(f"{failures} failure(s)")
            sys.exit(1)
        print("All S01 checks passed")
        return

    sample = next((c for c in categories if c.get("slug")), categories[0])
    slug = sample.get("slug")
    st_page, body_page, err_page = get(f"/api/categories/public/by-slug/{urllib.parse.quote(slug)}")
    if not ok(
        "category by-slug returns landing payload",
        st_page == 200
        and isinstance((body_page or {}).get("category"), dict)
        and isinstance((body_page or {}).get("courses"), list)
        and isinstance((body_page or {}).get("subcategories"), list),
        err_page or f"status={st_page}",
    ):
        failures += 1
    else:
        landing_id = ((body_page or {}).get("category") or {}).get("id")
        if not ok("by-slug category matches nav item", landing_id == sample.get("id"), f"id={landing_id}"):
            failures += 1

        allowed = {landing_id}
        for sub in (body_page or {}).get("subcategories") or []:
            if sub.get("id"):
                allowed.add(sub["id"])
        leaked = []
        for course in (body_page or {}).get("courses") or []:
            cid = course.get("category_id")
            sid = course.get("sub_category")
            if cid not in allowed and sid not in allowed:
                leaked.append(course.get("id"))
            if (course.get("settings") or {}).get("active") is False:
                leaked.append(course.get("id"))
        if not ok("landing courses stay in selected category tree", not leaked, f"leaked={len(leaked)}"):
            failures += 1
        print(f"        sample '{slug}' courses={len((body_page or {}).get('courses') or [])} subs={len((body_page or {}).get('subcategories') or [])}")

        st_id, body_id, err_id = get(f"/api/categories/public/by-slug/{urllib.parse.quote(sample['id'])}")
        if not ok(
            "category by-slug accepts id fallback",
            st_id == 200 and ((body_id or {}).get("category") or {}).get("id") == sample.get("id"),
            err_id or f"status={st_id}",
        ):
            failures += 1

    print()
    if failures:
        print(f"{failures} failure(s)")
        sys.exit(1)
    print("All S01 checks passed")


if __name__ == "__main__":
    main()
