"""UTC values for Keen's existing timestamp-without-time-zone columns.

Obtain an aware UTC value first, then deliberately remove tzinfo at the existing
naive-UTC storage boundary. This preserves comparisons, defaults and API output;
it is not a database timezone conversion. Use aware datetimes for APIs that
explicitly require them.
"""

from datetime import date, datetime, timedelta, timezone


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utc_from_timestamp_naive(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, timezone.utc).replace(tzinfo=None)


def after_inclusive_date(day: date) -> datetime:
    """The exclusive naive-UTC bound for a window that includes ``day``."""
    if day >= date.max:
        return datetime.max
    return datetime.combine(day + timedelta(days=1), datetime.min.time())
