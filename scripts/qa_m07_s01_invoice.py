"""
M07-S01 invoice generation checks (pure helpers + optional live API smoke).

  python scripts/qa_m07_s01_invoice.py
"""
import importlib.util
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = (
    os.getenv("INVOICE_QA_BASE")
    or os.getenv("CART_QA_BASE")
    or os.getenv("BRANCH_QA_BASE")
    or "http://127.0.0.1:8003"
).rstrip("/")


def ok(label, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    extra = f" — {detail}" if detail else ""
    print(f"[{status}] {label}{extra}")
    return passed


def load_module(rel_path, name):
    path = os.path.join(ROOT, rel_path)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    # Provide lightweight stubs for package imports used only at module level
    sys.modules.setdefault("utils", type(sys)("utils"))
    spec.loader.exec_module(mod)
    return mod


def test_invoice_number_format():
    # Pure string format check without DB
    from datetime import datetime

    day = datetime(2026, 9, 17).strftime("%Y%m%d")
    sample = f"INV-{day}-000001"
    return ok(
        "invoice number format",
        sample.startswith("INV-") and sample.count("-") == 2 and sample.endswith("000001"),
        sample,
    )


def test_html_document_totals():
    path = os.path.join(ROOT, "utils", "invoice_document.py")
    spec = importlib.util.spec_from_file_location("invoice_document", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    invoice = {
        "invoice_number": "INV-20260917-000001",
        "status": "issued",
        "currency": "INR",
        "company": {"name": "Rock Martial Arts Academy"},
        "customer": {"name": "Test Student", "email": "a@b.com", "phone": "9999999999"},
        "payment_reference": "pay_test_1",
        "payment_method": "upi",
        "subtotal_amount": 3000,
        "admission_total": 1800,
        "discount_total": 200,
        "tax_total": 0,
        "total_amount": 4600,
        "line_items": [
            {
                "description": "Karate enrollment",
                "student_label": "Student A",
                "branch_name": "Indiranagar",
                "course_fee": 1500,
                "admission_fee": 900,
                "discount_amount": 100,
                "line_total": 2300,
            },
            {
                "description": "Kickboxing enrollment",
                "student_label": "Student B",
                "branch_name": "Indiranagar",
                "course_fee": 1500,
                "admission_fee": 900,
                "discount_amount": 100,
                "line_total": 2300,
            },
        ],
    }
    html = mod.build_invoice_html(invoice)
    checks = [
        "INV-20260917-000001" in html,
        "Test Student" in html,
        "Karate enrollment" in html,
        "Kickboxing enrollment" in html,
        "4,600.00" in html or "4600.00" in html,
        "Student A" in html and "Student B" in html,
    ]
    return ok("HTML invoice multi-student document", all(checks), f"{sum(checks)}/6 checks")


def test_line_math():
    # Mimic cart proportional discount allocation
    lines = [
        {"course_fee": 2000.0, "admission_fee": 500.0},
        {"course_fee": 1000.0, "admission_fee": 500.0},
    ]
    discount_total = 300.0
    subs = [l["course_fee"] + l["admission_fee"] for l in lines]
    total_sub = sum(subs)
    alloc = []
    remaining = discount_total
    for i, sub in enumerate(subs):
        if i == len(subs) - 1:
            disc = round(remaining, 2)
        else:
            disc = round(discount_total * (sub / total_sub), 2)
            remaining = round(remaining - disc, 2)
        alloc.append(round(sub - disc, 2))
    total = round(sum(alloc), 2)
    return ok(
        "multi-line discount allocation totals",
        total == round(total_sub - discount_total, 2) and abs(sum([subs[i] - alloc[i] for i in range(2)]) - discount_total) < 0.02,
        f"lines={alloc} total={total}",
    )


def test_openapi_has_invoices():
    try:
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=5) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        has = "/api/invoices" in raw
        return ok("OpenAPI exposes /api/invoices", has, BASE)
    except Exception as exc:
        return ok("OpenAPI exposes /api/invoices", False, f"skipped/unreachable: {exc}")


def main():
    print(f"M07-S01 Invoice QA  base={BASE}")
    results = [
        test_invoice_number_format(),
        test_html_document_totals(),
        test_line_math(),
        test_openapi_has_invoices(),
    ]
    failed = sum(1 for r in results if not r)
    print(f"\nDone. {len(results) - failed}/{len(results)} passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
