from datetime import datetime
from typing import Optional

from fastapi import HTTPException

from models.location_models import CityCreate, CityUpdate, Location
from utils.database import get_db
from utils.geography import (
    name_conflict_query,
    serialize_city,
    unique_code,
    unique_slug,
)
from utils.branch_geography import count_active_branches_for_location
from utils.partner_tenant import (
    assert_tenant_doc_access,
    merge_and_clause,
    rock_owned_clause,
    stamp_tenant,
    tenant_or_rock_owned_filter,
    uniqueness_scope_for_user,
)


class CityController:
    @staticmethod
    async def _require_state(
        db, state_id: str, current_user: dict = None, require_active: bool = False
    ) -> dict:
        state = await db.states.find_one({"id": state_id})
        if not state:
            raise HTTPException(status_code=400, detail="Invalid state")
        if current_user is not None:
            assert_tenant_doc_access(state, current_user, entity="state")
        if require_active and not state.get("is_active", True):
            raise HTTPException(status_code=400, detail="Cannot assign a city to an inactive state")
        return state

    @staticmethod
    async def _city_name_taken(
        db,
        name: str,
        state_id: str,
        current_user: dict = None,
        exclude_id: Optional[str] = None,
    ) -> bool:
        query = name_conflict_query(name, extra={"state_id": state_id}, exclude_id=exclude_id)
        if current_user is not None:
            query = merge_and_clause(query, uniqueness_scope_for_user(current_user))
        found = await db.locations.find_one(query)
        return found is not None

    @staticmethod
    async def create_city(city_data: CityCreate, current_user: dict = None):
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        name = (city_data.name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="City name is required")
        if not (city_data.state_id or "").strip():
            raise HTTPException(status_code=400, detail="State is required")

        db = get_db()
        scope = uniqueness_scope_for_user(current_user)
        state = await CityController._require_state(
            db, city_data.state_id.strip(), current_user=current_user, require_active=True
        )

        if await CityController._city_name_taken(db, name, state["id"], current_user):
            raise HTTPException(
                status_code=400,
                detail="A city with this name already exists in the selected state",
            )

        code = (city_data.code or "").strip().upper() or await unique_code(
            db.locations, name, scope=scope
        )
        if await db.locations.find_one(merge_and_clause({"code": code}, scope)):
            raise HTTPException(status_code=400, detail="City code already exists")

        slug = (city_data.slug or "").strip() or await unique_slug(
            db.locations, name, scope=scope
        )
        if await db.locations.find_one(merge_and_clause({"slug": slug}, scope)):
            raise HTTPException(status_code=400, detail="City slug already exists")

        location = Location(
            name=name,
            code=code,
            state=state["name"],
            state_id=state["id"],
            slug=slug,
            country=city_data.country or "India",
            timezone=city_data.timezone or "Asia/Kolkata",
            is_active=city_data.is_active,
            display_order=city_data.display_order or 0,
            description=city_data.description,
        )
        location_dict = location.dict()
        stamp_tenant(location_dict, current_user)
        await db.locations.insert_one(location_dict)
        return {"message": "City created successfully", "city_id": location.id, "location_id": location.id}

    @staticmethod
    async def get_cities(
        state_id: Optional[str] = None,
        active_only: bool = False,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
        current_user: dict = None,
    ):
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()
        query = {}
        if state_id:
            query["state_id"] = state_id
        if active_only:
            query["is_active"] = True
        if search and search.strip():
            query["name"] = {"$regex": search.strip(), "$options": "i"}
        query = tenant_or_rock_owned_filter(query, current_user)

        cursor = db.locations.find(query).sort([("name", 1)]).skip(skip).limit(limit)
        cities = await cursor.to_list(limit)
        total = await db.locations.count_documents(query)

        state_ids = {c.get("state_id") for c in cities if c.get("state_id")}
        states_by_id = {}
        if state_ids:
            async for s in db.states.find({"id": {"$in": list(state_ids)}}):
                states_by_id[s["id"]] = s

        return {
            "message": f"Retrieved {len(cities)} cities successfully",
            "cities": [serialize_city(c, states_by_id.get(c.get("state_id"))) for c in cities],
            "total": total,
            "skip": skip,
            "limit": limit,
        }

    @staticmethod
    async def get_public_cities(
        state_id: Optional[str] = None,
        active_only: bool = True,
        skip: int = 0,
        limit: int = 200,
    ):
        db = get_db()
        if limit > 200:
            limit = 200

        query = merge_and_clause({}, rock_owned_clause())
        if active_only:
            query = merge_and_clause(query, {"is_active": True})

        active_state_ids = None
        if state_id:
            state = await db.states.find_one(
                merge_and_clause({"id": state_id}, rock_owned_clause())
            )
            if not state:
                return {
                    "message": "Retrieved 0 cities successfully",
                    "cities": [],
                    "total": 0,
                    "skip": skip,
                    "limit": limit,
                }
            if active_only and not state.get("is_active", True):
                return {
                    "message": "Retrieved 0 cities successfully",
                    "cities": [],
                    "total": 0,
                    "skip": skip,
                    "limit": limit,
                }
            query = merge_and_clause(query, {"state_id": state_id})
        elif active_only:
            active_states = await db.states.find(
                merge_and_clause({"is_active": True}, rock_owned_clause())
            ).to_list(500)
            active_state_ids = {s["id"] for s in active_states}
            # Include cities linked to active Rock states, plus legacy cities with no state_id
            if active_state_ids:
                query = merge_and_clause(
                    query,
                    {
                        "$or": [
                            {"state_id": {"$in": list(active_state_ids)}},
                            {"state_id": {"$exists": False}},
                            {"state_id": None},
                            {"state_id": ""},
                        ]
                    },
                )

        cursor = db.locations.find(query).sort([("name", 1)]).skip(skip).limit(limit)
        cities = await cursor.to_list(limit)
        total = await db.locations.count_documents(query)

        state_ids = {c.get("state_id") for c in cities if c.get("state_id")}
        states_by_id = {}
        if state_ids:
            async for s in db.states.find({"id": {"$in": list(state_ids)}}):
                states_by_id[s["id"]] = s

        public_cities = []
        for city in cities:
            parent = states_by_id.get(city.get("state_id"))
            if active_only and city.get("state_id") and parent and not parent.get("is_active", True):
                continue
            item = serialize_city(city, parent)
            public_cities.append({
                "id": item["id"],
                "name": item["name"],
                "code": item["code"],
                "slug": item.get("slug"),
                "state_id": item.get("state_id"),
                "state": item.get("state"),
                "state_name": item.get("state_name"),
                "display_order": item.get("display_order", 0),
                "branch_count": await count_active_branches_for_location(db, item["id"]),
            })

        return {
            "message": f"Retrieved {len(public_cities)} cities successfully",
            "cities": public_cities,
            "total": total,
            "skip": skip,
            "limit": limit,
        }

    @staticmethod
    async def update_city(city_id: str, city_update: CityUpdate, current_user: dict = None):
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()
        existing = await db.locations.find_one({"id": city_id})
        if not existing:
            raise HTTPException(status_code=404, detail="City not found")
        assert_tenant_doc_access(existing, current_user, entity="city")

        scope = uniqueness_scope_for_user(current_user)
        updates = {k: v for k, v in city_update.dict().items() if v is not None}
        next_state_id = updates.get("state_id", existing.get("state_id"))
        next_name = str(updates.get("name", existing.get("name") or "")).strip()

        if "name" in updates:
            if not next_name:
                raise HTTPException(status_code=400, detail="City name is required")
            updates["name"] = next_name

        if "state_id" in updates:
            state_id = str(updates["state_id"]).strip()
            if not state_id:
                raise HTTPException(status_code=400, detail="State is required")
            state = await CityController._require_state(
                db, state_id, current_user=current_user, require_active=True
            )
            updates["state_id"] = state["id"]
            updates["state"] = state["name"]
            next_state_id = state["id"]

        if ("name" in updates or "state_id" in updates) and next_state_id:
            if await CityController._city_name_taken(
                db, next_name, next_state_id, current_user, exclude_id=city_id
            ):
                raise HTTPException(
                    status_code=400,
                    detail="A city with this name already exists in the selected state",
                )

        if "code" in updates:
            code = str(updates["code"]).strip().upper()
            if not code:
                raise HTTPException(status_code=400, detail="City code is required")
            updates["code"] = code
            if await db.locations.find_one(
                merge_and_clause({"code": code, "id": {"$ne": city_id}}, scope)
            ):
                raise HTTPException(status_code=400, detail="City code already exists")

        if "slug" in updates:
            slug = str(updates["slug"]).strip()
            if not slug:
                raise HTTPException(status_code=400, detail="City slug is required")
            updates["slug"] = slug
            if await db.locations.find_one(
                merge_and_clause({"slug": slug, "id": {"$ne": city_id}}, scope)
            ):
                raise HTTPException(status_code=400, detail="City slug already exists")

        if not updates:
            return {"message": "No changes to update"}

        updates["updated_at"] = datetime.utcnow()
        await db.locations.update_one({"id": city_id}, {"$set": updates})
        return {"message": "City updated successfully"}
