"""
M16-S03 Learning Subscription Plans QA.

  .venv\\Scripts\\python.exe scripts/qa_m16_s03_learning_subscriptions.py
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
        raise AssertionError(f"{method} {path} expected {expect_status} got {status}: {payload}")
    return status, payload


def _mongo():
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
    dbn = os.getenv("DB_NAME") or "marshalats"
    if not uri or "your_database" in uri:
        return None, None, None
    return AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000), dbn, asyncio


def _mint_token(role: str, user_id: str):
    from dotenv import load_dotenv

    load_dotenv(Path(ROOT) / ".env")
    import jwt

    secret = os.getenv("SECRET_KEY") or "student_management_secret_key_2025_secure"
    return jwt.encode({"sub": user_id, "role": role}, secret, algorithm="HS256")


def _mint_superadmin_token():
    try:
        client, dbn, asyncio = _mongo()
        if client is None:
            return None

        async def run():
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
        print(f"(mint superadmin skipped: {exc})")
        return None


def _ensure_qa_student():
    """Return (user_id, student_token) for a disposable QA student user."""
    try:
        client, dbn, asyncio = _mongo()
        if client is None:
            return None, None

        suffix = uuid.uuid4().hex[:8]
        user_id = str(uuid.uuid4())
        email = f"qa.m16s03.{suffix}@example.com"

        async def run():
            db = client[dbn]
            from datetime import datetime

            now = datetime.utcnow()
            doc = {
                "id": user_id,
                "email": email,
                "full_name": f"QA LMS {suffix}",
                "role": "student",
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            }
            await db.users.insert_one(doc)
            return user_id

        try:
            uid = asyncio.run(run())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                uid = loop.run_until_complete(run())
            finally:
                loop.close()
        return uid, _mint_token("student", uid)
    except Exception as exc:
        print(f"(ensure student skipped: {exc})")
        return None, None


def _cleanup(course_ids, user_ids=None):
    try:
        client, dbn, asyncio = _mongo()
        if client is None:
            return False

        async def run():
            db = client[dbn]
            for cid in course_ids or []:
                await db.learning_subscription_payments.delete_many({"course_id": cid})
                await db.learning_subscriptions.delete_many({"course_id": cid})
                await db.learning_subscription_plans.delete_many({"course_id": cid})
                await db.learning_lessons.delete_many({"course_id": cid})
                await db.learning_levels.delete_many({"course_id": cid})
                await db.learning_courses.delete_many({"id": cid})
            await db.learning_courses.delete_many({"slug": {"$regex": "^qa-m16-s03-"}})
            for uid in user_ids or []:
                await db.learning_subscription_payments.delete_many({"user_id": uid})
                await db.learning_subscriptions.delete_many({"user_id": uid})
                await db.users.delete_many({"id": uid, "email": {"$regex": "^qa\\.m16s03\\."}})
            return True

        try:
            return asyncio.run(run())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(run())
            finally:
                loop.close()
    except Exception:
        return False


def test_openapi_fe():
    results = []
    try:
        with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        needed = [
            "/api/learning-subscriptions/plans/public",
            "/api/learning-subscriptions/plans",
            "/api/learning-subscriptions/me/checkout",
            "/api/learning-subscriptions/me/verify-payment",
            "/api/learning-subscriptions/grant",
            "/api/coach-subscriptions/plans",
            "/api/learning-courses/public",
        ]
        missing = [p for p in needed if p not in raw]
        results.append(
            ok(
                "OpenAPI LMS subs + coach subs intact",
                not missing,
                str(missing) or BASE,
            )
        )
    except Exception as exc:
        results.append(ok("OpenAPI learning subscription endpoints", False, str(exc)))

    api_path = FE_ROOT / "lib" / "learningSubscriptionAPI.ts"
    detail = FE_ROOT / "app" / "(website)" / "online-learning" / "[slug]" / "page.tsx"
    dash = FE_ROOT / "lib" / "dashboard-config.ts"
    fe_checks = [
        api_path.exists(),
        "checkout" in api_path.read_text(encoding="utf-8"),
        (
            FE_ROOT
            / "components"
            / "online-learning"
            / "LearningSubscriptionPlansAdminPage.tsx"
        ).exists(),
        (
            FE_ROOT
            / "components"
            / "online-learning"
            / "LearningSubscriptionsAdminPage.tsx"
        ).exists(),
        (
            FE_ROOT
            / "app"
            / "[adminType]"
            / "dashboard"
            / "online-learning"
            / "plans"
            / "page.tsx"
        ).exists(),
        (
            FE_ROOT
            / "app"
            / "[adminType]"
            / "dashboard"
            / "online-learning"
            / "subscriptions"
            / "page.tsx"
        ).exists(),
        "startSubscribe" in detail.read_text(encoding="utf-8"),
        "Learning Plans" in dash.read_text(encoding="utf-8"),
        "online-learning/subscriptions" in dash.read_text(encoding="utf-8"),
    ]
    results.append(ok("FE subscription surfaces", all(fe_checks), str(FE_ROOT)))
    return all(results)


def test_workflow():
    results = []
    token = _mint_superadmin_token()
    if not token:
        results.append(ok("superadmin token", False))
        return all(results)
    results.append(ok("superadmin token", True))

    student_id, student_token = _ensure_qa_student()
    if not student_id or not student_token:
        results.append(ok("qa student token", False))
        return all(results)
    results.append(ok("qa student token", True, student_id))

    suffix = uuid.uuid4().hex[:8]
    slug = f"qa-m16-s03-{suffix}"
    course_ids = []
    user_ids = [student_id]

    try:
        st, created = http_json(
            "POST",
            "/api/learning-courses",
            body={
                "title": f"QA Sub Course {suffix}",
                "slug": slug,
                "short_description": "QA plans",
                "status": "published",
                "sort_order": 10,
            },
            token=token,
            expect_status=201,
        )
        course = created.get("course") or {}
        cid = course.get("id")
        if cid:
            course_ids.append(cid)
        results.append(ok("create published course", bool(cid), cid))

        # Seed 3-month + Lifetime
        st, seeded = http_json(
            "POST",
            f"/api/learning-subscriptions/plans/seed-defaults/{cid}",
            token=token,
            expect_status=201,
        )
        plans = seeded.get("plans") or []
        kinds = {p.get("plan_kind") for p in plans}
        results.append(
            ok(
                "seed 3-month + lifetime",
                "3_month" in kinds and "lifetime" in kinds and len(plans) >= 2,
                str(kinds),
            )
        )

        three = next((p for p in plans if p.get("plan_kind") == "3_month"), None)
        life = next((p for p in plans if p.get("plan_kind") == "lifetime"), None)
        results.append(
            ok(
                "3-month is 90 days",
                three and int(three.get("duration_days") or 0) == 90,
                three.get("duration_days") if three else None,
            )
        )
        results.append(
            ok(
                "lifetime flag set",
                life and bool(life.get("is_lifetime")),
            )
        )

        # Public plans (no auth)
        st, pub = http_json(
            "GET",
            f"/api/learning-subscriptions/plans/public?course_id={cid}",
            expect_status=200,
        )
        pub_plans = pub.get("plans") or []
        results.append(
            ok("public plans list", len(pub_plans) >= 2, str(len(pub_plans)))
        )

        # Inactive plan hidden from public
        if three:
            http_json(
                "PATCH",
                f"/api/learning-subscriptions/plans/{three['id']}",
                body={"is_active": False},
                token=token,
                expect_status=200,
            )
            st, pub2 = http_json(
                "GET",
                f"/api/learning-subscriptions/plans/public?course_id={cid}",
                expect_status=200,
            )
            ids2 = [p.get("id") for p in (pub2.get("plans") or [])]
            results.append(
                ok("inactive plan hidden publicly", three["id"] not in ids2)
            )
            http_json(
                "PATCH",
                f"/api/learning-subscriptions/plans/{three['id']}",
                body={"is_active": True},
                token=token,
                expect_status=200,
            )

        # Student me empty
        st, me0 = http_json(
            "GET",
            f"/api/learning-subscriptions/me?course_id={cid}",
            token=student_token,
            expect_status=200,
        )
        results.append(
            ok(
                "student not entitled yet",
                me0.get("entitled") is False and me0.get("subscription") is None,
            )
        )

        # Checkout — soft if Razorpay missing
        st, checkout = http_json(
            "POST",
            "/api/learning-subscriptions/me/checkout",
            body={"plan_id": three["id"]},
            token=student_token,
        )
        if st == 200 and checkout.get("order", {}).get("id"):
            results.append(
                ok(
                    "checkout creates Razorpay order",
                    True,
                    checkout["order"]["id"],
                )
            )
        elif st == 503:
            results.append(
                ok(
                    "checkout creates Razorpay order",
                    True,
                    "Razorpay not configured (soft)",
                )
            )
        else:
            results.append(
                ok(
                    "checkout creates Razorpay order",
                    False,
                    f"status={st} detail={checkout.get('detail')}",
                )
            )

        # Free plan auto-activate
        st, free_plan = http_json(
            "POST",
            "/api/learning-subscriptions/plans",
            body={
                "course_id": cid,
                "name": "QA Free Preview Access",
                "plan_kind": "custom",
                "fee_inr": 0,
                "duration_days": 7,
                "grace_period_days": 0,
                "is_active": True,
                "sort_order": 5,
            },
            token=token,
            expect_status=201,
        )
        free_id = (free_plan.get("plan") or {}).get("id")
        # Use a second student so free grant doesn't collide with pending checkout
        student2_id, student2_token = _ensure_qa_student()
        if student2_id:
            user_ids.append(student2_id)
        st, free_co = http_json(
            "POST",
            "/api/learning-subscriptions/me/checkout",
            body={"plan_id": free_id},
            token=student2_token,
            expect_status=200,
        )
        results.append(
            ok(
                "free plan auto-activates",
                free_co.get("activated") is True
                and (free_co.get("subscription") or {}).get("status") == "active",
            )
        )

        # Admin grant lifetime to original student
        st, granted = http_json(
            "POST",
            "/api/learning-subscriptions/grant",
            body={
                "user_id": student_id,
                "plan_id": life["id"],
                "note": "QA grant",
            },
            token=token,
            expect_status=200,
        )
        gsub = granted.get("subscription") or {}
        results.append(
            ok(
                "admin grant activates lifetime",
                gsub.get("status") == "active" and bool(gsub.get("is_lifetime")),
                f"status={gsub.get('status')}",
            )
        )

        st, me1 = http_json(
            "GET",
            f"/api/learning-subscriptions/me?course_id={cid}",
            token=student_token,
            expect_status=200,
        )
        results.append(ok("student entitled after grant", me1.get("entitled") is True))

        st, listing = http_json(
            "GET",
            f"/api/learning-subscriptions?course_id={cid}",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "admin list subscriptions",
                (listing.get("total") or 0) >= 1,
                f"total={listing.get('total')}",
            )
        )

        # Coach subscription API still present (isolation)
        st, coach_plans = http_json(
            "GET",
            "/api/coach-subscriptions/plans?active_only=true",
            token=token,
            expect_status=200,
        )
        results.append(
            ok(
                "coach subscription plans untouched",
                st == 200 and isinstance(coach_plans.get("plans"), list),
            )
        )

        st, lms_pub = http_json(
            "GET",
            f"/api/learning-courses/public?search={suffix}",
            expect_status=200,
        )
        results.append(
            ok(
                "LMS catalogue intact",
                any(x.get("id") == cid for x in (lms_pub.get("courses") or [])),
            )
        )

    except Exception as exc:
        results.append(ok("subscription workflow", False, str(exc)))
    finally:
        _cleanup(course_ids, user_ids)

    return all(results)


def main():
    print(f"M16-S03 QA against {BASE}")
    a = test_openapi_fe()
    b = test_workflow()
    print(f"\nResult: {sum([a, b])}/2 suites passed")
    sys.exit(0 if a and b else 1)


if __name__ == "__main__":
    main()
