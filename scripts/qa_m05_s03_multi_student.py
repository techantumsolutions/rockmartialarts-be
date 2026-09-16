"""
Smoke checks for M05-S03 multiple students in one cart.

  python scripts/qa_m05_s03_multi_student.py
"""
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

BASE = (
    os.getenv("CART_QA_BASE")
    or os.getenv("BRANCH_QA_BASE")
    or "http://127.0.0.1:8003"
).rstrip("/")


def request(method, path, body=None, headers=None):
    url = f"{BASE}{path}"
    data = None
    hdrs = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
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


def ok(label, passed, detail=""):
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

    st_b, body_b, _ = request(
        "POST",
        "/api/carts/current/students/bulk",
        {
            "students": [
                {"label": "QA Student A"},
                {"label": "QA Student B"},
            ]
        },
        headers=headers,
    )
    cart = cart_body(body_b)
    groups = cart.get("student_groups") or []
    if not ok(
        "bulk add students",
        st_b == 200 and len(groups) == 2 and cart.get("totals", {}).get("student_count") == 2,
        f"status={st_b} groups={len(groups)}",
    ):
        failures += 1

    line_a = groups[0]["student_line_id"]
    line_b = groups[1]["student_line_id"]

    _, search_body, _ = request("GET", "/api/branches/public/search?active_only=true")
    branch = ((search_body or {}).get("branches") or [None])[0]
    if not branch:
        print("        no branch; skipping item isolation checks")
        sys.exit(failures)

    bid = branch["id"]
    import urllib.parse

    _, c_body, _ = request("GET", f"/api/courses/public/by-branch/{urllib.parse.quote(bid)}")
    courses = (c_body or {}).get("courses") or []
    if len(courses) < 2:
        print(f"        need 2+ courses; have {len(courses)}")
        sys.exit(failures)

    def add_one(line_id, course):
        dur = (course.get("available_durations") or [{}])[0]
        did = dur.get("id") or dur.get("code")
        return request(
            "POST",
            "/api/carts/current/items",
            {
                "student_line_id": line_id,
                "course_id": course["id"],
                "branch_id": bid,
                "duration_id": did,
            },
            headers=headers,
        )

    st1, body1, _ = add_one(line_a, courses[0])
    st2, body2, _ = add_one(line_b, courses[1])
    cart2 = cart_body(body2)
    g2 = cart2.get("student_groups") or []
    items_a = next((g for g in g2 if g["student_line_id"] == line_a), {}).get("items") or []
    items_b = next((g for g in g2 if g["student_line_id"] == line_b), {}).get("items") or []
    if not ok(
        "items isolated per student",
        st1 == 200 and st2 == 200 and len(items_a) == 1 and len(items_b) == 1,
        f"a={len(items_a)} b={len(items_b)}",
    ):
        failures += 1

    total_before = float(cart2.get("totals", {}).get("total_amount") or 0)
    st_rm, body_rm, _ = request(
        "DELETE",
        f"/api/carts/current/students/{line_a}",
        headers=headers,
    )
    cart3 = cart_body(body_rm)
    remaining_lines = {g["student_line_id"] for g in cart3.get("student_groups") or []}
    remaining_items = cart3.get("items") or []
    student_b_items = [i for i in remaining_items if i.get("student_line_id") == line_b]
    if not ok(
        "remove student drops only their items",
        st_rm == 200 and line_a not in remaining_lines and len(student_b_items) == 1,
        f"items_left={len(remaining_items)} b_items={len(student_b_items)}",
    ):
        failures += 1

    total_after = float(cart3.get("totals", {}).get("total_amount") or 0)
    if not ok(
        "cart total recalculated after student removal",
        total_after < total_before and total_after == student_b_items[0]["pricing"]["total_amount"],
        f"before={total_before} after={total_after}",
    ):
        failures += 1

    st_v, body_v, _ = request("POST", "/api/carts/current/validate", headers=headers)
    if not ok(
        "validate remaining multi-student cart",
        st_v == 200 and cart_body(body_v).get("is_valid") is True,
        f"status={st_v}",
    ):
        failures += 1

    print()
    if failures:
        print(f"{failures} failure(s)")
        sys.exit(1)
    print("All S03 checks passed")


if __name__ == "__main__":
    main()
