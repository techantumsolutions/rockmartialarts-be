"""
M05-S04 discount engine smoke tests (pure calculation + optional live cart).

  python scripts/qa_m05_s04_discount.py
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

import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE_PATH = os.path.join(ROOT, "utils", "discount_engine_core.py")
_spec = importlib.util.spec_from_file_location("discount_engine_core_qa", CORE_PATH)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["discount_engine_core_qa"] = _mod
_spec.loader.exec_module(_mod)
compute_cart_promotions = _mod.compute_cart_promotions


def ok(label, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    extra = f" — {detail}" if detail else ""
    print(f"[{status}] {label}{extra}")
    return passed


def sample_item(amount: float, *, sid: str, course: str, branch: str = "b1") -> dict:
    return {
        "id": str(uuid.uuid4()),
        "student_line_id": sid,
        "course_id": course,
        "branch_id": branch,
        "duration_id": "d1",
        "pricing": {"total_amount": amount, "course_fee": amount, "admission_fee": 0, "currency": "INR"},
    }


def main():
    failures = 0

    rules_multi_student = [
        {
            "id": "r1",
            "code": "FAMILY2",
            "name": "Family 2+ students",
            "trigger": "multi_student",
            "discount_kind": "percentage",
            "discount_value": 10,
            "min_students": 2,
            "apply_scope": "cart",
            "is_active": True,
            "priority": 10,
            "stackable": False,
        }
    ]
    cart_two_students = {
        "students": [
            {"student_line_id": "s1", "label": "A"},
            {"student_line_id": "s2", "label": "B"},
        ],
        "items": [
            sample_item(3000, sid="s1", course="c1"),
            sample_item(2000, sid="s2", course="c2"),
        ],
    }
    promo = compute_cart_promotions(cart_two_students, rules=rules_multi_student)
    if not ok(
        "multi_student 10% on 5000",
        promo.promo_discount_total == 500.0 and promo.total_amount == 4500.0,
        f"discount={promo.promo_discount_total} total={promo.total_amount}",
    ):
        failures += 1

    rules_one_student = list(rules_multi_student)
    cart_one = {
        "students": [{"student_line_id": "s1", "label": "A"}],
        "items": [sample_item(3000, sid="s1", course="c1")],
    }
    promo0 = compute_cart_promotions(cart_one, rules=rules_one_student)
    if not ok("multi_student not applied for 1 student", promo0.promo_discount_total == 0):
        failures += 1

    rules_per_student = [
        {
            "id": "r2",
            "code": "MULTI_COURSE",
            "name": "2 courses same student",
            "trigger": "multi_course_student",
            "discount_kind": "fixed",
            "discount_value": 500,
            "min_courses_per_student": 2,
            "apply_scope": "per_student_line",
            "is_active": True,
            "priority": 20,
            "stackable": False,
        }
    ]
    cart_multi_course = {
        "students": [{"student_line_id": "s1", "label": "A"}],
        "items": [
            sample_item(2000, sid="s1", course="c1"),
            sample_item(1500, sid="s1", course="c2"),
        ],
    }
    promo2 = compute_cart_promotions(cart_multi_course, rules=rules_per_student)
    if not ok(
        "per-student fixed discount",
        promo2.promo_discount_total == 500.0 and promo2.total_amount == 3000.0,
        f"discount={promo2.promo_discount_total}",
    ):
        failures += 1

    rules_min_amount = [
        {
            "id": "r3",
            "code": "BIGCART",
            "name": "Cart min amount",
            "trigger": "cart_min_amount",
            "discount_kind": "fixed",
            "discount_value": 200,
            "min_cart_amount": 4000,
            "apply_scope": "cart",
            "is_active": True,
            "priority": 5,
        }
    ]
    promo3 = compute_cart_promotions(cart_two_students, rules=rules_min_amount)
    if not ok("cart min amount fixed 200", promo3.promo_discount_total == 200.0):
        failures += 1

    inactive = [{**rules_multi_student[0], "is_active": False}]
    promo4 = compute_cart_promotions(cart_two_students, rules=inactive)
    if not ok("inactive rule ignored", promo4.promo_discount_total == 0):
        failures += 1

    def http_request(method, path, body=None, headers=None):
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
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {}
            return exc.code, payload

    st, _ = http_request("GET", "/health")
    if st == 200:
        guest = str(uuid.uuid4())
        headers = {"X-Guest-Cart-Token": guest}
        http_request(
            "POST",
            "/api/carts/current/students/bulk",
            {"students": [{"label": "D1"}, {"label": "D2"}]},
            headers=headers,
        )
        _, body = http_request("GET", "/api/carts/current", headers=headers)
        cart = (body or {}).get("cart") or {}
        if not ok(
            "live cart payload includes discount fields",
            "discount_breakdown" in cart and "subtotal_amount" in (cart.get("totals") or {}),
            "",
        ):
            failures += 1
    else:
        print("[SKIP] backend health — live cart discount fields")

    print()
    if failures:
        print(f"{failures} failure(s)")
        sys.exit(1)
    print("All S04 discount checks passed")


if __name__ == "__main__":
    main()
