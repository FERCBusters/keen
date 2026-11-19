from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import boto3
from sqlalchemy.orm import Session

from app.core.datetime_utils import utc_now_naive
from app.db.models import IngestionCursor
from app.ingest.common import RecordReader, store_event_with_artifact
from app.ingest.connections import (
    connection_runs,
    load_document,
    namespace,
    settings,
)
from app.ingest.log_pipeline import (
    brief_http_request,
    ensure_utc,
    make_fingerprint,
    truncate,
    try_parse_json_line,
)
from app.ingest.registry import HTTP_QUEUE, Source, register
from app.security.errors import collection_error
from app.security.regex import scan_pattern
from app.services.ingestion_pause import pausable


def _cloudwatch_client(region: str | None = None):
    # boto3 resolves credentials via the usual chain:
    # env vars, shared config files, instance profile, etc.
    kwargs: dict[str, Any] = {}
    if region:
        kwargs["region_name"] = region
    from app.ingest.connections import aws_credentials

    kwargs.update(aws_credentials())
    client = boto3.client("logs", **kwargs)
    # SDK environment/shared-config endpoint overrides must not send signed
    # requests or session tokens over HTTP. Certificate verification stays on.
    endpoint = urlsplit(client.meta.endpoint_url)
    if (
        endpoint.scheme != "https"
        or not endpoint.hostname
        or endpoint.username
        or endpoint.password
        or endpoint.fragment
    ):
        client.close()
        raise ValueError(
            "CloudWatch endpoint must use HTTPS without embedded credentials or a fragment"
        )
    return client


def load_cloudwatch_logs_config(path: str) -> dict[str, Any]:
    return load_document("cloudwatch_logs", path)


def _extract_actor(parsed: Any) -> str | None:
    """Best-effort actor extraction for common AWS/service log shapes."""

    if not isinstance(parsed, dict):
        return None

    # common generic keys
    for k in ("user", "username", "userName", "principal", "principalId"):
        v = parsed.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()[:120]

    # CloudTrail-like
    ui = parsed.get("userIdentity")
    if isinstance(ui, dict):
        for k in ("userName", "principalId", "arn"):
            v = ui.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()[:120]

    # ALB / nginx style sometimes has remote_user
    v = parsed.get("remote_user")
    if isinstance(v, str) and v.strip() and v.strip() != "-":
        return v.strip()[:120]

    return None


def _summarize_json(parsed: Any, line: str) -> tuple[str, dict[str, Any]]:
    """Return (summary, extracted) for JSON log lines."""

    extracted: dict[str, Any] = {}
    if isinstance(parsed, dict):
        # CloudTrail-ish: eventName + eventSource is extremely helpful
        en = parsed.get("eventName")
        es = parsed.get("eventSource")
        et = parsed.get("eventType")
        if isinstance(en, str) and en:
            extracted["eventName"] = en
        if isinstance(es, str) and es:
            extracted["eventSource"] = es
        if isinstance(et, str) and et:
            extracted["eventType"] = et

        # Generic levels
        lvl = parsed.get("level") or parsed.get("severity") or parsed.get("logLevel")
        if isinstance(lvl, (str, int)):
            extracted["level"] = lvl

        msg = (
            parsed.get("message")
            or parsed.get("msg")
            or parsed.get("error")
            or parsed.get("log")
            or parsed.get("event")
        )

        # Build a compact, useful summary
        parts: list[str] = []
        if isinstance(es, str) and es:
            parts.append(es)
        if isinstance(en, str) and en:
            parts.append(en)
        if isinstance(et, str) and et and et.lower() not in {"awsapi", ""}:
            parts.append(f"({et})")

        actor = _extract_actor(parsed)
        if actor:
            extracted["actor"] = actor
            parts.append(f"by {actor}")

        if isinstance(msg, str) and msg.strip():
            brief = brief_http_request(msg)
            parts.append("-")
            parts.append(brief or truncate(msg, 140))

        summary = " ".join([p for p in parts if p]).strip()
        if summary:
            return summary, extracted

    return truncate(line, 340), extracted


def _event_order(event: Any) -> tuple[int, str]:
    """An event's place in a page; one without a readable time sorts first."""
    if not isinstance(event, dict):
        return (0, "")
    try:
        timestamp = int(event.get("timestamp") or 0)
    except (TypeError, ValueError, OverflowError):
        timestamp = 0
    return (timestamp, str(event.get("eventId") or ""))


def ingest_cloudwatch_logs_once(
    db: Session, name: str, q: dict[str, Any], defaults: dict[str, Any]
) -> dict[str, Any]:
    cursor_name = namespace(f"cloudwatch_logs:{name}")
    cur = (
        db.query(IngestionCursor)
        .filter(IngestionCursor.name == cursor_name)
        .one_or_none()
    )
    # A cursor this run creates is kept by the first page it finishes; until
    # then a failed request leaves none behind.
    new_cursor = cur is None
    if cur is None:
        cur = IngestionCursor(
            name=cursor_name,
            last_ts=None,
            meta={
                "name": name,
                "log_group": q.get("log_group")
                or q.get("logGroup")
                or q.get("logGroupName"),
                "region": q.get("region") or settings.cloudwatch_logs_region or None,
            },
        )
        db.add(cur)
        db.flush()

    cur_last_ts = ensure_utc(cur.last_ts)
    if cur_last_ts is not cur.last_ts:
        cur.last_ts = cur_last_ts

    log_group = (
        q.get("log_group") or q.get("logGroup") or q.get("logGroupName") or ""
    ).strip()
    if not log_group:
        return {"name": name, "ok": False, "error": "missing log_group"}

    region = (q.get("region") or settings.cloudwatch_logs_region or "").strip() or None
    filter_pattern = (q.get("filter_pattern") or q.get("filterPattern") or "").strip()
    log_stream_prefix = (
        q.get("log_stream_prefix") or q.get("logStreamPrefix") or ""
    ).strip()
    log_stream_names = q.get("log_stream_names") or q.get("logStreamNames") or []
    if not isinstance(log_stream_names, list):
        log_stream_names = []

    event_defaults = q.get("event") or {}
    if not isinstance(event_defaults, dict):
        event_defaults = {}

    now = datetime.now(timezone.utc)
    lookback_minutes = int(defaults.get("lookback_minutes", 15))
    overlap_seconds = int(defaults.get("overlap_seconds", 2))
    # Start window: overlap slightly to avoid missing late-arriving events.
    if cur_last_ts:
        start = cur_last_ts - timedelta(seconds=overlap_seconds)
    else:
        start = now - timedelta(minutes=lookback_minutes)
    end = now

    limit = int(defaults.get("limit", 10000))
    max_pages = int(defaults.get("max_pages", 50))

    params: dict[str, Any] = {
        "logGroupName": log_group,
        "startTime": int(start.timestamp() * 1000),
        "endTime": int(end.timestamp() * 1000),
        "limit": limit,
        "interleaved": True,
    }
    if filter_pattern:
        params["filterPattern"] = filter_pattern
    if log_stream_prefix:
        params["logStreamNamePrefix"] = log_stream_prefix
    if log_stream_names:
        # AWS API limit applies; keep it as-is and let AWS validate.
        params["logStreamNames"] = [str(x) for x in log_stream_names if str(x).strip()]

    client = _cloudwatch_client(region)

    severity = (
        int(event_defaults.get("severity", 3))
        if event_defaults.get("severity") is not None
        else None
    )
    reader = RecordReader()
    created_events = 0
    created_artifacts = 0
    created_mappings = 0
    max_seen_ts = cur_last_ts

    # Pagination protection: AWS can sometimes repeat tokens.
    next_token: str | None = None
    seen_tokens: set[str] = set()
    page = 0

    while page < max_pages:
        page += 1
        if next_token:
            params["nextToken"] = next_token

        if not new_cursor:
            db.commit()  # No database transaction spans the remote wait.
        resp = client.filter_log_events(**params)
        events = resp.get("events") or []
        # Sort for stable cursor advancement.
        events_sorted = sorted(events, key=_event_order)

        def read(e):
            ts_ms = int(e.get("timestamp") or 0)
            if ts_ms <= 0:
                return None
            ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
            msg = str(e.get("message") or "")
            event_id = str(e.get("eventId") or "")
            log_stream = str(e.get("logStreamName") or "")
            ingestion_time_ms = e.get("ingestionTime")

            # Stable de-dupe key per source: AWS's eventId when it fits, a digest
            # of a long one, and for an event without one, a digest of where and
            # when it was logged and what it said.
            if not event_id:
                ext_id = make_fingerprint(log_group, log_stream, str(ts_ms), msg)
            elif len(event_id) <= 120:
                ext_id = event_id
            else:
                ext_id = make_fingerprint(log_group, event_id)

            parsed = try_parse_json_line(msg)
            actor = _extract_actor(parsed) if parsed is not None else None

            summary = truncate(msg, 340)
            normalized: dict[str, Any] = {
                "cloudwatch": {
                    "log_group": log_group,
                    "log_stream": log_stream,
                    "event_id": event_id,
                    "timestamp_ms": ts_ms,
                    "region": region,
                },
                "message": msg,
            }
            if ingestion_time_ms is not None:
                normalized["cloudwatch"]["ingestion_time_ms"] = int(ingestion_time_ms)

            if parsed is not None:
                summary, extracted = _summarize_json(parsed, msg)
                normalized["json"] = parsed
                if extracted:
                    normalized["extracted"] = extracted
                    # If we extracted actor from JSON summary, prefer it.
                    actor = actor or extracted.get("actor")

            raw_pointer = {
                "cloudwatch_logs": {
                    "name": name,
                    "region": region,
                    "log_group": log_group,
                    "log_stream": log_stream,
                    "event_id": event_id,
                    "filter_pattern": filter_pattern,
                    "window_start_ms": params["startTime"],
                    "window_end_ms": params["endTime"],
                }
            }

            ext = "json" if parsed is not None else "log"
            ctype = (
                "application/json"
                if parsed is not None
                else "text/plain; charset=utf-8"
            )
            return dict(
                timestamp=ts.replace(tzinfo=None),
                source="cloudwatch_logs",
                system=event_defaults.get("system") or name,
                actor=actor,
                action=event_defaults.get("action") or "log_event",
                outcome=event_defaults.get("outcome"),
                severity=severity,
                summary=summary,
                raw_pointer=raw_pointer,
                normalized_payload=normalized,
                external_id=ext_id,
                artifact_kind="log_event",
                artifact_bytes=msg.encode("utf-8", errors="replace"),
                artifact_content_type=ctype,
                # AWS event IDs can be long; the key uses a digest of the identity.
                artifact_key=(
                    f"cloudwatch_logs/{name}/{ts.date().isoformat()}/"
                    f"{make_fingerprint(ext_id)}.{ext}"
                ),
                captured_by="keen:cloudwatch_logs",
            )

        for event in reader.each(events_sorted, read):
            # Each event commits by itself, so the rows and locks a mapping
            # takes are held for one event, never across a page or a request
            # to AWS.
            stored = store_event_with_artifact(db, **event)
            if not stored.get("deduped"):
                created_events += 1
                created_artifacts += 1
                created_mappings += stored["mappings"]

            # Advance max_seen_ts even for deduped events.
            ts = event["timestamp"].replace(tzinfo=timezone.utc)
            if (max_seen_ts is None) or (ts > max_seen_ts):
                max_seen_ts = ts

        # Keep each finished page: a run that is stopped, by its time limit
        # or a failed request, continues from the newest event stored. Events
        # sharing that moment on a later page are covered by the overlap, and
        # repeats are recognised by their identifiers.
        if max_seen_ts is not None:
            cur.last_ts = max_seen_ts
            cur.updated_at = utc_now_naive()
            db.add(cur)
            db.commit()
            new_cursor = False

        next_token = resp.get("nextToken")
        if not next_token:
            break
        if next_token in seen_tokens:
            # Prevent infinite loops.
            break
        seen_tokens.add(next_token)

        # If AWS returned no events on this page, stop early.
        if not events:
            break

    cur.last_ts = max_seen_ts or end
    cur.updated_at = utc_now_naive()
    db.add(cur)
    db.commit()

    return {
        "name": name,
        "region": region,
        "log_group": log_group,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "created_events": created_events,
        "created_artifacts": created_artifacts,
        "created_mappings": created_mappings,
        "invalid_records": reader.invalid,
        "cursor_last_ts": cur.last_ts.isoformat() if cur.last_ts else None,
        "pages": page,
    }


@pausable("cloudwatch_logs")
@connection_runs("cloudwatch_logs")
def ingest_cloudwatch_logs_all(db: Session) -> list[dict[str, Any]]:
    cfg = load_cloudwatch_logs_config(settings.cloudwatch_logs_config_path)
    defaults = cfg.get("defaults", {}) or {}

    queries = (
        cfg.get("queries")
        or cfg.get("streams")
        or cfg.get("log_groups")
        or cfg.get("logGroups")
        or []
    )
    if not isinstance(queries, list):
        queries = []

    out: list[dict[str, Any]] = []
    for q in queries:
        if not isinstance(q, dict):
            continue
        name = (q.get("name") or q.get("label") or q.get("log_group") or "").strip()
        if not name:
            # Deterministic fallback name
            lg = str(
                q.get("log_group") or q.get("logGroupName") or "cloudwatch"
            ).strip()
            name = scan_pattern(r"[^a-zA-Z0-9._-]+").sub(
                "-", lg.strip("/") or "cloudwatch"
            )[:60]

        try:
            out.append(ingest_cloudwatch_logs_once(db, name, q, defaults))
        except Exception as e:
            db.rollback()
            out.append({"name": name, "error": collection_error(e)})
    return out


register(
    Source(
        name="cloudwatch_logs",
        module=__name__,
        queue=HTTP_QUEUE,
        schedule="*/30",
        config_setting="cloudwatch_logs_config_path",
        public_fields=("region",),
        secret_fields=(
            "aws_access_key_id",
            "aws_secret_access_key",
            "aws_session_token",
        ),
        input_sections=("queries", "streams", "log_groups", "logGroups"),
        section="queries",
        id_field="name",
        definition_sections=("queries",),
        key_field="name",
    )
)
