from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
import yaml
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Artifact, ControlItem, Event, IngestionCursor, Mapping
from app.mapping.rules import evaluate_by_framework, load_rules
from app.security.redaction import redact_bytes, redact_obj
from app.storage.s3 import put_bytes


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """Return a timezone-aware UTC datetime.

    Some deployments store cursor timestamps as TIMESTAMP WITHOUT TIME ZONE.
    SQLAlchemy then returns offset-naive datetimes, which cannot be compared to
    timezone-aware values.
    """

    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _cloudwatch_client(region: str | None = None):
    # boto3 resolves credentials via the usual chain:
    # env vars, shared config files, instance profile, etc.
    kwargs: dict[str, Any] = {}
    if region:
        kwargs["region_name"] = region
    return boto3.client("logs", **kwargs)


def load_cloudwatch_logs_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _fingerprint(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update((p or "").encode("utf-8", errors="replace"))
        h.update(b"|")
    return h.hexdigest()[:32]


def _try_parse_json_line(line: str) -> Any | None:
    s = (line or "").strip()
    if not s:
        return None
    if s[0] not in "{[":
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def _truncate(s: str, limit: int = 220) -> str:
    s2 = " ".join((s or "").split())
    if len(s2) <= limit:
        return s2
    return s2[: limit - 1] + "…"


def _brief_http_request(log_line: str) -> str:
    """Extract a compact HTTP request from common access-log formats."""

    s = str(log_line or "")
    m = re.search(r'"(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+([^\s\"]+)', s)
    if not m:
        return ""
    method = m.group(1)
    path = m.group(2).split("?", 1)[0]
    path = _truncate(path, 120)
    return f"{method} {path}"


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
            brief = _brief_http_request(msg)
            parts.append("-")
            parts.append(brief or _truncate(msg, 140))

        summary = " ".join([p for p in parts if p]).strip()
        if summary:
            return summary, extracted

        # Fall back to a message field if present
        if isinstance(msg, str) and msg.strip():
            return _truncate(msg, 340), extracted

    return _truncate(line, 340), extracted


def _ensure_control_items(
    db: Session, framework_slug: str, refs: list[str]
) -> dict[str, ControlItem]:
    found = (
        db.query(ControlItem)
        .filter(ControlItem.framework_slug == framework_slug, ControlItem.ref.in_(refs))
        .all()
    )
    by_ref = {c.ref: c for c in found}
    for ref in refs:
        if ref not in by_ref:
            c = ControlItem(
                framework_slug=framework_slug, type="annex_control", ref=ref, title=None
            )
            db.add(c)
            db.flush()
            by_ref[ref] = c
    return by_ref


def _apply_rules_and_store_mappings(db: Session, ev: Event) -> int:
    rules = load_rules(settings.rules_path)
    event_dict = {
        "source": ev.source,
        "system": ev.system,
        "actor": ev.actor,
        "action": ev.action,
        "outcome": ev.outcome,
        "severity": ev.severity,
        "summary": ev.summary,
        "raw_pointer": ev.raw_pointer,
        "normalized_payload": ev.normalized_payload,
    }
    matched = evaluate_by_framework(event_dict, rules)
    if not matched:
        return 0

    created = 0
    for framework_slug, entries in matched.items():
        normalized: list[dict[str, Any]] = []
        for it in entries or []:
            if isinstance(it, str):
                ref = it.strip()
                if not ref:
                    continue
                normalized.append(
                    {
                        "ref": ref,
                        "confidence": 0.8,
                        "rationale": f"auto by rules ({framework_slug})",
                    }
                )
                continue

            if isinstance(it, dict):
                ref = str(it.get("ref") or "").strip()
                if not ref:
                    continue
                conf_raw = it.get("confidence")
                try:
                    conf = float(conf_raw) if conf_raw is not None else 0.8
                except Exception:
                    conf = 0.8
                conf = max(0.0, min(1.0, conf))
                rationale = str(it.get("rationale") or "").strip() or (
                    f"auto by rules ({framework_slug})"
                )
                normalized.append(
                    {"ref": ref, "confidence": conf, "rationale": rationale}
                )

        if not normalized:
            continue

        refs = [x["ref"] for x in normalized]
        controls = _ensure_control_items(db, framework_slug=framework_slug, refs=refs)

        for item in normalized:
            ref = item["ref"]
            ci = controls.get(ref)
            if not ci:
                continue
            stmt = (
                pg_insert(Mapping)
                .values(
                    id=uuid.uuid4(),
                    event_id=ev.id,
                    control_item_id=ci.id,
                    confidence=item["confidence"],
                    method="rule",
                    rationale=item["rationale"],
                    mapped_by="system",
                )
                .on_conflict_do_nothing(index_elements=["event_id", "control_item_id"])
                .returning(Mapping.id)
            )
            if db.execute(stmt).scalar() is not None:
                created += 1
    return created


def ingest_cloudwatch_logs_once(
    db: Session, name: str, q: dict[str, Any], defaults: dict[str, Any]
) -> dict[str, Any]:
    cursor_name = f"cloudwatch_logs:{name}"
    cur = (
        db.query(IngestionCursor)
        .filter(IngestionCursor.name == cursor_name)
        .one_or_none()
    )
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

    cur_last_ts = _ensure_utc(cur.last_ts)
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
        elif "nextToken" in params:
            params.pop("nextToken", None)

        resp = client.filter_log_events(**params)
        events = resp.get("events") or []
        # Sort for stable cursor advancement.
        events_sorted = sorted(
            events,
            key=lambda e: (
                int(e.get("timestamp") or 0),
                str(e.get("eventId") or ""),
            ),
        )

        for e in events_sorted:
            ts_ms = int(e.get("timestamp") or 0)
            if ts_ms <= 0:
                continue
            ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
            msg = str(e.get("message") or "")
            event_id = str(e.get("eventId") or "")
            log_stream = str(e.get("logStreamName") or "")
            ingestion_time_ms = e.get("ingestionTime")

            # Stable de-dupe key per source. Prefer AWS eventId when reasonable.
            ext_id = (
                event_id
                if 0 < len(event_id) <= 120
                else _fingerprint(log_group, event_id)
            )
            if not ext_id:
                ext_id = _fingerprint(log_group, log_stream, str(ts_ms), msg)

            parsed = _try_parse_json_line(msg)
            actor = _extract_actor(parsed) if parsed is not None else None

            summary = _truncate(msg, 340)
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

            # Hardening: redact before persisting.
            raw_pointer_r = redact_obj(raw_pointer) or {}
            normalized_r = redact_obj(normalized) or {}

            ev_id = uuid.uuid4()
            sev = (
                int(event_defaults.get("severity", 3))
                if event_defaults.get("severity") is not None
                else None
            )

            stmt = (
                pg_insert(Event)
                .values(
                    id=ev_id,
                    timestamp=ts,
                    source="cloudwatch_logs",
                    system=event_defaults.get("system") or name,
                    actor=actor,
                    action=event_defaults.get("action") or "log_event",
                    outcome=event_defaults.get("outcome"),
                    severity=sev,
                    summary=summary,
                    raw_pointer=raw_pointer_r,
                    normalized_payload=normalized_r,
                    external_id=ext_id,
                )
                .on_conflict_do_nothing(index_elements=["source", "external_id"])
                .returning(Event.id)
            )
            inserted_id = db.execute(stmt).scalar()
            if inserted_id is not None:
                created_events += 1

                # Store artifact: raw message line.
                ext = "json" if parsed is not None else "log"
                ctype = (
                    "application/json"
                    if parsed is not None
                    else "text/plain; charset=utf-8"
                )
                # Keep key safe and predictable.
                safe_eid = re.sub(r"[^a-zA-Z0-9._-]+", "-", (event_id or ext_id))[:80]
                key = f"cloudwatch_logs/{name}/{ts.date().isoformat()}/{inserted_id}-{safe_eid}.{ext}"
                msg_bytes, redaction_status = redact_bytes(
                    msg.encode("utf-8", errors="replace"), ctype
                )
                stored = put_bytes(key=key, data=msg_bytes, content_type=ctype)
                art = Artifact(
                    event_id=inserted_id,
                    kind="log_event",
                    storage_uri=stored.uri,
                    sha256=stored.sha256,
                    content_type=ctype,
                    size_bytes=stored.size_bytes,
                    captured_by="keen:cloudwatch_logs",
                    redaction_status=redaction_status,
                )
                db.add(art)
                db.flush()
                created_artifacts += 1

                # Apply mapping rules.
                ev_for_rules = Event(
                    id=inserted_id,
                    timestamp=ts,
                    source="cloudwatch_logs",
                    system=event_defaults.get("system") or name,
                    actor=actor,
                    action=event_defaults.get("action") or "log_event",
                    outcome=event_defaults.get("outcome"),
                    severity=sev,
                    summary=summary,
                    raw_pointer=raw_pointer_r,
                    normalized_payload=normalized_r,
                    external_id=ext_id,
                )
                created_mappings += _apply_rules_and_store_mappings(db, ev_for_rules)

            # Advance max_seen_ts even for deduped events.
            if (max_seen_ts is None) or (ts > max_seen_ts):
                max_seen_ts = ts

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
    cur.updated_at = datetime.utcnow()
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
        "cursor_last_ts": cur.last_ts.isoformat() if cur.last_ts else None,
        "pages": page,
    }


def ingest_cloudwatch_logs_all(db: Session) -> list[dict[str, Any]]:
    if not settings.cloudwatch_logs_enabled:
        return [{"skipped": True, "reason": "KEEN_CLOUDWATCH_LOGS_ENABLED=false"}]

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
            name = re.sub(r"[^a-zA-Z0-9._-]+", "-", lg.strip("/") or "cloudwatch")[:60]

        try:
            out.append(ingest_cloudwatch_logs_once(db, name, q, defaults))
        except Exception as e:
            db.rollback()
            out.append({"name": name, "error": str(e)})
    return out
