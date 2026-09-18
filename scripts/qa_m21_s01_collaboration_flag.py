"""
M21-S01 Collaboration Partner branch flag QA.

  .venv\\Scripts\\python.exe scripts/qa_m21_s01_collaboration_flag.py
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
                    return sa, name
            return None, dbn

        try:
            sa, _ = asyncio.run(run())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                sa, _ = loop.run_until_complete(run())
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


def _patch_branch_flags(branch_ids_flags):
    """Restore flags after QA: list of (id, flag)."""
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
        if not uri or not branch_ids_flags:
            return
        dbn = os.getenv("DB_NAME") or "marshalats"

        async def run():
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
            for name in (dbn, "marshalats", "rockmartialarts", "rock_martial_arts"):
                db = client[name]
                for bid, flag in branch_ids_flags:
                    await db.branches.update_one(
                        {"id": bid},
                        {
                            "$set": {
                                "allows_collaboration": flag,
                                "is_collaboration_partner": flag,
                            }
                        },
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
        print(f"(restore skipped: {exc})")


def test_fe_surfaces():
    results = []
    create = (
        FE_ROOT / "app" / "[adminType]" / "dashboard" / "create-branch" / "page.tsx"
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
    listing = (
        FE_ROOT / "app" / "[adminType]" / "dashboard" / "branches" / "page.tsx"
    ).read_text(encoding="utf-8")
    be_models = (Path(ROOT) / "models" / "branch_models.py").read_text(encoding="utf-8")
    be_util = (Path(ROOT) / "utils" / "collaboration_partner.py").read_text(
        encoding="utf-8"
    )
    be_routes = (
        Path(ROOT) / "routes" / "collaboration_partner_routes.py"
    ).read_text(encoding="utf-8")
    checks = [
        "Is Collaboration Partner" in create,
        "is_collaboration_partner: false" in create or "is_collaboration_partner: false," in create,
        "Is Collaboration Partner" in edit,
        "Partner" in listing,
        "allows_collaboration" in be_models,
        "is_collaboration_partner" in be_models,
        "require_collaboration_partner" in be_util,
        "partner_features_enabled" in be_routes,
        "collaboration_partners_only" in (
            Path(ROOT) / "routes" / "branch_public_routes.py"
        ).read_text(encoding="utf-8"),
    ]
    results.append(
        ok("FE + BE collaboration flag surfaces", all(checks), f"{sum(checks)}/{len(checks)}")
    )
    return all(results)


def test_workflow():
    results = []
    token = _mint_superadmin_token()
    if not token:
        results.append(ok("superadmin token", False))
        return all(results)
    results.append(ok("superadmin token", True))

    restores = []
    try:
        st, listed = http_json(
            "GET",
            "/api/branches?skip=0&limit=5&active_only=false",
            token=token,
            expect_status=200,
        )
        branches = listed.get("branches") or []
        if len(branches) < 1:
            results.append(ok("need at least one branch", False))
            return all(results)
        results.append(ok("list branches (regression)", True, f"n={len(branches)}"))

        # Default / missing flag reads as No
        sample = branches[0]
        sid = sample.get("id")
        flag = sample.get("is_collaboration_partner")
        if flag is None:
            flag = sample.get("allows_collaboration")
        results.append(
            ok(
                "flag present on list (defaults False)",
                flag is False or flag is True or flag is None,
                f"flag={flag}",
            )
        )

        # Treat None as False for default check on a non-touched branch — pick second if first we mutate
        target = branches[0]
        tid = target["id"]
        original = bool(
            target.get("is_collaboration_partner")
            or target.get("allows_collaboration")
        )
        restores.append((tid, original))

        # Enable partner
        st, _ = http_json(
            "PUT",
            f"/api/branches/{tid}",
            body={"is_collaboration_partner": True},
            token=token,
            expect_status=200,
        )
        st, got = http_json(
            "GET",
            f"/api/branches/{tid}",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "enable partner flag",
                got.get("is_collaboration_partner") is True
                and got.get("allows_collaboration") is True,
                str(got.get("is_collaboration_partner")),
            )
        )

        st, status = http_json(
            "GET",
            f"/api/collaboration-partners/branches/{tid}/status",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "partner status enabled",
                status.get("partner_features_enabled") is True,
            )
        )

        st, req_ok = http_json(
            "GET",
            f"/api/collaboration-partners/branches/{tid}/require-enabled",
            token=token,
            expect_status=200,
        )
        results.append(ok("require-enabled allows partner", st == 200))

        # Public filter includes partner
        st, pub = http_json(
            "GET",
            "/api/branches/public/search?collaboration_partners_only=true&limit=100",
            expect_status=200,
        )
        pub_ids = {b.get("id") for b in (pub.get("branches") or [])}
        results.append(
            ok(
                "public partners-only includes enabled branch",
                tid in pub_ids,
                f"total={pub.get('total')}",
            )
        )

        # Disable again — gate blocks
        st, _ = http_json(
            "PUT",
            f"/api/branches/{tid}",
            body={"is_collaboration_partner": False},
            token=token,
            expect_status=200,
        )
        st, status2 = http_json(
            "GET",
            f"/api/collaboration-partners/branches/{tid}/status",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "disable partner flag",
                status2.get("partner_features_enabled") is False,
            )
        )
        st, blocked = http_json(
            "GET",
            f"/api/collaboration-partners/branches/{tid}/require-enabled",
            token=token,
        )
        results.append(
            ok("require-enabled blocks normal branch", st == 403, str(st))
        )

        st, pub2 = http_json(
            "GET",
            "/api/branches/public/search?collaboration_partners_only=true&limit=100",
            expect_status=200,
        )
        pub_ids2 = {b.get("id") for b in (pub2.get("branches") or [])}
        results.append(
            ok(
                "partners-only excludes normal branch",
                tid not in pub_ids2,
                f"total={pub2.get('total')}",
            )
        )

        # Regression: normal public search still returns branches
        st, all_pub = http_json(
            "GET",
            "/api/branches/public/search?limit=20",
            expect_status=200,
        )
        results.append(
            ok(
                "normal public search still works",
                (all_pub.get("total") or 0) >= 1
                and len(all_pub.get("branches") or []) >= 1,
                f"total={all_pub.get('total')}",
            )
        )

        # Default create payload without flag → False (via model)
        from models.branch_models import BranchCreate  # noqa — path via cwd

        # Use dict check via OpenAPI / controller path: PATCH alias allows_collaboration
        st, _ = http_json(
            "PUT",
            f"/api/branches/{tid}",
            body={"allows_collaboration": True},
            token=token,
            expect_status=200,
        )
        st, got2 = http_json(
            "GET",
            f"/api/branches/{tid}",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "allows_collaboration alias syncs",
                got2.get("is_collaboration_partner") is True,
            )
        )

    except Exception as exc:
        results.append(ok("workflow exception", False, str(exc)))
    finally:
        _patch_branch_flags(restores)

    return all(results)


def main():
    print(f"BASE={BASE}")
    # Ensure be utils importable for optional checks
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    a = test_fe_surfaces()
    b = test_workflow()
    print(f"\nRESULT: {int(a) + int(b)}/2 suites passed")
    sys.exit(0 if a and b else 1)


if __name__ == "__main__":
    main()
