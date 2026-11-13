"""
Socket.IO server for Training Chat between Students and Assigned Coaches.
Mounted onto FastAPI via socketio.ASGIApp in server.py.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import socketio

from utils.database import get_db
from utils.helpers import serialize_doc

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Socket.IO Async Server
# ---------------------------------------------------------------------------
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",  # CORS handled by FastAPI middleware; allow all here
    logger=False,
    engineio_logger=False,
    ping_timeout=60,
    ping_interval=25,
)

# In-memory maps  sid → user info
_sid_to_user: Dict[str, Dict[str, Any]] = {}

COL_MSG = "training_chat_messages"
COL_REQ = "training_requests"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


async def _get_request(request_id: str) -> Optional[dict]:
    db = get_db()
    if db is None:
        return None
    req = await db[COL_REQ].find_one({"id": request_id})
    if req:
        return serialize_doc(req)
    return None


async def _validate_token(token: str) -> Optional[dict]:
    """Decode JWT token and return payload dict or None on failure."""
    import os
    import jwt
    from pathlib import Path
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent.parent / ".env")
    secret = os.environ.get("SECRET_KEY", "student_management_secret_key_2025_secure")
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        return payload
    except Exception as e:
        logger.warning(f"[Socket] JWT validation failed: {e}")
        return None


async def _authorize_user(user_info: dict, request: dict) -> bool:
    """Check if user is allowed to join this chat room."""
    role = str(user_info.get("role", "")).lower()
    user_id = str(user_info.get("id") or user_info.get("sub") or user_info.get("user_id") or "")

    if role in ("super_admin", "superadmin"):
        return True

    if role == "coach":
        assigned = str(request.get("assigned_coach_id") or "")
        return bool(assigned and assigned == user_id)

    # student / training_client / public
    student_id = str(request.get("user_id") or "")
    if student_id and student_id == user_id:
        return True

    contact_email = str(request.get("contact_email") or "").lower().strip()
    contact_phone = str(request.get("contact_phone") or "").strip()

    caller_email = str(user_info.get("email") or "").lower().strip()
    caller_phone = str(user_info.get("phone") or user_info.get("contact_phone") or "").strip()

    if caller_email and contact_email and caller_email == contact_email:
        return True
    if caller_phone and contact_phone and caller_phone == contact_phone:
        return True

    # Look up user in db if caller info was minimal in JWT
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


async def _save_message(msg: dict) -> None:
    db = get_db()
    if db is None:
        return
    await db[COL_MSG].insert_one(msg.copy())
    # Update last message on training_requests
    await db[COL_REQ].update_one(
        {"id": msg["request_id"]},
        {
            "$set": {
                "chat_last_message": msg["content"][:120],
                "chat_last_message_at": msg["created_at"],
            },
            "$inc": {
                "chat_unread_student_count"
                if msg["sender_role"] == "coach"
                else "chat_unread_coach_count": 1
            },
        },
    )


async def _get_history(request_id: str, limit: int = 50) -> list:
    db = get_db()
    if db is None:
        return []
    cursor = (
        db[COL_MSG]
        .find({"request_id": request_id})
        .sort("created_at", -1)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    docs.reverse()
    return [serialize_doc(d) for d in docs]


# ---------------------------------------------------------------------------
# Event Handlers
# ---------------------------------------------------------------------------

@sio.event
async def connect(sid: str, environ: dict, auth: Optional[dict] = None):
    """Validate JWT on connection."""
    token = None
    if auth and isinstance(auth, dict):
        token = auth.get("token") or auth.get("access_token")

    if not token:
        logger.warning(f"[Socket] Connection rejected (no token): {sid}")
        return False  # Reject connection

    payload = await _validate_token(token)
    if not payload:
        logger.warning(f"[Socket] Connection rejected (bad token): {sid}")
        return False

    user_info = {
        "sid": sid,
        "id": payload.get("sub") or payload.get("id") or payload.get("user_id"),
        "email": payload.get("email", ""),
        "full_name": payload.get("full_name") or payload.get("name", ""),
        "role": payload.get("role", "student"),
        "phone": payload.get("phone") or payload.get("contact_phone", ""),
    }
    _sid_to_user[sid] = user_info
    logger.info(f"[Socket] Connected: {user_info.get('email')} ({sid})")


@sio.event
async def disconnect(sid: str):
    user = _sid_to_user.pop(sid, {})
    logger.info(f"[Socket] Disconnected: {user.get('email', sid)}")
    # Notify all rooms this user was in
    if user.get("id"):
        await sio.emit(
            "user_status",
            {"user_id": user["id"], "status": "offline"},
        )


@sio.event
async def join_room(sid: str, data: dict):
    """Client joins a chat room for a specific training request."""
    request_id = data.get("request_id")
    if not request_id:
        await sio.emit("error", {"message": "request_id is required"}, to=sid)
        return

    user = _sid_to_user.get(sid)
    if not user:
        await sio.emit("error", {"message": "Not authenticated"}, to=sid)
        return

    request = await _get_request(request_id)
    if not request:
        await sio.emit("error", {"message": "Training request not found"}, to=sid)
        return

    if not await _authorize_user(user, request):
        await sio.emit("error", {"message": "Not authorized for this chat room"}, to=sid)
        return

    room_id = f"training_chat_{request_id}"
    await sio.enter_room(sid, room_id)

    # Notify room members of this user coming online
    await sio.emit(
        "user_status",
        {"user_id": user["id"], "status": "online", "role": user["role"]},
        room=room_id,
    )

    # Send message history to the joining user
    history = await _get_history(request_id)
    await sio.emit("message_history", {"messages": history, "request_id": request_id}, to=sid)
    logger.info(f"[Socket] {user.get('email')} joined room {room_id}")


@sio.event
async def leave_room(sid: str, data: dict):
    request_id = data.get("request_id")
    if not request_id:
        return

    user = _sid_to_user.get(sid, {})
    room_id = f"training_chat_{request_id}"
    await sio.leave_room(sid, room_id)
    if user.get("id"):
        await sio.emit(
            "user_status",
            {"user_id": user["id"], "status": "offline", "role": user.get("role", "")},
            room=room_id,
        )


@sio.event
async def send_message(sid: str, data: dict):
    """Persist and broadcast a new chat message."""
    request_id = data.get("request_id")
    content = (data.get("content") or "").strip()

    if not request_id or not content:
        await sio.emit("error", {"message": "request_id and content are required"}, to=sid)
        return

    user = _sid_to_user.get(sid)
    if not user:
        await sio.emit("error", {"message": "Not authenticated"}, to=sid)
        return

    request = await _get_request(request_id)
    if not request:
        await sio.emit("error", {"message": "Training request not found"}, to=sid)
        return

    if not await _authorize_user(user, request):
        await sio.emit("error", {"message": "Not authorized"}, to=sid)
        return

    role = str(user.get("role", "student")).lower()
    sender_role = "coach" if role == "coach" else "student"

    # Determine recipient
    if sender_role == "student":
        recipient_id = request.get("assigned_coach_id", "")
        recipient_name = request.get("assigned_coach_name", "Coach")
    else:
        recipient_id = request.get("user_id", "")
        recipient_name = request.get("contact_name", "Student")

    msg = {
        "id": str(uuid.uuid4()),
        "request_id": request_id,
        "room_id": f"training_chat_{request_id}",
        "sender_id": user["id"],
        "sender_name": user.get("full_name") or user.get("email", ""),
        "sender_role": sender_role,
        "recipient_id": str(recipient_id),
        "recipient_name": recipient_name,
        "content": content,
        "created_at": _now_dt(),
        "is_read": False,
        "read_at": None,
    }

    await _save_message(msg)

    # Serialize for sending (convert datetime to ISO string)
    msg_out = {**msg, "created_at": msg["created_at"].isoformat()}

    room_id = f"training_chat_{request_id}"
    await sio.emit("new_message", msg_out, room=room_id)
    logger.info(f"[Socket] Message sent in {room_id} by {user.get('email')}")


@sio.event
async def typing(sid: str, data: dict):
    """Broadcast typing indicator to the room (excluding sender)."""
    request_id = data.get("request_id")
    is_typing = bool(data.get("is_typing", True))

    if not request_id:
        return

    user = _sid_to_user.get(sid, {})
    if not user:
        return

    room_id = f"training_chat_{request_id}"
    await sio.emit(
        "user_typing",
        {
            "user_id": user.get("id"),
            "user_name": user.get("full_name") or user.get("email", ""),
            "role": user.get("role", "student"),
            "is_typing": is_typing,
            "request_id": request_id,
        },
        room=room_id,
        skip_sid=sid,
    )


@sio.event
async def mark_read(sid: str, data: dict):
    """Mark all unread messages from counterpart as read."""
    request_id = data.get("request_id")
    if not request_id:
        return

    user = _sid_to_user.get(sid, {})
    if not user:
        return

    db = get_db()
    if db is None:
        return

    reader_id = user.get("id")
    role = str(user.get("role", "student")).lower()
    sender_role = "coach" if role != "coach" else "student"

    now = _now_dt()
    result = await db[COL_MSG].update_many(
        {
            "request_id": request_id,
            "sender_role": sender_role,
            "is_read": False,
        },
        {"$set": {"is_read": True, "read_at": now}},
    )

    # Reset unread counter on training_request
    counter_field = (
        "chat_unread_student_count" if role != "coach" else "chat_unread_coach_count"
    )
    await db[COL_REQ].update_one(
        {"id": request_id},
        {"$set": {counter_field: 0}},
    )

    if result.modified_count > 0:
        room_id = f"training_chat_{request_id}"
        await sio.emit(
            "messages_read",
            {"request_id": request_id, "reader_id": reader_id, "read_at": now.isoformat()},
            room=room_id,
        )

