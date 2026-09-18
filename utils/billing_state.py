"""
M07-S03 enrollment expiry, 10-day grace, and overdue-day helpers.

Does not change access-control / route-guard behavior — used for renewal quotes
and display. Grace length is configurable via ENROLLMENT_GRACE_DAYS (default 10).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Union

from utils.subscription_dates import subscription_end_of_day_utc

DateLike = Union[datetime, str, None]


def grace_days_configured() -> int:
    try:
        return max(0, int(os.getenv("ENROLLMENT_GRACE_DAYS", "10") or "10"))
    except (TypeError, ValueError):
        return 10


def _now_utc(now: Optional[datetime] = None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def compute_enrollment_billing_state(
    end_date: DateLike,
    *,
    now: Optional[datetime] = None,
    grace_days: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Returns a structured expiry / grace / overdue snapshot.

    States:
      - active: still within validity (end day not over)
      - grace: expired but within grace window (days 1..grace inclusive after expiry day)
      - overdue: past grace window
      - unknown: no end date
    """
    grace = grace_days if grace_days is not None else grace_days_configured()
    current = _now_utc(now)
    end_eod = subscription_end_of_day_utc(end_date)

    if end_eod is None:
        return {
            "billing_state": "unknown",
            "is_expired": False,
            "is_within_grace": False,
            "overdue_days": 0,
            "grace_days_total": grace,
            "grace_days_remaining": None,
            "days_until_expiry": None,
            "expiry_at": None,
            "grace_ends_at": None,
        }

    expired = current > end_eod
    grace_ends = end_eod + timedelta(days=grace)
    # Whole calendar days after expiry day
    overdue_days = 0
    if expired:
        delta = current.date() - end_eod.date()
        overdue_days = max(0, delta.days)

    within_grace = bool(expired and current <= grace_ends)
    if not expired:
        state = "active"
        days_until = (end_eod.date() - current.date()).days
        grace_remaining = None
    elif within_grace:
        state = "grace"
        days_until = 0
        grace_remaining = max(0, (grace_ends.date() - current.date()).days)
    else:
        state = "overdue"
        days_until = 0
        grace_remaining = 0

    return {
        "billing_state": state,
        "is_expired": expired,
        "is_within_grace": within_grace,
        "overdue_days": overdue_days,
        "grace_days_total": grace,
        "grace_days_remaining": grace_remaining,
        "days_until_expiry": days_until if not expired else 0,
        "expiry_at": end_eod,
        "grace_ends_at": grace_ends,
    }


def calculate_arrear_amount(
    *,
    overdue_days: int,
    course_fee: float,
    duration_months: int = 1,
    billing_state: str = "active",
) -> Dict[str, Any]:
    """
    M07-S03-T04 — arrear formula pending client confirmation.

    Until approved, always returns 0 and marks the rule as pending so quotes
    remain safe and transparent.
    """
    _ = (overdue_days, course_fee, duration_months, billing_state)
    return {
        "arrear_amount": 0.0,
        "arrear_applied": False,
        "arrear_rule": "pending_client_confirmation",
        "arrear_note": (
            "Arrear calculation is pending client-approved business formula. "
            "Renewal quote currently charges the normal course fee only."
        ),
    }
