"""
M18-S03 Branch-Specific SMS Templates QA.

  .venv\\Scripts\\python.exe scripts/qa_m18_s03_branch_templates.py
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


def _pick_branch_id():
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
        if not uri:
            return None
        dbn = os.getenv("DB_NAME") or "marshalats"

        async def run():
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
            for name in (dbn, "marshalats", "rockmartialarts", "rock_martial_arts"):
                b = await client[name].branches.find_one({"is_active": {"$ne": False}})
                if b and b.get("id"):
                    return str(b["id"])
            return None

        try:
            return asyncio.run(run())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(run())
            finally:
                loop.close()
    except Exception:
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
        FE_ROOT
        / "components"
        / "notifications"
        / "NotificationTemplatesAdminPage.tsx"
    ).read_text(encoding="utf-8")
    api = (FE_ROOT / "lib" / "notificationTemplateAPI.ts").read_text(encoding="utf-8")
    nav = (FE_ROOT / "lib" / "dashboard-config.ts").read_text(encoding="utf-8")
    be = (Path(ROOT) / "utils" / "notification_template_service.py").read_text(
        encoding="utf-8"
    )
    routes = (Path(ROOT) / "routes" / "notification_template_routes.py").read_text(
        encoding="utf-8"
    )
    checks = [
        "Rock default" in admin or "Rock Martial Arts" in admin,
        "Clone to my branch" in admin or "cloneToBranch" in admin,
        "scopeFilter" in admin or "Scope" in admin,
        "cloneToBranch" in api,
        "resolve(" in api,
        "branch-admin" in nav.lower() or "BRANCH_ADMIN" in nav,
        "settings/notification-templates" in nav,
        "resolve_notification_template" in be,
        "clone_rock_template_to_branch" in be,
        "assert_template_access" in be,
        "_global_branch_clause" in be,
        "/resolve" in routes,
        "clone-to-branch" in routes,
    ]
    results.append(
        ok("FE + BE branch template surfaces", all(checks), f"{sum(checks)}/{len(checks)}")
    )
    return all(results)


def test_workflow():
    results = []
    token = _mint_superadmin_token()
    if not token:
        results.append(ok("superadmin token", False))
        return all(results)
    results.append(ok("superadmin token", True))

    branch_id = _pick_branch_id()
    if not branch_id:
        results.append(ok("pick branch", False, "no active branch"))
        return all(results)
    results.append(ok("pick branch", True, branch_id[:8]))

    suffix = uuid.uuid4().hex[:8]
    name = f"qa_m18_s03_welcome_{suffix}"
    ids = []

    try:
        # Rock global default
        st, rock = http_json(
            "POST",
            "/api/notification-templates",
            body={
                "name": name,
                "display_name": f"QA Rock {suffix}",
                "channel": "sms",
                "category": "welcome",
                "status": "active",
                "body": "ROCK: Hi {{name}}, welcome from Rock Martial Arts.",
                "dlt_template_id": "DLT-ROCK-S03",
                "is_default": True,
            },
            token=token,
            expect_status=201,
        )
        rock_tpl = rock.get("template") or {}
        rid = rock_tpl.get("id")
        if rid:
            ids.append(rid)
        results.append(
            ok(
                "create Rock global default",
                bool(rid)
                and rock_tpl.get("is_global") is True
                and not rock_tpl.get("branch_id"),
                str(rock_tpl.get("scope")),
            )
        )

        # Resolve without branch → global
        st, res0 = http_json(
            "GET",
            f"/api/notification-templates/resolve?name={name}&channel=sms",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "resolve falls back to Rock",
                res0.get("resolution") == "global"
                and (res0.get("template") or {}).get("id") == rid,
                str(res0.get("resolution")),
            )
        )

        # Resolve with branch before override → still global fallback
        st, res1 = http_json(
            "GET",
            f"/api/notification-templates/resolve?name={name}&channel=sms&branch_id={branch_id}",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "resolve uses Rock when branch missing",
                res1.get("resolution") == "global"
                and res1.get("fallback_used") is True,
                str(res1.get("resolution")),
            )
        )

        # Branch override
        st, br = http_json(
            "POST",
            "/api/notification-templates",
            body={
                "name": name,
                "display_name": f"QA Branch {suffix}",
                "channel": "sms",
                "category": "welcome",
                "status": "active",
                "body": "BRANCH: Hi {{name}}, welcome to our branch dojo.",
                "dlt_template_id": "DLT-BRANCH-S03",
                "branch_id": branch_id,
            },
            token=token,
            expect_status=201,
        )
        br_tpl = br.get("template") or {}
        bid = br_tpl.get("id")
        if bid:
            ids.append(bid)
        results.append(
            ok(
                "create branch override",
                bool(bid)
                and br_tpl.get("branch_id") == branch_id
                and br_tpl.get("is_global") is False,
                str(br_tpl.get("branch_id"))[:8] if br_tpl.get("branch_id") else "",
            )
        )

        # Same name global + branch allowed (not 409 across scopes)
        results.append(
            ok(
                "same name allowed across scopes",
                rid != bid and bool(rid) and bool(bid),
            )
        )

        st, res2 = http_json(
            "GET",
            f"/api/notification-templates/resolve?name={name}&channel=sms&branch_id={branch_id}",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "resolve prefers branch override",
                res2.get("resolution") == "branch"
                and (res2.get("template") or {}).get("id") == bid
                and res2.get("fallback_used") is False,
                str(res2.get("resolution")),
            )
        )

        # Send via template_name + branch_id uses branch body
        st, sent = http_json(
            "POST",
            "/api/notifications/send",
            body={
                "recipient": "9876543210",
                "channel": "sms",
                "template_name": name,
                "branch_id": branch_id,
                "context": {"name": "Ravi"},
                "dry_run": True,
                "source": "qa_m18_s03",
            },
            token=token,
            expect_status=200,
        )
        msg = ((sent.get("log") or {}).get("message") or "")
        results.append(
            ok(
                "send resolves branch template",
                "BRANCH:" in msg and "Ravi" in msg,
                msg[:60],
            )
        )

        # Clone endpoint (second branch name unique via existing)
        clone_name = f"qa_m18_s03_clone_{suffix}"
        st, rock2 = http_json(
            "POST",
            "/api/notification-templates",
            body={
                "name": clone_name,
                "channel": "sms",
                "category": "reminder",
                "status": "active",
                "body": "Rock clone source {{name}}",
            },
            token=token,
            expect_status=201,
        )
        r2 = (rock2.get("template") or {}).get("id")
        if r2:
            ids.append(r2)

        st, cloned = http_json(
            "POST",
            "/api/notification-templates/clone-to-branch",
            body={
                "name": clone_name,
                "branch_id": branch_id,
                "channel": "sms",
            },
            token=token,
            expect_status=201,
        )
        ct = cloned.get("template") or {}
        if ct.get("id"):
            ids.append(ct["id"])
        results.append(
            ok(
                "clone Rock default to branch",
                ct.get("branch_id") == branch_id
                and ct.get("status") == "draft"
                and cloned.get("created") is True,
                str(ct.get("status")),
            )
        )

        # Isolation: list by branch only returns branch-scoped when scope=branch
        st, listed = http_json(
            "GET",
            f"/api/notification-templates?branch_id={branch_id}&search={name}",
            token=token,
            expect_status=200,
        )
        rows = listed.get("templates") or []
        results.append(
            ok(
                "list includes branch template",
                any(r.get("id") == bid for r in rows),
                f"count={len(rows)}",
            )
        )

        # Duplicate branch name rejected
        st, dup = http_json(
            "POST",
            "/api/notification-templates",
            body={
                "name": name,
                "channel": "sms",
                "body": "dup",
                "branch_id": branch_id,
                "status": "draft",
            },
            token=token,
        )
        results.append(ok("duplicate branch name rejected", st == 409, str(st)))

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
