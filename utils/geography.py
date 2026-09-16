"""Shared helpers for State / City (locations) masters."""
import logging
import re
from typing import Optional

from utils.helpers import serialize_doc

_slug_strip_re = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    text = (value or "").strip().lower()
    text = _slug_strip_re.sub("-", text).strip("-")
    return text or "item"


def make_code(value: str, max_len: int = 12) -> str:
    raw = re.sub(r"[^A-Za-z0-9]", "", (value or "").upper())
    if not raw:
        raw = "LOC"
    return raw[:max_len]


async def unique_slug(collection, base: str, exclude_id: Optional[str] = None) -> str:
    slug = slugify(base)
    candidate = slug
    n = 2
    while True:
        query = {"slug": candidate}
        if exclude_id:
            query["id"] = {"$ne": exclude_id}
        existing = await collection.find_one(query)
        if not existing:
            return candidate
        candidate = f"{slug}-{n}"
        n += 1
        if n > 500:
            return f"{slug}-{n}"


async def unique_code(collection, base: str, exclude_id: Optional[str] = None) -> str:
    code = make_code(base)
    candidate = code
    n = 2
    while True:
        query = {"code": candidate}
        if exclude_id:
            query["id"] = {"$ne": exclude_id}
        existing = await collection.find_one(query)
        if not existing:
            return candidate
        suffix = str(n)
        candidate = f"{code[: max(1, 12 - len(suffix))]}{suffix}"
        n += 1
        if n > 500:
            return f"{code}{n}"


def name_conflict_query(name: str, extra: Optional[dict] = None, exclude_id: Optional[str] = None) -> dict:
    query = extra.copy() if extra else {}
    query["name"] = {"$regex": f"^{re.escape(name.strip())}$", "$options": "i"}
    if exclude_id:
        query["id"] = {"$ne": exclude_id}
    return query


def serialize_state(doc: dict, city_count: Optional[int] = None) -> dict:
    item = serialize_doc(doc) or {}
    payload = {
        "id": item.get("id"),
        "name": item.get("name"),
        "code": item.get("code"),
        "slug": item.get("slug") or "",
        "is_active": bool(item.get("is_active", True)),
        "display_order": item.get("display_order", 0) or 0,
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }
    if city_count is not None:
        payload["city_count"] = city_count
    return payload


def serialize_city(doc: dict, state_doc: Optional[dict] = None) -> dict:
    item = serialize_doc(doc) or {}
    state_name = None
    if state_doc:
        state_name = state_doc.get("name")
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "code": item.get("code"),
        "slug": item.get("slug"),
        "state_id": item.get("state_id"),
        "state": item.get("state") or state_name or "",
        "state_name": state_name or item.get("state") or "",
        "country": item.get("country", "India"),
        "timezone": item.get("timezone", "Asia/Kolkata"),
        "is_active": bool(item.get("is_active", True)),
        "display_order": item.get("display_order", 0) or 0,
        "description": item.get("description"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


async def ensure_geography_indexes(mongo_db) -> None:
    """Additive indexes. Failures are logged and must not crash startup."""
    try:
        await mongo_db.states.create_index("code", unique=True, name="states_code_unique")
    except Exception:
        logging.exception("Failed to create states.code unique index")
    try:
        await mongo_db.states.create_index("slug", unique=True, name="states_slug_unique")
    except Exception:
        logging.exception("Failed to create states.slug unique index")
    try:
        await mongo_db.locations.create_index("code", unique=True, name="locations_code_unique")
    except Exception:
        logging.exception("Failed to create locations.code unique index")
    try:
        await mongo_db.locations.create_index("slug", unique=True, sparse=True, name="locations_slug_unique")
    except Exception:
        logging.exception("Failed to create locations.slug unique index")
    try:
        await mongo_db.locations.create_index(
            [("state_id", 1), ("name", 1)],
            unique=True,
            sparse=True,
            name="locations_state_id_name_unique",
        )
    except Exception:
        logging.exception("Failed to create locations (state_id, name) unique index")
