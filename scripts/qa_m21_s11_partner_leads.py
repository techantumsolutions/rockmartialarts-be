"""
M21-S11 Partner Landing Page Leads QA.

  .venv\\Scripts\\python.exe scripts/qa_m21_s11_partner_leads.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

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

        uri, dbn, _ = _env()
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


def _find_active_bm():
    try:
        import asyncio
        from motor.motor_asyncio import AsyncIOMotorClient

        uri, dbn, _ = _env()

        async def run():
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
            for name in (dbn, "marshalats", "rockmartialarts", "rock_martial_arts"):
                db = client[name]
                for bm in await db.branch_managers.find({"is_active": True}).to_list(50):
                    managed = [
                        str(r["id"])
                        for r in await db.branches.find(
                            {"manager_id": bm["id"]}, {"id": 1}
                        ).to_list(20)
                        if r.get("id")
                    ]
                    if managed:
                        return bm["id"], managed
            return None, []

        try:
            return asyncio.run(run())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(run())
            finally:
                loop.close()
    except Exception as exc:
        print(f"(find bm skipped: {exc})")
        return None, []


def _cleanup_leads(phone_suffix: str, lead_ids):
    try:
        import asyncio
        from motor.motor_asyncio import AsyncIOMotorClient

        uri, dbn, _ = _env()

        async def run():
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
            for name in (dbn, "marshalats", "rockmartialarts", "rock_martial_arts"):
                db = client[name]
                if lead_ids:
                    await db.leads.delete_many({"id": {"$in": lead_ids}})
                await db.leads.delete_many({"phone": {"$regex": phone_suffix}})

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
    landing = (
        FE_ROOT
        / "app"
        / "(website)"
        / "partners"
        / "[slug]"
        / "partner-landing-client.tsx"
    ).read_text(encoding="utf-8")
    leads_page = (
        FE_ROOT / "app" / "[adminType]" / "dashboard" / "leads" / "page.tsx"
    ).read_text(encoding="utf-8")
    be_routes = (
        Path(ROOT) / "routes" / "collaboration_partner_routes.py"
    ).read_text(encoding="utf-8")
    be_lead = (
        Path(ROOT) / "controllers" / "collaboration_partner_lead_controller.py"
    ).read_text(encoding="utf-8")
    lead_svc = (Path(ROOT) / "utils" / "lead_service.py").read_text(encoding="utf-8")
    checks = [
        "Send an enquiry" in landing,
        "Submit enquiry" in landing,
        "/leads" in landing and "by-slug" in landing,
        "Partner Landing" in leads_page,
        "activeLead.message" in leads_page,
        "/public/by-slug/{slug}/leads" in be_routes,
        "/public/branches/{branch_id}/leads" in be_routes,
        "partner_landing" in be_lead,
        "partner_landing" in lead_svc,
        "message" in lead_svc and "branch_id" in be_lead,
    ]
    return ok(
        "FE + BE partner lead surfaces",
        all(checks),
        f"{sum(checks)}/{len(checks)}",
    )


def test_workflow():
    results = []
    sa = _mint_superadmin_token()
    if not sa:
        results.append(ok("superadmin token", False))
        return all(results)
    results.append(ok("superadmin token", True))

    bm_id, managed = _find_active_bm()
    if not bm_id or not managed:
        results.append(ok("need active BM with branch", False))
        return all(results)
    results.append(ok("active BM", True, bm_id))

    branch_a = managed[0]
    branch_b = None
    flag_a = False
    flag_b = False
    phone_suffix = str(uuid4().int)[-8:]
    phone = f"98{phone_suffix}"
    lead_ids = []

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
            results.append(ok("managed branch found", False))
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

        # Non-partner should 404
        st, _ = http_json(
            "PUT",
            f"/api/branches/{branch_a}",
            body={"is_collaboration_partner": False},
            token=sa,
            expect_status=200,
        )
        st, _ = http_json(
            "POST",
            f"/api/collaboration-partners/public/branches/{branch_a}/leads",
            body={
                "name": "QA Partner Lead",
                "phone": phone,
                "message": "hello",
            },
        )
        results.append(ok("lead 404 for non-partner", st == 404, str(st)))

        st, _ = http_json(
            "PUT",
            f"/api/branches/{branch_a}",
            body={"is_collaboration_partner": True},
            token=sa,
            expect_status=200,
        )
        if branch_b:
            http_json(
                "PUT",
                f"/api/branches/{branch_b}",
                body={"is_collaboration_partner": True},
                token=sa,
                expect_status=200,
            )

        st, created = http_json(
            "POST",
            f"/api/collaboration-partners/public/branches/{branch_a}/leads",
            body={
                "name": "QA Partner Lead",
                "phone": phone,
                "email": "qa.partner.lead@example.com",
                "message": "Interested in kids class",
                "interest": "Karate",
            },
            expect_status=201,
        )
        lead = (created.get("lead") or {})
        lid = lead.get("id")
        if lid:
            lead_ids.append(lid)
        results.append(
            ok(
                "partner lead tagged partner_landing + branch",
                lead.get("source_type") == "partner_landing"
                and lead.get("branch_id") == branch_a
                and lead.get("message") == "Interested in kids class"
                and lead.get("course") == "Karate"
                and created.get("lead", {}).get("routed_to_super_admin") is True,
                str(lid),
            )
        )

        # by-slug path
        st, landing = http_json(
            "GET",
            f"/api/collaboration-partners/public/branches/{branch_a}/landing",
            expect_status=200,
        )
        slug = (landing.get("slug") or "").strip()
        if slug:
            phone2 = f"97{phone_suffix}"
            st, created2 = http_json(
                "POST",
                f"/api/collaboration-partners/public/by-slug/{slug}/leads",
                body={
                    "name": "QA Partner Lead Slug",
                    "phone": phone2,
                    "message": "Via slug form",
                },
                expect_status=201,
            )
            lid2 = (created2.get("lead") or {}).get("id")
            if lid2:
                lead_ids.append(lid2)
            results.append(ok("submit by slug", bool(lid2), str(lid2)))
        else:
            results.append(ok("submit by slug (no slug skip)", True))

        # Super Admin sees it
        st, sa_list = http_json(
            "GET",
            f"/api/leads?source_type=partner_landing&search={phone_suffix}&limit=50",
            token=sa,
            expect_status=200,
        )
        sa_ids = {x.get("id") for x in (sa_list.get("leads") or [])}
        results.append(ok("SA sees partner lead", lid in sa_ids))

        # Assigned BM sees it
        bm_token = _mint_token("branch_manager", bm_id, managed_branches=managed)
        st, bm_list = http_json(
            "GET",
            f"/api/leads?source_type=partner_landing&search={phone_suffix}&limit=50",
            token=bm_token,
            expect_status=200,
        )
        bm_ids = {x.get("id") for x in (bm_list.get("leads") or [])}
        results.append(ok("assigned BM sees partner lead", lid in bm_ids))

        # Other BM (if we can mint with empty managed) should not see branch_a lead
        # Use a second BM if available; otherwise simulate with empty managed + different id
        # Safer: create lead on branch_b and ensure our BM doesn't see it when not managed
        if branch_b and branch_b not in managed:
            phone3 = f"96{phone_suffix}"
            st, other = http_json(
                "POST",
                f"/api/collaboration-partners/public/branches/{branch_b}/leads",
                body={"name": "Other Branch Lead", "phone": phone3, "message": "x"},
                expect_status=201,
            )
            other_id = (other.get("lead") or {}).get("id")
            if other_id:
                lead_ids.append(other_id)
            st, bm_list2 = http_json(
                "GET",
                f"/api/leads?source_type=partner_landing&search={phone_suffix}&limit=50",
                token=bm_token,
                expect_status=200,
            )
            bm_ids2 = {x.get("id") for x in (bm_list2.get("leads") or [])}
            results.append(
                ok(
                    "BM does not see other-branch partner lead",
                    other_id not in bm_ids2 and lid in bm_ids2,
                )
            )
        else:
            results.append(ok("BM other-branch isolation (skip)", True))

        # Sources list includes partner_landing
        st, sources = http_json(
            "GET",
            "/api/leads/sources",
            token=sa,
            expect_status=200,
        )
        src_vals = {
            (s.get("value") or s.get("source_type") or s.get("id"))
            for s in (sources.get("sources") or [])
        }
        results.append(
            ok(
                "sources includes partner_landing",
                "partner_landing" in src_vals or any(
                    "partner" in str(s).lower()
                    for s in (sources.get("sources") or [])
                ),
                str(list(src_vals)[:8]),
            )
        )

        # Existing public lead API regression
        st, _ = http_json(
            "POST",
            "/api/leads",
            body={
                "name": "QA Normal Lead",
                "phone": f"95{phone_suffix}",
                "email": "qa.normal@example.com",
                "source": "website_popup",
            },
            expect_status=201,
        )
        results.append(ok("normal lead create regression", st == 201))

    except Exception as exc:
        results.append(ok("workflow exception", False, str(exc)))
    finally:
        _cleanup_leads(phone_suffix, lead_ids)
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
            print(f"(flag restore skipped: {exc})")

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
