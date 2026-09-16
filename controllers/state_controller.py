from datetime import datetime
from typing import Optional

from fastapi import HTTPException

from models.state_models import State, StateCreate, StateUpdate
from utils.database import get_db
from utils.geography import (
    name_conflict_query,
    serialize_state,
    unique_code,
    unique_slug,
)


class StateController:
    @staticmethod
    async def create_state(state_data: StateCreate, current_user: dict = None):
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        name = (state_data.name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="State name is required")

        db = get_db()
        existing_name = await db.states.find_one(name_conflict_query(name))
        if existing_name:
            raise HTTPException(status_code=400, detail="A state with this name already exists")

        code = (state_data.code or "").strip().upper() or await unique_code(db.states, name)
        code_conflict = await db.states.find_one({"code": code})
        if code_conflict:
            raise HTTPException(status_code=400, detail="State code already exists")

        slug = (state_data.slug or "").strip() or await unique_slug(db.states, name)
        slug_conflict = await db.states.find_one({"slug": slug})
        if slug_conflict:
            raise HTTPException(status_code=400, detail="State slug already exists")

        state = State(
            name=name,
            code=code,
            slug=slug,
            is_active=state_data.is_active,
            display_order=state_data.display_order or 0,
        )
        await db.states.insert_one(state.dict())
        return {"message": "State created successfully", "state_id": state.id}

    @staticmethod
    async def get_states(
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
        if active_only:
            query["is_active"] = True
        if search and search.strip():
            query["name"] = {"$regex": search.strip(), "$options": "i"}

        cursor = db.states.find(query).sort([("name", 1)]).skip(skip).limit(limit)
        states = await cursor.to_list(limit)
        total = await db.states.count_documents(query)

        enriched = []
        for state in states:
            city_count = await db.locations.count_documents({"state_id": state["id"]})
            enriched.append(serialize_state(state, city_count=city_count))

        return {
            "message": f"Retrieved {len(enriched)} states successfully",
            "states": enriched,
            "total": total,
            "skip": skip,
            "limit": limit,
        }

    @staticmethod
    async def get_public_states(active_only: bool = True, skip: int = 0, limit: int = 100):
        db = get_db()
        query = {}
        if active_only:
            query["is_active"] = True
        if limit > 200:
            limit = 200

        cursor = db.states.find(query).sort([("name", 1)]).skip(skip).limit(limit)
        states = await cursor.to_list(limit)
        total = await db.states.count_documents(query)
        public_states = [
            {
                "id": s["id"],
                "name": s["name"],
                "code": s.get("code"),
                "slug": s.get("slug"),
                "display_order": s.get("display_order", 0) or 0,
            }
            for s in states
        ]
        return {
            "message": f"Retrieved {len(public_states)} states successfully",
            "states": public_states,
            "total": total,
            "skip": skip,
            "limit": limit,
        }

    @staticmethod
    async def update_state(state_id: str, state_update: StateUpdate, current_user: dict = None):
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()
        existing = await db.states.find_one({"id": state_id})
        if not existing:
            raise HTTPException(status_code=404, detail="State not found")

        updates = {k: v for k, v in state_update.dict().items() if v is not None}
        if "name" in updates:
            name = str(updates["name"]).strip()
            if not name:
                raise HTTPException(status_code=400, detail="State name is required")
            updates["name"] = name
            name_conflict = await db.states.find_one(name_conflict_query(name, exclude_id=state_id))
            if name_conflict:
                raise HTTPException(status_code=400, detail="A state with this name already exists")

        if "code" in updates:
            code = str(updates["code"]).strip().upper()
            if not code:
                raise HTTPException(status_code=400, detail="State code is required")
            updates["code"] = code
            code_conflict = await db.states.find_one({"code": code, "id": {"$ne": state_id}})
            if code_conflict:
                raise HTTPException(status_code=400, detail="State code already exists")

        if "slug" in updates:
            slug = str(updates["slug"]).strip()
            if not slug:
                raise HTTPException(status_code=400, detail="State slug is required")
            updates["slug"] = slug
            slug_conflict = await db.states.find_one({"slug": slug, "id": {"$ne": state_id}})
            if slug_conflict:
                raise HTTPException(status_code=400, detail="State slug already exists")

        if not updates:
            return {"message": "No changes to update"}

        updates["updated_at"] = datetime.utcnow()
        await db.states.update_one({"id": state_id}, {"$set": updates})

        # Keep denormalized location.state in sync when the state name changes.
        if "name" in updates:
            await db.locations.update_many(
                {"state_id": state_id},
                {"$set": {"state": updates["name"], "updated_at": datetime.utcnow()}},
            )

        return {"message": "State updated successfully"}
