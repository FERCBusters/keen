"""Bounded, connection-scoped Elasticsearch document collection.

A cursor records a fully read time window. PIT/search_after tokens are only used
inside that window; a retry opens a fresh PIT and deduplicates persisted hits.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit

from app.core.datetime_utils import utc_now_naive
from app.db.models import IngestionCursor
from app.ingest.common import store_event_with_artifact
from app.ingest.connections import connection_runs, load_document, namespace, settings
from app.ingest.http import client as ingestion_client
from app.security.errors import collection_error
from app.services.ingestion_pause import pausable

FIELD_DEFAULTS = {
    "timestamp_field": "@timestamp",
    "message_field": "message",
    "system_field": "host.name",
    "actor_field": "user.name",
    "action_field": "event.action",
    "outcome_field": "event.outcome",
}
LIMITS = {
    "initial_lookback_minutes": (1440, 1, 43200),
    "overlap_minutes": (5, 0, 60),
    "window_minutes": (60, 1, 1440),
    "page_size": (500, 1, 1000),
    "max_pages": (100, 1, 100),
    "windows_per_run": (4, 1, 24),
}
RUN_SECONDS = 120


class ElasticsearchInputError(ValueError):
    """A safe, fixed diagnostic suitable for the administrator UI."""


def validate_input(entry):
    if not isinstance(entry, dict):
        raise ElasticsearchInputError("Elasticsearch inputs must be objects")
    name = entry.get("name")
    if (
        not isinstance(name, str)
        or not re.fullmatch(r"[A-Za-z0-9_. -]{1,128}", name)
        or not name.strip()
    ):
        raise ElasticsearchInputError(
            "Enter an input name using letters, numbers, spaces, dots, hyphens or underscores"
        )
    indices = entry.get("index_pattern")
    if not isinstance(indices, str) or not 1 <= len(indices) <= 512:
        raise ElasticsearchInputError("Enter an index, alias or data stream pattern")
    for part in indices.split(","):
        if not re.fullmatch(r"[a-z0-9.*][a-z0-9._*-]*", part) or part in (".", ".."):
            raise ElasticsearchInputError(
                "Use comma-separated index patterns with lowercase names and optional * wildcards"
            )
    query = entry.get("query", "")
    if not isinstance(query, str) or len(query) > 4096:
        raise ElasticsearchInputError("Query text must be at most 4096 characters")
    result = dict(entry)
    result["name"] = name.strip()
    result["query"] = query.strip()
    for key, default in FIELD_DEFAULTS.items():
        value = entry.get(key) or default
        if not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9_@][A-Za-z0-9_.@-]{0,255}", value
        ):
            raise ElasticsearchInputError(
                "Field mappings must be simple field names or dotted paths"
            )
        result[key] = value
    for key, (default, low, high) in LIMITS.items():
        value = entry.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ElasticsearchInputError(
                f"{key} must be an integer between {low} and {high}"
            )
        result[key] = value
    if result["window_minutes"] <= result["overlap_minutes"]:
        raise ElasticsearchInputError("Window minutes must exceed overlap minutes")
    return result


def validate_config(document):
    if not isinstance(document, dict):
        raise ElasticsearchInputError("Elasticsearch configuration must be an object")
    rows = document.get("queries", [])
    if not isinstance(rows, list) or len(rows) > 50:
        raise ElasticsearchInputError(
            "Configure up to 50 Elasticsearch inputs per connection"
        )
    entries = [validate_input(row) for row in rows]
    names = [entry["name"] for entry in entries]
    if len(set(names)) != len(names):
        raise ElasticsearchInputError("Elasticsearch input names must be distinct")
    return entries


def _client():
    base = settings.elasticsearch_base_url.rstrip("/")
    parsed = urlsplit(base)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ElasticsearchInputError(
            "Use an HTTPS Elasticsearch endpoint without embedded credentials, query or fragment"
        )
    headers = {}
    auth = None
    api_key = settings.elasticsearch_api_key
    username = settings.elasticsearch_username
    password = settings.elasticsearch_password
    if api_key and (username or password):
        raise ElasticsearchInputError(
            "Choose either an API key or username and password"
        )
    if api_key:
        if len(api_key) > 8192 or any(ord(c) < 33 or ord(c) > 126 for c in api_key):
            raise ElasticsearchInputError(
                "Enter the encoded Elasticsearch API key without the Authorization prefix"
            )
        headers["Authorization"] = "ApiKey " + api_key
    elif username or password:
        if not username or not password:
            raise ElasticsearchInputError("Supply both username and password")
        auth = (username, password)
    return ingestion_client(
        base_url=base + "/", headers=headers, auth=auth, timeout=20.0
    )


def _field(document, path):
    if path in document:
        return document[path]
    value = document
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def _text(value, limit):
    return str(value)[:limit] if isinstance(value, (str, int, float, bool)) else None


def _timestamp(value):
    # Elasticsearch date docvalues are requested in strict ISO format.
    if not isinstance(value, str) or len(value) > 64:
        raise ElasticsearchInputError(
            "The timestamp field must contain a single valid date"
        )
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError
        return dt.astimezone(timezone.utc)
    except ValueError:
        raise ElasticsearchInputError(
            "The timestamp field must contain a single valid date"
        ) from None


def _complete_response(data):
    if (
        not isinstance(data, dict)
        or data.get("timed_out")
        or data.get("terminated_early")
        or (data.get("_shards") or {}).get("failed", 0)
    ):
        raise ElasticsearchInputError(
            "Elasticsearch returned incomplete results; the window will be retried"
        )


def _store_hit(db, hit, entry):
    doc = hit.get("_source")
    index, doc_id = hit.get("_index"), hit.get("_id")
    if not isinstance(doc, dict) or not all(
        isinstance(v, str) and 0 < len(v) <= 1024 for v in (index, doc_id)
    ):
        raise ElasticsearchInputError(
            "Elasticsearch must return document IDs, index names and enabled _source data"
        )
    dates = (hit.get("fields") or {}).get(entry["timestamp_field"])
    if not isinstance(dates, list) or len(dates) != 1:
        raise ElasticsearchInputError(
            "The timestamp field must contain a single valid date"
        )
    ts = _timestamp(dates[0])
    external_id = hashlib.sha256(
        json.dumps([index, doc_id], separators=(",", ":")).encode()
    ).hexdigest()
    return store_event_with_artifact(
        db,
        timestamp=ts,
        source="elasticsearch",
        system=_text(_field(doc, entry["system_field"]), 128),
        actor=_text(_field(doc, entry["actor_field"]), 128),
        action=_text(_field(doc, entry["action_field"]), 128),
        outcome=_text(_field(doc, entry["outcome_field"]), 64),
        severity=None,
        summary=_text(_field(doc, entry["message_field"]), 2000)
        or "Elasticsearch log document",
        raw_pointer={
            "elasticsearch": {
                "query_name": entry["name"],
                "index": index,
                "document_id": doc_id,
            }
        },
        normalized_payload=doc,
        external_id=external_id,
        artifact_kind="log_document",
        artifact_bytes=json.dumps(doc, ensure_ascii=False).encode(),
        artifact_content_type="application/json",
        artifact_key=f"elasticsearch/{external_id}.json",
        captured_by="keen:elasticsearch",
        commit=False,
    )


def _window(db, client, entry, start, end, deadline):
    """Read a complete snapshot; never retain PIT-local sort values as a cursor."""
    pit = None
    created = 0
    try:
        response = client.post(
            quote(entry["index_pattern"], safe="*,-._") + "/_pit",
            params={"keep_alive": "2m", "allow_partial_search_results": "false"},
        )
        response.raise_for_status()
        data = response.json()
        pit = data.get("id")
        if not isinstance(pit, str) or not pit or len(pit) > 65536:
            raise ElasticsearchInputError(
                "Elasticsearch did not return a valid point-in-time identifier"
            )
        _complete_response(data)
        after = None
        for _ in range(entry["max_pages"]):
            if time.monotonic() >= deadline:
                raise ElasticsearchInputError(
                    "Collection reached its time budget; reduce the input window or narrow the query"
                )
            filters = [
                {
                    "range": {
                        entry["timestamp_field"]: {
                            "gte": start.isoformat(),
                            "lt": end.isoformat(),
                        }
                    }
                }
            ]
            if entry.get("query"):
                filters.append(
                    {
                        "simple_query_string": {
                            "query": entry["query"],
                            "fields": [entry["message_field"]],
                            "default_operator": "and",
                            "flags": "AND|OR|NOT|PHRASE|PRECEDENCE|WHITESPACE|ESCAPE",
                        }
                    }
                )
            body = {
                "size": entry["page_size"],
                "query": {"bool": {"filter": filters}},
                "pit": {"id": pit, "keep_alive": "2m"},
                "sort": [{"_shard_doc": "asc"}],
                "track_total_hits": False,
                "timeout": "15s",
                "docvalue_fields": [
                    {
                        "field": entry["timestamp_field"],
                        "format": "strict_date_optional_time_nanos",
                    }
                ],
            }
            if after is not None:
                body["search_after"] = after
            response = client.post(
                "_search", params={"allow_partial_search_results": "false"}, json=body
            )
            response.raise_for_status()
            data = response.json()
            updated_pit = data.get("pit_id", pit)
            if (
                not isinstance(updated_pit, str)
                or not updated_pit
                or len(updated_pit) > 65536
            ):
                raise ElasticsearchInputError(
                    "Elasticsearch returned an invalid point-in-time identifier"
                )
            pit = updated_pit
            _complete_response(data)
            hits = (data.get("hits") or {}).get("hits")
            if not isinstance(hits, list) or len(hits) > entry["page_size"]:
                raise ElasticsearchInputError(
                    "Elasticsearch returned an invalid search page"
                )
            for hit in hits:
                if time.monotonic() >= deadline:
                    raise ElasticsearchInputError(
                        "Collection reached its time budget; reduce the input window or narrow the query"
                    )
                result = _store_hit(db, hit, entry)
                created += not result.get("deduped", False)
            # Commit each page for bounded transactions and inexpensive retry deduplication.
            db.commit()
            if len(hits) < entry["page_size"]:
                return created
            next_after = hits[-1].get("sort")
            if (
                not isinstance(next_after, list)
                or len(next_after) != 1
                or type(next_after[0]) is not int
                or (after is not None and next_after[0] <= after[0])
            ):
                raise ElasticsearchInputError(
                    "Elasticsearch pagination did not progress; the window will be retried"
                )
            after = next_after
        raise ElasticsearchInputError(
            "Collection reached its page limit; reduce window_minutes or narrow the query. The window will be retried"
        )
    finally:
        if pit:
            try:
                client.request("DELETE", "_pit", json={"id": pit})
            except Exception:
                # PITs also expire after two minutes; never disclose upstream errors.
                pass


def ingest_elasticsearch_once(db, client, entry, deadline):
    entry = validate_input(entry)
    # Changes to the search definition receive a fresh bounded backfill.
    definition = {
        key: entry.get(key) for key in ("index_pattern", "query", *FIELD_DEFAULTS)
    }
    digest = hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()
    name = namespace("elasticsearch:" + entry["name"] + ":" + digest)
    cursor = db.query(IngestionCursor).filter_by(name=name).one_or_none()
    if cursor is None:
        cursor = IngestionCursor(name=name, last_ts=None, meta={})
        db.add(cursor)
        db.commit()
    now = datetime.now(timezone.utc)
    previous = cursor.last_ts
    if previous is not None:
        previous = (
            previous.replace(tzinfo=timezone.utc)
            if previous.tzinfo is None
            else previous.astimezone(timezone.utc)
        )
    # Remember the initial lower bound even if collection fails before completing
    # the first window, so repeated failures cannot slide it past unread evidence.
    meta = dict(cursor.meta or {})
    if not meta.get("initial_start"):
        meta["initial_start"] = (
            now - timedelta(minutes=entry["initial_lookback_minutes"])
        ).isoformat()
        cursor.meta = meta
        db.commit()
    start = (
        previous - timedelta(minutes=entry["overlap_minutes"])
        if previous
        else _timestamp(meta["initial_start"])
    )
    created = windows = 0
    for _ in range(entry["windows_per_run"]):
        if start >= now or time.monotonic() >= deadline:
            break
        end = min(now, start + timedelta(minutes=entry["window_minutes"]))
        created += _window(db, client, entry, start, end, deadline)
        cursor.last_ts = end.replace(tzinfo=None)
        cursor.updated_at = utc_now_naive()
        db.commit()
        windows += 1
        start = end
    return {
        "query_name": entry["name"],
        "created_events": created,
        "windows_completed": windows,
        "caught_up": start >= now,
        "cursor_last_ts": cursor.last_ts.isoformat() if cursor.last_ts else None,
    }


@pausable("elasticsearch")
@connection_runs("elasticsearch")
def ingest_elasticsearch_all(db):
    entries = validate_config(
        load_document("elasticsearch", settings.elasticsearch_config_path, db=db)
    )
    if not entries:
        return []
    results = []
    deadline = time.monotonic() + RUN_SECONDS
    with _client() as client:
        for entry in entries:
            if time.monotonic() >= deadline:
                results.append(
                    {
                        "query_name": entry["name"],
                        "skipped": True,
                        "reason": "Connection run budget exhausted; run collection again",
                    }
                )
                continue
            try:
                results.append(ingest_elasticsearch_once(db, client, entry, deadline))
            except Exception as exc:
                db.rollback()
                results.append(
                    {
                        "query_name": entry["name"],
                        "error": str(exc)
                        if isinstance(exc, ElasticsearchInputError)
                        else collection_error(exc),
                    }
                )
    return results
