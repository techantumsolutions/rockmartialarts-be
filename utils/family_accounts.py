"""Family / multi-student accounts.

Login lives on `accounts`. Each child is a `users` document with the same
`account_id`. JWT `sub` remains the currently selected student so existing
"my enrollments / attendance / payments" APIs stay unchanged.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid

from fastapi import HTTPException

COL_ACCOUNTS = "accounts"


def profile_summary(user: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": user.get("id"),
        "full_name": user.get("full_name") or "",
        "first_name": user.get("first_name"),
        "last_name": user.get("last_name"),
        "relationship": user.get("relationship") or "self",
        "profile_image": user.get("profile_image") or user.get("profile_photo") or user.get("photo"),
    }


async def list_profiles(db, account_id: str) -> List[Dict[str, Any]]:
    if not account_id:
        return []
    cursor = db.users.find({"account_id": account_id, "role": "student"})
    users = await cursor.to_list(length=50)
    users.sort(key=lambda u: (u.get("created_at") or datetime.min))
    return [profile_summary(u) for u in users]


async def ensure_account_indexes(db) -> None:
    try:
        await db[COL_ACCOUNTS].create_index("id", unique=True, name="accounts_id_unique")
    except Exception:
        pass
    try:
        await db[COL_ACCOUNTS].create_index("email", unique=True, sparse=True, name="accounts_email_unique")
    except Exception:
        pass
    try:
        await db.users.create_index("account_id", sparse=True, name="users_account_id")
    except Exception:
        pass
    try:
        await db[COL_ACCOUNTS].create_index("phone", sparse=True, name="accounts_phone")
    except Exception:
        pass


async def get_account(db, account_id: str) -> Optional[Dict[str, Any]]:
    if not account_id:
        return None
    return await db[COL_ACCOUNTS].find_one({"id": account_id})


async def get_account_by_email(db, email: str) -> Optional[Dict[str, Any]]:
    if not email:
        return None
    return await db[COL_ACCOUNTS].find_one({"email": email.strip().lower()})


async def get_account_by_phone(db, phone: str) -> Optional[Dict[str, Any]]:
    """Match an account by stored phone variants (national / E.164)."""
    raw = (phone or "").strip()
    digits = "".join(c for c in raw if c.isdigit())
    if len(digits) < 10:
        return None
    national = digits[-10:]
    variants = [
        national,
        f"91{national}",
        f"+91{national}",
        f"+91-{national}",
        f"+91 {national}",
        f"0{national}",
        f"91-{national}",
        raw,
    ]
    return await db[COL_ACCOUNTS].find_one({"phone": {"$in": variants}})


async def create_account(
    db,
    *,
    email: str,
    phone: str,
    password_hash: str,
    default_student_id: str,
) -> Dict[str, Any]:
    now = datetime.utcnow()
    acc = {
        "id": str(uuid.uuid4()),
        "email": (email or "").strip().lower(),
        "phone": phone or "",
        "password": password_hash,
        "default_student_id": default_student_id,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    await db[COL_ACCOUNTS].insert_one(acc)
    return acc


async def ensure_account_for_student(db, user: Dict[str, Any]) -> Dict[str, Any]:
    """Lazy 1:1 migration: existing students get an account on first login/register."""
    if not user or not user.get("id"):
        raise HTTPException(status_code=400, detail="Invalid student for account")

    account_id = user.get("account_id")
    if account_id:
        acc = await get_account(db, account_id)
        if acc:
            return acc

    email = (user.get("email") or "").strip().lower()
    acc = await get_account_by_email(db, email) if email else None
    password_hash = user.get("password") or user.get("password_hash") or ""
    if not acc:
        acc = await create_account(
            db,
            email=email,
            phone=user.get("phone") or "",
            password_hash=password_hash,
            default_student_id=user["id"],
        )
    elif not acc.get("default_student_id"):
        await db[COL_ACCOUNTS].update_one(
            {"id": acc["id"]},
            {"$set": {"default_student_id": user["id"], "updated_at": datetime.utcnow()}},
        )
        acc["default_student_id"] = user["id"]

    await db.users.update_one(
        {"id": user["id"]},
        {
            "$set": {
                "account_id": acc["id"],
                "relationship": user.get("relationship") or "self",
                "updated_at": datetime.utcnow(),
            }
        },
    )
    user["account_id"] = acc["id"]
    if not user.get("relationship"):
        user["relationship"] = "self"
    return acc


async def pick_login_student(db, acc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    student = None
    default_id = acc.get("default_student_id")
    if default_id:
        student = await db.users.find_one({"id": default_id, "account_id": acc["id"]})
        if student and student.get("is_active") is False:
            student = None
    if not student:
        student = await db.users.find_one(
            {
                "account_id": acc["id"],
                "role": "student",
                "is_active": {"$ne": False},
            }
        )
    return student


async def sync_account_password(db, user: Dict[str, Any], password_hash: str) -> None:
    """Keep account + sibling student hashes in sync after a password reset."""
    account_id = user.get("account_id")
    acc = await get_account(db, account_id) if account_id else None
    if not acc:
        email = (user.get("email") or "").strip().lower()
        acc = await get_account_by_email(db, email) if email else None
    now = datetime.utcnow()
    if acc:
        await db[COL_ACCOUNTS].update_one(
            {"id": acc["id"]},
            {"$set": {"password": password_hash, "updated_at": now}},
        )
        await db.users.update_many(
            {"account_id": acc["id"]},
            {"$set": {"password": password_hash, "updated_at": now}},
        )
    else:
        await db.users.update_one(
            {"id": user["id"]},
            {"$set": {"password": password_hash, "updated_at": now}},
        )


def student_login_user_payload(user: Dict[str, Any]) -> Dict[str, Any]:
    branch_id = user.get("branch_id")
    if not branch_id and user.get("branch"):
        branch_id = user["branch"].get("branch_id")
    profile_img = user.get("profile_image") or user.get("profile_photo") or user.get("photo")
    return {
        "id": user["id"],
        "email": user.get("email"),
        "role": user.get("role", "student"),
        "first_name": user.get("first_name"),
        "last_name": user.get("last_name"),
        "full_name": user.get("full_name", ""),
        "date_of_birth": user.get("date_of_birth"),
        "gender": user.get("gender"),
        "branch_id": branch_id,
        "course": user.get("course"),
        "branch": user.get("branch"),
        "profile_image": profile_img,
        "account_id": user.get("account_id"),
        "relationship": user.get("relationship") or "self",
    }
