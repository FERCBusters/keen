"""UTC values for Keen's existing timestamp-without-time-zone columns.

Obtain an aware UTC value first, then deliberately remove tzinfo at the existing
naive-UTC storage boundary. This preserves comparisons, defaults and API output;
it is not a database timezone conversion. Use aware datetimes for APIs that
explicitly require them.
"""

from calendar import monthrange
from datetime import date, datetime, timedelta, timezone


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_utc_naive(value: datetime) -> datetime:
    """Convert an aware time to naive UTC; a naive time is already naive UTC."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def utc_from_timestamp_naive(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, timezone.utc).replace(tzinfo=None)


def after_inclusive_date(day: date) -> datetime:
    """The exclusive naive-UTC bound for a window that includes ``day``."""
    if day >= date.max:
        return datetime.max
    return datetime.combine(day + timedelta(days=1), datetime.min.time())


def add_months(d: date, months: int) -> date:
    """Move ``d`` by whole months, clamping the day to the target month's end."""
    month_idx = (d.month - 1) + months
    year = d.year + month_idx // 12
    month = month_idx % 12 + 1
    day = min(d.day, monthrange(year, month)[1])
    return date(year, month, day)
