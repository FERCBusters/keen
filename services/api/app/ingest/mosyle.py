"""Mosyle Business administrator action logs.

Read-only. Each run signs in to the Mosyle API and reads the log in time
windows, oldest first. Each page of a window is stored and committed with the
page that follows it, and the cursor moves to a window's end only once every
page of it is stored. Mosyle gives a log no identifier, so its content tells
it apart and a log read again is stored once.
"""

from __future__ import annotations

import hashlib
from calendar import timegm
from datetime import datetime, timedelta
from typing import Any

import httpx2 as httpx
from mosp.core.datetime_utils import utc_now_naive
from mosp.core.email_address import single_mailbox
from mosp.security.errors import collection_error
from mosp.security.regex import scan_pattern

from app.db.models import IngestionCursor
from app.ingest.common import RecordReader, store_event_with_artifact
from app.ingest.connections import connection_runs, load_document, namespace, settings
from app.ingest.event_api import (
    EventApiError,
    canonical,
    one_stream_config,
    read_json,
    read_response,
    records,
    short,
    timestamp,
    window_setting,
)
from app.ingest.http import client as ingestion_client
from app.ingest.http import no_cookies
from app.ingest.registry import HTTP_QUEUE, Source, register
from app.services.ingestion_pause import pausable

PRODUCT = "Mosyle"
# The API is served only by Mosyle; the administrator's password, the access
# token and the token a sign-in returns are sent nowhere else.
API = "https://businessapi.mosyle.com/v1"
STREAM = "adminlogs"
# Mosyle chooses the page size; a page holding more than this is refused.
PAGE_ROWS = 5000
# A window with more pages than this is refused.
MAX_WINDOW_PAGES = 1000

_ACCESS_TOKEN = scan_pattern(r"[\x21-\x7e]{1,512}")
_PASSWORD = scan_pattern(r"[^\x00-\x1f\x7f]{1,1024}")
# The Authorization header a sign-in answers with.
_BEARER = scan_pattern(r"Bearer ([A-Za-z0-9._~+/=-]{1,16384})")
_DIGITS = scan_pattern(r"[0-9]{1,12}")
_WORD = scan_pattern(r"[a-z0-9]+")


class MosyleError(EventApiError):
    """Operator-safe message: never contains credentials or response bodies."""


def validate_config(config):
    return one_stream_config(config, product=PRODUCT, stream=STREAM, error=MosyleError)


class _Session:
    """The Mosyle API in one run: its client, access token and sign-in.

    The password is sent to the sign-in alone, in the request body. The bearer
    token it returns is kept for this run only. Neither it nor the access
    token is a default header of the client.
    """

    def __init__(self, client, access_token, email, password):
        self.client = client
        self.access_token = access_token
        self.login = {"email": email, "password": password}
        self.headers: dict[str, str] | None = None
        self.renewed = False

    def _sign_in(self):
        _, headers = read_response(
            self.client,
            "POST",
            API + "/login",
            product="Mosyle sign-in",
            headers={"accessToken": self.access_token},
            json=self.login,
        )
        bearer = _BEARER.fullmatch(headers.get("authorization", ""))
        if bearer is None:
            raise MosyleError("Mosyle sign-in returned no usable bearer token")
        self.headers = {
            "accessToken": self.access_token,
            "Authorization": "Bearer " + bearer[1],
        }

    def _post(self, path, body):
        return read_json(
            self.client,
            "POST",
            API + path,
            product=PRODUCT,
            headers=self.headers,
            json=body,
        )

    def page(self, body) -> dict:
        """The ``response`` object of Mosyle's answer to one request for logs."""
        if self.headers is None:
            self._sign_in()
        try:
            data = self._post("/" + STREAM, body)
        except EventApiError as exc:
            if exc.status != 401 or self.renewed:
                raise
            # Mosyle no longer accepts the bearer token: sign in once more.
            self.renewed = True
            self._sign_in()
            data = self._post("/" + STREAM, body)
        if data.get("status") != "OK":
            raise MosyleError(
                "Mosyle refused the request; check the API profile and its permissions"
            )
        listing = data.get("response")
        # An answer in another shape is refused, so it never counts as an
        # empty page.
        if not isinstance(listing, dict) or not isinstance(listing.get("logs"), list):
            raise MosyleError("Mosyle returned no listing of logs; cursor retained")
        return listing


def _count(value) -> int | None:
    """A count Mosyle writes as a number or as its digits, or None."""
    if type(value) is int:
        value = str(value)
    if isinstance(value, str) and _DIGITS.fullmatch(value):
        return int(value)
    return None


def _last(listing, number) -> bool:
    """Whether a page of logs is the last of its window.

    Mosyle states a page size and a count of logs. A page that holds fewer
    logs than the page size, and whose count reaches no further page, is the
    last. A full page states nothing, and nor does a count or a page size that
    cannot be read: the next page is asked for, and a page without logs ends
    the window.
    """
    logs = listing["logs"]
    if not logs:
        return True
    size, count = listing.get("page_size"), _count(listing.get("rows"))
    return (
        type(size) is int
        and count is not None
        and len(logs) < size
        and count <= number * size
    )


def _slug(value) -> str:
    """Mosyle's name for an action as part of an action name."""
    words = _WORD.findall(value[:64].lower()) if isinstance(value, str) else []
    return "_".join(words) or "unknown"


def _read(entry, item, collected):
    """What is stored for one log."""
    actor = short(item.get("username"))
    summary = "Mosyle admin: " + (short(item.get("action")) or "action")
    payload = {"stream": STREAM, "event": item}
    content = canonical(payload)
    digest = hashlib.sha256(API.encode() + b"\n" + content).hexdigest()
    return dict(
        # Mosyle writes a log's time without a time zone; it is read as UTC.
        # A log without a readable time is kept, dated when it was collected.
        timestamp=timestamp(item.get("action_date")) or collected,
        source="mosyle",
        system=entry.get("label") or PRODUCT,
        actor=actor,
        action="admin." + _slug(item.get("action")),
        outcome="info",
        severity=3,
        summary=summary + (f" by {actor}" if actor else ""),
        raw_pointer={"mosyle": {"section": "streams", "stream": STREAM}},
        normalized_payload=payload,
        external_id=digest,
        artifact_kind="mosyle_" + STREAM,
        artifact_bytes=content,
        artifact_content_type="application/json",
        artifact_key=f"mosyle/{STREAM}/{digest}.json",
        captured_by="keen:mosyle",
    )


def _epoch(when: datetime) -> str:
    return str(timegm(when.timetuple()))


def _collect(db, session, entry, config):
    """Read administrator action logs in time windows, oldest first."""
    digest = hashlib.sha256(f"{API}:{STREAM}".encode()).hexdigest()
    name = namespace("mosyle:" + digest)
    cursor = db.query(IngestionCursor).filter_by(name=name).one_or_none()
    if cursor is None:
        cursor = IngestionCursor(name=name, meta={})
    started = utc_now_naive().replace(microsecond=0)
    if cursor.last_ts is None:
        # The first run fixes where the log is read from.
        cursor.last_ts = started - timedelta(
            days=window_setting(config, "initial_lookback_days")
        )
        db.add(cursor)
    start = cursor.last_ts
    window = timedelta(hours=window_setting(config, "window_hours"))
    # Each window reaches back this far, for logs Mosyle lists late.
    overlap = timedelta(minutes=window_setting(config, "overlap_minutes"))
    # A window the last run left unfinished continues from its next page.
    meta = cursor.meta or {}
    until = timestamp(meta.get("until"))
    saved = meta.get("page")
    number = saved if type(saved) is int and 1 < saved <= MAX_WINDOW_PAGES else 1
    if not (until and number > 1 and start < until <= min(start + window, started)):
        until = None
    # No transaction is open while Mosyle answers: each holds the shared
    # ingestion lease, which an evidence purge must have to itself.
    db.commit()
    created = dropped = pages = 0
    reader = RecordReader()
    while pages < window_setting(config, "max_pages"):
        if until is None:
            if start >= started:
                break
            until, number = min(start + window, started), 1
        listing = session.page(
            {
                "page": number,
                "filter_options": {
                    "start_date": _epoch(start - overlap),
                    "end_date": _epoch(until),
                },
            }
        )
        items, invalid = records(listing["logs"], PAGE_ROWS, PRODUCT)
        for event in reader.each(items, lambda item: _read(entry, item, started)):
            result = store_event_with_artifact(db, **event)
            created += int(not result.get("deduped", False))
        dropped += invalid
        pages += 1
        if _last(listing, number):
            # The window is complete. Its end is never later than now.
            cursor.last_ts = start = until
            cursor.meta = {}
            until = None
        elif number >= MAX_WINDOW_PAGES:
            raise MosyleError(
                "Mosyle lists more pages than a window holds; lower "
                "window_hours. Cursor retained"
            )
        else:
            number += 1
            cursor.meta = {"until": until.isoformat(), "page": number}
        cursor.updated_at = utc_now_naive()
        db.add(cursor)
        db.commit()
    return {
        "stream": STREAM,
        "pages": pages,
        "created_events": created,
        "invalid_records": dropped + reader.invalid,
        # More remains when the page limit ended the run; the next continues.
        "more": until is not None or start < started,
        "cursor": start.isoformat() + "Z",
    }


@pausable("mosyle")
@connection_runs("mosyle")
def ingest_mosyle_all(db):
    if settings.demo_mode or not settings.mosyle_enabled:
        return [{"skipped": True, "reason": "Mosyle disabled (or demo mode)"}]
    token = str(settings.mosyle_access_token or "").strip()
    email = single_mailbox(settings.mosyle_email)
    password = str(settings.mosyle_password or "")
    if (
        not _ACCESS_TOKEN.fullmatch(token)
        or email is None
        or not _PASSWORD.fullmatch(password)
    ):
        return [
            {
                "error": "Set the Mosyle API access token and the email address "
                "and password of the administrator it signs in as"
            }
        ]
    try:
        config = validate_config(load_document("mosyle", settings.mosyle_config_path))
    except (ValueError, OSError):
        return [{"error": "Invalid Mosyle configuration; check streams"}]
    if not config.get("streams"):
        return [{"skipped": True, "reason": "Add the Mosyle adminlogs stream"}]
    results: list[dict[str, Any]] = []
    # No credential is a default header: each request names its own. Nor is a
    # cookie kept: Mosyle's sign-in sets one for every Mosyle host.
    with ingestion_client(
        timeout=httpx.Timeout(30, connect=10),
        verify=True,
        follow_redirects=False,
        headers={"Accept": "application/json"},
        cookies=no_cookies(),
    ) as client:
        session = _Session(client, token, email, password)
        for entry in config["streams"]:
            try:
                results.append(_collect(db, session, entry, config))
            except Exception as exc:
                db.rollback()
                results.append(
                    {
                        "stream": STREAM,
                        "error": str(exc)
                        if isinstance(exc, EventApiError)
                        else collection_error(exc),
                    }
                )
    return results


register(
    Source(
        name="mosyle",
        module=__name__,
        queue=HTTP_QUEUE,
        schedule=float(settings.mosyle_poll_seconds),
        config_setting="mosyle_config_path",
        public_fields=("email",),
        secret_fields=("access_token", "password"),
        input_sections=("streams",),
        section="streams",
        id_field="stream",
        definition_sections=("streams",),
        key_field="stream",
        demo=False,
        streams=(STREAM,),
        validate_config=validate_config,
    )
)
