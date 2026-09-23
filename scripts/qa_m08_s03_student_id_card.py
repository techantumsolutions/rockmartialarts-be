"""
M08-S03 student ID card QA (offline helpers + OpenAPI smoke).

  python scripts/qa_m08_s03_student_id_card.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import types
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = (
    os.getenv("STUDENT_QA_BASE")
    or os.getenv("BILLING_QA_BASE")
    or os.getenv("INVOICE_QA_BASE")
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

    if "utils" not in sys.modules:
        pkg = types.ModuleType("utils")
        pkg.__path__ = [os.path.join(ROOT, "utils")]
        sys.modules["utils"] = pkg

    database = types.ModuleType("utils.database")
    database.get_db = lambda: None
    sys.modules["utils.database"] = database

    helpers = types.ModuleType("utils.helpers")

    def serialize_doc(doc):
        if isinstance(doc, dict):
            return {k: v for k, v in doc.items() if k != "_id"}
        if isinstance(doc, list):
            return [serialize_doc(x) for x in doc]
        return doc

    helpers.serialize_doc = serialize_doc
    sys.modules["utils.helpers"] = helpers

    status_svc = types.ModuleType("utils.student_status_service")

    async def assert_can_manage_student_status(db, student, current_user):
        return None

    status_svc.assert_can_manage_student_status = assert_can_manage_student_status
    sys.modules["utils.student_status_service"] = status_svc

    if "fastapi" not in sys.modules:
        fastapi = types.ModuleType("fastapi")

        class HTTPException(Exception):
            def __init__(self, status_code=400, detail=""):
                self.status_code = status_code
                self.detail = detail
                super().__init__(detail)

        fastapi.HTTPException = HTTPException
        sys.modules["fastapi"] = fastapi

    path = os.path.join(ROOT, "utils", "student_id_card_service.py")
    spec = importlib.util.spec_from_file_location("utils.student_id_card_service", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["utils.student_id_card_service"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_card_number_unique_shape(mod):
    a = mod.generate_card_number()
    b = mod.generate_card_number()
    return ok(
        "card numbers unique + RMA prefix",
        a != b and a.startswith("RMA-") and b.startswith("RMA-"),
        f"{a} / {b}",
    )


def test_qr_token_not_student_id(mod):
    student_id = "550e8400-e29b-41d4-a716-446655440000"
    token = mod.generate_qr_token()
    return ok(
        "QR token opaque (not raw student UUID)",
        token != student_id and len(token) >= 32 and student_id not in token,
        f"len={len(token)}",
    )


def test_verify_url_uses_token(mod):
    token = "abcTOKEN1234567890xyz"
    url = mod.build_verify_url(token)
    return ok(
        "verify URL embeds token path only",
        url.endswith(f"/verify/student/{token}") and "550e8400" not in url,
        url,
    )


def test_qr_png_encodes_verify_url(mod):
    try:
        import qrcode  # noqa: F401
    except ImportError:
        return ok("QR PNG generation", False, "qrcode not installed")

    url = mod.build_verify_url("tok_" + ("x" * 40))
    b64 = mod.build_qr_png_base64(url)
    return ok("QR PNG base64 generated", isinstance(b64, str) and len(b64) > 100, f"len={len(b64)}")


def test_card_png_template(mod):
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        return ok("ID card PNG template", False, "Pillow not installed")

    payload = {
        "full_name": "Test Student",
        "card_number": "RMA-TEST-001",
        "branch_name": "Main",
        "course_name": "Karate",
        "account_status": "Active",
    }
    url = mod.build_verify_url("tok_" + ("y" * 40))
    b64 = mod.build_id_card_png_base64(payload, url)
    return ok("ID card PNG template generated", isinstance(b64, str) and len(b64) > 100, f"len={len(b64)}")


def test_verify_invalid_token_shape(mod):
    async def run():
        return await mod.verify_qr_token("short")

    out = asyncio.run(run())
    return ok(
        "short/invalid token returns invalid (no leak)",
        out.get("valid") is False and out.get("display") is None,
        str(out.get("status")),
    )


def test_openapi():
    try:
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=5) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        checks = [
            ("/id-card" in raw, "id-card"),
            ("student-id/verify" in raw or "/verify/" in raw, "public verify"),
        ]
        missing = [name for passed, name in checks if not passed]
        return ok(
            "OpenAPI exposes ID card + public verify",
            not missing,
            BASE if not missing else f"missing={missing}",
        )
    except Exception as exc:
        return ok(
            "OpenAPI exposes ID card + public verify",
            False,
            f"skipped/unreachable: {exc}",
        )


def main():
    print(f"M08-S03 Student ID Card QA  base={BASE}")
    mod = load_mod()
    results = [
        test_card_number_unique_shape(mod),
        test_qr_token_not_student_id(mod),
        test_verify_url_uses_token(mod),
        test_qr_png_encodes_verify_url(mod),
        test_card_png_template(mod),
        test_verify_invalid_token_shape(mod),
        test_openapi(),
    ]
    failed = sum(1 for r in results if not r)
    print(f"\nDone. {len(results) - failed}/{len(results)} passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
