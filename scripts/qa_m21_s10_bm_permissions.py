"""
M21-S10 Collaboration Branch Manager Permissions QA.

  .venv\\Scripts\\python.exe scripts/qa_m21_s10_bm_permissions.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
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
        with urllib.request.urlopen(req, timeout=30) as resp:
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


def _env():
    from dotenv import load_dotenv

    load_dotenv(Path(ROOT) / ".env")
    uri = (
        os.getenv("MONGO_URI")
        or os.getenv("MONGO_URL")
        or os.getenv("MONGODB_URL")
        or os.getenv("DATABASE_URL")
    )
    dbn = os.getenv("DB_NAME") or "marshalats"
    secret = os.getenv("SECRET_KEY") or "student_management_secret_key_2025_secure"
    return uri, dbn, secret


def _mint_token(role: str, user_id: str, managed_branches=None):
    import jwt

    _, _, secret = _env()
    payload = {"sub": user_id, "role": role}
    if managed_branches is not None:
        payload["managed_branches"] = managed_branches
    return jwt.encode(payload, secret, algorithm="HS256")


def _mint_superadmin_token():
    try:
        import asyncio
        from motor.motor_asyncio import AsyncIOMotorClient

        uri, dbn, secret = _env()
        if not uri or "your_database" in uri:
            return None

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
        return _mint_token("superadmin", sa["id"])
    except Exception as exc:
        print(f"(mint sa skipped: {exc})")
        return None


def test_fe_surfaces():
    banner = (
        FE_ROOT / "components" / "partner" / "PartnerCmsScopeBanner.tsx"
    ).read_text(encoding="utf-8")
    edit = (
        FE_ROOT
        / "app"
        / "[adminType]"
        / "dashboard"
        / "branches"
        / "edit"
        / "[id]"
        / "page.tsx"
    ).read_text(encoding="utf-8")
    nav = (FE_ROOT / "lib" / "dashboard-config.ts").read_text(encoding="utf-8")
    helpers = (
        Path(ROOT) / "utils" / "collaboration_partner.py"
    ).read_text(encoding="utf-8")
    routes = (
        Path(ROOT) / "routes" / "collaboration_partner_routes.py"
    ).read_text(encoding="utf-8")
    branch_ctrl = (
        Path(ROOT) / "controllers" / "branch_controller.py"
    ).read_text(encoding="utf-8")
    settings = (
        Path(ROOT) / "routes" / "settings_routes.py"
    ).read_text(encoding="utf-8")
    discount = (
        Path(ROOT) / "routes" / "discount_rule_routes.py"
    ).read_text(encoding="utf-8")
    invoice = (
        Path(ROOT) / "controllers" / "invoice_controller.py"
    ).read_text(encoding="utf-8")

    partner_pages_ok = all(
        "PartnerCmsScopeBanner" in (FE_ROOT / "app" / "[adminType]" / "dashboard" / p / "page.tsx").read_text(encoding="utf-8")
        for p in (
            "partner-profiles",
            "partner-branding",
            "partner-masters",
            "partner-gallery",
            "partner-testimonials",
            "partner-team",
            "partner-seo",
        )
    )
    checks = [
        "Assigned-branch Partner CMS" in banner,
        "can_manage_roles" in banner or "role management" in banner.lower(),
        "canToggleCollaborationPartner" in edit,
        "disabled={!canToggleCollaborationPartner}" in edit,
        "Partner Profiles" in nav and "BRANCH_ADMIN_BASE" in nav,
        "get_partner_branch_for_cms" in helpers,
        "assert_branch_manager_assigned" in helpers,
        "assert_can_toggle_collaboration_flag" in helpers,
        "partner_cms_permissions" in helpers,
        "/me/permissions" in routes,
        "assert_can_toggle_collaboration_flag" in branch_ctrl,
        "require_role_unified([UserRole.SUPER_ADMIN])" in settings,
        "require_role_unified([UserRole.SUPER_ADMIN])" in discount,
        "_managed_branch_ids" in invoice,
        partner_pages_ok,
        "Partner SEO" in nav,
        "Discount Rules" not in nav.split("BRANCH_ADMIN_MENU")[1].split("STUDENT_MENU")[0]
        if "BRANCH_ADMIN_MENU" in nav
        else False,
    ]
    return ok(
        "FE + BE BM permission surfaces",
        all(checks),
        f"{sum(checks)}/{len(checks)}",
    )


def _find_branch_manager():
    """Return (bm_id, managed_branch_ids) from Mongo."""
    try:
        import asyncio
        from motor.motor_asyncio import AsyncIOMotorClient

        uri, dbn, _ = _env()
        if not uri:
            return None, []

        async def run():
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
            for name in (dbn, "marshalats", "rockmartialarts", "rock_martial_arts"):
                db = client[name]
                # Prefer active managers with at least one assigned branch
                candidates = await db.branch_managers.find(
                    {"is_active": True}
                ).to_list(100)
                if not candidates:
                    candidates = await db.branch_managers.find({}).to_list(100)
                best = None
                best_managed = []
                for bm in candidates:
                    managed_rows = await db.branches.find(
                        {"manager_id": bm["id"]}, {"id": 1}
                    ).to_list(50)
                    managed = [str(r["id"]) for r in managed_rows if r.get("id")]
                    if managed and (
                        best is None
                        or (bm.get("is_active") and not (best or {}).get("is_active"))
                        or len(managed) > len(best_managed)
                    ):
                        best = bm
                        best_managed = managed
                if best:
                    return best["id"], best_managed, name
            return None, [], dbn

        try:
            return asyncio.run(run())[:2]
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                bm_id, managed, _ = loop.run_until_complete(run())
                return bm_id, managed
            finally:
                loop.close()
    except Exception as exc:
        print(f"(find bm skipped: {exc})")
        return None, []


def test_workflow():
    results = []
    sa = _mint_superadmin_token()
    if not sa:
        results.append(ok("superadmin token", False))
        return all(results)
    results.append(ok("superadmin token", True))

    bm_id, managed = _find_branch_manager()
    if not bm_id or not managed:
        results.append(ok("need existing branch manager with branch", False))
        return all(results)
    results.append(ok("reuse existing branch_manager", True, bm_id))

    branch_a = managed[0]
    branch_b = None
    flag_a = False
    flag_b = False
    try:
        st, listed = http_json(
            "GET",
            "/api/branches?skip=0&limit=50&active_only=false",
            token=sa,
            expect_status=200,
        )
        branches = listed.get("branches") or []
        by_id = {b["id"]: b for b in branches}
        if branch_a not in by_id:
            results.append(ok("managed branch visible to SA", False, branch_a))
            return all(results)
        flag_a = bool(
            by_id[branch_a].get("is_collaboration_partner")
            or by_id[branch_a].get("allows_collaboration")
        )
        for b in branches:
            if b["id"] != branch_a:
                branch_b = b["id"]
                flag_b = bool(
                    b.get("is_collaboration_partner") or b.get("allows_collaboration")
                )
                break
        if not branch_b:
            results.append(ok("need a second branch", False))
            return all(results)

        # Enable partner on both for CMS tests
        st, _ = http_json(
            "PUT",
            f"/api/branches/{branch_a}",
            body={"is_collaboration_partner": True},
            token=sa,
            expect_status=200,
        )
        st, _ = http_json(
            "PUT",
            f"/api/branches/{branch_b}",
            body={"is_collaboration_partner": True},
            token=sa,
            expect_status=200,
        )

        bm_token = _mint_token("branch_manager", bm_id, managed_branches=managed)

        st, perms = http_json(
            "GET",
            "/api/collaboration-partners/me/permissions",
            token=bm_token,
            expect_status=200,
        )
        results.append(
            ok(
                "BM permissions reuse branch_manager role",
                perms.get("reuses_branch_manager_role") is True
                and perms.get("is_branch_manager") is True
                and perms.get("can_toggle_collaboration_flag") is False
                and perms.get("can_access_platform_settings") is False
                and perms.get("can_manage_roles") is False
                and perms.get("can_view_other_branch_financials") is False,
            )
        )
        results.append(
            ok(
                "BM partner_cms scoped to assigned",
                branch_a in (perms.get("partner_cms_branch_ids") or [])
                and branch_b not in (perms.get("partner_cms_branch_ids") or []),
            )
        )

        st, partners = http_json(
            "GET",
            "/api/collaboration-partners/partner-branches?limit=100",
            token=bm_token,
            expect_status=200,
        )
        ids = {p.get("branch_id") for p in (partners.get("partners") or [])}
        results.append(
            ok(
                "BM partner list assigned only",
                branch_a in ids and branch_b not in ids,
            )
        )

        st, _ = http_json(
            "GET",
            f"/api/collaboration-partners/branches/{branch_a}/branding",
            token=bm_token,
            expect_status=200,
        )
        results.append(ok("BM can access assigned partner CMS", st == 200))

        st, _ = http_json(
            "GET",
            f"/api/collaboration-partners/branches/{branch_b}/branding",
            token=bm_token,
        )
        results.append(
            ok("BM blocked from other partner CMS", st == 403, str(st))
        )

        st, _ = http_json(
            "PUT",
            f"/api/branches/{branch_a}",
            body={"is_collaboration_partner": False},
            token=bm_token,
        )
        results.append(
            ok("BM cannot toggle partner flag", st == 403, str(st))
        )

        st, _ = http_json("GET", "/api/settings", token=bm_token)
        results.append(ok("BM blocked from platform settings", st in (401, 403), str(st)))

        # Role management / user list — prefer an SA-only write/list if available
        st, _ = http_json(
            "GET",
            "/api/discount-rules",
            token=bm_token,
        )
        results.append(
            ok("BM blocked from platform discount rules", st in (401, 403), str(st))
        )

        st, sa_partners = http_json(
            "GET",
            "/api/collaboration-partners/partner-branches?limit=100",
            token=sa,
            expect_status=200,
        )
        sa_ids = {p.get("branch_id") for p in (sa_partners.get("partners") or [])}
        results.append(
            ok(
                "SA still lists multiple partners",
                branch_a in sa_ids and branch_b in sa_ids,
            )
        )

        st, sa_perms = http_json(
            "GET",
            "/api/collaboration-partners/me/permissions",
            token=sa,
            expect_status=200,
        )
        results.append(
            ok(
                "SA can toggle flag + platform settings",
                sa_perms.get("can_toggle_collaboration_flag") is True
                and sa_perms.get("can_access_platform_settings") is True
                and sa_perms.get("can_manage_roles") is True,
            )
        )

        st, _ = http_json(
            "GET",
            f"/api/collaboration-partners/public/branches/{branch_a}/landing",
            expect_status=200,
        )
        results.append(ok("public landing regression", st == 200))

    except Exception as exc:
        results.append(ok("workflow exception", False, str(exc)))
    finally:
        try:
            if branch_a:
                http_json(
                    "PUT",
                    f"/api/branches/{branch_a}",
                    body={"is_collaboration_partner": flag_a},
                    token=sa,
                )
            if branch_b:
                http_json(
                    "PUT",
                    f"/api/branches/{branch_b}",
                    body={"is_collaboration_partner": flag_b},
                    token=sa,
                )
        except Exception as exc:
            print(f"(restore skipped: {exc})")

    return all(results)


def main():
    print(f"BASE={BASE}")
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    a = test_fe_surfaces()
    b = test_workflow()
    print(f"\nRESULT: {int(a) + int(b)}/2 suites passed")
    sys.exit(0 if a and b else 1)


if __name__ == "__main__":
    main()
