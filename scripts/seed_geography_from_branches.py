"""
Seed Rock-owned states/cities from branch addresses + locations dropdown.
Also backfills branches.location_id to city UUIDs.

Run:
  python scripts/seed_geography_from_branches.py
"""
from __future__ import annotations

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

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv(ROOT / ".env")

from utils.geography import make_code, slugify  # noqa: E402

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)

# Common dirty address fragments → clean state name
STATE_ALIASES = {
    "telangana": "Telangana",
    "telengana": "Telangana",
    "andhra pradesh": "Andhra Pradesh",
    "ap": "Andhra Pradesh",
    "karnataka": "Karnataka",
    "tamil nadu": "Tamil Nadu",
    "maharashtra": "Maharashtra",
}


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def clean_state_name(raw: str) -> str:
    s = norm(raw)
    if not s:
        return ""
    # Strip trailing pincode / punctuation: "Telangana - 500033"
    s = re.sub(r"\s*[-–]\s*\d{5,6}\s*$", "", s).strip()
    s = re.sub(r"\s+\d{5,6}\s*$", "", s).strip()
    key = s.lower()
    if key in STATE_ALIASES:
        return STATE_ALIASES[key]
    # Misplaced city-as-state
    if key in {"hyderabad", "secunderabad"}:
        return "Telangana"
    return s.title() if s == s.lower() else s


def clean_city_name(raw: str, state: str) -> str:
    s = norm(raw)
    if not s:
        return ""
    # Area-like values that aren't cities
    lower = s.lower()
    if lower in {"as rao nagar"} and state.lower() == "telangana":
        return "Hyderabad"
    return s


async def unique_field(collection, field: str, base: str, used: set) -> str:
    candidate = base or "x"
    n = 2
    while candidate.lower() in used or await collection.find_one({field: candidate}):
        if field == "code":
            candidate = f"{base[: max(1, 12 - len(str(n)))]}{n}"
        else:
            candidate = f"{base}-{n}"
        n += 1
    used.add(candidate.lower())
    return candidate


async def get_or_create_state(db, name: str, slug_used: set, code_used: set) -> dict | None:
    name = clean_state_name(name)
    if not name:
        return None
    existing = await db.states.find_one(
        {"name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}, **_rock_scope()}
    )
    if existing:
        return existing
    # Also match any rock-or-unstamped
    existing = await db.states.find_one(
        {"name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}}
    )
    if existing:
        return existing
    now = datetime.utcnow()
    doc = {
        "id": str(uuid.uuid4()),
        "name": name,
        "code": await unique_field(db.states, "code", make_code(name), code_used),
        "slug": await unique_field(db.states, "slug", slugify(name), slug_used),
        "is_active": True,
        "display_order": 0,
        "created_at": now,
        "updated_at": now,
    }
    await db.states.insert_one(doc)
    print(f"  + state {name}")
    return doc


def _rock_scope() -> dict:
    return {
        "$or": [
            {"partner_org_id": {"$exists": False}},
            {"partner_org_id": None},
            {"partner_org_id": ""},
        ]
    }


async def get_or_create_city(
    db, name: str, state_doc: dict, loc_code_used: set, loc_slug_used: set
) -> dict | None:
    name = clean_city_name(name, state_doc.get("name") or "")
    if not name or not state_doc:
        return None
    existing = await db.locations.find_one(
        {
            "name": {"$regex": f"^{re.escape(name)}$", "$options": "i"},
            "state_id": state_doc["id"],
        }
    )
    if existing:
        return existing
    existing = await db.locations.find_one(
        {"name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}}
    )
    if existing:
        patch = {
            "state_id": state_doc["id"],
            "state": state_doc["name"],
            "updated_at": datetime.utcnow(),
        }
        await db.locations.update_one({"id": existing["id"]}, {"$set": patch})
        existing.update(patch)
        return existing
    now = datetime.utcnow()
    doc = {
        "id": str(uuid.uuid4()),
        "name": name,
        "code": await unique_field(db.locations, "code", make_code(name), loc_code_used),
        "state": state_doc["name"],
        "state_id": state_doc["id"],
        "slug": await unique_field(db.locations, "slug", slugify(name), loc_slug_used),
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_active": True,
        "display_order": 0,
        "description": None,
        "created_at": now,
        "updated_at": now,
    }
    await db.locations.insert_one(doc)
    print(f"  + city {name} ({state_doc['name']})")
    return doc


async def main():
    mongo_url = os.getenv("MONGO_URL") or os.getenv("MONGO_URI") or "mongodb://localhost:27017"
    db_name = os.getenv("DB_NAME", "marshalats")
    client = AsyncIOMotorClient(mongo_url, tlsInsecure=True)
    db = client[db_name]
    print(f"Seeding geography into {db_name}")

    slug_used: set = set()
    code_used: set = set()
    loc_code_used: set = set()
    loc_slug_used: set = set()
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

    pairs: list[tuple[str, str]] = []

    # Always ensure Telangana / Hyderabad for this academy
    pairs.append(("Telangana", "Hyderabad"))

    async for b in db.branches.find({}):
        addr = ((b.get("branch") or {}).get("address") or {})
        if not isinstance(addr, dict):
            continue
        city = clean_city_name(str(addr.get("city") or ""), clean_state_name(str(addr.get("state") or "")))
        state = clean_state_name(str(addr.get("state") or ""))
        loc_hint = norm(str(b.get("location_id") or ""))
        if loc_hint and not UUID_RE.match(loc_hint):
            # location_id stored as city name historically
            city = city or clean_city_name(loc_hint, state or "Telangana")
            state = state or "Telangana"
        if city and not state:
            state = "Telangana"
        if state and city:
            pairs.append((state, city))

    dropdown = await db.dropdown_settings.find_one({"category": "locations"})
    for opt in (dropdown or {}).get("options") or []:
        label = norm(opt.get("label") or "")
        value = norm(opt.get("value") or "")
        if "," in label:
            city, state = [p.strip() for p in label.split(",", 1)]
        else:
            city, state = label or value, "Telangana"
        city = clean_city_name(city, state)
        state = clean_state_name(state) or "Telangana"
        if city:
            pairs.append((state, city))

    # Dedupe
    seen = set()
    unique_pairs = []
    for state, city in pairs:
        key = (state.lower(), city.lower())
        if key in seen:
            continue
        seen.add(key)
        unique_pairs.append((state, city))

    print(f"pairs to ensure: {unique_pairs}")
    city_by_name: dict[str, dict] = {}
    for state_name, city_name in unique_pairs:
        state_doc = await get_or_create_state(db, state_name, slug_used, code_used)
        if not state_doc:
            continue
        city_doc = await get_or_create_city(
            db, city_name, state_doc, loc_code_used, loc_slug_used
        )
        if city_doc:
            city_by_name[norm(city_doc["name"]).lower()] = city_doc

    # Backfill branches
    updated = 0
    async for branch in db.branches.find({}):
        loc_id = branch.get("location_id")
        addr = ((branch.get("branch") or {}).get("address") or {})
        if loc_id and UUID_RE.match(str(loc_id)):
            city = await db.locations.find_one({"id": loc_id})
            if city:
                # Normalize address city/state text
                patch = {
                    "branch.address.city": city.get("name"),
                    "branch.address.state": city.get("state"),
                    "updated_at": datetime.utcnow(),
                }
                await db.branches.update_one({"id": branch["id"]}, {"$set": patch})
                continue

        match = None
        if loc_id and not UUID_RE.match(str(loc_id)):
            match = city_by_name.get(norm(str(loc_id)).lower())
        if not match and isinstance(addr, dict):
            match = city_by_name.get(clean_city_name(str(addr.get("city") or ""), "").lower())
        if not match:
            match = city_by_name.get("hyderabad")
        if not match:
            print(f"  ! unresolved branch {branch.get('id')}")
            continue
        await db.branches.update_one(
            {"id": branch["id"]},
            {
                "$set": {
                    "location_id": match["id"],
                    "branch.address.city": match.get("name"),
                    "branch.address.state": match.get("state"),
                    "updated_at": datetime.utcnow(),
                }
            },
        )
        updated += 1
        print(f"  ~ branch {branch.get('id')} -> {match.get('name')}")

    print(
        f"done. states={await db.states.count_documents({})} "
        f"cities={await db.locations.count_documents({})} "
        f"branches_updated={updated}"
    )
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
