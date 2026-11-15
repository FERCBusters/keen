"""Shared reading of vendor event-log APIs (1Password, Bitwarden).

Each response is read as it arrives, within a byte limit, and parsed only when
it answered 200. Failures carry fixed messages: never a response body, a URL,
a header or a credential.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import httpx2 as httpx

from app.core.datetime_utils import to_utc_naive
from app.ingest.common import is_safe_url
from app.security.redaction import storable
from app.security.regex import scan_pattern

MAX_JSON_BYTES = 8 * 1024 * 1024
# An opaque paging value is stored with its cursor and sent back upstream, so
# it is printable ASCII of at most 4,096 characters.
_LABEL = scan_pattern(r"[A-Za-z0-9_.-]{1,64}")
_PAGING_TOKEN = scan_pattern(r"[\x21-\x7e]{1,4096}")


class EventApiError(ValueError):
    """Operator-safe message: never contains credentials or response bodies."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def safe_base(value: Any) -> str | None:
    """The endpoint without its trailing slash, or None when it is unsafe.

    An endpoint is HTTPS, names no credentials, query or fragment and resolves
    to an address collection may reach.
    """
    base = str(value or "").strip().rstrip("/")
    try:
        parsed = urlsplit(base)
    except ValueError:
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    return base if is_safe_url(base) else None


def read_json(client, method: str, url: str, *, product: str, **request) -> dict:
    """One JSON object from ``url``, or an EventApiError naming what failed."""
    try:
        with client.stream(method, url, **request) as response:
            status = response.status_code
            if status == 429:
                raise EventApiError(
                    f"{product} rate limit reached; the next run continues from the cursor",
                    status,
                )
            if status != 200:
                raise EventApiError(
                    f"{product} API HTTP {status}; check the credentials, their permissions and the endpoint",
                    status,
                )
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_JSON_BYTES:
                    raise EventApiError(f"{product} response exceeds the 8 MiB limit")
        data = json.loads(body)
    except httpx.HTTPError:
        raise EventApiError(
            f"{product} network or TLS failure; cursor retained"
        ) from None
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
        raise EventApiError(f"{product} returned invalid JSON") from None
    if not isinstance(data, dict):
        raise EventApiError(f"{product} returned an invalid JSON object")
    return data


def records(rows: Any, limit: int, product: str) -> tuple[list[dict], int]:
    """A page's records made storable, and how many entries were not records.

    A page holding more than ``limit`` entries is refused. An entry that is
    not an object is left out and counted, so it cannot hold up the records
    around it.
    """
    if not isinstance(rows, list):
        raise EventApiError(f"{product} returned an invalid page of events")
    if len(rows) > limit:
        raise EventApiError(f"{product} returned more events than a page holds")
    kept = [storable(row) for row in rows if isinstance(row, dict)]
    return kept, len(rows) - len(kept)


def paging_token(value: Any, product: str) -> str | None:
    """An upstream paging value fit to store and send back, or None if absent."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not _PAGING_TOKEN.fullmatch(value):
        raise EventApiError(f"{product} returned an invalid paging value")
    return value


def timestamp(value: Any) -> datetime | None:
    """A naive UTC time from an RFC 3339 value, or None when it holds none."""
    if not isinstance(value, str) or not 0 < len(value) <= 64:
        return None
    try:
        return to_utc_naive(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (ValueError, OverflowError):
        return None


def label(value: Any, default: str = "unknown") -> str:
    """An upstream code as part of an action name, or ``default``."""
    return value if isinstance(value, str) and _LABEL.fullmatch(value) else default


def text(value: Any) -> str | None:
    """A non-empty upstream string, or None."""
    return value if isinstance(value, str) and value.strip() else None


def member(record: dict, name: str) -> dict:
    """A nested object of ``record``, or an empty one."""
    value = record.get(name)
    return value if isinstance(value, dict) else {}
