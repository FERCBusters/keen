"""Shared reading of vendor event-log APIs (1Password, Bitwarden, Cloudflare,
Mosyle).

Each response is read as it arrives, within a byte limit, and parsed only when
it answered 200. Failures carry fixed messages: never a response body, a URL,
a header or a credential.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx2 as httpx
from myf.core.datetime_utils import parse_iso_datetime
from myf.security.json_shape import decode_json
from myf.security.redaction import storable, utf8_bytes
from myf.security.regex import scan_pattern

from app.db.models import IngestionCursor
from app.ingest.common import RecordReader, is_safe_url, store_event_with_artifact

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


# The settings of a log read in time windows: default, least and most.
WINDOW_SETTINGS = {
    "initial_lookback_days": (30, 0, 367),
    "window_hours": (24, 1, 168),
    "overlap_minutes": (60, 0, 1440),
    "max_pages": (100, 1, 1000),
}


def window_setting(config: dict, name: str) -> int:
    """One of ``WINDOW_SETTINGS`` from a validated configuration, or its default."""
    return config.get(name, WINDOW_SETTINGS[name][0])


def one_stream_config(
    config: Any,
    *,
    product: str,
    stream: str,
    error: type[EventApiError],
    extra: dict[str, tuple[int, int, int]] | None = None,
) -> dict:
    """The settings of a log with one stream, read in time windows.

    ``extra`` adds the source's own whole-number settings, each with its
    default, least and most. A setting out of bounds raises ``error``.
    """
    if not isinstance(config, dict):
        raise error(f"{product} settings must be an object")
    streams = config.get("streams", [])
    if not isinstance(streams, list) or len(streams) > 1:
        raise error(f"{product} collects one stream: {stream}")
    for entry in streams:
        if not isinstance(entry, dict) or entry.get("stream") != stream:
            raise error(f"{product} collects one stream: {stream}")
        if (
            not isinstance(entry.get("label", ""), str)
            or len(entry.get("label", "")) > 128
        ):
            raise error(f"{product} label must be at most 128 characters")
    for field, (default, lo, hi) in {**WINDOW_SETTINGS, **(extra or {})}.items():
        value = config.get(field, default)
        if type(value) is not int or not lo <= value <= hi:
            raise error(f"{product} {field} must be an integer between {lo} and {hi}")
    return config


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


def read_response(
    client, method: str, url: str, *, product: str, **request
) -> tuple[dict, httpx.Headers]:
    """One JSON object from ``url`` and the headers it came with, or an
    EventApiError naming what failed."""
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
            headers = response.headers
        data = decode_json(body)
    except httpx.HTTPError:
        raise EventApiError(
            f"{product} network or TLS failure; cursor retained"
        ) from None
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
        raise EventApiError(f"{product} returned invalid JSON") from None
    if not isinstance(data, dict):
        raise EventApiError(f"{product} returned an invalid JSON object")
    return data, headers


def read_json(client, method: str, url: str, *, product: str, **request) -> dict:
    """One JSON object from ``url``, or an EventApiError naming what failed."""
    return read_response(client, method, url, product=product, **request)[0]


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
        return parse_iso_datetime(value)
    except ValueError:
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


def short(value: Any) -> str | None:
    """An upstream string for a summary: its first 200 characters."""
    found = text(value)
    return found.strip()[:200] if found else None


def canonical(payload: Any) -> bytes:
    """What is stored for a record: its JSON, with keys sorted and no spaces."""
    return utf8_bytes(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )


def identity(*parts: str) -> str:
    """A record's identity from the parts that tell it apart."""
    return hashlib.sha256(
        utf8_bytes(json.dumps(list(parts), ensure_ascii=False))
    ).hexdigest()


@dataclass
class Page:
    """One page of a window: its records, how many entries were not records,
    and the paging value of the page after it, None on the window's last."""

    items: list[dict]
    invalid: int
    following: str | None


def read_windows(
    db,
    *,
    name: str,
    config: dict,
    product: str,
    stream: str,
    now: Callable[[], datetime],
    request: Callable[[datetime, datetime, str | None], Page],
    read: Callable[[dict, datetime], dict],
) -> dict:
    """Read an event log in time windows, oldest first, into the cursor ``name``.

    ``request(since, until, paging)`` answers one page of the window that ends
    at ``until``, from ``paging`` or from the window's first page. Each window
    reaches back ``overlap_minutes`` before its start, for events the vendor
    lists late, and the store recognises an event read twice. Each page is
    stored and committed with the paging value that follows it, so a run that
    stops continues from the next page, and the cursor moves to a window's end
    only once every page of it is stored. ``read(record, collected)`` turns
    one record into what is stored. ``now`` is the source's clock.
    """
    cursor = db.query(IngestionCursor).filter_by(name=name).one_or_none()
    if cursor is None:
        cursor = IngestionCursor(name=name, meta={})
    started = now()
    if cursor.last_ts is None:
        # The first run fixes where the log is read from.
        cursor.last_ts = started - timedelta(
            days=window_setting(config, "initial_lookback_days")
        )
        db.add(cursor)
    start = cursor.last_ts
    window = timedelta(hours=window_setting(config, "window_hours"))
    overlap = timedelta(minutes=window_setting(config, "overlap_minutes"))
    # A window the last run left unfinished continues from its paging value.
    meta = cursor.meta or {}
    # No transaction is open while the vendor answers: each holds the shared
    # ingestion lease, which an evidence purge must have to itself.
    db.commit()
    until = timestamp(meta.get("until"))
    token = meta.get("token")
    if not (until and isinstance(token, str) and start < until <= started):
        until = token = None
    resumed = token is not None
    created = dropped = pages = 0
    reader = RecordReader()
    seen: set[str] = set()
    while pages < window_setting(config, "max_pages"):
        if until is None:
            if start >= started:
                break
            until = min(start + window, started)
        try:
            page = request(start - overlap, until, token)
        except EventApiError as exc:
            if exc.status != 400 or not resumed:
                raise
            # The vendor no longer accepts the saved paging value: read the
            # window again from its first page.
            token, resumed = None, False
            continue
        resumed = False
        following = page.following
        if following and (following == token or following in seen):
            raise EventApiError(f"{product} repeated a page; cursor retained")
        for event in reader.each(page.items, lambda item: read(item, started)):
            result = store_event_with_artifact(db, **event)
            created += int(not result.get("deduped", False))
        dropped += page.invalid
        pages += 1
        if following:
            seen.add(following)
            token = following
            cursor.meta = {"until": until.isoformat(), "token": token}
        else:
            # The window is complete. Its end is never later than now.
            cursor.last_ts = start = until
            cursor.meta = {}
            until = token = None
            seen = set()
        cursor.updated_at = now()
        db.add(cursor)
        db.commit()
    return {
        "stream": stream,
        "pages": pages,
        "created_events": created,
        "invalid_records": dropped + reader.invalid,
        # More remains when the page limit ended the run; the next continues.
        "more": until is not None or start < started,
        "cursor": start.isoformat() + "Z",
    }
