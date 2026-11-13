"""
REST endpoints for Training Chat messages (history, mark-read, REST fallback send).
Route prefix: /api/training-requests/{requestId}/chat
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from utils.database import get_db
from utils.helpers import serialize_doc
from utils.unified_auth import get_current_user_or_superadmin, get_optional_current_user_or_superadmin

logger = logging.getLogger(__name__)

router = APIRouter()

COL_MSG = "training_chat_messages"
COL_REQ = "training_requests"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


async def _get_request(request_id: str):
    db = get_db()
    req = await db[COL_REQ].find_one({"id": request_id})
    if not req:
        raise HTTPException(status_code=404, detail="Training request not found")
    return serialize_doc(req)


async def _authorize(user: dict, request: dict) -> bool:
    role = str((user or {}).get("role", "")).lower()
    user_id = str((user or {}).get("id") or (user or {}).get("sub") or "")

    if role in ("super_admin", "superadmin"):
        return True
    if role == "coach":
        assigned = str(request.get("assigned_coach_id") or "")
        return bool(assigned and assigned == user_id)

    # student checks
    student_id = str(request.get("user_id") or "")
    if student_id and student_id == user_id:
        return True

    contact_email = str(request.get("contact_email") or "").lower().strip()
    contact_phone = str(request.get("contact_phone") or "").strip()

    caller_email = str((user or {}).get("email") or "").lower().strip()
    caller_phone = str((user or {}).get("phone") or (user or {}).get("contact_phone") or "").strip()

    if contact_email and caller_email and contact_email == caller_email:
        return True
    if contact_phone and caller_phone and contact_phone == caller_phone:
        return True

    db = get_db()
    if db is not None and user_id:
        u = await db["users"].find_one({"$or": [{"id": user_id}, {"_id": user_id}]})
        if not u:
            u = await db["accounts"].find_one({"$or": [{"id": user_id}, {"_id": user_id}]})
        if u:
            u_email = str(u.get("email") or "").lower().strip()
            u_phone = str(u.get("phone") or u.get("contact_phone") or "").strip()
            if u_email and contact_email and u_email == contact_email:
                return True
            if u_phone and contact_phone and (u_phone in contact_phone or contact_phone in u_phone):
                return True

    return False


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SendMessageBody(BaseModel):
    content: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/{request_id}/chat/messages")
async def get_chat_messages(
    request_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """Return paginated chat message history for a training request."""
    request = await _get_request(request_id)

    if current_user and not await _authorize(current_user, request):
        raise HTTPException(status_code=403, detail="Not authorized for this chat")

    db = get_db()
    total = await db[COL_MSG].count_documents({"request_id": request_id})
    cursor = (
        db[COL_MSG]
        .find({"request_id": request_id})
        .sort("created_at", 1)
        .skip(skip)
        .limit(limit)
    )
    msgs = await cursor.to_list(length=limit)

    # Coach info
    coach_info = None
    if request.get("assigned_coach_id"):
        coach_info = {
            "id": request.get("assigned_coach_id"),
            "name": request.get("assigned_coach_name"),
        }

    student_info = {
        "id": request.get("user_id"),
        "name": request.get("contact_name"),
        "email": request.get("contact_email"),
    }

    def _fmt(doc):
        d = serialize_doc(doc)
        if isinstance(d.get("created_at"), datetime):
            d["created_at"] = d["created_at"].isoformat()
        if isinstance(d.get("read_at"), datetime):
            d["read_at"] = d["read_at"].isoformat()
        return d

    return {
        "messages": [_fmt(m) for m in msgs],
        "total": total,
        "skip": skip,
        "limit": limit,
        "coach_info": coach_info,
        "student_info": student_info,
        "request_id": request_id,
    }


@router.post("/{request_id}/chat/mark-read")
async def mark_messages_read(
    request_id: str,
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """Mark all unread messages from counterpart as read for the caller."""
    request = await _get_request(request_id)

    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not await _authorize(current_user, request):
        raise HTTPException(status_code=403, detail="Not authorized")

    db = get_db()
    role = str(current_user.get("role", "student")).lower()
    # Mark messages sent BY the counterpart
    sender_role_to_mark = "coach" if role != "coach" else "student"
    now = _now_dt()
    result = await db[COL_MSG].update_many(
        {"request_id": request_id, "sender_role": sender_role_to_mark, "is_read": False},
        {"$set": {"is_read": True, "read_at": now}},
    )
    counter_field = "chat_unread_student_count" if role != "coach" else "chat_unread_coach_count"
    await db[COL_REQ].update_one({"id": request_id}, {"$set": {counter_field: 0}})
    return {"marked": result.modified_count, "request_id": request_id}


@router.post("/{request_id}/chat/messages")
async def send_message_rest(
    request_id: str,
    body: SendMessageBody,
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """REST fallback: Send a message when socket is unavailable."""
    if not body.content.strip():
        raise HTTPException(status_code=422, detail="content cannot be empty")

    request = await _get_request(request_id)

    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not await _authorize(current_user, request):
        raise HTTPException(status_code=403, detail="Not authorized")

    role = str(current_user.get("role", "student")).lower()
    sender_role = "coach" if role == "coach" else "student"
    user_id = str(current_user.get("id", ""))

    if sender_role == "student":
        recipient_id = str(request.get("assigned_coach_id") or "")
        recipient_name = request.get("assigned_coach_name") or "Coach"
    else:
        recipient_id = str(request.get("user_id") or "")
        recipient_name = request.get("contact_name") or "Student"

    now = _now_dt()
    msg = {
        "id": str(uuid.uuid4()),
        "request_id": request_id,
        "room_id": f"training_chat_{request_id}",
        "sender_id": user_id,
        "sender_name": current_user.get("full_name") or current_user.get("email", ""),
        "sender_role": sender_role,
        "recipient_id": recipient_id,
        "recipient_name": recipient_name,
        "content": body.content.strip(),
        "created_at": now,
        "is_read": False,
        "read_at": None,
    }

    db = get_db()
    await db[COL_MSG].insert_one(msg.copy())
    await db[COL_REQ].update_one(
        {"id": request_id},
        {
            "$set": {
                "chat_last_message": msg["content"][:120],
                "chat_last_message_at": now,
            },
            "$inc": {
                "chat_unread_student_count"
                if sender_role == "coach"
                else "chat_unread_coach_count": 1
            },
        },
    )

    # Also try to emit via Socket.IO if the server is running
    try:
        from utils.socket_server import sio
        msg_out = {**msg, "created_at": now.isoformat()}
        await sio.emit("new_message", msg_out, room=f"training_chat_{request_id}")
    except Exception as e:
        logger.debug(f"[ChatREST] Socket emit skipped: {e}")

    out = {**msg, "created_at": now.isoformat()}
    return out

