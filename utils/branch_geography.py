"""Helpers to keep branches aligned with S01 State → City masters."""
import logging
import re
from typing import Optional, Tuple

from fastapi import HTTPException

from utils.geography import unique_slug

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)


def is_uuid(value: Optional[str]) -> bool:
    return bool(value and UUID_RE.match(str(value).strip()))


def _name_regex(name: str) -> dict:
    return {"$regex": f"^{re.escape((name or '').strip())}$", "$options": "i"}


async def find_city_for_location_ref(db, location_ref: str) -> Optional[dict]:
    """Resolve a city by UUID first, then by unique name (legacy location_id)."""
    ref = (location_ref or "").strip()
    if not ref:
        return None
    city = await db.locations.find_one({"id": ref})
    if city:
        return city
    matches = await db.locations.find({"name": _name_regex(ref)}).to_list(5)
    if len(matches) == 1:
        return matches[0]
    return None


async def resolve_city_for_write(db, location_id: str) -> Tuple[dict, Optional[dict]]:
    """Require an active city (and active parent state when linked) for create/update."""
    ref = (location_id or "").strip()
    if not ref:
        raise HTTPException(status_code=400, detail="Location is required")

    city = await find_city_for_location_ref(db, ref)
    if not city:
        raise HTTPException(
            status_code=400,
            detail="Please select a valid city from State & City",
        )
    if city.get("is_active", True) is False:
        raise HTTPException(status_code=400, detail="Selected city is inactive")

    state = None
    state_id = (city.get("state_id") or "").strip()
    if state_id:
        state = await db.states.find_one({"id": state_id})
        if not state:
            raise HTTPException(status_code=400, detail="Selected city's state was not found")
        if state.get("is_active", True) is False:
            raise HTTPException(status_code=400, detail="Selected city's state is inactive")
    return city, state


def apply_city_to_branch_doc(branch_doc: dict, city: dict, state: Optional[dict] = None) -> None:
    """Set location_id to city UUID and keep address.city/state in sync."""
    branch_doc["location_id"] = city["id"]
    info = branch_doc.get("branch") if isinstance(branch_doc.get("branch"), dict) else {}
    address = info.get("address") if isinstance(info.get("address"), dict) else {}
    address["city"] = city.get("name") or address.get("city") or ""
    address["state"] = (
        (state.get("name") if state else None)
        or city.get("state")
        or address.get("state")
        or ""
    )
    info["address"] = address
    branch_doc["branch"] = info


async def assert_unique_branch_code(db, code: str, exclude_id: Optional[str] = None) -> str:
    cleaned = (code or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Branch code is required")
    query = {"branch.code": _name_regex(cleaned)}
    if exclude_id:
        query["id"] = {"$ne": exclude_id}
    existing = await db.branches.find_one(query)
    if existing:
        raise HTTPException(status_code=400, detail="Branch code already exists")
    return cleaned


async def ensure_unique_branch_slug(db, name: str, exclude_id: Optional[str] = None, existing_slug: Optional[str] = None) -> str:
    if existing_slug and str(existing_slug).strip():
        slug = str(existing_slug).strip().lower()
        query = {"slug": slug}
        if exclude_id:
            query["id"] = {"$ne": exclude_id}
        conflict = await db.branches.find_one(query)
        if conflict:
            raise HTTPException(status_code=400, detail="Branch slug already exists")
        return slug
    return await unique_slug(db.branches, name or "branch", exclude_id=exclude_id)


async def branches_matching_location_query(db, location_ref: str, active_only: bool = True) -> Tuple[dict, Optional[dict]]:
    """Match branches by city UUID, legacy name location_id, or address.city."""
    ref = (location_ref or "").strip()
    city = await find_city_for_location_ref(db, ref)
    ids = []
    names = []
    if city:
        if city.get("id"):
            ids.append(city["id"])
        if city.get("name"):
            names.append(str(city["name"]).strip())
    if ref and ref not in ids:
        ids.append(ref)
        names.append(ref)

    ors = []
    if ids:
        ors.append({"location_id": {"$in": ids}})
    seen_names = set()
    for name in names:
        key = (name or "").strip().lower()
        if not key or key in seen_names:
            continue
        seen_names.add(key)
        ors.append({"location_id": _name_regex(name)})
        ors.append({"branch.address.city": _name_regex(name)})

    query: dict = {"$or": ors} if ors else {"location_id": ref}
    if active_only:
        query["is_active"] = True
    return query, city


async def public_branch_discovery_query(
    db,
    *,
    state_id: Optional[str] = None,
    city_id: Optional[str] = None,
    q: Optional[str] = None,
    active_only: bool = True,
) -> dict:
    """Public listing filters. Each of state, city, and text search can be used alone or together."""
    clauses = []
    if active_only:
        clauses.append({"is_active": True})

    city_ref = (city_id or "").strip()
    if city_ref:
        loc_query, _ = await branches_matching_location_query(db, city_ref, active_only=False)
        loc_query.pop("is_active", None)
        clauses.append(loc_query)

    sid = (state_id or "").strip()
    if sid:
        state = await db.states.find_one({"id": sid})
        if not state or (active_only and state.get("is_active", True) is False):
            return {"id": {"$in": []}}
        cities = await db.locations.find({"state_id": sid}).to_list(500)
        ors = []
        city_ids = [c.get("id") for c in cities if c.get("id")]
        if city_ids:
            ors.append({"location_id": {"$in": city_ids}})
        state_name = (state.get("name") or "").strip()
        if state_name:
            ors.append({"branch.address.state": _name_regex(state_name)})
        seen = set()
        for city in cities:
            name = (city.get("name") or "").strip()
            key = name.lower()
            if not name or key in seen:
                continue
            seen.add(key)
            ors.append({"branch.address.city": _name_regex(name)})
            ors.append({"location_id": _name_regex(name)})
        clauses.append({"$or": ors} if ors else {"id": {"$in": []}})

    text = (q or "").strip()
    if text:
        rx = {"$regex": re.escape(text), "$options": "i"}
        clauses.append({
            "$or": [
                {"branch.name": rx},
                {"branch.code": rx},
                {"slug": rx},
                {"branch.address.city": rx},
                {"branch.address.area": rx},
                {"branch.address.line1": rx},
                {"branch.address.state": rx},
            ]
        })

    if not clauses:
        return {}
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


async def count_active_branches_for_location(db, location_ref: str) -> int:
    query, _ = await branches_matching_location_query(db, location_ref, active_only=True)
    return await db.branches.count_documents(query)


async def assert_branch_accepts_enrollment(db, branch_id: str) -> dict:
    """Block new enrollments / branch-changes onto an inactive branch. Existing records stay."""
    branch = await db.branches.find_one({"id": branch_id})
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")
    if branch.get("is_active", True) is False:
        raise HTTPException(
            status_code=400,
            detail="This branch is not accepting new enrollments",
        )
    return branch


async def ensure_branch_indexes(mongo_db) -> None:
    """Additive indexes. Failures are logged and must not crash startup."""
    try:
        await mongo_db.branches.create_index(
            "slug", unique=True, sparse=True, name="branches_slug_unique"
        )
    except Exception:
        logging.exception("Failed to create branches.slug unique index")
    try:
        await mongo_db.branches.create_index(
            "branch.code", unique=True, sparse=True, name="branches_code_unique"
        )
    except Exception:
        logging.exception("Failed to create branches.branch.code unique index")


__all__ = [
    "apply_city_to_branch_doc",
    "assert_branch_accepts_enrollment",
    "assert_unique_branch_code",
    "branches_matching_location_query",
    "count_active_branches_for_location",
    "ensure_branch_indexes",
    "ensure_unique_branch_slug",
    "find_city_for_location_ref",
    "is_uuid",
    "public_branch_discovery_query",
    "resolve_city_for_write",
]
