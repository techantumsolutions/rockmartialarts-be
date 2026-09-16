"""
M02-S02 branch geography backfill.

- Sets location_id to a city UUID when it can be resolved
- Syncs address.city / address.state from the city master
- Fills slug and allows_collaboration=false when missing

Does not change branch ids. Unresolved rows are left as-is.

Run from backend repo root:
  python scripts/migrate_branch_geography.py --dry-run
  python scripts/migrate_branch_geography.py
"""
import argparse
import asyncio
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

load_dotenv(ROOT / ".env")

from utils.geography import slugify  # noqa: E402

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)


def norm(value) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


async def unique_slug(db, base: str, used: set) -> str:
    slug = slugify(base)
    candidate = slug
    n = 2
    while candidate in used or await db.branches.find_one({"slug": candidate}):
        candidate = f"{slug}-{n}"
        n += 1
        if n > 500:
            break
    used.add(candidate)
    return candidate


async def run(dry_run: bool) -> dict:
    mongo_url = os.getenv("MONGO_URL") or os.getenv("MONGODB_URL")
    db_name = os.getenv("DB_NAME") or os.getenv("MONGO_DB")
    if not mongo_url or not db_name:
        raise SystemExit("MONGO_URL and DB_NAME must be set")

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    stats = {
        "branches_examined": 0,
        "location_updated": 0,
        "address_synced": 0,
        "slug_set": 0,
        "collaboration_set": 0,
        "skipped": 0,
        "unresolved": [],
    }

    cities = await db.locations.find({}).to_list(5000)
    cities_by_id = {c.get("id"): c for c in cities if c.get("id")}
    cities_by_name = {}
    for city in cities:
        cities_by_name.setdefault(norm(city.get("name") or "").lower(), []).append(city)

    states_by_id = {}
    async for state in db.states.find({}):
        if state.get("id"):
            states_by_id[state["id"]] = state

    used_slugs = set()
    async for row in db.branches.find({"slug": {"$exists": True, "$nin": [None, ""]}}):
        used_slugs.add(str(row.get("slug")).strip().lower())

    branches = await db.branches.find({}).to_list(5000)
    stats["branches_examined"] = len(branches)
    print(f"examined branches: {len(branches)}")

    for branch in branches:
        loc_id = branch.get("location_id")
        addr = ((branch.get("branch") or {}).get("address") or {})
        city = None
        if loc_id and UUID_RE.match(str(loc_id)) and loc_id in cities_by_id:
            city = cities_by_id[loc_id]
        elif loc_id:
            matches = cities_by_name.get(norm(str(loc_id)).lower()) or []
            city = matches[0] if len(matches) == 1 else None
        if not city:
            matches = cities_by_name.get(norm(addr.get("city") or "").lower()) or []
            city = matches[0] if len(matches) == 1 else None

        set_fields = {}
        if city:
            if loc_id != city.get("id"):
                set_fields["location_id"] = city["id"]
                stats["location_updated"] += 1
            state = states_by_id.get(city.get("state_id")) if city.get("state_id") else None
            city_name = city.get("name") or ""
            state_name = (state.get("name") if state else None) or city.get("state") or ""
            if city_name and addr.get("city") != city_name:
                set_fields["branch.address.city"] = city_name
                stats["address_synced"] += 1
            if state_name and addr.get("state") != state_name:
                set_fields["branch.address.state"] = state_name
                stats["address_synced"] += 1
        elif loc_id and not (UUID_RE.match(str(loc_id)) and loc_id in cities_by_id):
            stats["unresolved"].append(
                f"branch {branch.get('id')} ({(branch.get('branch') or {}).get('name')}) location_id={loc_id!r}"
            )
        else:
            stats["skipped"] += 1

        if not (branch.get("slug") or "").strip():
            name = ((branch.get("branch") or {}).get("name") or "branch")
            if dry_run:
                stats["slug_set"] += 1
            else:
                set_fields["slug"] = await unique_slug(db, name, used_slugs)
                stats["slug_set"] += 1

        if "allows_collaboration" not in branch:
            set_fields["allows_collaboration"] = False
            stats["collaboration_set"] += 1

        if not set_fields:
            continue
        if dry_run:
            print(f"  [dry-run] {branch.get('id')} -> {set_fields}")
            continue
        set_fields["updated_at"] = datetime.utcnow()
        await db.branches.update_one({"id": branch["id"]}, {"$set": set_fields})

    print("\n=== summary ===")
    for key, value in stats.items():
        if key == "unresolved":
            print(f"unresolved: {len(value)}")
            for row in value:
                print(f"  - {row}")
        else:
            print(f"{key}: {value}")

    client.close()
    return stats


def main():
    parser = argparse.ArgumentParser(description="Migrate branch geography fields")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.dry_run))


if __name__ == "__main__":
    main()
