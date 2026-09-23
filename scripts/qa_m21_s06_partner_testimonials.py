"""
M21-S06 Collaboration Partner Testimonials QA.

  .venv\\Scripts\\python.exe scripts/qa_m21_s06_partner_testimonials.py
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
                    await db.collaboration_partner_testimonials.delete_many(
                        {"id": {"$in": ids}}
                    )
                await db.collaboration_partner_testimonials.delete_many(
                    {"branch_id": branch_id, "person_name": {"$regex": "^QA Partner"}}
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
        / "partner-testimonials"
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
        Path(ROOT) / "models" / "collaboration_partner_testimonial_models.py"
    ).read_text(encoding="utf-8")
    be_routes = (
        Path(ROOT) / "routes" / "collaboration_partner_routes.py"
    ).read_text(encoding="utf-8")
    checks = [
        "Partner Testimonials" in page,
        "person_role" in page or "Parent" in page,
        "rating" in page.lower(),
        "Publish" in page,
        "Partner Testimonials" in nav,
        "partner-testimonials" in nav,
        "partner-testimonials?branchId=" in branches,
        "collaboration-partners/public/branches" in public,
        "person_name" in be_models,
        "related_student_name" in be_models,
        "rating" in be_models,
        "PUBLISHED" in be_models or "published" in be_models,
        "/branches/{branch_id}/testimonials" in be_routes,
        "/public/branches/{branch_id}/testimonials" in be_routes,
    ]
    return ok(
        "FE + BE partner testimonials surfaces",
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
            f"/api/collaboration-partners/branches/{branch_id}/testimonials",
            token=token,
        )
        results.append(ok("LIST testimonials 403 for normal branch", st == 403, str(st)))

        # Public endpoint on non-partner returns empty (not 403)
        st, pub_empty = http_json(
            "GET",
            f"/api/collaboration-partners/public/branches/{branch_id}/testimonials",
            expect_status=200,
        )
        results.append(
            ok(
                "public empty for non-partner",
                pub_empty.get("is_collaboration_partner") is False
                and (pub_empty.get("testimonials") or []) == [],
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
            f"/api/collaboration-partners/branches/{branch_id}/testimonials",
            body={
                "person_name": "QA Partner Parent",
                "person_role": "parent",
                "related_student_name": "QA Kid",
                "photo_url": "/uploads/images/qa-testimonial.jpg",
                "testimonial_text": "Great partner academy experience for our child.",
                "rating": 5,
                "status": "draft",
                "display_order": 10,
            },
            token=token,
            expect_status=200,
        )
        t = created.get("testimonial") or {}
        tid = t.get("id")
        if tid:
            ids.append(tid)
        results.append(
            ok(
                "create draft with parent fields + photo + rating",
                bool(tid)
                and t.get("person_role") == "parent"
                and t.get("related_student_name") == "QA Kid"
                and t.get("rating") == 5
                and t.get("status") == "draft",
                str(tid),
            )
        )

        st, pub1 = http_json(
            "GET",
            f"/api/collaboration-partners/public/branches/{branch_id}/testimonials",
            expect_status=200,
        )
        pub_ids = {x.get("id") for x in (pub1.get("testimonials") or [])}
        results.append(ok("draft not on public list", tid not in pub_ids))

        st, _ = http_json(
            "PUT",
            f"/api/collaboration-partners/branches/{branch_id}/testimonials/{tid}",
            body={"publish": True},
            token=token,
            expect_status=200,
        )
        st, pub2 = http_json(
            "GET",
            f"/api/collaboration-partners/public/branches/{branch_id}/testimonials",
            expect_status=200,
        )
        pub_rows = pub2.get("testimonials") or []
        pub_ids2 = {x.get("id") for x in pub_rows}
        match = next((x for x in pub_rows if x.get("id") == tid), None)
        results.append(
            ok(
                "published appears on public list",
                tid in pub_ids2
                and match
                and match.get("student_name") == "QA Partner Parent"
                and "Parent of QA Kid" in (match.get("role") or ""),
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
                "status has_testimonials",
                status.get("has_testimonials") is True
                and (status.get("testimonials_count") or 0) >= 1,
            )
        )

        # Legacy student testimonials API still works
        st, legacy = http_json(
            "GET",
            f"/api/testimonials?branch_id={branch_id}&limit=5",
        )
        results.append(
            ok(
                "legacy testimonials API regression",
                st == 200 and "testimonials" in legacy,
                str(st),
            )
        )

        st, _ = http_json(
            "DELETE",
            f"/api/collaboration-partners/branches/{branch_id}/testimonials/{tid}",
            token=token,
            expect_status=200,
        )
        st, pub3 = http_json(
            "GET",
            f"/api/collaboration-partners/public/branches/{branch_id}/testimonials",
            expect_status=200,
        )
        results.append(
            ok(
                "deactivated removed from public",
                tid not in {x.get("id") for x in (pub3.get("testimonials") or [])},
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
            f"/api/collaboration-partners/branches/{branch_id}/testimonials",
            token=token,
        )
        results.append(ok("admin testimonials gated after flag off", st == 403, str(st)))

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
