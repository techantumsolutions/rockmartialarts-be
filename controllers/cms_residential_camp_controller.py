from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import HTTPException, status

from models.cms_residential_camp_models import (
    CampEventFields,
    CampEventResponse,
    ResidentialCampContent,
    ResidentialCampResponse,
    default_residential_camp_dict,
    new_camp_event_id,
)
from utils.database import get_db, serialize_doc

EVENT_FIELD_KEYS = (
    "event_name",
    "start_date",
    "end_date",
    "min_age",
    "max_age",
    "camp_fee",
    "event_location",
)


def _dump(model) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _event_fields(source: Dict[str, Any]) -> dict:
    out = {}
    for key in EVENT_FIELD_KEYS:
        out[key] = str(source.get(key) or "").strip()
    fee = out.get("camp_fee") or ""
    if fee and not fee.startswith("₹") and fee[:1].isdigit():
        out["camp_fee"] = f"₹{fee}"
    if not out.get("event_name"):
        out["event_name"] = "Dussehra Special – Shaolin Kungfu Residential Camp"
    return out


def _fmt_iso_date(iso: str) -> str:
    raw = (iso or "").strip()
    try:
        d = datetime.strptime(raw, "%Y-%m-%d")
    except Exception:
        return ""
    return d.strftime("%d %b %Y").replace(" 0", " ").lstrip("0")


def _date_range(start: str, end: str) -> str:
    a = _fmt_iso_date(start)
    b = _fmt_iso_date(end)
    if a and b:
        return f"{a} – {b}"
    return a or b


def _age_group(min_age: str, max_age: str) -> str:
    low = (min_age or "").strip()
    high = (max_age or "").strip()
    if low and high:
        return f"{low}–{high} Years"
    if low:
        return f"{low}+ Years"
    if high:
        return f"Up to {high} Years"
    return ""


FACT_SLOTS = (
    ("Dates", "dates"),
    ("Location", "location"),
    ("Age Group", "age"),
    ("Camp Fee", "fee"),
)


def _fact_slot_value(fields: Dict[str, Any], kind: str) -> str:
    if kind == "dates":
        return _date_range(fields.get("start_date") or "", fields.get("end_date") or "")
    if kind == "location":
        return (fields.get("event_location") or "").strip()
    if kind == "age":
        return _age_group(fields.get("min_age") or "", fields.get("max_age") or "")
    return (fields.get("camp_fee") or "").strip()


def _ensure_event_facts(facts: Any, fields: Dict[str, Any]) -> list:
    unused = []
    for item in facts or []:
        unused.append(dict(item) if isinstance(item, dict) else {"label": str(item), "value": ""})
    out = []
    for default_label, kind in FACT_SLOTS:
        want = default_label.lower()
        idx = next(
            (i for i, row in enumerate(unused) if (row.get("label") or "").strip().lower() == want),
            None,
        )
        if idx is None and unused:
            idx = 0
        src = unused.pop(idx) if idx is not None else {}
        label = (src.get("label") or "").strip() or default_label
        value = _fact_slot_value(fields, kind) or (src.get("value") or "").strip()
        out.append({"label": label, "value": value})
    return out


def _merge_event_into_cms(cms: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
    fields = _event_fields(event)
    merged = {**cms, **fields, "event_id": (event.get("event_id") or cms.get("event_id") or "").strip()}
    merged["facts"] = _ensure_event_facts(merged.get("facts"), fields)
    if fields["camp_fee"]:
        schedule = dict(merged.get("schedule") or {})
        price = dict(schedule.get("price") or {})
        price["amount"] = fields["camp_fee"]
        schedule["price"] = price
        merged["schedule"] = schedule
    return merged


class CMSResidentialCampController:
    COLLECTION = "cms_residential_camp"
    EVENTS_COLLECTION = "camp_events"

    @staticmethod
    async def get_content() -> ResidentialCampResponse:
        try:
            db = get_db()
            collection = db[CMSResidentialCampController.COLLECTION]
            doc = await collection.find_one({}, sort=[("updated_at", -1), ("created_at", -1)])
            if not doc:
                doc = await CMSResidentialCampController._create_default()
            doc = await CMSResidentialCampController._ensure_event(doc)
            event = await CMSResidentialCampController._ensure_current_event(doc)
            merged = _merge_event_into_cms(doc, event)
            return CMSResidentialCampController._to_response(merged)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to retrieve residential camp content: {str(e)}",
            )

    @staticmethod
    async def update_content(data: ResidentialCampContent) -> ResidentialCampResponse:
        try:
            db = get_db()
            collection = db[CMSResidentialCampController.COLLECTION]
            existing = await collection.find_one({}, sort=[("updated_at", -1), ("created_at", -1)])
            payload = _dump(data)
            if existing:
                incoming_id = (payload.get("event_id") or "").strip()
                existing_id = (existing.get("event_id") or "").strip()
                payload["event_id"] = incoming_id or existing_id or new_camp_event_id()
            elif not (payload.get("event_id") or "").strip():
                payload["event_id"] = new_camp_event_id()
            if not (payload.get("event_name") or "").strip():
                payload["event_name"] = "Dussehra Special – Shaolin Kungfu Residential Camp"
            payload["updated_at"] = datetime.utcnow()

            if existing:
                await collection.update_one({"_id": existing["_id"]}, {"$set": payload})
                updated = await collection.find_one({"_id": existing["_id"]})
            else:
                payload["created_at"] = datetime.utcnow()
                result = await collection.insert_one(payload)
                updated = await collection.find_one({"_id": result.inserted_id})

            event = await CMSResidentialCampController._upsert_current_event(updated, _event_fields(payload))
            merged = _merge_event_into_cms(updated, event)
            return CMSResidentialCampController._to_response(merged)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update residential camp content: {str(e)}",
            )

    @staticmethod
    async def start_new_event(fields: Optional[CampEventFields] = None) -> ResidentialCampResponse:
        try:
            db = get_db()
            collection = db[CMSResidentialCampController.COLLECTION]
            existing = await collection.find_one({}, sort=[("updated_at", -1), ("created_at", -1)])
            if not existing:
                existing = await CMSResidentialCampController._create_default()
            existing = await CMSResidentialCampController._ensure_event(existing)
            incoming = _event_fields(_dump(fields) if fields else existing)
            current = await CMSResidentialCampController._ensure_current_event(existing)
            now = datetime.utcnow()
            events = db[CMSResidentialCampController.EVENTS_COLLECTION]
            if current:
                await events.update_one(
                    {"_id": current["_id"]},
                    {"$set": {"status": "archived", "updated_at": now}},
                )
            new_id = new_camp_event_id()
            new_doc = {
                "event_id": new_id,
                **incoming,
                "status": "current",
                "created_at": now,
                "updated_at": now,
            }
            await events.insert_one(new_doc)
            merged_fields = {**incoming, "event_id": new_id}
            cms_patch = _merge_event_into_cms(existing, merged_fields)
            await collection.update_one(
                {"_id": existing["_id"]},
                {
                    "$set": {
                        "event_id": new_id,
                        **incoming,
                        "facts": cms_patch.get("facts"),
                        "schedule": cms_patch.get("schedule"),
                        "updated_at": now,
                    }
                },
            )
            updated = await collection.find_one({"_id": existing["_id"]})
            return CMSResidentialCampController._to_response(_merge_event_into_cms(updated, merged_fields))
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to start a new camp event: {str(e)}",
            )

    @staticmethod
    async def update_event(event_id: str, fields: CampEventFields) -> CampEventResponse:
        eid = (event_id or "").strip()
        if not eid:
            raise HTTPException(status_code=400, detail="Event ID is required")
        db = get_db()
        events = db[CMSResidentialCampController.EVENTS_COLLECTION]
        doc = await events.find_one({"event_id": eid})
        if not doc:
            raise HTTPException(status_code=404, detail="Camp event not found")
        incoming = _event_fields(_dump(fields))
        now = datetime.utcnow()
        await events.update_one({"_id": doc["_id"]}, {"$set": {**incoming, "updated_at": now}})
        saved = await events.find_one({"_id": doc["_id"]})
        cms = await db[CMSResidentialCampController.COLLECTION].find_one(
            {}, sort=[("updated_at", -1), ("created_at", -1)]
        )
        if cms and (cms.get("event_id") or "") == eid:
            merged = _merge_event_into_cms(cms, saved)
            await db[CMSResidentialCampController.COLLECTION].update_one(
                {"_id": cms["_id"]},
                {
                    "$set": {
                        **incoming,
                        "facts": merged.get("facts"),
                        "schedule": merged.get("schedule"),
                        "updated_at": now,
                    }
                },
            )
        return CMSResidentialCampController._to_event_response(saved)

    @staticmethod
    async def _ensure_event(doc: Dict[str, Any]) -> Dict[str, Any]:
        if doc.get("event_id") and doc.get("event_name"):
            return doc
        patch = {}
        if not doc.get("event_id"):
            patch["event_id"] = new_camp_event_id()
        if not (doc.get("event_name") or "").strip():
            patch["event_name"] = "Dussehra Special – Shaolin Kungfu Residential Camp"
        if patch:
            patch["updated_at"] = datetime.utcnow()
            db = get_db()
            collection = db[CMSResidentialCampController.COLLECTION]
            await collection.update_one({"_id": doc["_id"]}, {"$set": patch})
            return await collection.find_one({"_id": doc["_id"]})
        return doc

    @staticmethod
    async def _ensure_current_event(cms: Dict[str, Any]) -> Dict[str, Any]:
        db = get_db()
        events = db[CMSResidentialCampController.EVENTS_COLLECTION]
        event_id = (cms.get("event_id") or "").strip()
        found = None
        if event_id:
            found = await events.find_one({"event_id": event_id})
        if not found:
            found = await events.find_one({"status": "current"}, sort=[("updated_at", -1)])
        if found:
            if (found.get("status") or "") != "current":
                await events.update_one({"_id": found["_id"]}, {"$set": {"status": "current"}})
                found["status"] = "current"
            return found
        now = datetime.utcnow()
        if not event_id:
            event_id = new_camp_event_id()
            await db[CMSResidentialCampController.COLLECTION].update_one(
                {"_id": cms["_id"]},
                {"$set": {"event_id": event_id, "updated_at": now}},
            )
            cms["event_id"] = event_id
        doc = {
            "event_id": event_id,
            **_event_fields(cms),
            "status": "current",
            "created_at": now,
            "updated_at": now,
        }
        await events.insert_one(doc)
        return doc

    @staticmethod
    async def _upsert_current_event(cms: Dict[str, Any], fields: Dict[str, Any]) -> Dict[str, Any]:
        current = await CMSResidentialCampController._ensure_current_event(cms)
        now = datetime.utcnow()
        db = get_db()
        await db[CMSResidentialCampController.EVENTS_COLLECTION].update_one(
            {"_id": current["_id"]},
            {"$set": {**fields, "status": "current", "updated_at": now}},
        )
        current.update(fields)
        current["status"] = "current"
        return current

    @staticmethod
    async def _create_default() -> Dict[str, Any]:
        db = get_db()
        collection = db[CMSResidentialCampController.COLLECTION]
        now = datetime.utcnow()
        default = default_residential_camp_dict()
        default["created_at"] = now
        default["updated_at"] = now
        result = await collection.insert_one(default)
        return await collection.find_one({"_id": result.inserted_id})

    @staticmethod
    def _to_event_response(doc: Dict[str, Any]) -> CampEventResponse:
        ser = serialize_doc(doc) or {}
        fields = _event_fields(ser)
        return CampEventResponse(
            event_id=ser.get("event_id") or "",
            status=ser.get("status") or "current",
            created_at=ser.get("created_at"),
            updated_at=ser.get("updated_at"),
            **fields,
        )

    @staticmethod
    def _to_response(doc: Dict[str, Any]) -> ResidentialCampResponse:
        serialized = serialize_doc(doc) or {}
        defaults = default_residential_camp_dict()
        payload = {**defaults}
        for key in defaults:
            if key in serialized and serialized[key] is not None:
                payload[key] = serialized[key]
        for key in EVENT_FIELD_KEYS:
            if serialized.get(key) not in (None, ""):
                payload[key] = serialized[key]
        if serialized.get("event_id"):
            payload["event_id"] = serialized["event_id"]
        content = ResidentialCampContent(**payload)
        data = _dump(content)
        data["id"] = serialized.get("id") or ""
        data["created_at"] = serialized.get("created_at")
        data["updated_at"] = serialized.get("updated_at")
        return ResidentialCampResponse(**data)
