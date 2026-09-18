"""
M18-S02 Notification Sending Service.

- Unified send (template or raw body)
- Normalized notification_logs
- Scheduled outbox + cron processor
- Retry for failed deliveries
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.notification_send_models import (
    NotificationCronBody,
    NotificationDeliveryStatus,
    NotificationScheduleRequest,
    NotificationSendRequest,
)
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.notification_providers import deliver_channel, provider_info
from utils.notification_template_service import (
    enrich_notification_template,
    ensure_notification_template_indexes,
)

logger = logging.getLogger(__name__)

COL_LOGS = "notification_logs"
COL_OUTBOX = "notification_outbox"
COL_TEMPLATES = "notification_templates"

DEFAULT_MAX_ATTEMPTS = int(os.getenv("NOTIFICATION_MAX_ATTEMPTS", "3") or "3")
RETRY_BACKOFF_MINUTES = int(os.getenv("NOTIFICATION_RETRY_BACKOFF_MIN", "15") or "15")


def _cron_secret_ok(secret: str) -> bool:
    expected = (
        os.getenv("NOTIFICATION_CRON_SECRET", "").strip()
        or os.getenv("ACADEMY_EVENT_CRON_SECRET", "").strip()
        or os.getenv("REG_CHECKOUT_CRON_SECRET", "").strip()
    )
    return bool(expected) and secret == expected


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1]
        if "+" in text[10:]:
            text = text.split("+")[0]
        if len(text) == 16:
            text = text + ":00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _render(text: str, context: Optional[Dict[str, Any]]) -> str:
    out = text or ""
    for key, value in (context or {}).items():
        out = out.replace(f"{{{{{key}}}}}", str(value))
    return out


def _missing_placeholders(text: str) -> List[str]:
    return list(dict.fromkeys(re.findall(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", text or "")))


async def ensure_notification_send_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL_LOGS].create_index("id", unique=True)
        await database[COL_LOGS].create_index([("status", 1), ("created_at", -1)])
        await database[COL_LOGS].create_index([("channel", 1), ("created_at", -1)])
        await database[COL_LOGS].create_index([("recipient", 1), ("created_at", -1)])
        await database[COL_LOGS].create_index([("template_id", 1), ("created_at", -1)])
        await database[COL_LOGS].create_index([("source", 1), ("created_at", -1)])
        await database[COL_LOGS].create_index("next_retry_at")
        await database[COL_OUTBOX].create_index("id", unique=True)
        await database[COL_OUTBOX].create_index(
            [("status", 1), ("scheduled_at", 1)]
        )
        await database[COL_OUTBOX].create_index([("created_at", -1)])
    except Exception:
        logger.exception("Failed ensuring notification send indexes")
    await ensure_notification_template_indexes(database)


def enrich_notification_log(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    out = serialize_doc(doc)
    # Legacy NotificationLog shape: type/content/user_id
    if not out.get("channel") and out.get("type"):
        out["channel"] = out.get("type")
    if not out.get("message") and out.get("content"):
        out["message"] = out.get("content")
    if not out.get("recipient") and out.get("user_id"):
        out["recipient"] = out.get("user_id")
    out["status"] = (out.get("status") or "unknown").lower()
    out["channel"] = (out.get("channel") or out.get("type") or "sms").lower()
    out["attempt"] = int(out.get("attempt") or 1)
    out["max_attempts"] = int(out.get("max_attempts") or DEFAULT_MAX_ATTEMPTS)
    out["can_retry"] = out["status"] == NotificationDeliveryStatus.FAILED.value and (
        out["attempt"] < out["max_attempts"]
        or bool(out.get("force_retry_allowed", True))
    )
    return out


async def _load_template(
    db,
    *,
    template_id: Optional[str] = None,
    template_name: Optional[str] = None,
    channel: Optional[str] = None,
    branch_id: Optional[str] = None,
) -> Optional[dict]:
    from utils.notification_template_service import resolve_notification_template

    # Prefer id lookup first
    if template_id:
        doc = await db[COL_TEMPLATES].find_one({"id": template_id})
        if not doc:
            doc = await db[COL_TEMPLATES].find_one({"name": template_id})
        if doc:
            return enrich_notification_template(doc)

    if template_name:
        # M18-S03: branch override → Rock global fallback
        resolved = await resolve_notification_template(
            name=template_name,
            channel=channel or "sms",
            branch_id=branch_id,
        )
        if resolved.get("template"):
            tpl = resolved["template"]
            tpl["_resolution"] = resolved.get("resolution")
            tpl["_fallback_used"] = resolved.get("fallback_used")
            return tpl
        # legacy flat name match
        doc = await db[COL_TEMPLATES].find_one(
            {"name": template_name, "channel": channel}
            if channel
            else {"name": template_name}
        )
        if not doc and channel:
            doc = await db[COL_TEMPLATES].find_one(
                {"name": template_name, "type": channel}
            )
        if not doc:
            doc = await db[COL_TEMPLATES].find_one({"name": template_name})
        if doc:
            return enrich_notification_template(doc)
    return None


async def _resolve_message(
    db,
    *,
    template_id: Optional[str],
    template_name: Optional[str],
    channel: Optional[str],
    body: Optional[str],
    subject: Optional[str],
    context: Optional[Dict[str, Any]],
    branch_id: Optional[str] = None,
) -> Dict[str, Any]:
    tpl = await _load_template(
        db,
        template_id=template_id,
        template_name=template_name,
        channel=channel,
        branch_id=branch_id,
    )
    if tpl:
        status = (tpl.get("status") or "").lower()
        if status in ("archived", "inactive") or tpl.get("is_active") is False:
            if status == "archived":
                raise HTTPException(status_code=400, detail="Template is archived")
        ch = channel or tpl.get("channel") or "sms"
        raw_body = body if body is not None else str(tpl.get("body") or "")
        raw_subject = subject if subject is not None else tpl.get("subject")
        rendered = _render(raw_body, context)
        rendered_subject = _render(str(raw_subject or ""), context) if raw_subject else None
        return {
            "channel": ch,
            "message": rendered,
            "subject": rendered_subject,
            "template_id": tpl.get("id"),
            "template_name": tpl.get("name"),
            "dlt_template_id": tpl.get("dlt_template_id"),
            "provider_template_name": tpl.get("provider_template_name"),
            "missing_placeholders": _missing_placeholders(rendered),
            "template_status": status or ("active" if tpl.get("is_active") else "inactive"),
            "template_branch_id": tpl.get("branch_id"),
            "resolution": tpl.get("_resolution"),
            "fallback_used": tpl.get("_fallback_used"),
        }

    if not body:
        raise HTTPException(
            status_code=400,
            detail="template_id/template_name or body is required",
        )
    ch = channel or "sms"
    rendered = _render(body, context)
    return {
        "channel": ch,
        "message": rendered,
        "subject": _render(subject, context) if subject else None,
        "template_id": None,
        "template_name": template_name,
        "dlt_template_id": None,
        "provider_template_name": None,
        "missing_placeholders": _missing_placeholders(rendered),
        "template_status": None,
        "template_branch_id": None,
        "resolution": None,
        "fallback_used": False,
    }


def _new_log_doc(
    *,
    resolved: Dict[str, Any],
    recipient: str,
    status: str,
    source: str,
    context: Optional[Dict[str, Any]],
    user_id: Optional[str],
    branch_id: Optional[str],
    metadata: Optional[Dict[str, Any]],
    attempt: int = 1,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    error: Optional[str] = None,
    provider_message_id: Optional[str] = None,
    outbox_id: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    now = datetime.utcnow()
    channel = resolved["channel"]
    message = resolved["message"]
    return {
        "id": str(uuid.uuid4()),
        "channel": channel,
        "type": channel,  # legacy compat
        "template_id": resolved.get("template_id"),
        "template_name": resolved.get("template_name"),
        "recipient": recipient,
        "user_id": user_id,
        "branch_id": branch_id,
        "status": status,
        "message": message,
        "content": message,  # legacy
        "subject": resolved.get("subject"),
        "error": error,
        "provider": provider_info().get(
            "sms_provider" if channel == "sms" else "whatsapp_provider"
        ),
        "provider_message_id": provider_message_id,
        "dlt_template_id": resolved.get("dlt_template_id"),
        "provider_template_name": resolved.get("provider_template_name"),
        "attempt": attempt,
        "max_attempts": max_attempts,
        "next_retry_at": None,
        "source": source,
        "context": context or {},
        "metadata": metadata or {},
        "outbox_id": outbox_id,
        "dry_run": dry_run,
        "missing_placeholders": resolved.get("missing_placeholders") or [],
        "template_resolution": resolved.get("resolution"),
        "fallback_used": bool(resolved.get("fallback_used")),
        "created_at": now,
        "updated_at": now,
        "sent_at": now
        if status
        in (
            NotificationDeliveryStatus.SENT.value,
            NotificationDeliveryStatus.STUBBED.value,
            NotificationDeliveryStatus.DELIVERED.value,
        )
        else None,
    }


async def send_notification(
    body: NotificationSendRequest,
    *,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_notification_send_indexes(db)

    resolved = await _resolve_message(
        db,
        template_id=body.template_id,
        template_name=body.template_name,
        channel=body.channel,
        body=body.body,
        subject=body.subject,
        context=body.context,
        branch_id=body.branch_id,
    )

    if (
        resolved.get("template_status") in ("inactive", "archived")
        and not body.dry_run
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Template is {resolved.get('template_status')}",
        )

    source = body.source or "admin_send"
    if current_user and current_user.get("id") and source == "admin_send":
        meta = dict(body.metadata or {})
        meta["actor_user_id"] = current_user.get("id")
    else:
        meta = body.metadata

    if body.dry_run:
        log = _new_log_doc(
            resolved=resolved,
            recipient=body.recipient,
            status=NotificationDeliveryStatus.SKIPPED.value,
            source=source,
            context=body.context,
            user_id=body.user_id,
            branch_id=body.branch_id,
            metadata=meta,
            dry_run=True,
        )
        # Do not persist dry_run by default — return preview only
        return {
            "message": "Dry run — not sent",
            "log": enrich_notification_log(log),
            "provider": provider_info(),
        }

    status, error, provider_msg_id = await deliver_channel(
        resolved["channel"],
        body.recipient,
        resolved["message"],
        subject=resolved.get("subject"),
        dlt_template_id=resolved.get("dlt_template_id"),
        provider_template_name=resolved.get("provider_template_name"),
        dry_run=False,
    )

    outbox_id = None
    if isinstance(meta, dict):
        outbox_id = meta.get("outbox_id")

    log = _new_log_doc(
        resolved=resolved,
        recipient=body.recipient,
        status=status,
        source=source,
        context=body.context,
        user_id=body.user_id,
        branch_id=body.branch_id,
        metadata=meta,
        error=error,
        provider_message_id=provider_msg_id,
        outbox_id=outbox_id,
    )
    if status == NotificationDeliveryStatus.FAILED.value:
        log["next_retry_at"] = datetime.utcnow() + timedelta(
            minutes=max(1, RETRY_BACKOFF_MINUTES)
        )

    await db[COL_LOGS].insert_one(dict(log))
    return {
        "message": "Notification processed",
        "log": enrich_notification_log(log),
        "provider": provider_info(),
    }


async def schedule_notification(
    body: NotificationScheduleRequest,
    *,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_notification_send_indexes(db)

    when = _parse_dt(body.scheduled_at)
    if not when:
        raise HTTPException(status_code=400, detail="Invalid scheduled_at")

    # Validate template/body resolves
    resolved = await _resolve_message(
        db,
        template_id=body.template_id,
        template_name=body.template_name,
        channel=body.channel,
        body=body.body,
        subject=body.subject,
        context=body.context,
        branch_id=body.branch_id,
    )

    now = datetime.utcnow()
    outbox = {
        "id": str(uuid.uuid4()),
        "recipient": body.recipient,
        "channel": resolved["channel"],
        "template_id": resolved.get("template_id"),
        "template_name": resolved.get("template_name"),
        "body": body.body,
        "subject": body.subject,
        "context": body.context or {},
        "source": body.source or "scheduled",
        "user_id": body.user_id,
        "branch_id": body.branch_id,
        "metadata": {
            **(body.metadata or {}),
            **(
                {"actor_user_id": current_user.get("id")}
                if current_user and current_user.get("id")
                else {}
            ),
        },
        "scheduled_at": when,
        "status": NotificationDeliveryStatus.QUEUED.value,
        "attempt": 0,
        "max_attempts": body.max_attempts,
        "last_error": None,
        "log_id": None,
        "created_at": now,
        "updated_at": now,
    }
    await db[COL_OUTBOX].insert_one(dict(outbox))
    return {
        "message": "Notification scheduled",
        "outbox": serialize_doc(outbox),
    }


async def process_notification_outbox(
    *,
    now: Optional[datetime] = None,
    dry_run: bool = False,
    limit: int = 50,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database not initialized")
    await ensure_notification_send_indexes(db)

    clock = now or datetime.utcnow()
    limit = max(1, min(limit, 200))
    cursor = (
        db[COL_OUTBOX]
        .find(
            {
                "status": NotificationDeliveryStatus.QUEUED.value,
                "scheduled_at": {"$lte": clock},
            }
        )
        .sort([("scheduled_at", 1)])
        .limit(limit)
    )
    rows = await cursor.to_list(length=limit)
    sent = stubbed = failed = skipped = 0

    for row in rows:
        if dry_run:
            skipped += 1
            continue
        req = NotificationSendRequest(
            recipient=row["recipient"],
            channel=row.get("channel"),
            template_id=row.get("template_id"),
            template_name=row.get("template_name"),
            body=row.get("body"),
            subject=row.get("subject"),
            context=row.get("context") or {},
            source=row.get("source") or "scheduled",
            user_id=row.get("user_id"),
            branch_id=row.get("branch_id"),
            metadata={**(row.get("metadata") or {}), "outbox_id": row.get("id")},
            dry_run=False,
        )
        try:
            result = await send_notification(req)
            log = result.get("log") or {}
            st = log.get("status")
            await db[COL_OUTBOX].update_one(
                {"id": row["id"]},
                {
                    "$set": {
                        "status": st
                        if st
                        in (
                            NotificationDeliveryStatus.SENT.value,
                            NotificationDeliveryStatus.STUBBED.value,
                            NotificationDeliveryStatus.DELIVERED.value,
                            NotificationDeliveryStatus.FAILED.value,
                        )
                        else NotificationDeliveryStatus.SENT.value,
                        "attempt": int(row.get("attempt") or 0) + 1,
                        "log_id": log.get("id"),
                        "last_error": log.get("error"),
                        "updated_at": datetime.utcnow(),
                    }
                },
            )
            if st == "sent" or st == "delivered":
                sent += 1
            elif st == "stubbed":
                stubbed += 1
            elif st == "failed":
                failed += 1
            else:
                skipped += 1
        except Exception as exc:
            logger.exception("Outbox item %s failed", row.get("id"))
            await db[COL_OUTBOX].update_one(
                {"id": row["id"]},
                {
                    "$set": {
                        "status": NotificationDeliveryStatus.FAILED.value,
                        "last_error": str(exc)[:240],
                        "attempt": int(row.get("attempt") or 0) + 1,
                        "updated_at": datetime.utcnow(),
                    }
                },
            )
            failed += 1

    return {
        "message": "Outbox processed",
        "now": clock.isoformat(),
        "considered": len(rows),
        "sent": sent,
        "stubbed": stubbed,
        "failed": failed,
        "skipped": skipped,
        "dry_run": dry_run,
    }


async def process_notification_retries(
    *,
    now: Optional[datetime] = None,
    dry_run: bool = False,
    limit: int = 50,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database not initialized")
    await ensure_notification_send_indexes(db)
    clock = now or datetime.utcnow()
    limit = max(1, min(limit, 200))
    cursor = (
        db[COL_LOGS]
        .find(
            {
                "status": NotificationDeliveryStatus.FAILED.value,
                "next_retry_at": {"$lte": clock},
                "$expr": {"$lt": ["$attempt", "$max_attempts"]},
            }
        )
        .sort([("next_retry_at", 1)])
        .limit(limit)
    )
    rows = await cursor.to_list(length=limit)
    # Fallback if $expr unsupported / missing max_attempts on legacy
    if not rows:
        cursor2 = (
            db[COL_LOGS]
            .find(
                {
                    "status": NotificationDeliveryStatus.FAILED.value,
                    "next_retry_at": {"$lte": clock},
                }
            )
            .sort([("next_retry_at", 1)])
            .limit(limit)
        )
        rows = [
            r
            for r in await cursor2.to_list(length=limit)
            if int(r.get("attempt") or 1) < int(r.get("max_attempts") or DEFAULT_MAX_ATTEMPTS)
        ]

    retried = skipped = 0
    for row in rows:
        if dry_run:
            skipped += 1
            continue
        await retry_notification_log(str(row["id"]), force=False)
        retried += 1

    return {
        "message": "Retries processed",
        "now": clock.isoformat(),
        "considered": len(rows),
        "retried": retried,
        "skipped": skipped,
        "dry_run": dry_run,
    }


async def run_notification_cron(body: NotificationCronBody) -> Dict[str, Any]:
    if not _cron_secret_ok(body.secret):
        raise HTTPException(status_code=403, detail="Invalid cron secret")
    outbox = await process_notification_outbox(
        dry_run=body.dry_run, limit=body.limit
    )
    retries = await process_notification_retries(
        dry_run=body.dry_run, limit=body.limit
    )
    return {
        "message": "Notification cron completed",
        "outbox": outbox,
        "retries": retries,
        "provider": provider_info(),
    }


async def retry_notification_log(
    log_id: str,
    *,
    force: bool = False,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL_LOGS].find_one({"id": log_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Notification log not found")

    attempt = int(doc.get("attempt") or 1)
    max_attempts = int(doc.get("max_attempts") or DEFAULT_MAX_ATTEMPTS)
    if doc.get("status") != NotificationDeliveryStatus.FAILED.value and not force:
        raise HTTPException(status_code=400, detail="Only failed notifications can be retried")
    if attempt >= max_attempts and not force:
        raise HTTPException(status_code=400, detail="Max retry attempts reached")

    resolved = {
        "channel": doc.get("channel") or doc.get("type") or "sms",
        "message": doc.get("message") or doc.get("content") or "",
        "subject": doc.get("subject"),
        "template_id": doc.get("template_id"),
        "template_name": doc.get("template_name"),
        "dlt_template_id": doc.get("dlt_template_id"),
        "provider_template_name": doc.get("provider_template_name"),
        "missing_placeholders": doc.get("missing_placeholders") or [],
    }
    if not resolved["message"]:
        raise HTTPException(status_code=400, detail="Log has no message to resend")

    new_attempt = attempt + 1
    status, error, provider_msg_id = await deliver_channel(
        resolved["channel"],
        doc.get("recipient") or "",
        resolved["message"],
        subject=resolved.get("subject"),
        dlt_template_id=resolved.get("dlt_template_id"),
        provider_template_name=resolved.get("provider_template_name"),
    )

    now = datetime.utcnow()
    patch: Dict[str, Any] = {
        "status": status,
        "error": error,
        "provider_message_id": provider_msg_id or doc.get("provider_message_id"),
        "attempt": new_attempt,
        "updated_at": now,
        "last_retry_at": now,
        "next_retry_at": (
            now + timedelta(minutes=max(1, RETRY_BACKOFF_MINUTES) * new_attempt)
            if status == NotificationDeliveryStatus.FAILED.value
            and new_attempt < max_attempts
            else None
        ),
        "sent_at": now
        if status
        in (
            NotificationDeliveryStatus.SENT.value,
            NotificationDeliveryStatus.STUBBED.value,
            NotificationDeliveryStatus.DELIVERED.value,
        )
        else doc.get("sent_at"),
    }
    if current_user and current_user.get("id"):
        patch["retried_by"] = current_user["id"]

    await db[COL_LOGS].update_one({"id": log_id}, {"$set": patch})
    updated = await db[COL_LOGS].find_one({"id": log_id})
    return {
        "message": "Retry processed",
        "log": enrich_notification_log(updated),
    }


async def list_notification_logs(
    *,
    channel: Optional[str] = None,
    status: Optional[str] = None,
    template_id: Optional[str] = None,
    recipient: Optional[str] = None,
    source: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_notification_send_indexes(db)

    q: Dict[str, Any] = {}
    and_clauses: List[Dict[str, Any]] = []
    if channel and channel != "all":
        and_clauses.append({"$or": [{"channel": channel}, {"type": channel}]})
    if status and status != "all":
        q["status"] = status
    if template_id:
        q["template_id"] = template_id
    if recipient:
        q["recipient"] = {"$regex": recipient.strip(), "$options": "i"}
    if source and source != "all":
        q["source"] = source
    if search and search.strip():
        term = search.strip()
        and_clauses.append(
            {
                "$or": [
                    {"message": {"$regex": term, "$options": "i"}},
                    {"content": {"$regex": term, "$options": "i"}},
                    {"template_name": {"$regex": term, "$options": "i"}},
                    {"recipient": {"$regex": term, "$options": "i"}},
                    {"error": {"$regex": term, "$options": "i"}},
                ]
            }
        )
    if and_clauses:
        q["$and"] = and_clauses

    skip = max(0, skip)
    limit = max(1, min(limit, 200))
    total = await db[COL_LOGS].count_documents(q)
    rows = (
        await db[COL_LOGS]
        .find(q)
        .sort([("created_at", -1)])
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )

    # Summary counts (lightweight)
    pipeline = [
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    summary_rows = await db[COL_LOGS].aggregate(pipeline).to_list(length=50)
    by_status = {r["_id"] or "unknown": r["count"] for r in summary_rows}

    return {
        "logs": [enrich_notification_log(r) for r in rows],
        "total": total,
        "skip": skip,
        "limit": limit,
        "summary": {"by_status": by_status, "provider": provider_info()},
    }


async def get_notification_log(log_id: str) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL_LOGS].find_one({"id": log_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Notification log not found")
    return {"log": enrich_notification_log(doc)}


async def list_notification_outbox(
    *,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_notification_send_indexes(db)
    q: Dict[str, Any] = {}
    if status and status != "all":
        q["status"] = status
    skip = max(0, skip)
    limit = max(1, min(limit, 200))
    total = await db[COL_OUTBOX].count_documents(q)
    rows = (
        await db[COL_OUTBOX]
        .find(q)
        .sort([("scheduled_at", 1)])
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {
        "outbox": [serialize_doc(r) for r in rows],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


async def mark_notification_delivered(
    log_id: str,
    *,
    provider_message_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Webhook/helper to mark delivery status (additive for providers)."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL_LOGS].find_one({"id": log_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Notification log not found")
    patch = {
        "status": NotificationDeliveryStatus.DELIVERED.value,
        "updated_at": datetime.utcnow(),
        "delivered_at": datetime.utcnow(),
    }
    if provider_message_id:
        patch["provider_message_id"] = provider_message_id
    await db[COL_LOGS].update_one({"id": log_id}, {"$set": patch})
    updated = await db[COL_LOGS].find_one({"id": log_id})
    return {"log": enrich_notification_log(updated)}
