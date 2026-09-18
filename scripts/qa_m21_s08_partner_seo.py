"""
M21-S08 Collaboration Partner SEO QA.

  .venv\\Scripts\\python.exe scripts/qa_m21_s08_partner_seo.py
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


def _mongo_cleanup(branch_id: str, restore_flag: bool):
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
            return
        dbn = os.getenv("DB_NAME") or "marshalats"

        async def run():
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
            for name in (dbn, "marshalats", "rockmartialarts", "rock_martial_arts"):
                db = client[name]
                await db.collaboration_partner_seo.delete_many(
                    {"branch_id": branch_id}
                )
                await db.branches.update_one(
                    {"id": branch_id},
                    {
                        "$set": {
                            "allows_collaboration": restore_flag,
                            "is_collaboration_partner": restore_flag,
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
        print(f"(cleanup skipped: {exc})")


def test_fe_surfaces():
    page = (
        FE_ROOT
        / "app"
        / "[adminType]"
        / "dashboard"
        / "partner-seo"
        / "page.tsx"
    ).read_text(encoding="utf-8")
    nav = (FE_ROOT / "lib" / "dashboard-config.ts").read_text(encoding="utf-8")
    branches = (
        FE_ROOT / "app" / "[adminType]" / "dashboard" / "branches" / "page.tsx"
    ).read_text(encoding="utf-8")
    public = (
        FE_ROOT
        / "app"
        / "(website)"
        / "branches"
        / "[slug]"
        / "page.tsx"
    ).read_text(encoding="utf-8")
    be_models = (
        Path(ROOT) / "models" / "collaboration_partner_seo_models.py"
    ).read_text(encoding="utf-8")
    be_routes = (
        Path(ROOT) / "routes" / "collaboration_partner_routes.py"
    ).read_text(encoding="utf-8")
    be_ctrl = (
        Path(ROOT) / "controllers" / "collaboration_partner_seo_controller.py"
    ).read_text(encoding="utf-8")
    checks = [
        "Partner SEO" in page,
        "meta_title" in page,
        "meta_description" in page,
        "keywords" in page,
        "og_image" in page or "OG Image" in page,
        "Partner SEO" in nav,
        "partner-seo" in nav,
        "partner-seo?branchId=" in branches,
        "public/branches" in public and "/seo" in public,
        "generateMetadata" in public,
        "meta_title" in be_models,
        "meta_description" in be_models,
        "keywords" in be_models,
        "og_image" in be_models,
        "/branches/{branch_id}/seo" in be_routes,
        "/public/branches/{branch_id}/seo" in be_routes,
        "collaboration_partner_seo" in be_ctrl,
        "get_public" in be_ctrl,
    ]
    return ok(
        "FE + BE partner SEO surfaces",
        all(checks),
        f"{sum(checks)}/{len(checks)}",
    )


def test_workflow():
    results = []
    token = _mint_superadmin_token()
    if not token:
        results.append(ok("superadmin token", False))
        return all(results)
    results.append(ok("superadmin token", True))

    branch_id = None
    original_flag = False
    try:
        st, listed = http_json(
            "GET",
            "/api/branches?skip=0&limit=10&active_only=false",
            token=token,
            expect_status=200,
        )
        branches = listed.get("branches") or []
        if not branches:
            results.append(ok("need a branch", False))
            return all(results)
        target = branches[0]
        branch_id = target["id"]
        original_flag = bool(
            target.get("is_collaboration_partner")
            or target.get("allows_collaboration")
        )

        _mongo_cleanup(branch_id, restore_flag=False)

        st, _ = http_json(
            "PUT",
            f"/api/branches/{branch_id}",
            body={"is_collaboration_partner": False},
            token=token,
            expect_status=200,
        )
        st, _ = http_json(
            "GET",
            f"/api/collaboration-partners/branches/{branch_id}/seo",
            token=token,
        )
        results.append(ok("GET seo 403 for normal branch", st == 403, str(st)))

        st, pub0 = http_json(
            "GET",
            f"/api/collaboration-partners/public/branches/{branch_id}/seo",
            expect_status=200,
        )
        results.append(
            ok(
                "public empty for non-partner",
                pub0.get("is_collaboration_partner") is False
                and pub0.get("has_seo") is False,
            )
        )

        st, _ = http_json(
            "PUT",
            f"/api/branches/{branch_id}",
            body={"is_collaboration_partner": True},
            token=token,
            expect_status=200,
        )

        st, empty = http_json(
            "GET",
            f"/api/collaboration-partners/branches/{branch_id}/seo",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "empty SEO for new partner",
                empty.get("has_seo") is False
                and empty.get("meta_title") is None,
            )
        )

        st, saved = http_json(
            "PUT",
            f"/api/collaboration-partners/branches/{branch_id}/seo",
            body={
                "meta_title": "QA Partner Meta Title",
                "meta_description": "QA partner meta description for search.",
                "keywords": "qa, martial arts, partner",
                "og_image": "/uploads/images/qa-partner-og.jpg",
            },
            token=token,
            expect_status=200,
        )
        seo = saved.get("seo") or {}
        results.append(
            ok(
                "upsert meta title/description/keywords/og",
                seo.get("has_seo") is True
                and seo.get("meta_title") == "QA Partner Meta Title"
                and seo.get("meta_description")
                and seo.get("keywords") == "qa, martial arts, partner"
                and seo.get("og_image") == "/uploads/images/qa-partner-og.jpg",
            )
        )

        st, pub1 = http_json(
            "GET",
            f"/api/collaboration-partners/public/branches/{branch_id}/seo",
            expect_status=200,
        )
        results.append(
            ok(
                "public SEO returns fields",
                pub1.get("has_seo") is True
                and pub1.get("meta_title") == "QA Partner Meta Title"
                and pub1.get("og_image") == "/uploads/images/qa-partner-og.jpg",
            )
        )

        st, status = http_json(
            "GET",
            f"/api/collaboration-partners/branches/{branch_id}/status",
            token=token,
            expect_status=200,
        )
        results.append(ok("status has_seo", status.get("has_seo") is True))

        st, partners = http_json(
            "GET",
            "/api/collaboration-partners/partner-branches?limit=50",
            token=token,
            expect_status=200,
        )
        row = next(
            (
                p
                for p in (partners.get("partners") or [])
                if p.get("branch_id") == branch_id
            ),
            None,
        )
        results.append(
            ok("partner list has_seo", bool(row) and row.get("has_seo") is True)
        )

        # Partial update keeps other fields
        st, patched = http_json(
            "PUT",
            f"/api/collaboration-partners/branches/{branch_id}/seo",
            body={"meta_title": "QA Partner Meta Title Updated"},
            token=token,
            expect_status=200,
        )
        seo2 = patched.get("seo") or {}
        results.append(
            ok(
                "partial update preserves keywords/og",
                seo2.get("meta_title") == "QA Partner Meta Title Updated"
                and seo2.get("keywords") == "qa, martial arts, partner"
                and seo2.get("og_image") == "/uploads/images/qa-partner-og.jpg",
            )
        )

        # Branch list regression (normal branches still work)
        st, still = http_json(
            "GET",
            "/api/branches?skip=0&limit=5&active_only=false",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "normal branch list regression",
                len(still.get("branches") or []) >= 1,
            )
        )

        st, _ = http_json(
            "PUT",
            f"/api/branches/{branch_id}",
            body={"is_collaboration_partner": False},
            token=token,
            expect_status=200,
        )
        st, _ = http_json(
            "GET",
            f"/api/collaboration-partners/branches/{branch_id}/seo",
            token=token,
        )
        results.append(ok("seo gated after flag off", st == 403, str(st)))

        st, pub2 = http_json(
            "GET",
            f"/api/collaboration-partners/public/branches/{branch_id}/seo",
            expect_status=200,
        )
        results.append(
            ok(
                "public SEO empty after flag off",
                pub2.get("is_collaboration_partner") is False
                and pub2.get("has_seo") is False,
            )
        )

    except Exception as exc:
        results.append(ok("workflow exception", False, str(exc)))
    finally:
        if branch_id:
            _mongo_cleanup(branch_id, restore_flag=original_flag)

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
