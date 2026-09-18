"""
M18-S01 Notification Template Master QA.

  .venv\\Scripts\\python.exe scripts/qa_m18_s01_notification_templates.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = (
    os.getenv("STUDENT_QA_BASE")
    or os.getenv("BILLING_QA_BASE")
    or os.getenv("INVOICE_QA_BASE")
    or "http://127.0.0.1:8003"
).rstrip("/")
FE_ROOT = Path(ROOT).parent / "rockmartialarts-fe"


def ok(label, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    extra = f" — {detail}" if detail else ""
    line = f"[{status}] {label}{extra}"
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))
    return passed


def http_json(method, path, body=None, token=None, expect_status=None):
    url = f"{BASE}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            payload = json.loads(raw) if raw else {}
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="ignore")
        try:
            payload = json.loads(raw) if raw else {}
        except Exception:
            payload = {"detail": raw}
        status = exc.code
    if expect_status is not None and status != expect_status:
        raise AssertionError(
            f"{method} {path} expected {expect_status} got {status}: {payload}"
        )
    return status, payload


def _mint_superadmin_token():
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(ROOT) / ".env")
        import asyncio
        import jwt
        from motor.motor_asyncio import AsyncIOMotorClient

        uri = (
            os.getenv("MONGO_URI")
            or os.getenv("MONGO_URL")
            or os.getenv("MONGODB_URL")
            or os.getenv("DATABASE_URL")
        )
        if not uri or "your_database" in uri:
            return None
        dbn = os.getenv("DB_NAME") or "marshalats"
        secret = os.getenv("SECRET_KEY") or "student_management_secret_key_2025_secure"

        async def run():
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
            for name in (dbn, "marshalats", "rockmartialarts", "rock_martial_arts"):
                sa = await client[name].superadmins.find_one({})
                if sa:
                    return sa
            return None

        try:
            sa = asyncio.run(run())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                sa = loop.run_until_complete(run())
            finally:
                loop.close()
        if not sa:
            return None
        return jwt.encode(
            {"sub": sa["id"], "role": "superadmin"},
            secret,
            algorithm="HS256",
        )
    except Exception as exc:
        print(f"(mint token skipped: {exc})")
        return None


def _cleanup(template_ids):
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(ROOT) / ".env")
        import asyncio
        from motor.motor_asyncio import AsyncIOMotorClient

        uri = (
            os.getenv("MONGO_URI")
            or os.getenv("MONGO_URL")
            or os.getenv("MONGODB_URL")
            or os.getenv("DATABASE_URL")
        )
        if not uri or not template_ids:
            return
        dbn = os.getenv("DB_NAME") or "marshalats"

        async def run():
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
            for name in (dbn, "marshalats", "rockmartialarts", "rock_martial_arts"):
                await client[name].notification_templates.delete_many(
                    {"id": {"$in": template_ids}}
                )

        try:
            asyncio.run(run())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(run())
            finally:
                loop.close()
    except Exception as exc:
        print(f"(cleanup skipped: {exc})")


def test_fe_surfaces():
    results = []
    admin = (
        FE_ROOT / "components" / "notifications" / "NotificationTemplatesAdminPage.tsx"
    ).read_text(encoding="utf-8")
    api = (FE_ROOT / "lib" / "notificationTemplateAPI.ts").read_text(encoding="utf-8")
    page = (
        FE_ROOT
        / "app"
        / "[adminType]"
        / "dashboard"
        / "settings"
        / "notification-templates"
        / "page.tsx"
    ).read_text(encoding="utf-8")
    nav = (FE_ROOT / "lib" / "dashboard-config.ts").read_text(encoding="utf-8")
    be_models = (
        Path(ROOT) / "models" / "notification_template_models.py"
    ).read_text(encoding="utf-8")
    be_routes = (
        Path(ROOT) / "routes" / "notification_template_routes.py"
    ).read_text(encoding="utf-8")
    checks = [
        "placeholders" in admin,
        "DLT template ID" in admin,
        "WhatsApp provider template" in admin,
        "notification-templates" in api,
        "NotificationTemplatesAdminPage" in page,
        "Notification Templates" in nav,
        "settings/notification-templates" in nav,
        "provider_template_name" in be_models,
        "dlt_template_id" in be_models,
        "normalize_placeholders" in be_models,
        "/preview" in be_routes,
        "list_notification_templates" in be_routes
        or "create_notification_template" in be_routes,
    ]
    results.append(
        ok("FE + BE template surfaces", all(checks), f"{sum(checks)}/{len(checks)}")
    )
    return all(results)


def test_workflow():
    results = []
    token = _mint_superadmin_token()
    if not token:
        results.append(ok("superadmin token", False))
        return all(results)
    results.append(ok("superadmin token", True))

    suffix = uuid.uuid4().hex[:8]
    ids = []

    try:
        st, meta = http_json(
            "GET",
            "/api/notification-templates/meta",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "template meta catalog",
                "sms" in (meta.get("channels") or [])
                and "whatsapp" in (meta.get("channels") or [])
                and len(meta.get("placeholders") or []) >= 3,
                f"channels={meta.get('channels')}",
            )
        )

        # SMS template
        st, sms = http_json(
            "POST",
            "/api/notification-templates",
            body={
                "name": f"qa_m18_s01_sms_{suffix}",
                "display_name": f"QA SMS {suffix}",
                "channel": "sms",
                "category": "reminder",
                "status": "active",
                "body": "Hi {{name}}, reminder for {{when}} at {{venue}}.",
                "dlt_template_id": "DLT-QA-001",
                "placeholders": [
                    {"key": "name", "label": "Name", "sample": "Ravi"},
                    {"key": "when", "label": "When", "sample": "10:00"},
                ],
            },
            token=token,
            expect_status=201,
        )
        sms_tpl = sms.get("template") or {}
        sid = sms_tpl.get("id")
        if sid:
            ids.append(sid)
        results.append(
            ok(
                "create SMS template",
                bool(sid)
                and sms_tpl.get("channel") == "sms"
                and sms_tpl.get("status") == "active"
                and sms_tpl.get("dlt_template_id") == "DLT-QA-001"
                and "name" in (sms_tpl.get("placeholder_keys") or [])
                and "venue" in (sms_tpl.get("placeholder_keys") or []),
                str(sms_tpl.get("placeholder_keys")),
            )
        )

        # WhatsApp template
        st, wa = http_json(
            "POST",
            "/api/notification-templates",
            body={
                "name": f"qa_m18_s01_wa_{suffix}",
                "display_name": f"QA WA {suffix}",
                "channel": "whatsapp",
                "category": "invoice",
                "status": "draft",
                "body": "Hi {{customer_name}}, invoice {{invoice_number}} amount {{amount}}.",
                "provider_template_name": "rock_invoice_qa_v1",
                "provider_reference": "rock_invoice_qa_v1",
            },
            token=token,
            expect_status=201,
        )
        wa_tpl = wa.get("template") or {}
        wid = wa_tpl.get("id")
        if wid:
            ids.append(wid)
        results.append(
            ok(
                "create WhatsApp template",
                bool(wid)
                and wa_tpl.get("channel") == "whatsapp"
                and wa_tpl.get("provider_template_name") == "rock_invoice_qa_v1"
                and "customer_name" in (wa_tpl.get("placeholder_keys") or []),
                str(wa_tpl.get("placeholder_keys")),
            )
        )

        st, patched = http_json(
            "PATCH",
            f"/api/notification-templates/{sid}",
            body={
                "status": "inactive",
                "body": "Hi {{name}}, updated reminder {{when}}.",
                "dlt_template_id": "DLT-QA-002",
            },
            token=token,
            expect_status=200,
        )
        pt = patched.get("template") or {}
        results.append(
            ok(
                "patch SMS template status/body",
                pt.get("status") == "inactive"
                and pt.get("dlt_template_id") == "DLT-QA-002"
                and pt.get("is_active") is False,
                str(pt.get("status")),
            )
        )

        st, preview = http_json(
            "POST",
            f"/api/notification-templates/{wid}/preview",
            body={
                "context": {
                    "customer_name": "Ravi",
                    "invoice_number": "INV-9",
                    "amount": "Rs 100",
                }
            },
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "preview WhatsApp template",
                "Ravi" in (preview.get("rendered_body") or "")
                and "INV-9" in (preview.get("rendered_body") or "")
                and not (preview.get("missing_placeholders") or []),
                preview.get("rendered_body", "")[:80],
            )
        )

        st, listed = http_json(
            "GET",
            f"/api/notification-templates?channel=sms&search=qa_m18_s01_sms_{suffix}",
            token=token,
            expect_status=200,
        )
        rows = listed.get("templates") or []
        results.append(
            ok(
                "list/filter SMS templates",
                any(r.get("id") == sid for r in rows),
                f"total={listed.get('total')}",
            )
        )

        # Duplicate name same channel -> 409
        st, dup = http_json(
            "POST",
            "/api/notification-templates",
            body={
                "name": f"qa_m18_s01_sms_{suffix}",
                "channel": "sms",
                "body": "dup",
                "status": "draft",
            },
            token=token,
        )
        results.append(ok("duplicate name rejected", st == 409, str(st)))

        st, archived = http_json(
            "DELETE",
            f"/api/notification-templates/{sid}",
            token=token,
            expect_status=200,
        )
        at = archived.get("template") or {}
        results.append(
            ok(
                "soft-archive template",
                at.get("status") == "archived" and at.get("is_active") is False,
                str(at.get("status")),
            )
        )

        # Existing invoice template collection still readable (enrich)
        st, all_tpl = http_json(
            "GET",
            "/api/notification-templates?include_archived=true&limit=5",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "list templates (legacy-safe)",
                isinstance(all_tpl.get("templates"), list),
                f"total={all_tpl.get('total')}",
            )
        )

    except Exception as exc:
        results.append(ok("workflow exception", False, str(exc)))
    finally:
        _cleanup(ids)

    return all(results)


def main():
    print(f"BASE={BASE}")
    a = test_fe_surfaces()
    b = test_workflow()
    print(f"\nRESULT: {int(a) + int(b)}/2 suites passed")
    sys.exit(0 if a and b else 1)


if __name__ == "__main__":
    main()
