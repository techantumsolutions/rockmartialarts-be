"""
Smoke checks for M05-S01 enrollment cart foundation.

  python scripts/qa_m05_s01_cart.py

Optional:
  CART_QA_BASE=http://127.0.0.1:8003
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = (
    os.getenv("CART_QA_BASE")
    or os.getenv("BRANCH_QA_BASE")
    or os.getenv("COURSE_QA_BASE")
    or "http://127.0.0.1:8003"
).rstrip("/")


def request(method: str, path: str, body=None, headers=None):
    url = f"{BASE}{path}"
    data = None
    hdrs = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}, None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"detail": raw}
        return exc.code, payload, str(exc)
    except Exception as exc:
        return None, None, str(exc)


def ok(label: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    extra = f" — {detail}" if detail else ""
    print(f"[{status}] {label}{extra}")
    return passed


def cart_body(payload):
    return (payload or {}).get("cart") or {}


def main():
    failures = 0
    st, _, err = request("GET", "/health")
    if not ok("backend health", st == 200, err or f"status={st}"):
        sys.exit(1)

    guest = str(uuid.uuid4())
    headers = {"X-Guest-Cart-Token": guest}

    st0, body0, err0 = request("GET", "/api/carts/current", headers=headers)
    if not ok("get current cart", st0 == 200 and cart_body(body0).get("id"), err0 or f"status={st0}"):
        failures += 1
        print(f"{failures} failure(s)")
        sys.exit(1)

    st_s, body_s, err_s = request(
        "POST",
        "/api/carts/current/students",
        {"label": "QA Student A"},
        headers=headers,
    )
    line_a = None
    if not ok("add student", st_s == 200 and cart_body(body_s).get("students"), err_s or f"status={st_s}"):
        failures += 1
    else:
        line_a = cart_body(body_s)["students"][0]["student_line_id"]

    st_search, search_body, _ = request("GET", "/api/branches/public/search?active_only=true")
    branch = None
    if st_search == 200:
        branches = (search_body or {}).get("branches") or []
        branch = branches[0] if branches else None

    course_id = None
    duration_id = None
    if branch:
        bid = branch.get("id")
        st_c, c_body, _ = request("GET", f"/api/courses/public/by-branch/{urllib.parse.quote(bid)}")
        if st_c == 200:
            courses = (c_body or {}).get("courses") or []
            if courses:
                course_id = courses[0].get("id")
                durs = courses[0].get("available_durations") or []
                if durs:
                    duration_id = durs[0].get("id") or durs[0].get("code")

    if line_a and branch and course_id and duration_id:
        st_i, body_i, err_i = request(
            "POST",
            "/api/carts/current/items",
            {
                "student_line_id": line_a,
                "course_id": course_id,
                "branch_id": branch["id"],
                "duration_id": duration_id,
            },
            headers=headers,
        )
        item_id = None
        if not ok(
            "add cart item",
            st_i == 200 and len(cart_body(body_i).get("items") or []) >= 1,
            err_i or f"status={st_i}",
        ):
            failures += 1
        else:
            item_id = cart_body(body_i)["items"][0]["id"]
            totals = cart_body(body_i).get("totals") or {}
            if not ok("totals computed", (totals.get("total_amount") or 0) > 0, str(totals)):
                failures += 1

        st_dup, _, _ = request(
            "POST",
            "/api/carts/current/items",
            {
                "student_line_id": line_a,
                "course_id": course_id,
                "branch_id": branch["id"],
                "duration_id": duration_id,
            },
            headers=headers,
        )
        if not ok("duplicate item rejected", st_dup == 400, f"status={st_dup}"):
            failures += 1

        if item_id:
            st_rm, body_rm, err_rm = request(
                "DELETE",
                f"/api/carts/current/items/{urllib.parse.quote(item_id)}",
                headers=headers,
            )
            if not ok(
                "remove item",
                st_rm == 200 and len(cart_body(body_rm).get("items") or []) == 0,
                err_rm or f"status={st_rm}",
            ):
                failures += 1

    st_v, body_v, err_v = request("POST", "/api/carts/current/validate", headers=headers)
    if not ok("validate cart", st_v == 200 and "is_valid" in cart_body(body_v), err_v or f"status={st_v}"):
        failures += 1

    print()
    if failures:
        print(f"{failures} failure(s)")
        sys.exit(1)
    print("All S01 cart checks passed")


if __name__ == "__main__":
    main()
