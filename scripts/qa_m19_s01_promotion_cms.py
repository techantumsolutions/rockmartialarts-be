"""
M19-S01 Student Promotion CMS QA.

  .venv\\Scripts\\python.exe scripts/qa_m19_s01_promotion_cms.py
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


def _cleanup(ids):
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
        if not uri or not ids:
            return
        dbn = os.getenv("DB_NAME") or "marshalats"

        async def run():
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
            for name in (dbn, "marshalats", "rockmartialarts", "rock_martial_arts"):
                await client[name].student_promotions.delete_many(
                    {"id": {"$in": ids}}
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
        FE_ROOT / "components" / "promotions" / "StudentPromotionsAdminPage.tsx"
    ).read_text(encoding="utf-8")
    api = (FE_ROOT / "lib" / "studentPromotionAPI.ts").read_text(encoding="utf-8")
    page = (
        FE_ROOT / "app" / "[adminType]" / "dashboard" / "promotions" / "page.tsx"
    ).read_text(encoding="utf-8")
    nav = (FE_ROOT / "lib" / "dashboard-config.ts").read_text(encoding="utf-8")
    be_models = (Path(ROOT) / "models" / "student_promotion_models.py").read_text(
        encoding="utf-8"
    )
    be_routes = (Path(ROOT) / "routes" / "student_promotion_routes.py").read_text(
        encoding="utf-8"
    )
    checks = [
        "Banner / media" in admin,
        "Call to action" in admin,
        "datetime-local" in admin,
        "uploadBanner" in api,
        "student-promotions" in api,
        "StudentPromotionsAdminPage" in page,
        "/promotions" in nav,
        "cta_type" in be_models,
        "start_at" in be_models,
        "banner_url" in be_models,
        "PromotionStatus" in be_models,
        "student-promotions" in be_routes or "create_student_promotion" in be_routes,
    ]
    results.append(
        ok("FE + BE promotion CMS surfaces", all(checks), f"{sum(checks)}/{len(checks)}")
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
        st, created = http_json(
            "POST",
            "/api/student-promotions",
            body={
                "title": f"QA Summer Offer {suffix}",
                "slug": f"qa-summer-{suffix}",
                "short_description": "Limited time",
                "description": "Full details for QA",
                "banner_url": "https://example.com/banner.jpg",
                "banner_media_type": "image",
                "cta_type": "path",
                "cta_label": "View payments",
                "cta_url": "/student-dashboard/payments",
                "start_at": "2030-01-01T00:00:00",
                "end_at": "2030-12-31T23:59:00",
                "status": "draft",
                "priority": 50,
            },
            token=token,
            expect_status=201,
        )
        promo = created.get("promotion") or {}
        pid = promo.get("id")
        if pid:
            ids.append(pid)
        results.append(
            ok(
                "create promotion with CTA + dates + banner",
                bool(pid)
                and promo.get("cta_type") == "path"
                and promo.get("banner_media_type") == "image"
                and promo.get("status") == "draft"
                and promo.get("start_at")
                and promo.get("end_at"),
                str(promo.get("slug")),
            )
        )

        # CTA without url rejected
        st, bad = http_json(
            "POST",
            "/api/student-promotions",
            body={
                "title": f"QA Bad CTA {suffix}",
                "cta_type": "url",
                "status": "draft",
            },
            token=token,
        )
        results.append(ok("CTA requires url", st == 422, str(st)))

        st, patched = http_json(
            "PATCH",
            f"/api/student-promotions/{pid}",
            body={"status": "active", "is_active": True, "priority": 10},
            token=token,
            expect_status=200,
        )
        pp = patched.get("promotion") or {}
        results.append(
            ok(
                "activate promotion",
                pp.get("status") == "active" and pp.get("is_active") is True,
                str(pp.get("status")),
            )
        )

        # Future schedule → not live yet
        results.append(
            ok(
                "schedule respected (not live before start)",
                pp.get("in_schedule") is False and pp.get("is_live") is False,
                f"in_schedule={pp.get('in_schedule')} is_live={pp.get('is_live')}",
            )
        )

        # Live window now
        st, live_patch = http_json(
            "PATCH",
            f"/api/student-promotions/{pid}",
            body={
                "start_at": "2020-01-01T00:00:00",
                "end_at": "2099-12-31T23:59:00",
            },
            token=token,
            expect_status=200,
        )
        lp = live_patch.get("promotion") or {}
        results.append(
            ok(
                "live when active + in window",
                lp.get("is_live") is True and lp.get("in_schedule") is True,
                str(lp.get("is_live")),
            )
        )

        st, listed = http_json(
            "GET",
            f"/api/student-promotions?search={suffix}",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "list/filter promotions",
                any(r.get("id") == pid for r in (listed.get("promotions") or [])),
                f"total={listed.get('total')}",
            )
        )

        st, got = http_json(
            "GET",
            f"/api/student-promotions/{pid}",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "get promotion by id",
                (got.get("promotion") or {}).get("id") == pid,
            )
        )

        st, deactivated = http_json(
            "PATCH",
            f"/api/student-promotions/{pid}",
            body={"is_active": False},
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "deactivate via is_active",
                (deactivated.get("promotion") or {}).get("status") == "inactive",
                str((deactivated.get("promotion") or {}).get("status")),
            )
        )

        st, archived = http_json(
            "DELETE",
            f"/api/student-promotions/{pid}",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "soft-archive promotion",
                (archived.get("promotion") or {}).get("status") == "archived",
                str((archived.get("promotion") or {}).get("status")),
            )
        )

        # end before start rejected
        st, bad_dates = http_json(
            "POST",
            "/api/student-promotions",
            body={
                "title": f"QA Bad Dates {suffix}",
                "start_at": "2031-06-01T10:00:00",
                "end_at": "2031-01-01T10:00:00",
                "status": "draft",
            },
            token=token,
        )
        results.append(ok("rejects end before start", st == 422, str(st)))

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
