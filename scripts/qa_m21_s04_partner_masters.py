"""
M21-S04 Collaboration Partner Masters / Experts QA.

  .venv\\Scripts\\python.exe scripts/qa_m21_s04_partner_masters.py
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


def _mongo_cleanup(branch_id: str, restore_flag: bool, master_ids=None):
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
        master_ids = master_ids or []

        async def run():
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
            for name in (dbn, "marshalats", "rockmartialarts", "rock_martial_arts"):
                db = client[name]
                if master_ids:
                    await db.collaboration_partner_masters.delete_many(
                        {"id": {"$in": master_ids}}
                    )
                await db.collaboration_partner_masters.delete_many(
                    {"branch_id": branch_id, "name": {"$regex": "^QA Master"}}
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
        / "partner-masters"
        / "page.tsx"
    ).read_text(encoding="utf-8")
    nav = (FE_ROOT / "lib" / "dashboard-config.ts").read_text(encoding="utf-8")
    branches = (
        FE_ROOT / "app" / "[adminType]" / "dashboard" / "branches" / "page.tsx"
    ).read_text(encoding="utf-8")
    be_models = (
        Path(ROOT) / "models" / "collaboration_partner_master_models.py"
    ).read_text(encoding="utf-8")
    be_routes = (
        Path(ROOT) / "routes" / "collaboration_partner_routes.py"
    ).read_text(encoding="utf-8")
    checks = [
        "Partner Masters / Experts" in page,
        "Specializations" in page,
        "Achievements" in page,
        "Biography" in page,
        "Experience" in page,
        "Partner Masters" in nav,
        "partner-masters" in nav,
        "partner-masters?branchId=" in branches,
        "photo_url" in be_models,
        "specializations" in be_models,
        "achievements" in be_models,
        "experience_years" in be_models,
        "/branches/{branch_id}/masters" in be_routes,
        "collaboration_partner_masters" in (
            Path(ROOT) / "controllers" / "collaboration_partner_master_controller.py"
        ).read_text(encoding="utf-8"),
    ]
    return ok(
        "FE + BE partner masters surfaces",
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
    other_id = None
    original_flag = False
    master_ids = []
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
        if len(branches) > 1:
            other_id = branches[1]["id"]

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
            f"/api/collaboration-partners/branches/{branch_id}/masters",
            token=token,
        )
        results.append(ok("LIST masters 403 for normal branch", st == 403, str(st)))
        st, _ = http_json(
            "POST",
            f"/api/collaboration-partners/branches/{branch_id}/masters",
            body={"name": "QA Master Blocked"},
            token=token,
        )
        results.append(ok("POST master 403 for normal branch", st == 403, str(st)))

        st, _ = http_json(
            "PUT",
            f"/api/branches/{branch_id}",
            body={"is_collaboration_partner": True},
            token=token,
            expect_status=200,
        )

        st, empty = http_json(
            "GET",
            f"/api/collaboration-partners/branches/{branch_id}/masters",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "LIST empty masters for partner",
                empty.get("branch_id") == branch_id
                and isinstance(empty.get("masters"), list),
            )
        )

        body = {
            "name": "QA Master Sensei",
            "designation": "Head Instructor",
            "photo_url": "/uploads/images/qa-master.jpg",
            "biography": "QA biography for partner master",
            "experience_years": 12,
            "experience_summary": "National coach",
            "specializations": ["Karate", "Self defense"],
            "achievements": ["Gold medal 2020", "Coach of year"],
            "display_order": 10,
            "is_active": True,
        }
        st, created = http_json(
            "POST",
            f"/api/collaboration-partners/branches/{branch_id}/masters",
            body=body,
            token=token,
            expect_status=200,
        )
        master = created.get("master") or {}
        mid = master.get("id")
        if mid:
            master_ids.append(mid)
        results.append(
            ok(
                "create master with photo/bio/experience",
                bool(mid)
                and master.get("name") == "QA Master Sensei"
                and master.get("photo_url") == "/uploads/images/qa-master.jpg"
                and master.get("experience_years") == 12
                and "Karate" in (master.get("specializations") or [])
                and "Gold medal 2020" in (master.get("achievements") or []),
                str(mid),
            )
        )

        st, got = http_json(
            "GET",
            f"/api/collaboration-partners/branches/{branch_id}/masters/{mid}",
            token=token,
            expect_status=200,
        )
        results.append(
            ok("GET master by id", got.get("id") == mid and got.get("branch_id") == branch_id)
        )

        st, updated = http_json(
            "PUT",
            f"/api/collaboration-partners/branches/{branch_id}/masters/{mid}",
            body={
                "designation": "Chief Master",
                "achievements": ["Gold medal 2020", "Updated award"],
            },
            token=token,
            expect_status=200,
        )
        um = updated.get("master") or {}
        results.append(
            ok(
                "update master designation + achievements",
                um.get("designation") == "Chief Master"
                and "Updated award" in (um.get("achievements") or [])
                and um.get("photo_url") == "/uploads/images/qa-master.jpg",
            )
        )

        # Create second master; ensure list scoped to branch
        st, created2 = http_json(
            "POST",
            f"/api/collaboration-partners/branches/{branch_id}/masters",
            body={"name": "QA Master Two", "display_order": 20},
            token=token,
            expect_status=200,
        )
        mid2 = (created2.get("master") or {}).get("id")
        if mid2:
            master_ids.append(mid2)

        st, listed_m = http_json(
            "GET",
            f"/api/collaboration-partners/branches/{branch_id}/masters",
            token=token,
            expect_status=200,
        )
        ids = {m.get("id") for m in (listed_m.get("masters") or [])}
        results.append(
            ok(
                "list scoped to branch",
                mid in ids and mid2 in ids and (listed_m.get("total") or 0) >= 2,
            )
        )

        if other_id:
            # Enable other briefly only to prove isolation: listing other without masters empty
            # Don't enable — listing other when not partner should 403
            st, other_list = http_json(
                "GET",
                f"/api/collaboration-partners/branches/{other_id}/masters",
                token=token,
            )
            # other may or may not be partner; if not partner expect 403; if partner expect empty of our QA ids
            if st == 403:
                results.append(ok("other non-partner branch gated", True))
            else:
                other_ids = {m.get("id") for m in (other_list.get("masters") or [])}
                results.append(
                    ok(
                        "other partner branch does not include this branch masters",
                        mid not in other_ids and mid2 not in other_ids,
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
                "status has_masters + count",
                status.get("has_masters") is True
                and (status.get("masters_count") or 0) >= 2,
            )
        )

        # Soft delete
        st, _ = http_json(
            "DELETE",
            f"/api/collaboration-partners/branches/{branch_id}/masters/{mid}",
            token=token,
            expect_status=200,
        )
        st, active_only = http_json(
            "GET",
            f"/api/collaboration-partners/branches/{branch_id}/masters",
            token=token,
            expect_status=200,
        )
        active_ids = {m.get("id") for m in (active_only.get("masters") or [])}
        results.append(ok("soft delete hides from active list", mid not in active_ids))

        st, with_inactive = http_json(
            "GET",
            f"/api/collaboration-partners/branches/{branch_id}/masters?include_inactive=true",
            token=token,
            expect_status=200,
        )
        all_ids = {m.get("id") for m in (with_inactive.get("masters") or [])}
        results.append(ok("include_inactive shows deactivated", mid in all_ids))

        # Global champions collection untouched smoke: list champions still works
        st, champs = http_json(
            "GET",
            "/api/champions?limit=5&include_archived=true",
            token=token,
        )
        results.append(
            ok(
                "global champions API regression",
                st == 200 and "champions" in champs,
                str(st),
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
            f"/api/collaboration-partners/branches/{branch_id}/masters",
            token=token,
        )
        results.append(ok("masters gated after flag off", st == 403, str(st)))

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
            _mongo_cleanup(branch_id, restore_flag=original_flag, master_ids=master_ids)

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
