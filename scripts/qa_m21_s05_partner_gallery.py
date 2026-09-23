"""
M21-S05 Collaboration Partner Gallery QA.

  .venv\\Scripts\\python.exe scripts/qa_m21_s05_partner_gallery.py
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


def _mongo_cleanup(branch_id: str, restore_flag: bool, item_ids=None):
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
        item_ids = item_ids or []

        async def run():
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
            for name in (dbn, "marshalats", "rockmartialarts", "rock_martial_arts"):
                db = client[name]
                if item_ids:
                    await db.collaboration_partner_gallery.delete_many(
                        {"id": {"$in": item_ids}}
                    )
                await db.collaboration_partner_gallery.delete_many(
                    {"branch_id": branch_id, "caption": {"$regex": "^QA Gallery"}}
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
        / "partner-gallery"
        / "page.tsx"
    ).read_text(encoding="utf-8")
    nav = (FE_ROOT / "lib" / "dashboard-config.ts").read_text(encoding="utf-8")
    branches = (
        FE_ROOT / "app" / "[adminType]" / "dashboard" / "branches" / "page.tsx"
    ).read_text(encoding="utf-8")
    be_models = (
        Path(ROOT) / "models" / "collaboration_partner_gallery_models.py"
    ).read_text(encoding="utf-8")
    be_routes = (
        Path(ROOT) / "routes" / "collaboration_partner_routes.py"
    ).read_text(encoding="utf-8")
    checks = [
        "Partner Gallery" in page,
        "Caption" in page,
        "video" in page.lower(),
        "gallery/reorder" in page or "reorder" in page,
        "Partner Gallery" in nav,
        "partner-gallery" in nav,
        "partner-gallery?branchId=" in branches,
        "media_type" in be_models,
        "caption" in be_models,
        "display_order" in be_models,
        "video_url" in be_models,
        "/branches/{branch_id}/gallery" in be_routes,
        "gallery/reorder" in be_routes,
        "collaboration_partner_gallery" in (
            Path(ROOT)
            / "controllers"
            / "collaboration_partner_gallery_controller.py"
        ).read_text(encoding="utf-8"),
    ]
    return ok(
        "FE + BE partner gallery surfaces",
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
    item_ids = []
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
        gallery_before = target.get("gallery_images")

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
            f"/api/collaboration-partners/branches/{branch_id}/gallery",
            token=token,
        )
        results.append(ok("LIST gallery 403 for normal branch", st == 403, str(st)))

        st, _ = http_json(
            "PUT",
            f"/api/branches/{branch_id}",
            body={"is_collaboration_partner": True},
            token=token,
            expect_status=200,
        )

        st, empty = http_json(
            "GET",
            f"/api/collaboration-partners/branches/{branch_id}/gallery",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "LIST empty gallery for partner",
                empty.get("branch_id") == branch_id
                and isinstance(empty.get("items"), list),
            )
        )

        st, created = http_json(
            "POST",
            f"/api/collaboration-partners/branches/{branch_id}/gallery",
            body={
                "media_type": "image",
                "media_url": "/uploads/images/qa-gallery-1.jpg",
                "caption": "QA Gallery Image One",
                "display_order": 20,
                "is_active": True,
            },
            token=token,
            expect_status=200,
        )
        item = created.get("item") or {}
        iid = item.get("id")
        if iid:
            item_ids.append(iid)
        results.append(
            ok(
                "create image gallery item",
                bool(iid)
                and item.get("media_type") == "image"
                and item.get("caption") == "QA Gallery Image One",
                str(iid),
            )
        )

        st, created_v = http_json(
            "POST",
            f"/api/collaboration-partners/branches/{branch_id}/gallery",
            body={
                "media_type": "video",
                "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "thumbnail_url": "/uploads/images/qa-thumb.jpg",
                "caption": "QA Gallery Video",
                "display_order": 10,
            },
            token=token,
            expect_status=200,
        )
        vid = (created_v.get("item") or {}).get("id")
        if vid:
            item_ids.append(vid)
        results.append(
            ok(
                "create video gallery item",
                bool(vid)
                and (created_v.get("item") or {}).get("media_type") == "video"
                and (created_v.get("item") or {}).get("video_url"),
                str(vid),
            )
        )

        st, listed_g = http_json(
            "GET",
            f"/api/collaboration-partners/branches/{branch_id}/gallery",
            token=token,
            expect_status=200,
        )
        items = listed_g.get("items") or []
        results.append(
            ok(
                "list ordered by display_order",
                len(items) >= 2
                and items[0].get("id") == vid
                and (items[0].get("display_order") or 0)
                <= (items[1].get("display_order") or 0),
            )
        )

        st, _ = http_json(
            "PUT",
            f"/api/collaboration-partners/branches/{branch_id}/gallery/reorder",
            body={
                "items": [
                    {"id": iid, "display_order": 5},
                    {"id": vid, "display_order": 50},
                ]
            },
            token=token,
            expect_status=200,
        )
        st, after = http_json(
            "GET",
            f"/api/collaboration-partners/branches/{branch_id}/gallery",
            token=token,
            expect_status=200,
        )
        after_items = after.get("items") or []
        results.append(
            ok(
                "reorder updates order",
                after_items
                and after_items[0].get("id") == iid
                and after_items[0].get("display_order") == 5,
            )
        )

        st, updated = http_json(
            "PUT",
            f"/api/collaboration-partners/branches/{branch_id}/gallery/{iid}",
            body={"caption": "QA Gallery Image One Updated"},
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "update caption",
                (updated.get("item") or {}).get("caption")
                == "QA Gallery Image One Updated",
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
                "status has_gallery + count",
                status.get("has_gallery") is True
                and (status.get("gallery_count") or 0) >= 2,
            )
        )

        st, _ = http_json(
            "DELETE",
            f"/api/collaboration-partners/branches/{branch_id}/gallery/{iid}",
            token=token,
            expect_status=200,
        )
        st, active = http_json(
            "GET",
            f"/api/collaboration-partners/branches/{branch_id}/gallery",
            token=token,
            expect_status=200,
        )
        active_ids = {x.get("id") for x in (active.get("items") or [])}
        results.append(ok("soft delete hides from active list", iid not in active_ids))

        st, with_inactive = http_json(
            "GET",
            f"/api/collaboration-partners/branches/{branch_id}/gallery?include_inactive=true",
            token=token,
            expect_status=200,
        )
        all_ids = {x.get("id") for x in (with_inactive.get("items") or [])}
        results.append(ok("include_inactive shows deactivated", iid in all_ids))

        # Branch master gallery_images unchanged
        st, branch_doc = http_json(
            "GET",
            f"/api/branches/{branch_id}",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "branch.gallery_images not overwritten by partner gallery",
                branch_doc.get("gallery_images") == gallery_before
                or (
                    gallery_before in (None, [])
                    and (branch_doc.get("gallery_images") in (None, []))
                ),
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
            f"/api/collaboration-partners/branches/{branch_id}/gallery",
            token=token,
        )
        results.append(ok("gallery gated after flag off", st == 403, str(st)))

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
            _mongo_cleanup(branch_id, restore_flag=original_flag, item_ids=item_ids)

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
