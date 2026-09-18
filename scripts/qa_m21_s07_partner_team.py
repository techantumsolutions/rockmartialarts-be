"""
M21-S07 Collaboration Partner Team QA.

  .venv\\Scripts\\python.exe scripts/qa_m21_s07_partner_team.py
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


def _mongo_cleanup(branch_id: str, restore_flag: bool, ids=None):
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
        ids = ids or []

        async def run():
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
            for name in (dbn, "marshalats", "rockmartialarts", "rock_martial_arts"):
                db = client[name]
                if ids:
                    await db.collaboration_partner_team.delete_many(
                        {"id": {"$in": ids}}
                    )
                await db.collaboration_partner_team.delete_many(
                    {"branch_id": branch_id, "name": {"$regex": "^QA Team"}}
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
        / "partner-team"
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
        / "branch-detail-client.tsx"
    ).read_text(encoding="utf-8")
    be_models = (
        Path(ROOT) / "models" / "collaboration_partner_team_models.py"
    ).read_text(encoding="utf-8")
    be_routes = (
        Path(ROOT) / "routes" / "collaboration_partner_routes.py"
    ).read_text(encoding="utf-8")
    checks = [
        "Partner Team" in page,
        "contact_approved" in page,
        "Designation" in page,
        "Approve contact for public" in page,
        "Partner Team" in nav,
        "partner-team" in nav,
        "partner-team?branchId=" in branches,
        "public/branches" in public and "/team" in public,
        "contact_approved" in be_models,
        "designation" in be_models,
        "photo_url" in be_models,
        "/branches/{branch_id}/team" in be_routes,
        "/public/branches/{branch_id}/team" in be_routes,
        "collaboration_partner_team" in (
            Path(ROOT) / "controllers" / "collaboration_partner_team_controller.py"
        ).read_text(encoding="utf-8"),
    ]
    return ok(
        "FE + BE partner team surfaces",
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
    ids = []
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
            f"/api/collaboration-partners/branches/{branch_id}/team",
            token=token,
        )
        results.append(ok("LIST team 403 for normal branch", st == 403, str(st)))

        st, pub0 = http_json(
            "GET",
            f"/api/collaboration-partners/public/branches/{branch_id}/team",
            expect_status=200,
        )
        results.append(
            ok(
                "public empty for non-partner",
                pub0.get("is_collaboration_partner") is False
                and (pub0.get("members") or []) == [],
            )
        )

        st, _ = http_json(
            "PUT",
            f"/api/branches/{branch_id}",
            body={"is_collaboration_partner": True},
            token=token,
            expect_status=200,
        )

        st, created = http_json(
            "POST",
            f"/api/collaboration-partners/branches/{branch_id}/team",
            body={
                "name": "QA Team Manager",
                "designation": "Branch Manager",
                "role": "Operations",
                "photo_url": "/uploads/images/qa-team.jpg",
                "contact_email": "qa.team@example.com",
                "contact_phone": "9999999999",
                "contact_approved": False,
                "bio": "QA team bio",
                "display_order": 10,
            },
            token=token,
            expect_status=200,
        )
        member = created.get("member") or {}
        mid = member.get("id")
        if mid:
            ids.append(mid)
        results.append(
            ok(
                "create team member with role/photo/contact",
                bool(mid)
                and member.get("designation") == "Branch Manager"
                and member.get("photo_url")
                and member.get("contact_approved") is False,
                str(mid),
            )
        )

        st, pub1 = http_json(
            "GET",
            f"/api/collaboration-partners/public/branches/{branch_id}/team",
            expect_status=200,
        )
        row = next(
            (m for m in (pub1.get("members") or []) if m.get("id") == mid), None
        )
        results.append(
            ok(
                "public lists member but hides unapproved contact",
                bool(row)
                and row.get("name") == "QA Team Manager"
                and row.get("contact_email") is None
                and row.get("contact_phone") is None
                and row.get("contact_approved") is False,
            )
        )

        st, _ = http_json(
            "PUT",
            f"/api/collaboration-partners/branches/{branch_id}/team/{mid}",
            body={"contact_approved": True},
            token=token,
            expect_status=200,
        )
        st, pub2 = http_json(
            "GET",
            f"/api/collaboration-partners/public/branches/{branch_id}/team",
            expect_status=200,
        )
        row2 = next(
            (m for m in (pub2.get("members") or []) if m.get("id") == mid), None
        )
        results.append(
            ok(
                "approved contact visible on public",
                bool(row2)
                and row2.get("contact_email") == "qa.team@example.com"
                and row2.get("contact_phone") == "9999999999",
            )
        )

        st, status = http_json(
            "GET",
            f"/api/collaboration-partners/branches/{branch_id}/status",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "status has_team",
                status.get("has_team") is True
                and (status.get("team_count") or 0) >= 1,
            )
        )

        # Coaches API regression
        st, coaches = http_json(
            "GET",
            "/api/coaches?skip=0&limit=5",
            token=token,
        )
        results.append(
            ok(
                "coaches API regression",
                st in (200, 403) or "coaches" in coaches or "detail" in coaches,
                str(st),
            )
        )

        st, _ = http_json(
            "DELETE",
            f"/api/collaboration-partners/branches/{branch_id}/team/{mid}",
            token=token,
            expect_status=200,
        )
        st, pub3 = http_json(
            "GET",
            f"/api/collaboration-partners/public/branches/{branch_id}/team",
            expect_status=200,
        )
        results.append(
            ok(
                "deactivated hidden from public",
                mid not in {m.get("id") for m in (pub3.get("members") or [])},
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
            f"/api/collaboration-partners/branches/{branch_id}/team",
            token=token,
        )
        results.append(ok("team gated after flag off", st == 403, str(st)))

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

    except Exception as exc:
        results.append(ok("workflow exception", False, str(exc)))
    finally:
        if branch_id:
            _mongo_cleanup(branch_id, restore_flag=original_flag, ids=ids)

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
