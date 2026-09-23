"""
M11-S01 Course syllabus service — admin CMS.

Does not modify courses.course_content.syllabus text or public uploads.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse

from models.course_syllabus_models import CourseSyllabusMetaUpdate
from utils.course_syllabus_storage import (
    read_and_validate_pdf,
    resolve_syllabus_path,
    write_syllabus_bytes,
)
from utils.database import get_db
from utils.helpers import serialize_doc

logger = logging.getLogger(__name__)

COL = "course_syllabi"


async def ensure_course_syllabus_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL].create_index("id", unique=True)
        await database[COL].create_index(
            [("course_id", 1), ("version", 1)],
            unique=True,
            name="uniq_course_syllabus_version",
        )
        await database[COL].create_index(
            [("course_id", 1)],
            unique=True,
            partialFilterExpression={"is_active": True},
            name="uniq_one_active_syllabus_per_course",
        )
        await database[COL].create_index([("course_id", 1), ("created_at", -1)])
        await database[COL].create_index("is_active")
    except Exception:
        logger.exception("Failed ensuring course_syllabi indexes")


def _actor(user: dict) -> Dict[str, Optional[str]]:
    return {
        "id": user.get("id"),
        "name": user.get("full_name") or user.get("email") or user.get("id"),
    }


async def _require_course(db, course_id: str) -> dict:
    if not course_id:
        raise HTTPException(status_code=400, detail="course_id is required")
    course = await db.courses.find_one({"id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    return course


async def _next_version(db, course_id: str) -> int:
    latest = (
        await db[COL]
        .find({"course_id": course_id})
        .sort([("version", -1)])
        .limit(1)
        .to_list(1)
    )
    if not latest:
        return 1
    return int(latest[0].get("version") or 0) + 1


async def _deactivate_others(
    db, course_id: str, *, except_id: Optional[str] = None
) -> int:
    q: Dict[str, Any] = {"course_id": course_id, "is_active": True}
    if except_id:
        q["id"] = {"$ne": except_id}
    res = await db[COL].update_many(
        q,
        {
            "$set": {
                "is_active": False,
                "updated_at": datetime.utcnow(),
            }
        },
    )
    return int(res.modified_count or 0)


def _public_row(doc: dict, course: Optional[dict] = None) -> dict:
    row = serialize_doc(doc)
    # Never expose absolute disk paths
    row.pop("absolute_path", None)
    if course:
        row["course_name"] = course.get("title") or course.get("name") or course.get("id")
    return row


async def list_syllabi(
    *,
    course_id: Optional[str] = None,
    active_only: bool = False,
    skip: int = 0,
    limit: int = 100,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_course_syllabus_indexes(db)

    q: Dict[str, Any] = {}
    if course_id:
        q["course_id"] = course_id
    if active_only:
        q["is_active"] = True

    skip = max(0, skip)
    limit = max(1, min(limit, 200))
    total = await db[COL].count_documents(q)
    rows = (
        await db[COL]
        .find(q)
        .sort([("course_id", 1), ("version", -1)])
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )

    course_ids = list({r.get("course_id") for r in rows if r.get("course_id")})
    course_map = {}
    if course_ids:
        courses = await db.courses.find({"id": {"$in": course_ids}}).to_list(
            length=len(course_ids)
        )
        course_map = {c["id"]: c for c in courses if c.get("id")}

    out = [_public_row(r, course_map.get(r.get("course_id"))) for r in rows]
    return {"syllabi": out, "total": total, "count": len(out)}


async def get_syllabus(syllabus_id: str) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": syllabus_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Syllabus not found")
    course = await db.courses.find_one({"id": doc.get("course_id")})
    return {"syllabus": _public_row(doc, course)}


async def create_syllabus(
    *,
    course_id: str,
    file: UploadFile,
    current_user: dict,
    title: Optional[str] = None,
    notes: Optional[str] = None,
    activate: bool = False,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_course_syllabus_indexes(db)

    course = await _require_course(db, course_id)
    parsed = await read_and_validate_pdf(file)
    version = await _next_version(db, course_id)
    storage_key = write_syllabus_bytes(
        course_id=course_id,
        stored_filename=parsed["stored_filename"],
        data=parsed["data"],
    )

    now = datetime.utcnow()
    actor = _actor(current_user)
    doc_id = str(uuid.uuid4())
    doc: Dict[str, Any] = {
        "id": doc_id,
        "course_id": course_id,
        "title": (title or "").strip() or f"Syllabus v{version}",
        "notes": (notes or "").strip() or None,
        "version": version,
        "is_active": False,
        "original_filename": parsed["original_filename"],
        "stored_filename": parsed["stored_filename"],
        "content_type": parsed["content_type"],
        "size_bytes": parsed["size_bytes"],
        "storage_key": storage_key,
        "sha256": parsed["sha256"],
        "effective_from": now if activate else None,
        "effective_to": None,
        "superseded_at": None,
        "superseded_by_id": None,
        "uploaded_by": actor["id"],
        "uploaded_by_name": actor["name"],
        "activated_by": None,
        "activated_at": None,
        "created_at": now,
        "updated_at": now,
    }
    await db[COL].insert_one(doc)

    if activate:
        await _deactivate_others(db, course_id, except_id=doc_id)
        await db[COL].update_one(
            {"id": doc_id},
            {
                "$set": {
                    "is_active": True,
                    "activated_by": actor["id"],
                    "activated_at": now,
                    "effective_from": now,
                    "updated_at": now,
                }
            },
        )
        doc = await db[COL].find_one({"id": doc_id})

    return {
        "message": "Syllabus uploaded",
        "syllabus": _public_row(doc, course),
    }


async def replace_syllabus(
    syllabus_id: str,
    *,
    file: UploadFile,
    current_user: dict,
    title: Optional[str] = None,
    notes: Optional[str] = None,
    activate: bool = True,
) -> Dict[str, Any]:
    """
    Upload a new version for the same course. Marks the source row as superseded.
    Keeps historical PDF on disk and in Mongo.
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_course_syllabus_indexes(db)

    old = await db[COL].find_one({"id": syllabus_id})
    if not old:
        raise HTTPException(status_code=404, detail="Syllabus not found")
    course_id = old["course_id"]
    course = await _require_course(db, course_id)

    parsed = await read_and_validate_pdf(file)
    version = await _next_version(db, course_id)
    storage_key = write_syllabus_bytes(
        course_id=course_id,
        stored_filename=parsed["stored_filename"],
        data=parsed["data"],
    )

    now = datetime.utcnow()
    actor = _actor(current_user)
    new_id = str(uuid.uuid4())
    new_doc: Dict[str, Any] = {
        "id": new_id,
        "course_id": course_id,
        "title": (title or "").strip() or f"Syllabus v{version}",
        "notes": (notes or "").strip() or old.get("notes"),
        "version": version,
        "is_active": False,
        "original_filename": parsed["original_filename"],
        "stored_filename": parsed["stored_filename"],
        "content_type": parsed["content_type"],
        "size_bytes": parsed["size_bytes"],
        "storage_key": storage_key,
        "sha256": parsed["sha256"],
        "effective_from": None,
        "effective_to": None,
        "superseded_at": None,
        "superseded_by_id": None,
        "uploaded_by": actor["id"],
        "uploaded_by_name": actor["name"],
        "activated_by": None,
        "activated_at": None,
        "created_at": now,
        "updated_at": now,
        "replaces_id": syllabus_id,
    }
    await db[COL].insert_one(new_doc)

    await db[COL].update_one(
        {"id": syllabus_id},
        {
            "$set": {
                "superseded_at": now,
                "superseded_by_id": new_id,
                "updated_at": now,
                "is_active": False,
            }
        },
    )

    if activate:
        await _deactivate_others(db, course_id, except_id=new_id)
        await db[COL].update_one(
            {"id": new_id},
            {
                "$set": {
                    "is_active": True,
                    "activated_by": actor["id"],
                    "activated_at": now,
                    "effective_from": now,
                    "updated_at": now,
                }
            },
        )

    new_doc = await db[COL].find_one({"id": new_id})
    return {
        "message": "Syllabus replaced with new version",
        "syllabus": _public_row(new_doc, course),
        "previous_id": syllabus_id,
    }


async def activate_syllabus(syllabus_id: str, *, current_user: dict) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": syllabus_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Syllabus not found")

    now = datetime.utcnow()
    actor = _actor(current_user)
    await _deactivate_others(db, doc["course_id"], except_id=syllabus_id)
    await db[COL].update_one(
        {"id": syllabus_id},
        {
            "$set": {
                "is_active": True,
                "activated_by": actor["id"],
                "activated_at": now,
                "effective_from": doc.get("effective_from") or now,
                "effective_to": None,
                "updated_at": now,
            }
        },
    )
    # Verify only one active
    active_count = await db[COL].count_documents(
        {"course_id": doc["course_id"], "is_active": True}
    )
    if active_count > 1:
        # safety net
        await _deactivate_others(db, doc["course_id"], except_id=syllabus_id)

    course = await db.courses.find_one({"id": doc["course_id"]})
    updated = await db[COL].find_one({"id": syllabus_id})
    return {
        "message": "Syllabus activated",
        "syllabus": _public_row(updated, course),
        "active_count_for_course": await db[COL].count_documents(
            {"course_id": doc["course_id"], "is_active": True}
        ),
    }


async def deactivate_syllabus(syllabus_id: str, *, current_user: dict) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": syllabus_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Syllabus not found")

    now = datetime.utcnow()
    await db[COL].update_one(
        {"id": syllabus_id},
        {
            "$set": {
                "is_active": False,
                "effective_to": now,
                "updated_at": now,
            }
        },
    )
    course = await db.courses.find_one({"id": doc["course_id"]})
    updated = await db[COL].find_one({"id": syllabus_id})
    return {
        "message": "Syllabus deactivated",
        "syllabus": _public_row(updated, course),
    }


async def update_syllabus_meta(
    syllabus_id: str,
    body: CourseSyllabusMetaUpdate,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": syllabus_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Syllabus not found")

    patch = {k: v for k, v in body.dict(exclude_unset=True).items()}
    if "title" in patch and patch["title"] is not None:
        patch["title"] = str(patch["title"]).strip() or doc.get("title")
    if "notes" in patch and patch["notes"] is not None:
        patch["notes"] = str(patch["notes"]).strip() or None
    if not patch:
        course = await db.courses.find_one({"id": doc["course_id"]})
        return {"message": "No changes", "syllabus": _public_row(doc, course)}

    patch["updated_at"] = datetime.utcnow()
    await db[COL].update_one({"id": syllabus_id}, {"$set": patch})
    updated = await db[COL].find_one({"id": syllabus_id})
    course = await db.courses.find_one({"id": doc["course_id"]})
    return {"message": "Syllabus updated", "syllabus": _public_row(updated, course)}


async def stream_syllabus_file(syllabus_id: str) -> FileResponse:
    """
    Authenticated inline PDF stream (private storage).
    M11-S03: discourage caching / attachment; no public URL.
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": syllabus_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Syllabus not found")
    path = resolve_syllabus_path(doc.get("storage_key") or "")
    # Use a generic inline name — avoid suggesting "download as ..."
    return FileResponse(
        path,
        media_type="application/pdf",
        filename="syllabus.pdf",
        content_disposition_type="inline",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, private",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "X-Robots-Tag": "noindex, nofollow",
            # Hints for intermediaries; browser save/print cannot be fully blocked
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )
