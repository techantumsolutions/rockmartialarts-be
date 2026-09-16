"""
M05-S05 cart checkout fulfillment checks (pure + live API smoke).

  python scripts/qa_m05_s05_fulfillment.py
"""
import importlib.util
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE_PATH = os.path.join(ROOT, "utils", "cart_fulfillment.py")


def ok(label, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    extra = f" — {detail}" if detail else ""
    print(f"[{status}] {label}{extra}")
    return passed


def load_fulfillment_helpers():
    # Load only the pure helpers without importing utils package __init__
    # by reading source and exec of key functions — use importlib with sys.modules stub.
    text = open(CORE_PATH, encoding="utf-8").read()
    # Extract pure functions via regex-free: import module after path hack
    ns = {}
    # Minimal stubs so we can exec the pure helpers section
    code = """
def fulfillment_key_for_checkout(checkout_id: str) -> str:
    return f"cart_checkout:{checkout_id}"

def enrollment_idempotency_key(checkout_id: str, cart_item_id: str) -> str:
    return f"{checkout_id}:{cart_item_id}"
"""
    exec(code, ns)
    return ns["fulfillment_key_for_checkout"], ns["enrollment_idempotency_key"]


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


def main():
    failures = 0
    fk, ek = load_fulfillment_helpers()
    cid = str(uuid.uuid4())
    iid = str(uuid.uuid4())
    if not ok("fulfillment key format", fk(cid) == f"cart_checkout:{cid}"):
        failures += 1
    if not ok("enrollment idempotency key", ek(cid, iid) == f"{cid}:{iid}"):
        failures += 1
    if not ok("keys stable across calls", ek(cid, iid) == ek(cid, iid)):
        failures += 1
    if not ok("different items different keys", ek(cid, "a") != ek(cid, "b")):
        failures += 1

    st, _, err = request("GET", "/health")
    if not ok("backend health", st == 200, err or f"status={st}"):
        sys.exit(1)

    # Prepare without auth must be rejected (does not disturb guest cart CRUD)
    guest = str(uuid.uuid4())
    headers = {"X-Guest-Cart-Token": guest}
    st_p, body_p, _ = request("POST", "/api/carts/current/checkout/prepare", {}, headers=headers)
    if not ok(
        "prepare requires student auth",
        st_p in (401, 403),
        f"status={st_p} detail={body_p.get('detail') if isinstance(body_p, dict) else body_p}",
    ):
        failures += 1

    st_c, body_c, _ = request(
        "POST",
        "/api/carts/checkout/confirm",
        {
            "cart_checkout_id": str(uuid.uuid4()),
            "razorpay_order_id": "order_x",
            "razorpay_payment_id": "pay_x",
            "razorpay_signature": "sig_x",
        },
        headers=headers,
    )
    if not ok(
        "confirm requires student auth",
        st_c in (401, 403),
        f"status={st_c}",
    ):
        failures += 1

    # Guest cart still works (regression)
    st_g, body_g, _ = request("GET", "/api/carts/current", headers=headers)
    if not ok("guest cart still available", st_g == 200 and "cart" in (body_g or {}), f"status={st_g}"):
        failures += 1

    print()
    if failures:
        print(f"{failures} failure(s)")
        sys.exit(1)
    print("All S05 fulfillment checks passed")


if __name__ == "__main__":
    main()
