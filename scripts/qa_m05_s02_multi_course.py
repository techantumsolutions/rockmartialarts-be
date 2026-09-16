"""
Smoke checks for M05-S02 multiple courses per student in cart.

  python scripts/qa_m05_s02_multi_course.py

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

    _, body_s, _ = request(
        "POST",
        "/api/carts/current/students",
        {"label": "QA Multi Student"},
        headers=headers,
    )
    line = (cart_body(body_s).get("students") or [{}])[0].get("student_line_id")
    if not line:
        print("[FAIL] could not create student")
        sys.exit(1)

    st_b, search_body, _ = request("GET", "/api/branches/public/search?active_only=true")
    branch = ((search_body or {}).get("branches") or [None])[0]
    if not branch:
        print("        no branch; skipping multi-course checks")
        sys.exit(0)

    bid = branch["id"]
    st_c, c_body, _ = request("GET", f"/api/courses/public/by-branch/{urllib.parse.quote(bid)}")
    courses = (c_body or {}).get("courses") or []
    if len(courses) < 2:
        print(f"        branch has {len(courses)} course(s); need 2+ for multi add test")
        if len(courses) == 1:
            c = courses[0]
            dur = (c.get("available_durations") or [{}])[0]
            did = dur.get("id") or dur.get("code")
            st1, b1, _ = request(
                "POST",
                "/api/carts/current/items",
                {
                    "student_line_id": line,
                    "course_id": c["id"],
                    "branch_id": bid,
                    "duration_id": did,
                },
                headers=headers,
            )
            if not ok("single course add", st1 == 200, f"status={st1}"):
                failures += 1
        print()
        sys.exit(failures)

    selections = []
    for c in courses[:3]:
        dur = (c.get("available_durations") or [{}])[0]
        did = dur.get("id") or dur.get("code")
        if c.get("id") and did:
            selections.append({"course_id": c["id"], "duration_id": did})

    st_m, body_m, err_m = request(
        "POST",
        "/api/carts/current/items/multi",
        {"student_line_id": line, "branch_id": bid, "selections": selections},
        headers=headers,
    )
    added = (body_m or {}).get("bulk_summary", {}).get("added")
    item_count = cart_body(body_m).get("totals", {}).get("item_count")
    if not ok(
        "multi add returns cart",
        st_m == 200 and item_count == added and added >= 2,
        err_m or f"status={st_m} added={added} items={item_count}",
    ):
        failures += 1

    groups = cart_body(body_m).get("student_groups") or []
    one_student_items = groups[0].get("items") if groups else []
    line_total = groups[0].get("line_total") if groups else 0
    sum_lines = round(sum(float(i.get("pricing", {}).get("total_amount") or 0) for i in one_student_items), 2)
    if not ok("student line total matches items", line_total == sum_lines, f"{line_total} vs {sum_lines}"):
        failures += 1

    first = selections[0]
    st_dup, _, _ = request(
        "POST",
        "/api/carts/current/items",
        {
            "student_line_id": line,
            "course_id": first["course_id"],
            "branch_id": bid,
            "duration_id": first["duration_id"],
        },
        headers=headers,
    )
    if not ok("duplicate same course rejected", st_dup == 400, f"status={st_dup}"):
        failures += 1

    st_v, body_v, _ = request("POST", "/api/carts/current/validate", headers=headers)
    if not ok(
        "validate multi-course cart",
        st_v == 200 and cart_body(body_v).get("is_valid") is True,
        f"status={st_v}",
    ):
        failures += 1

    print()
    if failures:
        print(f"{failures} failure(s)")
        sys.exit(1)
    print("All S02 checks passed")


if __name__ == "__main__":
    main()
