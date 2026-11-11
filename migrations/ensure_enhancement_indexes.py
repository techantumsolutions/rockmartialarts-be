#!/usr/bin/env python3
"""
Ensure MongoDB collections/indexes for the 11 targeted enhancements.
Safe to re-run. Creates collections implicitly via create_index.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


async def main() -> None:
    from motor.motor_asyncio import AsyncIOMotorClient

    uri = os.environ.get("MONGO_URI") or os.environ.get("MONGODB_URI")
    # Must match server.py: os.getenv("DB_NAME", "marshalats")
    db_name = (
        os.environ.get("DB_NAME")
        or os.environ.get("MONGO_DB")
        or os.environ.get("MONGODB_DB")
        or "marshalats"
    )
    if not uri:
        raise SystemExit("MONGO_URI is not set")

    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=20000)
    db = client[db_name]
    await client.admin.command("ping")
    print(f"Connected to MongoDB database: {db_name}")

    from utils.lead_notification_service import ensure_lead_notification_indexes
    from utils.partner_org_service import ensure_partner_org_indexes
    from utils.training_request_service import ensure_training_request_indexes
    from utils.coach_registration_service import ensure_coach_registration_indexes
    from utils.lead_service import ensure_lead_indexes

    await ensure_lead_indexes(db)
    print("✓ leads indexes")
    await ensure_lead_notification_indexes(db)
    print("✓ lead_notifications indexes (collection ready)")
    await ensure_training_request_indexes(db)
    print("✓ training_requests indexes (incl. notified_coach_ids)")
    await ensure_partner_org_indexes(db)
    print("✓ partner_organizations indexes (incl. valid_from/valid_until)")
    await ensure_coach_registration_indexes(db)
    print("✓ coaches indexes (incl. training_mode)")

    # Touch collections so they exist even before first write
    for col in (
        "lead_notifications",
        "training_requests",
        "training_request_status_history",
        "partner_organizations",
        "coaches",
        "leads",
    ):
        names = await db.list_collection_names()
        if col not in names:
            await db.create_collection(col)
            print(f"✓ created collection {col}")
        else:
            print(f"· collection exists: {col}")

    client.close()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
