"""
M09-S02 student ↔ vendor biometric mapping helpers.

Uses users.biometric_id / users.essl_user_id (existing fields).
Provides dedicated set/clear with friendly duplicate (409) checks.
Does not change enrollment or payment data.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from utils.database import get_db
from utils.helpers import serialize_doc
from utils.student_status_service import (
    assert_can_manage_student_status,
    get_managed_branch_ids_for_user,
)

logger = logging.getLogger(__name__)


class StudentBiometricMappingBody(BaseModel):
    """Create/update mapping. Empty string clears that field; omit to leave unchanged on partial update is not used — PUT replaces."""

    biometric_id: Optional[str] = Field(
        default=None,
        max_length=128,
        description="External vendor/device user id (primary)",
    )
    essl_user_id: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Optional alias; defaults to biometric_id when omitted on set",
    )


def _clean_id(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _student_name(student: dict) -> str:
    return (
        (student.get("full_name") or "").strip()
        or f"{student.get('first_name', '')} {student.get('last_name', '')}".strip()
        or student.get("email")
        or student.get("id")
        or "Student"
    )


async def _load_student(db, student_id: str) -> dict:
    student = await db.users.find_one({"id": student_id})
    if not student or str(student.get("role") or "").lower() != "student":
        raise HTTPException(status_code=404, detail="Student not found")
    return student


async def _find_duplicate(
    db,
    *,
    field: str,
    value: str,
    exclude_student_id: str,
) -> Optional[dict]:
    if not value:
        return None
    return await db.users.find_one(
        {
            "role": "student",
            field: value,
            "id": {"$ne": exclude_student_id},
        },
        {"id": 1, "full_name": 1, "first_name": 1, "last_name": 1, "email": 1, "biometric_id": 1, "essl_user_id": 1},
    )


async def _raise_if_duplicate(db, student_id: str, biometric_id: Optional[str], essl_user_id: Optional[str]) -> None:
    checks = []
    if biometric_id:
        checks.append(("biometric_id", biometric_id))
        checks.append(("essl_user_id", biometric_id))  # prevent conflict with other's essl
    if essl_user_id and essl_user_id != biometric_id:
        checks.append(("essl_user_id", essl_user_id))
        checks.append(("biometric_id", essl_user_id))

    seen = set()
    for field, value in checks:
        key = (field, value)
        if key in seen:
            continue
        seen.add(key)
        other = await _find_duplicate(db, field=field, value=value, exclude_student_id=student_id)
        if other:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Biometric ID '{value}' is already mapped to "
                    f"{_student_name(other)} ({other.get('id')})."
                ),
            )


def mapping_payload(student: dict) -> Dict[str, Any]:
    bio = student.get("biometric_id")
    essl = student.get("essl_user_id")
    return {
        "student_id": student.get("id"),
        "full_name": _student_name(student),
        "email": student.get("email"),
        "phone": student.get("phone"),
        "is_active": bool(student.get("is_active", True)),
        "branch_id": student.get("branch_id"),
        "biometric_id": bio,
        "essl_user_id": essl,
        "is_mapped": bool(bio or essl),
    }


async def get_mapping(student_id: str, current_user: dict) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    student = await _load_student(db, student_id)
    await assert_can_manage_student_status(db, student, current_user)
    return {"mapping": mapping_payload(student), "message": "OK"}


async def set_mapping(
    student_id: str,
    body: StudentBiometricMappingBody,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    student = await _load_student(db, student_id)
    await assert_can_manage_student_status(db, student, current_user)

    biometric_id = _clean_id(body.biometric_id)
    essl_user_id = _clean_id(body.essl_user_id)

    # If only biometric_id provided, mirror to essl (same as user create)
    if biometric_id and not essl_user_id:
        essl_user_id = biometric_id
    # If only essl provided, mirror to biometric
    if essl_user_id and not biometric_id:
        biometric_id = essl_user_id

    if not biometric_id and not essl_user_id:
        raise HTTPException(
            status_code=400,
            detail="Provide biometric_id (or essl_user_id). Use DELETE to clear mapping.",
        )

    await _raise_if_duplicate(db, student_id, biometric_id, essl_user_id)

    now = datetime.utcnow()
    await db.users.update_one(
        {"id": student_id},
        {
            "$set": {
                "biometric_id": biometric_id,
                "essl_user_id": essl_user_id,
                "updated_at": now,
            }
        },
    )
    refreshed = await db.users.find_one({"id": student_id})
    return {
        "mapping": mapping_payload(refreshed),
        "message": "Biometric mapping saved",
    }


async def clear_mapping(student_id: str, current_user: dict) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    student = await _load_student(db, student_id)
    await assert_can_manage_student_status(db, student, current_user)

    now = datetime.utcnow()
    await db.users.update_one(
        {"id": student_id},
        {
            "$unset": {"biometric_id": "", "essl_user_id": ""},
            "$set": {"updated_at": now},
        },
    )
    refreshed = await db.users.find_one({"id": student_id})
    return {
        "mapping": mapping_payload(refreshed),
        "message": "Biometric mapping cleared",
    }


async def list_mappings(
    current_user: dict,
    *,
    q: Optional[str] = None,
    branch_id: Optional[str] = None,
    mapped: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> Dict[str, Any]:
    """
    List students with biometric mapping status for Admin/BM UI.
    mapped: all | yes | no
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    role = str((current_user or {}).get("role") or "").lower()
    clauses: List[Dict[str, Any]] = [{"role": "student"}]

    if role == "branch_manager":
        managed = await get_managed_branch_ids_for_user(db, current_user)
        if not managed:
            return {"students": [], "total": 0, "count": 0, "message": "No branches assigned"}
        if branch_id and branch_id not in managed:
            raise HTTPException(
                status_code=403,
                detail="You cannot list biometric mappings for another branch.",
            )
        scope_branches = [branch_id] if branch_id else managed
        scoped_ids = await db.enrollments.distinct("student_id", {"branch_id": {"$in": scope_branches}})
        direct = await db.users.distinct(
            "id",
            {"role": "student", "branch_id": {"$in": scope_branches}},
        )
        allowed = list({*(scoped_ids or []), *(direct or [])})
        if not allowed:
            return {"students": [], "total": 0, "count": 0, "message": "No students in scope"}
        clauses.append({"id": {"$in": allowed}})
    elif branch_id:
        scoped_ids = await db.enrollments.distinct("student_id", {"branch_id": branch_id})
        direct = await db.users.distinct("id", {"role": "student", "branch_id": branch_id})
        allowed = list({*(scoped_ids or []), *(direct or [])})
        clauses.append({"id": {"$in": allowed or ["__none__"]}})

    mapped_norm = (mapped or "all").strip().lower()
    if mapped_norm in {"yes", "true", "mapped", "1"}:
        clauses.append(
            {
                "$or": [
                    {"biometric_id": {"$exists": True, "$nin": [None, ""]}},
                    {"essl_user_id": {"$exists": True, "$nin": [None, ""]}},
                ]
            }
        )
    elif mapped_norm in {"no", "false", "unmapped", "0"}:
        clauses.append(
            {
                "$and": [
                    {
                        "$or": [
                            {"biometric_id": {"$exists": False}},
                            {"biometric_id": None},
                            {"biometric_id": ""},
                        ]
                    },
                    {
                        "$or": [
                            {"essl_user_id": {"$exists": False}},
                            {"essl_user_id": None},
                            {"essl_user_id": ""},
                        ]
                    },
                ]
            }
        )

    if q and len(str(q).strip()) >= 2:
        pattern = {"$regex": re.escape(str(q).strip()), "$options": "i"}
        clauses.append(
            {
                "$or": [
                    {"full_name": pattern},
                    {"first_name": pattern},
                    {"last_name": pattern},
                    {"email": pattern},
                    {"phone": pattern},
                    {"biometric_id": pattern},
                    {"essl_user_id": pattern},
                    {"id": pattern},
                ]
            }
        )

    query: Dict[str, Any] = {"$and": clauses} if len(clauses) > 1 else clauses[0]
    skip = max(0, int(skip or 0))
    limit = max(1, min(int(limit or 100), 500))
    total = await db.users.count_documents(query)
    cursor = db.users.find(query).sort("full_name", 1).skip(skip).limit(limit)
    rows = await cursor.to_list(length=limit)

    students = []
    for row in rows:
        item = mapping_payload(row)
        bid = row.get("branch_id")
        if not bid:
            enr = await db.enrollments.find_one(
                {"student_id": row.get("id")},
                sort=[("created_at", -1)],
            )
            bid = (enr or {}).get("branch_id")
        if bid:
            branch = await db.branches.find_one({"id": bid})
            item["branch_id"] = bid
            item["branch_name"] = (
                ((branch or {}).get("branch") or {}).get("name")
                or (branch or {}).get("name")
                or ""
            )
        students.append(item)

    return {
        "students": serialize_doc(students),
        "total": total,
        "count": len(students),
        "skip": skip,
        "limit": limit,
        "message": f"Found {total} student(s)",
    }
