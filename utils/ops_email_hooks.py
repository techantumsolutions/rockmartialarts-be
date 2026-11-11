"""
High-level fire-and-forget ops notification hooks (email + WhatsApp + SMS).

Uses W1–W45 catalog bodies. Channels per event:
  - Customer acks / confirmations: email + WhatsApp when contact available
  - Payment / renewal / password-critical: also SMS (ops_notify.SMS_PRIORITY_CODES)
  - Staff alerts: email (SA/env list); WhatsApp only when staff phone passed

Never raises into live flows.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from utils.ops_email_notify import (
    DEFAULT_ACADEMY,
    resolve_branch_manager_email,
    resolve_partner_admin_email,
)
from utils.ops_notify import notify_ops, notify_staff_ops

logger = logging.getLogger(__name__)


def _money(v: Any) -> str:
    try:
        if v is None:
            return "0"
        return f"{float(v):.0f}" if float(v) == int(float(v)) else f"{float(v):.2f}"
    except Exception:
        return str(v or "0")


def _date(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%d %b %Y")
    return str(v)


def _phone(*candidates: Any) -> Optional[str]:
    for c in candidates:
        if c is None:
            continue
        s = str(c).strip()
        if s:
            return s
    return None


async def fire_lead_created(db, lead: dict, *, was_merged: bool = False) -> None:
    """W32 ack + W33 staff (+ W22 BM if branch known). Skip ack on merge to avoid spam."""
    try:
        if not lead:
            return
        ctx = {
            "academy_name": DEFAULT_ACADEMY,
            "lead_name": lead.get("name") or "there",
            "lead_phone": lead.get("phone") or "",
            "lead_source": lead.get("source") or lead.get("source_type") or "website",
            "branch_name": lead.get("branch_name") or "",
        }
        phone = _phone(lead.get("phone"))
        if not was_merged and (lead.get("email") or phone):
            await notify_ops(
                "rock_lead_received_ack",
                email=lead.get("email"),
                phone=phone,
                context=ctx,
            )
        extras: List[str] = []
        bm = await resolve_branch_manager_email(db, lead.get("branch_id"))
        if bm:
            extras.append(bm)
            await notify_ops(
                "rock_bm_lead_alert",
                email=bm,
                context={**ctx, "bm_name": "Branch Manager"},
                channels=["email"],
            )
        await notify_staff_ops("rock_lead_staff_alert", ctx, db=db, extra_emails=extras)
    except Exception:
        logger.exception("fire_lead_created failed")


async def fire_callback_created(db, doc: dict) -> None:
    try:
        ctx = {
            "academy_name": DEFAULT_ACADEMY,
            "lead_name": (doc or {}).get("name") or "there",
            "lead_phone": (doc or {}).get("phone") or "",
        }
        phone = _phone((doc or {}).get("phone"))
        if doc.get("email") or phone:
            await notify_ops(
                "rock_callback_request_ack",
                email=doc.get("email"),
                phone=phone,
                context=ctx,
            )
        await notify_staff_ops("rock_callback_staff_alert", ctx, db=db)
    except Exception:
        logger.exception("fire_callback_created failed")


async def fire_demo_booking(db, booking: dict, *, confirmed: bool) -> None:
    """W34/W35 — fire on free confirm or paid verify."""
    try:
        if not confirmed or not booking:
            return
        preferred = (
            booking.get("preferred_date")
            or booking.get("slot_date")
            or booking.get("demo_date")
            or ""
        )
        ctx = {
            "academy_name": DEFAULT_ACADEMY,
            "lead_name": booking.get("participant_name") or "there",
            "lead_phone": booking.get("participant_phone") or "",
            "branch_name": booking.get("branch_name") or "",
            "preferred_date": _date(preferred),
        }
        phone = _phone(booking.get("participant_phone"), booking.get("phone"))
        if booking.get("participant_email") or phone:
            await notify_ops(
                "rock_demo_request_ack",
                email=booking.get("participant_email"),
                phone=phone,
                context=ctx,
            )
        await notify_staff_ops("rock_demo_staff_alert", ctx, db=db)
    except Exception:
        logger.exception("fire_demo_booking failed")


async def fire_training_request(db, doc: dict) -> None:
    try:
        details = doc.get("details") or {}
        if isinstance(details, dict):
            detail_txt = ", ".join(f"{k}={v}" for k, v in list(details.items())[:8])
        else:
            detail_txt = str(details or "")
        ctx = {
            "academy_name": DEFAULT_ACADEMY,
            "lead_name": doc.get("contact_name") or doc.get("name") or "there",
            "lead_phone": doc.get("contact_phone") or doc.get("phone") or "",
            "request_details": detail_txt or (doc.get("type") or "training"),
        }
        email = doc.get("contact_email") or doc.get("email")
        phone = _phone(doc.get("contact_phone"), doc.get("phone"))
        if email or phone:
            await notify_ops(
                "rock_training_request_ack",
                email=email,
                phone=phone,
                context=ctx,
            )
        await notify_staff_ops("rock_training_request_staff", ctx, db=db)
    except Exception:
        logger.exception("fire_training_request failed")


async def fire_camp_registration(db, reg: dict, camp: Optional[dict] = None) -> None:
    try:
        camp = camp or {}
        ctx = {
            "academy_name": DEFAULT_ACADEMY,
            "registrant_name": reg.get("participant_name") or reg.get("name") or "there",
            "registrant_phone": reg.get("participant_phone") or reg.get("phone") or "",
            "camp_name": camp.get("name") or reg.get("camp_name") or "Camp",
            "camp_dates": _date(camp.get("start_date") or reg.get("camp_dates")),
        }
        email = reg.get("participant_email") or reg.get("email")
        phone = _phone(reg.get("participant_phone"), reg.get("phone"))
        if email or phone:
            await notify_ops(
                "rock_camp_confirm",
                email=email,
                phone=phone,
                context=ctx,
            )
        await notify_staff_ops("rock_camp_staff_alert", ctx, db=db)
    except Exception:
        logger.exception("fire_camp_registration failed")


async def fire_event_registration(db, reg: dict, event: Optional[dict] = None) -> None:
    try:
        event = event or {}
        ctx = {
            "academy_name": DEFAULT_ACADEMY,
            "registrant_name": reg.get("participant_name") or reg.get("name") or "there",
            "event_name": event.get("title") or event.get("name") or reg.get("event_name") or "Event",
            "event_date": _date(
                event.get("event_date") or event.get("start_date") or reg.get("event_date")
            ),
            "event_venue": event.get("venue") or event.get("location") or reg.get("event_venue") or "",
        }
        email = reg.get("participant_email") or reg.get("email")
        phone = _phone(
            reg.get("participant_phone"),
            reg.get("phone"),
            reg.get("mobile"),
        )
        if email or phone:
            await notify_ops(
                "rock_event_confirm",
                email=email,
                phone=phone,
                context=ctx,
            )
    except Exception:
        logger.exception("fire_event_registration failed")


async def fire_event_reminder(reg: dict, event: Optional[dict] = None) -> None:
    try:
        event = event or {}
        ctx = {
            "academy_name": DEFAULT_ACADEMY,
            "registrant_name": reg.get("participant_name") or reg.get("name") or "there",
            "event_name": event.get("title") or event.get("name") or reg.get("event_name") or "Event",
            "event_date": _date(
                event.get("start_at")
                or event.get("event_date")
                or event.get("start_date")
                or reg.get("event_date")
            ),
            "event_venue": event.get("venue") or event.get("location") or reg.get("event_venue") or "",
        }
        email = reg.get("participant_email") or reg.get("email")
        phone = _phone(
            reg.get("participant_phone"),
            reg.get("phone"),
            reg.get("mobile"),
        )
        if email or phone:
            await notify_ops(
                "rock_event_reminder",
                email=email,
                phone=phone,
                context=ctx,
            )
    except Exception:
        logger.exception("fire_event_reminder failed")


async def fire_partner_landing_lead(db, lead: dict, *, partner_branch_name: str = "") -> None:
    try:
        ctx = {
            "academy_name": DEFAULT_ACADEMY,
            "lead_name": lead.get("name") or "there",
            "lead_phone": lead.get("phone") or "",
            "partner_branch_name": partner_branch_name or lead.get("branch_name") or "Partner Branch",
        }
        phone = _phone(lead.get("phone"))
        if lead.get("email") or phone:
            await notify_ops(
                "rock_collab_partner_lead_ack",
                email=lead.get("email"),
                phone=phone,
                context=ctx,
            )
        extras: List[str] = []
        if lead.get("partner_org_id"):
            pe = await resolve_partner_admin_email(db, lead.get("partner_org_id"))
            if pe:
                extras.append(pe)
        await notify_staff_ops(
            "rock_collab_partner_lead_staff", ctx, db=db, extra_emails=extras
        )
    except Exception:
        logger.exception("fire_partner_landing_lead failed")


async def fire_payment_success(
    db,
    *,
    student_email: Optional[str],
    student_name: str,
    amount: Any,
    course_or_plan: str,
    txn_id: str = "",
    payment_date: Any = None,
    invoice_id: str = "",
    invoice_no: str = "",
    enrollment_course: str = "",
    branch_name: str = "",
    branch_id: Optional[str] = None,
    start_date: Any = None,
    partner_org_id: Optional[str] = None,
    partner_admin_name: str = "",
    student_phone: Optional[str] = None,
) -> None:
    """W6 + W8 (if invoice) + W10 + W21/W23 + W30/W31 when partner."""
    try:
        ctx_pay = {
            "academy_name": DEFAULT_ACADEMY,
            "student_name": student_name or "Student",
            "amount": _money(amount),
            "course_or_plan": course_or_plan or enrollment_course or "Course",
            "payment_date": _date(payment_date or datetime.utcnow()),
            "txn_id": txn_id or "",
            "invoice_id": invoice_id or "",
            "invoice_no": invoice_no or "",
            "course_name": enrollment_course or course_or_plan or "Course",
            "branch_name": branch_name or "",
            "start_date": _date(start_date),
        }
        phone = _phone(student_phone)
        if student_email or phone:
            await notify_ops(
                "rock_payment_success",
                email=student_email,
                phone=phone,
                context=ctx_pay,
            )
            if invoice_id or invoice_no:
                await notify_ops(
                    "rock_invoice_issued",
                    email=student_email,
                    phone=phone,
                    context=ctx_pay,
                )
            await notify_ops(
                "rock_enrollment_confirm",
                email=student_email,
                phone=phone,
                context=ctx_pay,
            )

        bm = await resolve_branch_manager_email(db, branch_id)
        if bm:
            await notify_ops(
                "rock_bm_new_registration",
                email=bm,
                context={**ctx_pay, "bm_name": "Branch Manager"},
                channels=["email"],
            )
        await notify_staff_ops("rock_sa_new_registration", ctx_pay, db=db)

        if partner_org_id:
            pe = await resolve_partner_admin_email(db, partner_org_id)
            if pe:
                pctx = {
                    **ctx_pay,
                    "partner_admin_name": partner_admin_name or "Partner Admin",
                }
                await notify_ops(
                    "rock_partner_new_student",
                    email=pe,
                    context=pctx,
                    channels=["email"],
                )
                await notify_ops(
                    "rock_partner_payment_received",
                    email=pe,
                    context=pctx,
                    channels=["email"],
                )
    except Exception:
        logger.exception("fire_payment_success failed")


async def fire_payment_failed(
    *,
    student_email: Optional[str],
    student_name: str = "Student",
    amount: Any = "",
    failure_reason: str = "Payment was not completed",
    student_phone: Optional[str] = None,
) -> None:
    try:
        phone = _phone(student_phone)
        if not student_email and not phone:
            return
        await notify_ops(
            "rock_payment_failed",
            email=student_email,
            phone=phone,
            context={
                "academy_name": DEFAULT_ACADEMY,
                "student_name": student_name or "Student",
                "amount": _money(amount) if amount != "" else "",
                "failure_reason": failure_reason or "Payment was not completed",
            },
        )
    except Exception:
        logger.exception("fire_payment_failed failed")


async def fire_family_profile_added(
    *,
    parent_email: Optional[str],
    parent_name: str,
    student_name: str,
    parent_phone: Optional[str] = None,
) -> None:
    try:
        phone = _phone(parent_phone)
        if not parent_email and not phone:
            return
        await notify_ops(
            "rock_family_profile_added",
            email=parent_email,
            phone=phone,
            context={
                "academy_name": DEFAULT_ACADEMY,
                "parent_name": parent_name or "Parent",
                "student_name": student_name or "Student",
            },
        )
    except Exception:
        logger.exception("fire_family_profile_added failed")


async def fire_student_self_welcome(
    *,
    student_email: Optional[str],
    student_name: str,
    student_phone: Optional[str] = None,
) -> None:
    try:
        phone = _phone(student_phone)
        if not student_email and not phone:
            return
        await notify_ops(
            "rock_student_welcome_self",
            email=student_email,
            phone=phone,
            context={"academy_name": DEFAULT_ACADEMY, "student_name": student_name or "Student"},
        )
    except Exception:
        logger.exception("fire_student_self_welcome failed")


async def fire_student_account_ready(
    *,
    student_email: Optional[str],
    student_name: str,
    student_phone: Optional[str] = None,
) -> None:
    try:
        phone = _phone(student_phone)
        if not student_email and not phone:
            return
        await notify_ops(
            "rock_student_account_ready",
            email=student_email,
            phone=phone,
            context={"academy_name": DEFAULT_ACADEMY, "student_name": student_name or "Student"},
        )
    except Exception:
        logger.exception("fire_student_account_ready failed")


async def fire_coach_lead_assigned(
    *,
    coach_email: Optional[str],
    coach_name: str,
    lead_name: str,
    lead_phone: str = "",
    lead_source: str = "",
    coach_phone: Optional[str] = None,
) -> None:
    try:
        phone = _phone(coach_phone)
        if not coach_email and not phone:
            return
        await notify_ops(
            "rock_coach_lead_assigned",
            email=coach_email,
            phone=phone,
            context={
                "academy_name": DEFAULT_ACADEMY,
                "coach_name": coach_name or "Coach",
                "lead_name": lead_name or "Lead",
                "lead_phone": lead_phone or "",
                "lead_source": lead_source or "",
            },
        )
    except Exception:
        logger.exception("fire_coach_lead_assigned failed")


async def fire_schedule_update_students(
    *,
    student_emails: list,
    course_name: str,
    new_schedule: str,
    student_phones: Optional[Sequence[Optional[str]]] = None,
) -> None:
    try:
        phones = list(student_phones or [])
        emails = list(student_emails or [])
        n = max(len(emails), len(phones))
        for i in range(n):
            email = emails[i] if i < len(emails) else None
            phone = phones[i] if i < len(phones) else None
            if not email and not phone:
                continue
            await notify_ops(
                "rock_course_schedule_update",
                email=email,
                phone=_phone(phone),
                context={
                    "academy_name": DEFAULT_ACADEMY,
                    "student_name": "Student",
                    "course_name": course_name or "Course",
                    "new_schedule": new_schedule or "",
                },
            )
    except Exception:
        logger.exception("fire_schedule_update_students failed")


async def fire_coach_batch_update(
    *,
    coach_emails: list,
    course_name: str,
    change_summary: str,
    coach_phones: Optional[Sequence[Optional[str]]] = None,
) -> None:
    try:
        phones = list(coach_phones or [])
        emails = list(coach_emails or [])
        n = max(len(emails), len(phones))
        for i in range(n):
            email = emails[i] if i < len(emails) else None
            phone = phones[i] if i < len(phones) else None
            if not email and not phone:
                continue
            await notify_ops(
                "rock_coach_batch_update",
                email=email,
                phone=_phone(phone),
                context={
                    "academy_name": DEFAULT_ACADEMY,
                    "coach_name": "Coach",
                    "course_name": course_name or "Course",
                    "change_summary": change_summary or "",
                },
            )
    except Exception:
        logger.exception("fire_coach_batch_update failed")


async def fire_partner_payment_success(org: dict, *, amount: Any = "", txn_id: str = "") -> None:
    try:
        email = (org or {}).get("email")
        phone = _phone((org or {}).get("phone"), (org or {}).get("contact_phone"))
        if not email and not phone:
            return
        ctx = {
            "partner_name": org.get("name") or "Partner",
            "partner_slug": org.get("slug") or "",
            "amount": _money(amount) if amount != "" else "",
            "txn_id": txn_id or org.get("razorpay_payment_id") or "",
            "academy_name": DEFAULT_ACADEMY,
        }
        await notify_ops("rock_partner_payment_success", email=email, phone=phone, context=ctx)
        await notify_ops("rock_partner_activated", email=email, phone=phone, context=ctx)
    except Exception:
        logger.exception("fire_partner_payment_success failed")


async def fire_partner_payment_failed(org: dict) -> None:
    try:
        email = (org or {}).get("email")
        phone = _phone((org or {}).get("phone"), (org or {}).get("contact_phone"))
        if not email and not phone:
            return
        await notify_ops(
            "rock_partner_payment_failed",
            email=email,
            phone=phone,
            context={
                "partner_name": org.get("name") or "Partner",
                "partner_slug": org.get("slug") or "",
                "academy_name": DEFAULT_ACADEMY,
            },
        )
    except Exception:
        logger.exception("fire_partner_payment_failed failed")


async def fire_partner_status_change(org: dict, *, is_active: bool) -> None:
    try:
        email = (org or {}).get("email")
        phone = _phone((org or {}).get("phone"), (org or {}).get("contact_phone"))
        if not email and not phone:
            return
        ctx = {
            "partner_name": org.get("name") or "Partner",
            "partner_slug": org.get("slug") or "",
            "support_contact": "support@rockmartialartsacademy.com",
            "academy_name": DEFAULT_ACADEMY,
        }
        code = "rock_partner_activated" if is_active else "rock_partner_deactivated"
        await notify_ops(code, email=email, phone=phone, context=ctx)
    except Exception:
        logger.exception("fire_partner_status_change failed")


async def fire_stock_low(
    db,
    *,
    item_name: str,
    quantity: Any,
    branch_name: str = "",
) -> None:
    try:
        ctx = {
            "academy_name": DEFAULT_ACADEMY,
            "item_name": item_name or "Item",
            "quantity": str(quantity),
            "branch_name": branch_name or "",
        }
        await notify_staff_ops("rock_stock_low_alert", ctx, db=db)
    except Exception:
        logger.exception("fire_stock_low failed")


async def fire_payment_reminder(
    *,
    student_email: Optional[str],
    student_name: str,
    amount: Any,
    due_date: Any = None,
    student_phone: Optional[str] = None,
    channels: Optional[Sequence[str]] = None,
) -> None:
    try:
        phone = _phone(student_phone)
        if not student_email and not phone:
            return
        await notify_ops(
            "rock_payment_reminder",
            email=student_email,
            phone=phone,
            context={
                "academy_name": DEFAULT_ACADEMY,
                "student_name": student_name or "Student",
                "amount": _money(amount),
                "due_date": _date(due_date),
            },
            channels=channels,
        )
    except Exception:
        logger.exception("fire_payment_reminder failed")


async def fire_renewal_reminder(
    *,
    student_email: Optional[str],
    student_name: str,
    course_name: str,
    end_date: Any = None,
    student_phone: Optional[str] = None,
    channels: Optional[Sequence[str]] = None,
) -> None:
    try:
        phone = _phone(student_phone)
        if not student_email and not phone:
            return
        await notify_ops(
            "rock_renewal_reminder",
            email=student_email,
            phone=phone,
            context={
                "academy_name": DEFAULT_ACADEMY,
                "student_name": student_name or "Student",
                "course_name": course_name or "Course",
                "end_date": _date(end_date),
            },
            channels=channels,
        )
    except Exception:
        logger.exception("fire_renewal_reminder failed")


async def fire_enrollment_expired(
    *,
    student_email: Optional[str],
    student_name: str,
    course_name: str,
    student_phone: Optional[str] = None,
) -> None:
    try:
        phone = _phone(student_phone)
        if not student_email and not phone:
            return
        await notify_ops(
            "rock_enrollment_expired",
            email=student_email,
            phone=phone,
            context={
                "academy_name": DEFAULT_ACADEMY,
                "student_name": student_name or "Student",
                "course_name": course_name or "Course",
            },
        )
    except Exception:
        logger.exception("fire_enrollment_expired failed")
