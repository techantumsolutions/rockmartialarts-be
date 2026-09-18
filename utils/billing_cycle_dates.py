"""Calendar month billing-date helpers (M07-S02) — month-end safe."""
from __future__ import annotations

import calendar
from datetime import date, datetime, timezone
from typing import Any, Optional, Tuple, Union


DateLike = Union[datetime, date, str, None]


def parse_to_naive_datetime(value: DateLike) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if type(value) is date:
        return datetime(value.year, value.month, value.day)
    s = str(value).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            return None


def add_calendar_months(
    dt: datetime,
    months: int,
    *,
    anchor_day: Optional[int] = None,
) -> datetime:
    """
    Add calendar months preserving anchor day-of-month when possible.

    Examples (anchor 31):
      Jan 31 + 1m → Feb 28/29
      Jan 31 + 3m → Apr 30
      Mar 31 + 1m → Apr 30
    """
    if not isinstance(dt, datetime):
        raise TypeError("dt must be datetime")
    months = int(months or 0)
    y = dt.year
    m = dt.month - 1 + months
    y += m // 12
    m = m % 12 + 1
    day = int(anchor_day) if anchor_day is not None else dt.day
    if day < 1:
        day = 1
    last = calendar.monthrange(y, m)[1]
    day = min(day, last)
    return datetime(y, m, day, dt.hour, dt.minute, dt.second, dt.microsecond)


def compute_billing_period(
    paid_at: datetime,
    duration_months: int,
    *,
    anchor_day: Optional[int] = None,
) -> Tuple[datetime, datetime, datetime, int]:
    """
    Paid-date billing cycle:
      period_start = paid_at
      period_end / next_due = paid_at + duration_months (calendar, month-end clamped)
    """
    months = max(1, int(duration_months or 1))
    anchor = anchor_day if anchor_day is not None else paid_at.day
    period_start = paid_at
    period_end = add_calendar_months(paid_at, months, anchor_day=anchor)
    next_due = period_end
    return period_start, period_end, next_due, int(anchor)


def derive_cycle_status(
    period_end: DateLike,
    *,
    now: Optional[datetime] = None,
    due_soon_days: int = 7,
    cancelled: bool = False,
) -> str:
    if cancelled:
        return "cancelled"
    end = parse_to_naive_datetime(period_end)
    if end is None:
        return "active"
    current = now or datetime.utcnow()
    if current.tzinfo:
        current = current.astimezone(timezone.utc).replace(tzinfo=None)
    end_day = datetime(end.year, end.month, end.day, 23, 59, 59, 999999)
    if current > end_day:
        return "overdue"
    delta = (end_day.date() - current.date()).days
    if delta <= max(0, int(due_soon_days)):
        return "due_soon"
    return "active"
