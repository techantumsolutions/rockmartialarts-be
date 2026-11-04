"""Seed daily_life + gallery on the current residential camp CMS document."""
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from models.cms_residential_camp_models import default_residential_camp_dict  # noqa: E402


async def main():
    mongo_url = os.getenv("MONGO_URL") or os.getenv("MONGO_URI") or "mongodb://localhost:27017"
    db_name = os.getenv("DB_NAME", "marshalats")
    client = AsyncIOMotorClient(mongo_url, tlsInsecure=True)
    db = client[db_name]
    collection = db["cms_residential_camp"]
    defaults = default_residential_camp_dict()
    now = datetime.utcnow()
    doc = await collection.find_one({}, sort=[("updated_at", -1), ("created_at", -1)])
    patch = {
        "daily_life": defaults["daily_life"],
        "gallery": defaults.get("gallery") or {"enabled": True, "kicker": "Camp moments", "h2": "Training Gallery", "items": []},
        "updated_at": now,
    }
    if doc:
        nav = dict(doc.get("nav") or {})
        nav.setdefault("link_daily", "Schedule")
        nav.setdefault("link_gallery", "Gallery")
        patch["nav"] = nav
        await collection.update_one({"_id": doc["_id"]}, {"$set": patch})
        print("updated existing cms_residential_camp document")
    else:
        payload = {**defaults, **patch, "created_at": now}
        await collection.insert_one(payload)
        print("inserted cms_residential_camp document")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
