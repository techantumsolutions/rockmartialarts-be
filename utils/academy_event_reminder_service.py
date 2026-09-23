"""
M17-S06 Academy Event Reminders.

Audience: confirmed registrations with phone.
Schedule: hours-before offsets on academy_events.
Delivery: SMS via reg-checkout SMS helpers + notification_logs / reminder_logs.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.academy_event_models import (
    AcademyEventStatus,
    normalize_reminder_offsets,
)
from models.academy_event_registration_models import AcademyEventRegistrationStatus
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.indian_phone import canonical_indian_phone
from utils.reg_checkout_sms import send_registration_sms, sms_provider_expects_delivery

logger = logging.getLogger(__name__)

COL_EVENTS = "academy_events"
COL_REGS = "academy_event_registrations"
COL_LOGS = "academy_event_reminder_logs"
COL_NOTIF = "notification_logs"

# Cron lookback: catch due reminders within this window after due time
LOOKBACK_HOURS = float(os.getenv("ACADEMY_EVENT_REMINDER_LOOKBACK_HOURS", "2"))


async def ensure_academy_event_reminder_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL_LOGS].create_index("id", unique=True)
        await database[COL_LOGS].create_index(
            [("registration_id", 1), ("offset_hours", 1)], unique=True
        )
        await database[COL_LOGS].create_index([("event_id", 1), ("created_at", -1)])
        await database[COL_LOGS].create_index([("status", 1), ("created_at", -1)])
    except Exception:
        logger.exception("Failed ensuring academy event reminder indexes")


def _parse_event_start(start_at: Any) -> Optional[datetime]:
    if start_at is None:
        return None
    if isinstance(start_at, datetime):
        return start_at.replace(tzinfo=None) if start_at.tzinfo else start_at
    text = str(start_at).strip()
    if not text:
        return None
    # Support "YYYY-MM-DDTHH:MM:SS" and with Z / offset
    try:
        if text.endswith("Z"):
            text = text[:-1]
        if "+" in text[10:]:
            text = text.split("+")[0]
        if text.count("-") > 2 and "T" in text:
            # e.g. 2031-10-01T10:00:00-05:00 — strip trailing offset naively
            m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?)", text)
            if m:
                text = m.group(1)
        if len(text) == 16:
            text = text + ":00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _reminder_sms_message(
    *,
    name: str,
    event_title: str,
    start_at: datetime,
    venue: Optional[str],
) -> str:
    tpl = os.getenv("DLT_EVENT_REMINDER_MESSAGE", "").strip()
    when = start_at.strftime("%d-%m-%Y %H:%M")
    venue_bit = f" Venue: {venue}." if venue else ""
    if tpl:
        try:
            return tpl % {
                "name": name,
                "event_title": event_title,
                "when": when,
                "venue": venue or "",
            }
        except Exception:
            logger.exception("DLT_EVENT_REMINDER_MESSAGE format failed")
    return (
        f"ROCK MARTIAL ARTS ACADEMY: Hi {name}, reminder — {event_title} is on {when}."
        f"{venue_bit} See you there!"
    )


def _otp_dlt_template_id() -> Optional[str]:
    """Reuse OTP / transactional DLT template id when event-specific id not set."""
    v = (
        os.getenv("DLT_EVENT_REMINDER_TEMPLATE_ID", "").strip()
        or os.getenv("DLT_REMINDER_TEMPLATE_ID", "").strip()
        or os.getenv("DLT_TRANSACTIONAL_TEMPLATE_ID", "").strip()
        or os.getenv("DLT_OTP_TEMPLATE_ID", "").strip()
    )
    return v or None


def _allow_sms_stub() -> bool:
    return os.getenv("ACADEMY_EVENT_ALLOW_SMS_STUB", "").lower() in (
        "1",
        "true",
        "yes",
    ) or os.getenv("REG_CHECKOUT_ALLOW_SMS_STUB", "").lower() in ("1", "true", "yes")


def _cron_secret_ok(secret: str) -> bool:
    expected = (
        os.getenv("ACADEMY_EVENT_CRON_SECRET", "").strip()
        or os.getenv("REG_CHECKOUT_CRON_SECRET", "").strip()
    )
    return bool(expected) and secret == expected


async def _already_sent(db, registration_id: str, offset_hours: int) -> bool:
    doc = await db[COL_LOGS].find_one(
        {
            "registration_id": registration_id,
            "offset_hours": offset_hours,
            "status": {"$in": ["sent", "stubbed"]},
        }
    )
    return bool(doc)


async def _write_logs(
    db,
    *,
    event: dict,
    reg: dict,
    offset_hours: int,
    phone: str,
    message: str,
    status: str,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    now = datetime.utcnow()
    log = {
        "id": str(uuid.uuid4()),
        "event_id": event.get("id"),
        "event_title": event.get("title"),
        "registration_id": reg.get("id"),
        "participant_name": reg.get("participant_name"),
        "participant_phone": phone,
        "offset_hours": offset_hours,
        "channel": "sms",
        "status": status,
        "message": message,
        "error": error,
        "created_at": now,
    }
    try:
        await db[COL_LOGS].update_one(
            {
                "registration_id": reg.get("id"),
                "offset_hours": offset_hours,
            },
            {"$set": log},
            upsert=True,
        )
    except Exception:
        # Race on unique index — treat as already logged
        logger.debug("reminder log upsert race", exc_info=True)

    try:
        await db[COL_NOTIF].insert_one(
            {
                "id": str(uuid.uuid4()),
                "type": "sms",
                "channel": "sms",
                "template_name": "academy_event_reminder",
                "recipient": phone,
                "status": status,
                "event_id": event.get("id"),
                "registration_id": reg.get("id"),
                "offset_hours": offset_hours,
                "message": message,
                "error": error,
                "created_at": now,
                "source": "academy_event_reminder",
            }
        )
    except Exception:
        logger.exception("Failed writing notification_logs for event reminder")

    return log


async def _send_one(
    db,
    *,
    event: dict,
    reg: dict,
    offset_hours: int,
    start_at: datetime,
    dry_run: bool,
) -> str:
    """Returns status: sent|stubbed|failed|skipped|dry_run."""
    if await _already_sent(db, str(reg.get("id")), offset_hours):
        return "skipped"

    phone = canonical_indian_phone(reg.get("participant_phone")) or str(
        reg.get("participant_phone") or ""
    ).strip()
    if not phone:
        return "skipped"

    name = str(reg.get("participant_name") or "Participant").strip() or "Participant"
    title = str(event.get("title") or "Event").strip()
    venue = event.get("venue") or reg.get("event_venue")
    msg = _reminder_sms_message(
        name=name, event_title=title, start_at=start_at, venue=venue
    )

    if dry_run:
        return "dry_run"

    tid = _otp_dlt_template_id()
    ok, err = send_registration_sms(phone, msg, template_id=tid)
    expects = sms_provider_expects_delivery()
    if ok:
        await _write_logs(
            db,
            event=event,
            reg=reg,
            offset_hours=offset_hours,
            phone=phone,
            message=msg,
            status="sent",
        )
        return "sent"

    if _allow_sms_stub() or not expects:
        await _write_logs(
            db,
            event=event,
            reg=reg,
            offset_hours=offset_hours,
            phone=phone,
            message=msg,
            status="stubbed",
            error=err or "SMS stub / provider not configured",
        )
        return "stubbed"

    await _write_logs(
        db,
        event=event,
        reg=reg,
        offset_hours=offset_hours,
        phone=phone,
        message=msg,
        status="failed",
        error=err,
    )
    return "failed"


def _offsets_due(
    start_at: datetime, now: datetime, offsets: List[int]
) -> List[int]:
    """Offsets whose due time is in [now - lookback, now] and event not started."""
    if start_at <= now:
        return []
    lookback = timedelta(hours=max(0.25, LOOKBACK_HOURS))
    due: List[int] = []
    for h in offsets:
        due_at = start_at - timedelta(hours=h)
        if due_at <= now and (now - due_at) <= lookback:
            due.append(h)
    return due


async def process_academy_event_reminders(
    *,
    now: Optional[datetime] = None,
    dry_run: bool = False,
    event_id: Optional[str] = None,
    force_offsets: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """
    Send due SMS reminders to confirmed registrants.
    force_offsets: ignore schedule window (admin/QA) — still skips already-sent.
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database not initialized")
    await ensure_academy_event_reminder_indexes(db)

    clock = now or datetime.utcnow()
    q: Dict[str, Any] = {
        "status": AcademyEventStatus.PUBLISHED.value,
        "reminders_enabled": {"$ne": False},
    }
    if event_id:
        q["id"] = event_id

    events = await db[COL_EVENTS].find(q).to_list(length=500)
    sent = stubbed = failed = skipped = dry = 0
    processed_events = 0

    for event in events:
        start_at = _parse_event_start(event.get("start_at"))
        if not start_at:
            skipped += 1
            continue
        offsets = normalize_reminder_offsets(event.get("reminder_offsets_hours"))
        if force_offsets is not None:
            due_offsets = normalize_reminder_offsets(force_offsets)
        else:
            due_offsets = _offsets_due(start_at, clock, offsets)
        if not due_offsets:
            continue

        processed_events += 1
        regs = await db[COL_REGS].find(
            {
                "event_id": event["id"],
                "status": AcademyEventRegistrationStatus.CONFIRMED.value,
            }
        ).to_list(length=2000)

        for reg in regs:
            for h in due_offsets:
                result = await _send_one(
                    db,
                    event=event,
                    reg=reg,
                    offset_hours=h,
                    start_at=start_at,
                    dry_run=dry_run,
                )
                if result == "sent":
                    sent += 1
                elif result == "stubbed":
                    stubbed += 1
                elif result == "failed":
                    failed += 1
                elif result == "dry_run":
                    dry += 1
                else:
                    skipped += 1

    return {
        "message": "Event reminders processed",
        "now": clock.isoformat(),
        "events_considered": len(events),
        "events_due": processed_events,
        "sent": sent,
        "stubbed": stubbed,
        "failed": failed,
        "skipped": skipped,
        "dry_run_count": dry,
        "dry_run": dry_run,
    }


async def run_academy_event_reminders(
    *,
    secret: str,
    now: Optional[datetime] = None,
    dry_run: bool = False,
    event_id: Optional[str] = None,
    force_offsets: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Cron entry — requires ACADEMY_EVENT_CRON_SECRET or REG_CHECKOUT_CRON_SECRET."""
    if not _cron_secret_ok(secret):
        raise HTTPException(status_code=403, detail="Invalid cron secret")
    return await process_academy_event_reminders(
        now=now,
        dry_run=dry_run,
        event_id=event_id,
        force_offsets=force_offsets,
    )


async def list_academy_event_reminder_logs(
    *,
    event_id: Optional[str] = None,
    registration_id: Optional[str] = None,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    q: Dict[str, Any] = {}
    if event_id:
        q["event_id"] = event_id
    if registration_id:
        q["registration_id"] = registration_id
    if status:
        q["status"] = status
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
    return {
        "logs": [serialize_doc(r) for r in rows],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


async def admin_trigger_event_reminders(
    event_id: str,
    *,
    offset_hours: Optional[List[int]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Admin-triggered send for one event (bypasses cron secret; auth via route)."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    event = await db[COL_EVENTS].find_one({"id": event_id})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return await process_academy_event_reminders(
        dry_run=dry_run,
        event_id=event_id,
        force_offsets=offset_hours
        or normalize_reminder_offsets(event.get("reminder_offsets_hours")),
    )
