"""
M21-S02 Collaboration Partner business & contact profile QA.

  .venv\\Scripts\\python.exe scripts/qa_m21_s02_partner_profile.py
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
                await db.collaboration_partner_profiles.delete_many(
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
        / "partner-profiles"
        / "page.tsx"
    ).read_text(encoding="utf-8")
    nav = (FE_ROOT / "lib" / "dashboard-config.ts").read_text(encoding="utf-8")
    branches = (
        FE_ROOT / "app" / "[adminType]" / "dashboard" / "branches" / "page.tsx"
    ).read_text(encoding="utf-8")
    be_models = (
        Path(ROOT) / "models" / "collaboration_partner_models.py"
    ).read_text(encoding="utf-8")
    be_routes = (
        Path(ROOT) / "routes" / "collaboration_partner_routes.py"
    ).read_text(encoding="utf-8")
    be_pid = (Path(ROOT) / "utils" / "partner_id.py").read_text(encoding="utf-8")
    checks = [
        "Partner Business & Contact Profile" in page,
        "Partner ID" in page,
        "GSTIN" in page,
        "Collaboration agreement" in page,
        "Partner Profiles" in nav,
        "partner-profiles" in nav,
        "partner-profiles?branchId=" in branches,
        "partner_id" in be_models,
        "gstin" in be_models,
        "PartnerAgreementInfo" in be_models,
        "/branches/{branch_id}/profile" in be_routes,
        "CP-" in be_pid or 'DEFAULT_PREFIX = "CP"' in be_pid,
    ]
    return ok(
        "FE + BE partner profile surfaces",
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

        # Ensure clean profile for test
        _mongo_cleanup(branch_id, restore_flag=False)

        # Non-partner: profile blocked
        st, _ = http_json(
            "PUT",
            f"/api/branches/{branch_id}",
            body={"is_collaboration_partner": False},
            token=token,
            expect_status=200,
        )
        st, blocked = http_json(
            "GET",
            f"/api/collaboration-partners/branches/{branch_id}/profile",
            token=token,
        )
        results.append(ok("GET profile 403 for normal branch", st == 403, str(st)))
        st, blocked_put = http_json(
            "PUT",
            f"/api/collaboration-partners/branches/{branch_id}/profile",
            body={
                "registration": {"legal_business_name": "Should Fail"},
            },
            token=token,
        )
        results.append(ok("PUT profile 403 for normal branch", st == 403, str(st)))

        # Enable partner
        st, _ = http_json(
            "PUT",
            f"/api/branches/{branch_id}",
            body={"is_collaboration_partner": True},
            token=token,
            expect_status=200,
        )

        st, empty = http_json(
            "GET",
            f"/api/collaboration-partners/branches/{branch_id}/profile",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "GET empty scaffold for partner",
                empty.get("has_profile") is False
                and empty.get("partner_id") is None
                and empty.get("branch_id") == branch_id,
            )
        )

        body = {
            "registration": {
                "legal_business_name": "QA Partner Pvt Ltd",
                "trade_name": "QA Partner",
                "registration_number": "CIN-QA-001",
                "gstin": "29AAAAA0000A1Z5",
                "pan": "AAAAA0000A",
                "tan": "AAAA00000A",
                "tax_notes": "QA tax note",
            },
            "contact": {
                "primary_contact_name": "QA Contact",
                "primary_contact_designation": "Manager",
                "primary_contact_phone": "9999999999",
                "primary_contact_email": "qa.partner@example.com",
                "billing_email": "billing.qa@example.com",
                "city": "Bengaluru",
                "state": "Karnataka",
                "pincode": "560001",
                "country": "India",
            },
            "agreement": {
                "agreement_type": "affiliation",
                "agreement_reference": "AGR-QA-001",
                "agreement_status": "active",
                "start_date": "2026-01-01",
                "end_date": "2027-01-01",
                "signed_by_name": "QA Signer",
                "revenue_share_percent": 12.5,
                "notes": "QA agreement",
            },
        }
        st, created = http_json(
            "PUT",
            f"/api/collaboration-partners/branches/{branch_id}/profile",
            body=body,
            token=token,
            expect_status=200,
        )
        profile = created.get("profile") or {}
        pid = profile.get("partner_id")
        results.append(
            ok(
                "create profile + auto Partner ID",
                bool(pid)
                and str(pid).startswith("CP-")
                and profile.get("has_profile") is True
                and (profile.get("registration") or {}).get("gstin")
                == "29AAAAA0000A1Z5",
                str(pid),
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
                "status includes partner_id",
                status.get("has_profile") is True
                and status.get("partner_id") == pid
                and status.get("partner_features_enabled") is True,
            )
        )

        # Update keeps same partner_id
        st, updated = http_json(
            "PUT",
            f"/api/collaboration-partners/branches/{branch_id}/profile",
            body={
                "registration": {"legal_business_name": "QA Partner Updated"},
                "contact": {"primary_contact_name": "QA Contact 2"},
                "agreement": {"agreement_status": "active", "notes": "updated"},
            },
            token=token,
            expect_status=200,
        )
        up = updated.get("profile") or {}
        results.append(
            ok(
                "update keeps Partner ID immutable",
                up.get("partner_id") == pid
                and (up.get("registration") or {}).get("legal_business_name")
                == "QA Partner Updated",
            )
        )

        st, listed_partners = http_json(
            "GET",
            "/api/collaboration-partners/partner-branches?limit=100",
            token=token,
            expect_status=200,
        )
        ids = {p.get("branch_id") for p in (listed_partners.get("partners") or [])}
        match = next(
            (
                p
                for p in (listed_partners.get("partners") or [])
                if p.get("branch_id") == branch_id
            ),
            None,
        )
        results.append(
            ok(
                "partner-branches lists profile",
                branch_id in ids
                and match
                and match.get("partner_id") == pid
                and match.get("has_profile") is True,
            )
        )

        # Disable partner → profile APIs blocked again; normal branch list still works
        st, _ = http_json(
            "PUT",
            f"/api/branches/{branch_id}",
            body={"is_collaboration_partner": False},
            token=token,
            expect_status=200,
        )
        st, blocked2 = http_json(
            "GET",
            f"/api/collaboration-partners/branches/{branch_id}/profile",
            token=token,
        )
        results.append(ok("profile gated after flag off", st == 403, str(st)))

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
