"""1Password Events API: audit events, item usage and sign-in attempts.

The source is named ``op_events``: a key holding "password" is a secret to
redaction, which would replace this source's provenance wherever it is a key.

Read-only. Each stream is read a page at a time from the position 1Password
returned with the previous page; every page is stored and committed with that
position before the next is requested.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta

import httpx2 as httpx

from app.core.datetime_utils import utc_now_naive
from app.db.models import IngestionCursor
from app.ingest.common import RecordReader, store_event_with_artifact
from app.ingest.connections import connection_runs, load_document, namespace, settings
from app.ingest.event_api import (
    EventApiError,
    label,
    member,
    paging_token,
    read_json,
    records,
    text,
    timestamp,
)
from app.ingest.http import client as ingestion_client
from app.ingest.registry import HTTP_QUEUE, Source, register
from app.security.errors import collection_error
from app.security.redaction import utf8_bytes
from app.security.regex import scan_pattern
from app.services.ingestion_pause import pausable

PRODUCT = "1Password"
# The Events API is served only by 1Password; a token is sent nowhere else.
SERVERS = (
    "https://events.1password.com",
    "https://events.ent.1password.com",
    "https://events.1password.ca",
    "https://events.1password.eu",
)
STREAMS = {
    "auditevents": "audit events",
    "itemusages": "item usage",
    "signinattempts": "sign-in attempts",
}
# Read again from this long before the newest stored event after a restart.
OVERLAP = timedelta(minutes=5)
_TOKEN = scan_pattern(r"[\x21-\x7e]{1,8192}")


class OnePasswordError(EventApiError):
    """Operator-safe message: never contains credentials or response bodies."""


def validate_config(config):
    if not isinstance(config, dict):
        raise OnePasswordError("1Password settings must be an object")
    streams = config.get("streams", [])
    if not isinstance(streams, list) or len(streams) > len(STREAMS):
        raise OnePasswordError("1Password streams must list each stream at most once")
    seen = set()
    for entry in streams:
        stream = entry.get("stream") if isinstance(entry, dict) else None
        if not isinstance(stream, str) or stream not in STREAMS:
            raise OnePasswordError(
                "Choose a 1Password stream: auditevents, itemusages or signinattempts"
            )
        if stream in seen:
            raise OnePasswordError("Duplicate 1Password stream")
        seen.add(stream)
        if (
            not isinstance(entry.get("label", ""), str)
            or len(entry.get("label", "")) > 128
        ):
            raise OnePasswordError("1Password label must be at most 128 characters")
    for field, default, lo, hi in [
        ("initial_lookback_days", 7, 0, 365),
        ("page_size", 100, 1, 1000),
        ("max_pages", 100, 1, 1000),
    ]:
        value = config.get(field, default)
        if type(value) is not int or not lo <= value <= hi:
            raise OnePasswordError(
                f"1Password {field} must be an integer between {lo} and {hi}"
            )
    return config


def _audit(item):
    actor = member(item, "actor_details")
    who = text(actor.get("email")) or text(actor.get("name"))
    action = label(item.get("action"))
    kind = label(item.get("object_type"))
    return (
        who or text(item.get("actor_uuid")),
        f"audit.{kind}.{action}",
        "info",
        f"1Password audit: {action} {kind}",
    )


def _item_usage(item):
    action = label(item.get("action"))
    return (
        text(member(item, "user").get("email")),
        f"item.{action}",
        "info",
        f"1Password item usage: {action}",
    )


def _sign_in(item):
    category = label(item.get("category"))
    succeeded = category in ("success", "firewall_reported_success")
    return (
        text(member(item, "target_user").get("email")),
        f"signin.{category}",
        "success" if succeeded else "failure",
        f"1Password sign-in attempt: {category}",
    )


_DESCRIBE = {
    "auditevents": _audit,
    "itemusages": _item_usage,
    "signinattempts": _sign_in,
}


def _read(base, entry, item, collected):
    """What is stored for one event."""
    stream = entry["stream"]
    actor, action, outcome, summary = _DESCRIBE[stream](item)
    # An event without a readable time is kept, dated when it was collected.
    when = timestamp(item.get("timestamp")) or collected
    payload = {"stream": stream, "event": item}
    content = utf8_bytes(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )
    ident = text(item.get("uuid"))
    # An event without an identifier is told apart by its content.
    identity = ident or hashlib.sha256(content).hexdigest()
    digest = hashlib.sha256(
        utf8_bytes(json.dumps([base, stream, identity], ensure_ascii=False))
    ).hexdigest()
    return dict(
        timestamp=when,
        source="op_events",
        system=entry.get("label") or PRODUCT,
        actor=actor,
        action=action,
        outcome=outcome,
        severity=6 if outcome == "failure" else 3,
        summary=summary + (f" by {actor}" if actor else ""),
        raw_pointer={
            "op_events": {
                "section": "streams",
                "stream": stream,
                "uuid": ident[:128] if ident else None,
            }
        },
        normalized_payload=payload,
        external_id=digest,
        artifact_kind="op_events_" + stream,
        artifact_bytes=content,
        artifact_content_type="application/json",
        artifact_key=f"op_events/{stream}/{digest}.json",
        captured_by="keen:op_events",
    )


def _restart(cursor, started, limit, config):
    """A request that starts the stream again, shortly before the newest event
    stored, or as far back as the first run reads."""
    since = (
        cursor.last_ts - OVERLAP
        if cursor.last_ts
        else started - timedelta(days=config.get("initial_lookback_days", 7))
    )
    return {
        "limit": limit,
        "start_time": since.replace(microsecond=0).isoformat() + "Z",
    }


def _page(client, url, body, restart):
    """One page, and the request that returned it.

    A position 1Password no longer accepts is replaced once by a restart;
    events it returns again are recognised by their identifiers.
    """
    try:
        return read_json(client, "POST", url, product=PRODUCT, json=body), body
    except EventApiError as exc:
        if exc.status != 400 or "cursor" not in body:
            raise
    body = restart()
    return read_json(client, "POST", url, product=PRODUCT, json=body), body


def _stream(db, client, base, entry, config):
    stream = entry["stream"]
    limit = config.get("page_size", 100)
    digest = hashlib.sha256(f"{base}:{stream}".encode()).hexdigest()
    name = namespace("op_events:" + digest)
    cursor = db.query(IngestionCursor).filter_by(name=name).one_or_none()
    if cursor is None:
        cursor = IngestionCursor(name=name, meta={})
    started = utc_now_naive()
    meta = cursor.meta or {}

    def restart():
        return _restart(cursor, started, limit, config)

    # A position carries the page size it began with, so a changed page size
    # starts the stream again.
    position = meta.get("cursor") if meta.get("limit") == limit else None
    body = {"cursor": position} if isinstance(position, str) else restart()
    url = f"{base}/api/v2/{stream}"
    created = dropped = pages = 0
    reader = RecordReader()
    seen = set()
    more = True
    while more and pages < config.get("max_pages", 100):
        data, body = _page(client, url, body, restart)
        items, invalid = records(data.get("items"), limit, PRODUCT)
        position = paging_token(data.get("cursor"), PRODUCT)
        more = data.get("has_more")
        if position is None or type(more) is not bool:
            raise OnePasswordError("1Password returned invalid paging data")
        if more and (position == body.get("cursor") or position in seen):
            raise OnePasswordError("1Password repeated a page; cursor retained")
        seen.add(position)
        newest = cursor.last_ts
        for event in reader.each(items, lambda item: _read(base, entry, item, started)):
            result = store_event_with_artifact(db, **event)
            created += int(not result.get("deduped", False))
            when = event["timestamp"]
            newest = max(newest, when) if newest else when
        dropped += invalid
        pages += 1
        # Each page is committed with its position before the next is read.
        # The cursor keeps its time at or behind the present, whatever an
        # event claims.
        cursor.last_ts = newest
        cursor.meta = {"cursor": position, "limit": limit}
        cursor.updated_at = utc_now_naive()
        db.add(cursor)
        db.commit()
        body = {"cursor": position}
    return {
        "stream": stream,
        "pages": pages,
        "created_events": created,
        "invalid_records": dropped + reader.invalid,
        # More remains when the page limit ended the run; the next continues.
        "more": more,
    }


@pausable("op_events")
@connection_runs("op_events")
def ingest_op_events_all(db):
    if settings.demo_mode or not settings.op_events_enabled:
        return [{"skipped": True, "reason": "1Password disabled (or demo mode)"}]
    base = str(settings.op_events_base_url or "").strip().rstrip("/")
    if base not in SERVERS:
        return [
            {
                "error": "Set the 1Password Events API server to one of "
                + ", ".join(SERVERS)
            }
        ]
    token = str(settings.op_events_token or "").strip()
    if not _TOKEN.fullmatch(token):
        return [{"error": "Set the 1Password Events API bearer token"}]
    try:
        config = validate_config(
            load_document("op_events", settings.op_events_config_path)
        )
    except (ValueError, OSError):
        return [{"error": "Invalid 1Password configuration; check streams"}]
    if not config.get("streams"):
        return [{"skipped": True, "reason": "Add a 1Password stream to collect"}]
    results = []
    with ingestion_client(
        timeout=httpx.Timeout(30, connect=10),
        verify=True,
        follow_redirects=False,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
    ) as client:
        for entry in config["streams"]:
            try:
                results.append(_stream(db, client, base, entry, config))
            except Exception as exc:
                db.rollback()
                results.append(
                    {
                        "stream": entry["stream"],
                        "error": str(exc)
                        if isinstance(exc, EventApiError)
                        else collection_error(exc),
                    }
                )
    return results


register(
    Source(
        name="op_events",
        module=__name__,
        queue=HTTP_QUEUE,
        schedule=float(settings.op_events_poll_seconds),
        config_setting="op_events_config_path",
        public_fields=("base_url",),
        secret_fields=("token",),
        input_sections=("streams",),
        section="streams",
        id_field="stream",
        definition_sections=("streams",),
        key_field="stream",
        demo=False,
    )
)
