"""
Partner SMS / WhatsApp messaging quota QA (static + optional live).

  .venv/bin/python scripts/qa_partner_messaging_quota.py
  STUDENT_QA_BASE=http://127.0.0.1:8003 .venv/bin/python scripts/qa_partner_messaging_quota.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FE_ROOT = ROOT.parent / "rockmartialarts-fe"

passed = 0
failed = 0


def ok(label: str, cond: bool, detail: str = "") -> bool:
    global passed, failed
    status = "PASS" if cond else "FAIL"
    if cond:
        passed += 1
    else:
        failed += 1
    extra = f" — {detail}" if detail else ""
    print(f"[{status}] {label}{extra}")
    return cond


def static_checks() -> None:
    be = ROOT
    fe = FE_ROOT
    quota_py = (be / "utils" / "partner_messaging_quota.py").read_text(encoding="utf-8")
    models = (be / "models" / "partner_org_models.py").read_text(encoding="utf-8")
    routes = (be / "routes" / "partner_org_routes.py").read_text(encoding="utf-8")
    helpers = (be / "utils" / "helpers.py").read_text(encoding="utf-8")
    ops = (be / "utils" / "ops_notify.py").read_text(encoding="utf-8")
    inv = (be / "utils" / "invoice_whatsapp_service.py").read_text(encoding="utf-8")
    notif = (be / "utils" / "notification_send_service.py").read_text(encoding="utf-8")

    ok("quota module exists", "reserve_units" in quota_py and "MESSAGE_QUOTA_EXCEEDED" in quota_py)
    ok("confirm/release present", "confirm_reservation" in quota_py and "release_reservation" in quota_py)
    ok("ledger collection named", "partner_message_quota_ledger" in quota_py)
    ok("env defaults", "PARTNER_DEFAULT_SMS_QUOTA" in quota_py and "PARTNER_DEFAULT_WHATSAPP_QUOTA" in quota_py)
    ok("create model accepts allocated", "sms_allocated" in models and "PartnerMessagingQuotaUpdate" in models)
    ok("public payload includes messaging_quota", "messaging_quota" in models)
    ok("routes: me messaging-quota", "/me/messaging-quota" in routes)
    ok("routes: patch messaging-quota", "messaging-quota" in routes and "PartnerMessagingQuotaUpdate" in routes)
    ok("helpers.send_sms accepts partner_org_id", "partner_org_id" in helpers and "dispatch_with_partner_quota" in helpers)
    ok("ops_notify partner-aware", "partner_org_id" in ops and "dispatch_with_partner_quota" in ops)
    ok("invoice whatsapp wraps quota", "partner_org_id=" in inv and "MESSAGE_QUOTA_EXCEEDED" in inv)
    ok("notification send resolves partner", "resolve_partner_org_id_for_actor" in notif)

    fe_api = (fe / "lib" / "partnerOrgAPI.ts").read_text(encoding="utf-8")
    fe_create = (fe / "app" / "[adminType]" / "dashboard" / "partner-profiles" / "create" / "page.tsx").read_text(
        encoding="utf-8"
    )
    fe_controls = (fe / "components" / "dashboard" / "PartnerProfileAdminControls.tsx").read_text(
        encoding="utf-8"
    )
    fe_cards = (fe / "components" / "dashboard" / "PartnerMessagingUsageCards.tsx").read_text(
        encoding="utf-8"
    )
    fe_dash = (fe / "app" / "[adminType]" / "dashboard" / "page.tsx").read_text(encoding="utf-8")

    ok("FE API getMyMessagingQuota", "getMyMessagingQuota" in fe_api and "patchMessagingQuota" in fe_api)
    ok("FE create form quota section", "Messaging Quota Configuration" in fe_create)
    ok("FE edit utilization table", "Utilized" in fe_controls and "Save messaging quota" in fe_controls)
    ok("FE partner dashboard cards", "Messaging Usage" in fe_cards)
    ok("FE dashboard mounts cards", "PartnerMessagingUsageCards" in fe_dash)


async def unit_quota_logic() -> None:
    from utils.partner_messaging_quota import (
        MESSAGE_QUOTA_EXCEEDED,
        messaging_quota_defaults,
        quota_summary_from_doc,
        remaining_units,
    )

    d = messaging_quota_defaults()
    ok("defaults non-negative", d["sms_allocated"] >= 0 and d["whatsapp_allocated"] >= 0)
    ok("defaults utilized zero", d["sms_utilized"] == 0 and d["whatsapp_utilized"] == 0)

    rem = remaining_units(100, 90, 5)
    ok("remaining calc", rem == 5, f"got {rem}")

    # Reducing allocated below utilized → remaining 0, utilized preserved
    summary = quota_summary_from_doc(
        {
            "sms_allocated": 10,
            "sms_utilized": 50,
            "sms_reserved": 0,
            "whatsapp_allocated": 0,
            "whatsapp_utilized": 3,
            "whatsapp_reserved": 0,
        }
    )
    ok("reduce-below-utilized remaining 0", summary["sms"]["remaining"] == 0)
    ok("utilized preserved in summary", summary["sms"]["utilized"] == 50)
    ok("whatsapp 0 is disabled", summary["whatsapp"]["disabled"] is True)
    ok("error code constant", MESSAGE_QUOTA_EXCEEDED == "MESSAGE_QUOTA_EXCEEDED")


async def live_mongo_roundtrip() -> None:
    """Optional: requires MONGO_URI. Creates a throwaway partner doc."""
    uri = (
        os.getenv("MONGO_URI")
        or os.getenv("MONGO_URL")
        or os.getenv("MONGODB_URL")
        or ""
    ).strip()
    if not uri or "your_database" in uri:
        print("[SKIP] live mongo — MONGO_URI not set")
        return

    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except Exception:
        pass

    from motor.motor_asyncio import AsyncIOMotorClient
    from utils.partner_messaging_quota import (
        confirm_reservation,
        release_reservation,
        reserve_units,
        set_partner_messaging_limits,
        ensure_partner_messaging_quota_indexes,
        messaging_quota_defaults,
    )

    db_name = os.getenv("DB_NAME") or os.getenv("MONGO_DB") or "marshalats"
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
    db = client[db_name]

    # Patch get_db used by quota module
    import utils.database as database_mod
    import utils.partner_messaging_quota as quota_mod

    database_mod.db = db
    if hasattr(database_mod, "get_db"):
        pass

    pid = f"qa-quota-{uuid.uuid4()}"
    defaults = messaging_quota_defaults()
    defaults["sms_allocated"] = 5
    defaults["whatsapp_allocated"] = 2
    await db.partner_organizations.insert_one(
        {
            "id": pid,
            "name": "QA Quota Partner",
            "slug": pid,
            "email": f"{pid}@example.com",
            "phone": "9999999999",
            "address": "QA",
            "status": "active",
            "is_active": True,
            **defaults,
        }
    )
    await ensure_partner_messaging_quota_indexes(db)

    # Monkeypatch get_db inside quota module
    quota_mod.get_db = lambda: db

    try:
        r1 = await reserve_units(pid, "sms", 1, idempotency_key=f"{pid}-sms-1")
        ok("reserve sms", r1.get("status") == "reserved", str(r1.get("reservation_id")))
        await confirm_reservation(r1["reservation_id"])
        org = await db.partner_organizations.find_one({"id": pid})
        ok("confirm increments utilized", int(org.get("sms_utilized") or 0) == 1)

        r2 = await reserve_units(pid, "sms", 1, idempotency_key=f"{pid}-sms-1")
        ok("idempotent reserve", r2.get("duplicate") is True)

        # Exhaust remaining (allocated 5, utilized 1 → can reserve 4 more then fail)
        for i in range(4):
            rr = await reserve_units(pid, "sms", 1, idempotency_key=f"{pid}-fill-{i}")
            await confirm_reservation(rr["reservation_id"])

        exceeded = False
        try:
            await reserve_units(pid, "sms", 1, idempotency_key=f"{pid}-over")
        except Exception as exc:
            detail = getattr(exc, "detail", None)
            exceeded = (
                isinstance(detail, dict) and detail.get("code") == "MESSAGE_QUOTA_EXCEEDED"
            ) or "MESSAGE_QUOTA_EXCEEDED" in str(detail)
        ok("quota exceeded raises", exceeded)

        await set_partner_messaging_limits(pid, sms_allocated=3)
        org2 = await db.partner_organizations.find_one({"id": pid})
        ok(
            "reduce limit keeps utilized",
            int(org2.get("sms_utilized") or 0) == 5 and int(org2.get("sms_allocated") or 0) == 3,
        )

        rw = await reserve_units(pid, "whatsapp", 1, idempotency_key=f"{pid}-wa-rel")
        await release_reservation(rw["reservation_id"])
        org3 = await db.partner_organizations.find_one({"id": pid})
        ok("release clears reserved", int(org3.get("whatsapp_reserved") or 0) == 0)
    finally:
        await db.partner_organizations.delete_one({"id": pid})
        await db.partner_message_quota_ledger.delete_many({"partner_org_id": pid})
        client.close()


def main() -> int:
    print("=== Partner messaging quota QA ===")
    static_checks()
    asyncio.run(unit_quota_logic())
    try:
        asyncio.run(live_mongo_roundtrip())
    except Exception as exc:
        ok("live mongo roundtrip", False, str(exc)[:200])
    print(f"\nResult: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
