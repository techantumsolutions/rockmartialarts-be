"""
M02-S01 geography migration.

Creates `states` from existing location/dropdown data, sets locations.state_id,
imports dropdown-only cities into `locations`, and backfills branches.location_id
to city UUIDs.

Idempotent and non-destructive. Existing IDs and the `state` string on locations
are preserved.

Run from backend repo root:
  python scripts/migrate_geography.py --dry-run
  python scripts/migrate_geography.py
"""
import argparse
import asyncio
import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

load_dotenv(ROOT / ".env")

from utils.geography import make_code, slugify  # noqa: E402

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def parse_city_state_label(label: str, value: str = ""):
    text = norm(label) or norm(value)
    if not text:
        return "", ""
    if "," in text:
        city, state = [p.strip() for p in text.split(",", 1)]
        return city, state
    return text, ""


async def unique_field(collection, field: str, base: str, used: set) -> str:
    candidate = base
    n = 2
    while candidate.lower() in used or await collection.find_one({field: candidate}):
        if field == "code":
            candidate = f"{base[: max(1, 12 - len(str(n)))]}{n}"
        else:
            candidate = f"{base}-{n}"
        n += 1
    used.add(candidate.lower())
    return candidate


async def get_or_create_state(db, name: str, dry_run: bool, stats: dict, slug_used: set, code_used: set):
    name = norm(name)
    if not name:
        return None
    existing = await db.states.find_one({"name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}})
    if existing:
        return existing
    stats["states_created"] += 1
    if dry_run:
        print(f"  [dry-run] would create state: {name}")
        return {"id": f"dry-{name}", "name": name, "is_active": True}
    slug = await unique_field(db.states, "slug", slugify(name), slug_used)
    code = await unique_field(db.states, "code", make_code(name), code_used)
    now = datetime.utcnow()
    doc = {
        "id": str(uuid.uuid4()),
        "name": name,
        "code": code,
        "slug": slug,
        "is_active": True,
        "display_order": 0,
        "created_at": now,
        "updated_at": now,
    }
    await db.states.insert_one(doc)
    print(f"  created state: {name} ({doc['id']})")
    return doc


async def find_city(db, name: str, state_id: str = None):
    name = norm(name)
    if not name:
        return None
    query = {"name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}}
    if state_id:
        by_state = await db.locations.find_one({**query, "state_id": state_id})
        if by_state:
            return by_state
    return await db.locations.find_one(query)


async def migrate(dry_run: bool):
    mongo_url = os.getenv("MONGO_URL") or os.getenv("MONGO_URI") or "mongodb://localhost:27017"
    db_name = os.getenv("DB_NAME", "marshalats")
    client = AsyncIOMotorClient(mongo_url, tlsInsecure=True)
    db = client[db_name]

    stats = {
        "states_created": 0,
        "locations_linked": 0,
        "locations_created": 0,
        "locations_skipped": 0,
        "branches_updated": 0,
        "branches_skipped": 0,
        "unresolved": [],
    }

    print("=== M02-S01 geography migration ===")
    print(f"database={db_name} dry_run={dry_run}")
    print()

    slug_used = set()
    code_used = set()
    loc_code_used = set()
    loc_slug_used = set()
    async for s in db.states.find({}):
        if s.get("slug"):
            slug_used.add(str(s["slug"]).lower())
        if s.get("code"):
            code_used.add(str(s["code"]).lower())
    async for loc in db.locations.find({}):
        if loc.get("code"):
            loc_code_used.add(str(loc["code"]).lower())
        if loc.get("slug"):
            loc_slug_used.add(str(loc["slug"]).lower())

    # 1) Distinct states from locations + dropdown
    state_names = set()
    locations = await db.locations.find({}).to_list(5000)
    print(f"examined locations: {len(locations)}")
    for loc in locations:
        if norm(loc.get("state") or ""):
            state_names.add(norm(loc["state"]))

    dropdown = await db.dropdown_settings.find_one({"category": "locations"})
    dropdown_options = (dropdown or {}).get("options") or []
    print(f"examined dropdown location options: {len(dropdown_options)}")
    parsed_dropdown = []
    for opt in dropdown_options:
        city, state = parse_city_state_label(opt.get("label") or "", opt.get("value") or "")
        if state:
            state_names.add(state)
        parsed_dropdown.append({
            "city": city,
            "state": state,
            "is_active": opt.get("is_active", True) is not False,
            "order": opt.get("order", 0) or 0,
            "value": opt.get("value") or city,
        })

    print(f"distinct state names: {sorted(state_names)}")
    states_by_norm = {}
    for name in sorted(state_names):
        doc = await get_or_create_state(db, name, dry_run, stats, slug_used, code_used)
        if doc:
            states_by_norm[name.lower()] = doc

    # 2) Link existing locations
    print("\n--- linking existing locations ---")
    for loc in locations:
        loc_id = loc.get("id")
        if loc.get("state_id") and await db.states.find_one({"id": loc["state_id"]}):
            stats["locations_skipped"] += 1
            continue
        state_name = norm(loc.get("state") or "")
        state_doc = states_by_norm.get(state_name.lower()) if state_name else None
        if not state_doc:
            stats["unresolved"].append(f"location {loc_id} ({loc.get('name')}) has no resolvable state")
            continue
        stats["locations_linked"] += 1
        if dry_run:
            print(f"  [dry-run] would set state_id on location {loc.get('name')} -> {state_doc.get('name')}")
            continue
        set_fields = {
            "state_id": state_doc["id"],
            "state": state_doc["name"],
            "updated_at": datetime.utcnow(),
        }
        if not loc.get("slug"):
            set_fields["slug"] = await unique_field(db.locations, "slug", slugify(loc.get("name") or "city"), loc_slug_used)
        await db.locations.update_one({"id": loc_id}, {"$set": set_fields})

    # 3) Dropdown-only cities
    print("\n--- importing dropdown-only cities ---")
    for item in parsed_dropdown:
        city_name = item["city"]
        if not city_name:
            continue
        state_doc = states_by_norm.get(item["state"].lower()) if item["state"] else None
        existing_city = await find_city(db, city_name, state_doc["id"] if state_doc and not dry_run else None)
        if existing_city:
            stats["locations_skipped"] += 1
            continue
        if not state_doc:
            stats["unresolved"].append(f"dropdown city '{city_name}' has no state")
            continue
        stats["locations_created"] += 1
        if dry_run:
            print(f"  [dry-run] would create city {city_name} in {state_doc.get('name')}")
            continue
        now = datetime.utcnow()
        code = await unique_field(db.locations, "code", make_code(city_name), loc_code_used)
        slug = await unique_field(db.locations, "slug", slugify(city_name), loc_slug_used)
        doc = {
            "id": str(uuid.uuid4()),
            "name": city_name,
            "code": code,
            "state": state_doc["name"],
            "state_id": state_doc["id"],
            "slug": slug,
            "country": "India",
            "timezone": "Asia/Kolkata",
            "is_active": item["is_active"],
            "display_order": item["order"],
            "description": None,
            "created_at": now,
            "updated_at": now,
        }
        await db.locations.insert_one(doc)
        print(f"  created city: {city_name} ({doc['id']})")

    # 4) Branch location_id backfill
    print("\n--- backfilling branches.location_id ---")
    branches = await db.branches.find({}).to_list(5000)
    print(f"examined branches: {len(branches)}")
    all_cities = await db.locations.find({}).to_list(5000)
    cities_by_id = {c.get("id"): c for c in all_cities if c.get("id")}
    cities_by_name = {}
    for c in all_cities:
        cities_by_name.setdefault(norm(c.get("name") or "").lower(), []).append(c)

    for branch in branches:
        loc_id = branch.get("location_id")
        addr = ((branch.get("branch") or {}).get("address") or {})
        if loc_id and UUID_RE.match(str(loc_id)) and loc_id in cities_by_id:
            stats["branches_skipped"] += 1
            continue

        match = None
        if loc_id and UUID_RE.match(str(loc_id)):
            stats["unresolved"].append(
                f"branch {branch.get('id')} location_id={loc_id} is UUID but not a known city"
            )
        elif loc_id:
            matches = cities_by_name.get(norm(str(loc_id)).lower()) or []
            match = matches[0] if matches else None

        if not match:
            city_name = norm(addr.get("city") or "")
            matches = cities_by_name.get(city_name.lower()) if city_name else []
            match = matches[0] if matches else None

        if not match:
            stats["unresolved"].append(
                f"branch {branch.get('id')} ({(branch.get('branch') or {}).get('name')}) unresolved location_id={loc_id!r}"
            )
            continue

        stats["branches_updated"] += 1
        if dry_run:
            print(f"  [dry-run] would set branch {branch.get('id')} location_id -> {match.get('name')} ({match.get('id')})")
            continue
        await db.branches.update_one(
            {"id": branch["id"]},
            {"$set": {"location_id": match["id"], "updated_at": datetime.utcnow()}},
        )

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
    parser = argparse.ArgumentParser(description="Migrate State/City geography masters")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    args = parser.parse_args()
    asyncio.run(migrate(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
