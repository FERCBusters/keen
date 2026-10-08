"""UTC values for KEEN's existing timestamp-without-time-zone columns.

Obtain an aware UTC value first, then deliberately remove tzinfo at the existing
naive-UTC storage boundary. This preserves comparisons, defaults and API output;
it is not a database timezone conversion. Use aware datetimes for APIs that
explicitly require them.
"""
from datetime import datetime, timezone


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utc_from_timestamp_naive(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, timezone.utc).replace(tzinfo=None)
