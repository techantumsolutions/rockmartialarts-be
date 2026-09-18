"""
M19-S02 Promotion targeting QA.

  .venv\\Scripts\\python.exe scripts/qa_m19_s02_promotion_targeting.py
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


def _db_fixtures():
    """Return (student_a, student_b, branch_id, course_id, batch_ref) or None."""
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
            return None
        dbn = os.getenv("DB_NAME") or "marshalats"
        tag = f"qa_m19_s02_{uuid.uuid4().hex[:8]}"

        async def run():
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
            db = None
            for name in (dbn, "marshalats", "rockmartialarts", "rock_martial_arts"):
                if await client[name].superadmins.find_one({}):
                    db = client[name]
                    break
            if db is None:
                db = client[dbn]

            branch = await db.branches.find_one({}, {"id": 1, "name": 1})
            course = await db.courses.find_one({}, {"id": 1, "name": 1})
            if not branch or not course:
                return None

            sid_a = str(uuid.uuid4())
            sid_b = str(uuid.uuid4())
            batch = f"{tag}_batch"
            await db.users.insert_many(
                [
                    {
                        "id": sid_a,
                        "role": "student",
                        "full_name": f"QA Target A {tag}",
                        "email": f"{tag}_a@example.com",
                        "phone": "9000000001",
                        "batch_ref": batch,
                        "is_active": True,
                    },
                    {
                        "id": sid_b,
                        "role": "student",
                        "full_name": f"QA Target B {tag}",
                        "email": f"{tag}_b@example.com",
                        "phone": "9000000002",
                        "batch_ref": f"{tag}_other",
                        "is_active": True,
                    },
                ]
            )
            await db.enrollments.insert_one(
                {
                    "id": str(uuid.uuid4()),
                    "student_id": sid_a,
                    "branch_id": branch["id"],
                    "course_id": course["id"],
                    "is_active": True,
                    "source": "qa_m19_s02",
                }
            )
            return {
                "tag": tag,
                "student_a": sid_a,
                "student_b": sid_b,
                "branch_id": branch["id"],
                "course_id": course["id"],
                "batch": batch,
                "db_name": db.name,
            }

        try:
            return asyncio.run(run())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(run())
            finally:
                loop.close()
    except Exception as exc:
        print(f"(fixtures skipped: {exc})")
        return None


def _cleanup(promo_ids, fixtures):
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
        dbn = (fixtures or {}).get("db_name") or os.getenv("DB_NAME") or "marshalats"

        async def run():
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
            db = client[dbn]
            if promo_ids:
                await db.student_promotions.delete_many({"id": {"$in": promo_ids}})
            if fixtures:
                await db.users.delete_many(
                    {"id": {"$in": [fixtures["student_a"], fixtures["student_b"]]}}
                )
                await db.enrollments.delete_many(
                    {"student_id": fixtures["student_a"], "source": "qa_m19_s02"}
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
    be_models = (Path(ROOT) / "models" / "student_promotion_models.py").read_text(
        encoding="utf-8"
    )
    be_elig = (Path(ROOT) / "utils" / "promotion_eligibility.py").read_text(
        encoding="utf-8"
    )
    be_routes = (Path(ROOT) / "routes" / "student_promotion_routes.py").read_text(
        encoding="utf-8"
    )
    checks = [
        "Audience targeting" in admin,
        "Preview audience" in admin,
        "branch_ids" in admin,
        "course_ids" in admin,
        "group_ids" in admin,
        "targetingOptions" in api,
        "previewEligibility" in api,
        "PromotionTarget" in api or "EMPTY_PROMOTION_TARGET" in api,
        "normalize_promotion_target" in be_models,
        "PromotionTarget" in be_models,
        "is_student_eligible_for_target" in be_elig,
        "resolve_eligible_student_ids" in be_elig,
        "targeting/options" in be_routes,
        "eligibility/preview" in be_routes,
        "eligibility/check" in be_routes,
    ]
    results.append(
        ok(
            "FE + BE targeting surfaces",
            all(checks),
            f"{sum(checks)}/{len(checks)}",
        )
    )
    return all(results)


def test_workflow():
    results = []
    token = _mint_superadmin_token()
    if not token:
        results.append(ok("superadmin token", False))
        return all(results)
    results.append(ok("superadmin token", True))

    fixtures = _db_fixtures()
    if not fixtures:
        results.append(ok("seed fixtures", False, "need branch+course"))
        return all(results)
    results.append(ok("seed fixtures", True, fixtures["tag"]))

    suffix = fixtures["tag"]
    ids = []

    try:
        st, opts = http_json(
            "GET",
            "/api/student-promotions/targeting/options",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "targeting options",
                isinstance(opts.get("branches"), list)
                and isinstance(opts.get("courses"), list)
                and isinstance(opts.get("groups"), list)
                and isinstance(opts.get("students"), list),
                f"b={len(opts.get('branches') or [])} c={len(opts.get('courses') or [])}",
            )
        )

        # All students promo
        st, created_all = http_json(
            "POST",
            "/api/student-promotions",
            body={
                "title": f"QA All Target {suffix}",
                "status": "draft",
                "target": {"mode": "all"},
            },
            token=token,
            expect_status=201,
        )
        promo_all = created_all.get("promotion") or {}
        if promo_all.get("id"):
            ids.append(promo_all["id"])
        results.append(
            ok(
                "create with mode=all",
                (promo_all.get("target") or {}).get("mode") == "all",
                str((promo_all.get("target") or {}).get("mode")),
            )
        )

        st, prev_all = http_json(
            "POST",
            "/api/student-promotions/eligibility/preview",
            body={"target": {"mode": "all"}, "sample_limit": 3},
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "preview all students",
                (prev_all.get("eligible_count") or 0) >= 2,
                str(prev_all.get("eligible_count")),
            )
        )

        # Student-only targeting
        st, created_stu = http_json(
            "POST",
            "/api/student-promotions",
            body={
                "title": f"QA Student Target {suffix}",
                "status": "draft",
                "target": {
                    "mode": "targeted",
                    "student_ids": [fixtures["student_a"]],
                },
            },
            token=token,
            expect_status=201,
        )
        promo_stu = created_stu.get("promotion") or {}
        if promo_stu.get("id"):
            ids.append(promo_stu["id"])
        tgt = promo_stu.get("target") or {}
        results.append(
            ok(
                "persist student targeting",
                tgt.get("mode") == "targeted"
                and fixtures["student_a"] in (tgt.get("student_ids") or []),
                str(tgt.get("student_ids")),
            )
        )

        st, chk_a = http_json(
            "POST",
            "/api/student-promotions/eligibility/check",
            body={
                "student_id": fixtures["student_a"],
                "promotion_id": promo_stu.get("id"),
            },
            token=token,
            expect_status=200,
        )
        st, chk_b = http_json(
            "POST",
            "/api/student-promotions/eligibility/check",
            body={
                "student_id": fixtures["student_b"],
                "promotion_id": promo_stu.get("id"),
            },
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "student targeting eligibility",
                chk_a.get("eligible") is True and chk_b.get("eligible") is False,
                f"a={chk_a.get('eligible')} b={chk_b.get('eligible')}",
            )
        )

        # Branch targeting
        st, prev_branch = http_json(
            "POST",
            "/api/student-promotions/eligibility/preview",
            body={
                "target": {
                    "mode": "targeted",
                    "branch_ids": [fixtures["branch_id"]],
                },
                "sample_limit": 5,
            },
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "branch targeting finds enrolled student",
                fixtures["student_a"]
                in {s.get("id") for s in (prev_branch.get("sample") or [])}
                or (prev_branch.get("eligible_count") or 0) >= 1,
                str(prev_branch.get("eligible_count")),
            )
        )

        # Course targeting
        st, prev_course = http_json(
            "POST",
            "/api/student-promotions/eligibility/preview",
            body={
                "target": {
                    "mode": "targeted",
                    "course_ids": [fixtures["course_id"]],
                },
                "sample_limit": 5,
            },
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "course targeting finds enrolled student",
                (prev_course.get("eligible_count") or 0) >= 1,
                str(prev_course.get("eligible_count")),
            )
        )

        # Group (batch_ref) targeting
        st, prev_group = http_json(
            "POST",
            "/api/student-promotions/eligibility/preview",
            body={
                "target": {
                    "mode": "targeted",
                    "group_ids": [fixtures["batch"]],
                },
                "sample_limit": 5,
            },
            token=token,
            expect_status=200,
        )
        sample_ids = {s.get("id") for s in (prev_group.get("sample") or [])}
        results.append(
            ok(
                "group/batch targeting",
                fixtures["student_a"] in sample_ids
                or (prev_group.get("eligible_count") or 0) == 1,
                str(prev_group.get("eligible_count")),
            )
        )

        # AND combination: branch + student_b → empty (B not enrolled)
        st, prev_and = http_json(
            "POST",
            "/api/student-promotions/eligibility/preview",
            body={
                "target": {
                    "mode": "targeted",
                    "student_ids": [fixtures["student_b"]],
                    "branch_ids": [fixtures["branch_id"]],
                },
                "sample_limit": 5,
            },
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "AND combination excludes non-match",
                (prev_and.get("eligible_count") or 0) == 0,
                str(prev_and.get("eligible_count")),
            )
        )

        # Empty targeted → 0
        st, prev_empty = http_json(
            "POST",
            "/api/student-promotions/eligibility/preview",
            body={"target": {"mode": "targeted"}, "sample_limit": 0},
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "empty targeted = zero eligible",
                (prev_empty.get("eligible_count") or 0) == 0,
                str(prev_empty.get("eligible_count")),
            )
        )

        # Update target on existing promo
        st, patched = http_json(
            "PATCH",
            f"/api/student-promotions/{promo_all.get('id')}",
            body={
                "target": {
                    "mode": "targeted",
                    "course_ids": [fixtures["course_id"]],
                    "group_ids": [fixtures["batch"]],
                }
            },
            token=token,
            expect_status=200,
        )
        pt = (patched.get("promotion") or {}).get("target") or {}
        results.append(
            ok(
                "update target on promotion",
                pt.get("mode") == "targeted"
                and fixtures["course_id"] in (pt.get("course_ids") or [])
                and fixtures["batch"] in (pt.get("group_ids") or []),
                str(pt),
            )
        )

        st, by_id = http_json(
            "GET",
            f"/api/student-promotions/{promo_stu.get('id')}/eligibility?sample_limit=5",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "GET promotion eligibility",
                by_id.get("eligible_count") == 1,
                str(by_id.get("eligible_count")),
            )
        )

    except Exception as exc:
        results.append(ok("workflow exception", False, str(exc)))
    finally:
        _cleanup(ids, fixtures)

    return all(results)


def main():
    print(f"BASE={BASE}")
    a = test_fe_surfaces()
    b = test_workflow()
    print(f"\nRESULT: {int(a) + int(b)}/2 suites passed")
    sys.exit(0 if a and b else 1)


if __name__ == "__main__":
    main()
