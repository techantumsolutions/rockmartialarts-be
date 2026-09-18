"""
M07-S05 Invoice WhatsApp delivery QA (template/context + OpenAPI smoke).

  python scripts/qa_m07_s05_invoice_whatsapp.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = (
    os.getenv("INVOICE_QA_BASE")
    or os.getenv("BILLING_QA_BASE")
    or os.getenv("CART_QA_BASE")
    or "http://127.0.0.1:8003"
).rstrip("/")


def ok(label, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    extra = f" — {detail}" if detail else ""
    line = f"[{status}] {label}{extra}"
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))
    return passed


def load_mod():
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)

    # Stub helpers.send_whatsapp to avoid importing full helpers deps path issues
    if "utils" not in sys.modules:
        pkg = types.ModuleType("utils")
        pkg.__path__ = [os.path.join(ROOT, "utils")]
        sys.modules["utils"] = pkg

    # Load helpers lightly if possible; otherwise stub
    helpers_path = os.path.join(ROOT, "utils", "helpers.py")
    try:
        spec = importlib.util.spec_from_file_location("utils.helpers", helpers_path)
        helpers = importlib.util.module_from_spec(spec)
        sys.modules["utils.helpers"] = helpers
        # Minimal stubs before exec to avoid fastapi import chain if needed
        spec.loader.exec_module(helpers)
    except Exception:
        helpers = types.ModuleType("utils.helpers")

        async def send_whatsapp(phone, message):
            return True

        def serialize_doc(doc):
            return doc

        helpers.send_whatsapp = send_whatsapp
        helpers.serialize_doc = serialize_doc
        sys.modules["utils.helpers"] = helpers

    # Stub database get_db
    database = types.ModuleType("utils.database")

    def get_db():
        return None

    database.get_db = get_db
    sys.modules["utils.database"] = database

    path = os.path.join(ROOT, "utils", "invoice_whatsapp_service.py")
    spec = importlib.util.spec_from_file_location("utils.invoice_whatsapp_service", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["utils.invoice_whatsapp_service"] = mod
    spec.loader.exec_module(mod)
    return mod


def sample_invoice():
    return {
        "id": "inv-test-1",
        "invoice_number": "INV-20260917-000001",
        "total_amount": 2500,
        "currency": "INR",
        "paid_at": datetime(2026, 9, 17, 10, 0, 0),
        "payment_reference": "pay_ABC123456",
        "customer": {"name": "Test Student", "phone": "9876543210"},
        "line_items": [
            {"course_name": "Karate Beginner", "branch_name": "Indiranagar"},
        ],
    }


def test_mask_phone(mod):
    return ok(
        "mask phone keeps last 4",
        mod.mask_phone("+91 98765 43210") == "****3210",
        mod.mask_phone("+91 98765 43210"),
    )


def test_context_variables(mod):
    ctx = mod.build_invoice_whatsapp_context(sample_invoice())
    required = {
        "customer_name",
        "invoice_number",
        "amount",
        "paid_date",
        "payment_ref",
        "course_summary",
        "branch_name",
        "invoice_link",
    }
    return ok(
        "template variable mapping",
        required.issubset(ctx.keys())
        and ctx["customer_name"] == "Test Student"
        and "INV-20260917-000001" in ctx["invoice_number"]
        and "inv-test-1" in ctx["invoice_link"],
        str(ctx),
    )


def test_render_message(mod):
    ctx = mod.build_invoice_whatsapp_context(sample_invoice())
    msg = mod.render_invoice_whatsapp_message(ctx)
    return ok(
        "message renders without leftover placeholders",
        "{{" not in msg and "Test Student" in msg and "INV-20260917-000001" in msg,
        msg[:120],
    )


def test_resolve_phone(mod):
    inv = sample_invoice()
    return ok(
        "resolve phone from customer",
        mod.resolve_invoice_phone(inv) == "9876543210",
        mod.resolve_invoice_phone(inv),
    )


def test_public_summary(mod):
    summary = mod.public_whatsapp_delivery_summary(
        {
            "id": "d1",
            "status": "delivered",
            "phone_masked": "****3210",
            "attempt": 1,
            "sent_at": "2026-09-17T10:00:00",
            "secret_phone": "9876543210",
        }
    )
    return ok(
        "public summary hides raw phone",
        summary is not None
        and "secret_phone" not in summary
        and summary.get("phone_masked") == "****3210",
        str(summary),
    )


def test_openapi():
    try:
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=5) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        checks = [
            "whatsapp/resend" in raw,
            "whatsapp/deliveries" in raw,
        ]
        return ok(
            "OpenAPI exposes invoice WhatsApp endpoints",
            all(checks),
            BASE,
        )
    except Exception as exc:
        return ok("OpenAPI exposes invoice WhatsApp endpoints", False, f"skipped/unreachable: {exc}")


def main():
    print(f"M07-S05 Invoice WhatsApp QA  base={BASE}")
    mod = load_mod()
    results = [
        test_mask_phone(mod),
        test_context_variables(mod),
        test_render_message(mod),
        test_resolve_phone(mod),
        test_public_summary(mod),
        test_openapi(),
    ]
    failed = sum(1 for r in results if not r)
    print(f"\nDone. {len(results) - failed}/{len(results)} passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
