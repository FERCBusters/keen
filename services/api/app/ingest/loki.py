from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import yaml
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Event, Artifact, IngestionCursor, ControlItem, Mapping
from app.ingest.common import is_safe_url
from app.storage.s3 import put_bytes
from app.mapping.rules import load_rules, evaluate_by_framework
from app.security.redaction import redact_obj, redact_bytes


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """Return a timezone-aware UTC datetime.

    Some deployments store cursor timestamps as TIMESTAMP WITHOUT TIME ZONE.
    SQLAlchemy then returns offset-naive datetimes, which cannot be compared to
    timezone-aware values (e.g. datetime.now(timezone.utc)).
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_ns(dt: datetime) -> int:
    # Loki expects ns timestamps for start/end in query_range.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


def _loki_client() -> httpx.Client:
    headers = {}
    if settings.loki_header_name and settings.loki_header_value:
        headers[settings.loki_header_name] = settings.loki_header_value

    auth = None
    if settings.loki_username and settings.loki_password:
        auth = (settings.loki_username, settings.loki_password)

    # Validate the base URL to prevent SSRF attacks
    base_url = settings.loki_base_url.rstrip("/")
    if not is_safe_url(base_url):
        raise ValueError("Invalid or unsafe Loki base URL")

    return httpx.Client(
        base_url=base_url,
        headers=headers,
        auth=auth,
        timeout=30.0,
        verify=True,
    )


def load_loki_queries(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _fingerprint(ts_ns: str, labels: dict[str, str], line: str) -> str:
    payload = f"{ts_ns}|{json.dumps(labels, sort_keys=True)}|{line}".encode(
        "utf-8", errors="replace"
    )
    return hashlib.sha256(payload).hexdigest()[:32]


# --- parsing helpers ---


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
    """Extract a compact HTTP request from common access-log formats.

    Example:
      "... \"GET /path?query=... HTTP/1.1\" ..." -> "GET /path"
    """
    s = str(log_line or "")
    m = re.search(r"\"(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+([^\s\"]+)", s)
    if not m:
        return ""
    method = m.group(1)
    path = m.group(2)
    # Drop querystring (usually the noisiest part)
    path = path.split("?", 1)[0]
    # Keep it compact even if someone logs a massive path
    path = _truncate(path, 120)
    return f"{method} {path}"


def _summarize_json(
    parsed: Any, labels: dict[str, str], line: str
) -> tuple[str, dict[str, Any]]:
    """Return (summary, extracted). Handles OSSEC/"rule" shaped logs well."""
    extracted: dict[str, Any] = {}
    if isinstance(parsed, dict):
        rule = parsed.get("rule") if isinstance(parsed.get("rule"), dict) else {}
        sid = rule.get("sidid") or rule.get("sid") or rule.get("id")
        comment = rule.get("comment") or rule.get("description")
        level = (
            parsed.get("rule.level_extracted")
            or rule.get("level_extracted")
            or rule.get("level")
            or parsed.get("level")
        )

        agent = (
            parsed.get("agent_name")
            or parsed.get("hostname")
            or parsed.get("agent")
            or labels.get("agent")
        )
        user = (
            parsed.get("user")
            or parsed.get("srcuser")
            or parsed.get("dstuser")
            or parsed.get("program_name")
        )
        full_log = (
            parsed.get("full_log")
            or parsed.get("message")
            or parsed.get("msg")
            or parsed.get("log")
        )

        # stash useful extracted fields
        if sid is not None:
            extracted["sid"] = sid
        if comment:
            extracted["rule_comment"] = comment
        if level is not None:
            extracted["rule_level"] = level
        if agent:
            extracted["agent"] = agent
        if user:
            extracted["user"] = user

        parts = []
        if sid is not None:
            parts.append(f"[sid:{sid}]")
        if comment:
            parts.append(str(comment))
        if agent:
            parts.append(f"@ {agent}")
        if user:
            parts.append(f"user={user}")
        if full_log:
            brief = _brief_http_request(str(full_log))
            parts.append("-")
            parts.append(brief or _truncate(str(full_log), 120))

        summary = " ".join([p for p in parts if p])
        if summary:
            return summary, extracted

        # fall back to common message-ish fields
        for k in ("message", "msg", "event", "text"):
            if isinstance(parsed.get(k), str) and parsed.get(k).strip():
                return _truncate(parsed.get(k), 260), extracted

    # Default: keep line but truncate to keep UI usable
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
            # Create placeholder, titles/rationale come later from SoA import.
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
    # Use ON CONFLICT DO NOTHING to avoid noisy constraint errors.
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


def _setting_from_query(
    query_config: dict[str, Any], defaults: dict[str, Any], key: str, fallback: Any
) -> Any:
    """Resolve a Loki setting from query config, then defaults, then settings.py."""
    if key in query_config:
        return query_config.get(key)
    if key in defaults:
        return defaults.get(key)
    return fallback


def _positive_int(value: Any, fallback: int, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = fallback
    if parsed < minimum:
        return fallback if fallback >= minimum else minimum
    return parsed


def _normalize_loki_direction(value: Any) -> str:
    direction = str(value or "forward").strip().lower()
    if direction not in {"forward", "backward"}:
        return "forward"
    return direction


def ingest_loki_once(
    db: Session,
    query_name: str,
    logql: str,
    event_defaults: dict[str, Any],
    query_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    query_config = query_config or {}

    # Cursor
    cursor_name = f"loki:{query_name}"
    cur = (
        db.query(IngestionCursor)
        .filter(IngestionCursor.name == cursor_name)
        .one_or_none()
    )
    if cur is None:
        cur = IngestionCursor(name=cursor_name, last_ts=None, meta={})
        db.add(cur)
        db.flush()

    # Normalize cursor timestamps to tz-aware UTC. Some Postgres schemas store
    # these as TIMESTAMP WITHOUT TIME ZONE, which SQLAlchemy returns as naive
    # datetimes. Comparing those to tz-aware datetimes raises:
    # "can't compare offset-naive and offset-aware datetimes".
    cur_last_ts = _ensure_utc(cur.last_ts)
    if cur_last_ts is not cur.last_ts:
        cur.last_ts = cur_last_ts

    cfg = load_loki_queries(settings.loki_queries_path)
    defaults = cfg.get("defaults", {}) or {}

    limit = _positive_int(
        _setting_from_query(query_config, defaults, "limit", 5000), 5000, minimum=1
    )
    direction = _normalize_loki_direction(
        _setting_from_query(query_config, defaults, "direction", "forward")
    )
    step_seconds = _positive_int(
        _setting_from_query(query_config, defaults, "step_seconds", 0), 0, minimum=0
    )
    initial_lookback_minutes = _positive_int(
        _setting_from_query(
            query_config,
            defaults,
            "initial_lookback_minutes",
            settings.loki_initial_lookback_minutes,
        ),
        settings.loki_initial_lookback_minutes,
        minimum=0,
    )
    max_query_range_hours = _positive_int(
        _setting_from_query(
            query_config,
            defaults,
            "max_query_range_hours",
            settings.loki_max_query_range_hours,
        ),
        settings.loki_max_query_range_hours,
        minimum=0,
    )
    max_catchup_chunks = _positive_int(
        _setting_from_query(
            query_config,
            defaults,
            "catchup_chunks_per_run",
            settings.loki_catchup_chunks_per_run,
        ),
        settings.loki_catchup_chunks_per_run,
        minimum=1,
    )
    overlong_strategy = (
        str(
            _setting_from_query(
                query_config,
                defaults,
                "overlong_range_strategy",
                settings.loki_overlong_range_strategy,
            )
            or "chunk"
        )
        .strip()
        .lower()
    )
    if overlong_strategy not in {"chunk", "reset"}:
        overlong_strategy = "chunk"

    # Time window. New cursors look back a little so startup clock skew does not
    # miss recent rows. Existing cursors continue from the last processed Loki
    # timestamp plus a tiny epsilon to avoid re-fetching the exact boundary row.
    now = datetime.now(timezone.utc)
    start = (
        (cur_last_ts + timedelta(microseconds=1))
        if cur_last_ts
        else (now - timedelta(minutes=initial_lookback_minutes))
    )
    final_end = now
    max_range = (
        timedelta(hours=max_query_range_hours) if max_query_range_hours > 0 else None
    )

    requested_seconds = max(0.0, (final_end - start).total_seconds())
    max_seconds = max_range.total_seconds() if max_range is not None else None

    # If the cursor is too far behind and the operator prefers not to backfill,
    # reset it to now and skip the Loki call. The next scheduled run will ingest
    # only new rows.
    if (
        max_range is not None
        and requested_seconds > max_range.total_seconds()
        and overlong_strategy == "reset"
    ):
        meta = dict(cur.meta or {})
        meta.update(
            {
                "last_reset_at": now.isoformat(),
                "last_reset_reason": "loki query range exceeded configured maximum",
                "last_reset_previous_cursor": (
                    cur_last_ts.isoformat() if cur_last_ts else None
                ),
                "last_reset_max_query_range_hours": max_query_range_hours,
            }
        )
        cur.meta = meta
        cur.last_ts = now
        cur.updated_at = datetime.utcnow()
        db.add(cur)
        db.commit()
        return {
            "query_name": query_name,
            "skipped": True,
            "strategy": "reset",
            "reason": "cursor was older than the configured Loki query range limit",
            "requested_range_seconds": requested_seconds,
            "max_query_range_seconds": max_seconds,
            "cursor_last_ts": cur.last_ts.isoformat() if cur.last_ts else None,
        }

    created_events = 0
    created_artifacts = 0
    created_mappings = 0
    chunks: list[dict[str, Any]] = []
    processed_chunks = 0

    # Loki query_range endpoint. When the cursor is far behind and strategy is
    # "chunk", each pass asks Loki for only one bounded window. If the response
    # hits the Loki limit, advance only to the newest seen timestamp; otherwise
    # advance to the end of that chunk so empty trailing periods are not re-read.
    with _loki_client() as client:
        while start < final_end and processed_chunks < max_catchup_chunks:
            end = final_end
            if max_range is not None:
                end = min(start + max_range, final_end)

            params = {
                "query": logql,
                "start": str(_to_ns(start)),
                "end": str(_to_ns(end)),
                "limit": str(limit),
                "direction": direction,
            }
            if step_seconds > 0:
                params["step"] = str(step_seconds)

            resp = client.get("/loki/api/v1/query_range", params=params)
            resp.raise_for_status()
            data = resp.json()

            results = (data.get("data", {}) or {}).get("result", []) or []
            chunk_created_events = 0
            chunk_created_artifacts = 0
            chunk_created_mappings = 0
            chunk_returned_values = 0
            chunk_max_seen_ts: datetime | None = None

            for stream in results:
                labels = stream.get("stream", {}) or {}
                values = stream.get("values", []) or []
                chunk_returned_values += len(values)
                for ts_ns, line in values:
                    # Loki timestamps are strings; keep for fingerprint
                    external_id = _fingerprint(ts_ns, labels, line)
                    ts = datetime.fromtimestamp(
                        int(ts_ns) / 1_000_000_000, tz=timezone.utc
                    )
                    parsed = _try_parse_json_line(line)
                    summary = line
                    actor = None
                    normalized: dict[str, Any] = {"labels": labels, "line": line}

                    if parsed is not None:
                        summary, extracted = _summarize_json(parsed, labels, line)
                        normalized["json"] = parsed
                        if extracted:
                            normalized["extracted"] = extracted
                        # If we managed to extract a user, treat it as the actor.
                        actor = (
                            extracted.get("user")
                            if isinstance(extracted, dict)
                            else None
                        )
                    else:
                        # Keep plain-text lines readable too
                        summary = _truncate(line, 340)

                    raw_pointer = {
                        "loki": {
                            "query_name": query_name,
                            "logql": logql,
                            "labels": labels,
                            "ts_ns": ts_ns,
                        }
                    }

                    # Hardening: redact before persist
                    raw_pointer_r = redact_obj(raw_pointer) or {}
                    normalized_r = redact_obj(normalized) or {}

                    # Insert event with ON CONFLICT DO NOTHING to avoid noisy duplicate-key logs.
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
                            source="loki",
                            system=event_defaults.get("system"),
                            actor=actor,
                            action=event_defaults.get("action"),
                            outcome=event_defaults.get("outcome"),
                            severity=sev,
                            summary=summary,
                            raw_pointer=raw_pointer_r,
                            normalized_payload=normalized_r,
                            external_id=external_id,
                        )
                        .on_conflict_do_nothing(
                            index_elements=["source", "external_id"]
                        )
                        .returning(Event.id)
                    )
                    inserted_id = db.execute(stmt).scalar()
                    if inserted_id is None:
                        # deduped
                        if (chunk_max_seen_ts is None) or (ts > chunk_max_seen_ts):
                            chunk_max_seen_ts = ts
                        continue

                    created_events += 1
                    chunk_created_events += 1

                    # Store artifact (raw log line) as immutable object
                    ext = "json" if parsed is not None else "log"
                    ctype = (
                        "application/json"
                        if parsed is not None
                        else "text/plain; charset=utf-8"
                    )
                    key = (
                        f"loki/{query_name}/{ts.date().isoformat()}/{inserted_id}.{ext}"
                    )
                    line_bytes, redaction_status = redact_bytes(
                        line.encode("utf-8", errors="replace"), ctype
                    )
                    stored = put_bytes(key=key, data=line_bytes, content_type=ctype)
                    art = Artifact(
                        event_id=inserted_id,
                        kind="log_line",
                        storage_uri=stored.uri,
                        sha256=stored.sha256,
                        content_type=ctype,
                        size_bytes=stored.size_bytes,
                        captured_by="keen:loki",
                        redaction_status=redaction_status,
                    )
                    db.add(art)
                    db.flush()
                    created_artifacts += 1
                    chunk_created_artifacts += 1

                    # Apply mapping rules
                    ev_for_rules = Event(
                        id=inserted_id,
                        timestamp=ts,
                        source="loki",
                        system=event_defaults.get("system"),
                        actor=actor,
                        action=event_defaults.get("action"),
                        outcome=event_defaults.get("outcome"),
                        severity=sev,
                        summary=summary,
                        raw_pointer=raw_pointer_r,
                        normalized_payload=normalized_r,
                        external_id=external_id,
                    )
                    mapped = _apply_rules_and_store_mappings(db, ev_for_rules)
                    created_mappings += mapped
                    chunk_created_mappings += mapped

                    if (chunk_max_seen_ts is None) or (ts > chunk_max_seen_ts):
                        chunk_max_seen_ts = ts

            hit_limit = chunk_returned_values >= limit
            advance_to = chunk_max_seen_ts if (hit_limit and chunk_max_seen_ts) else end
            cur.last_ts = advance_to
            cur.updated_at = datetime.utcnow()
            meta = dict(cur.meta or {})
            meta.update(
                {
                    "last_run_at": now.isoformat(),
                    "last_run_strategy": overlong_strategy,
                    "last_run_hit_limit": hit_limit,
                    "last_run_chunk_start": start.isoformat(),
                    "last_run_chunk_end": end.isoformat(),
                }
            )
            cur.meta = meta
            db.add(cur)
            db.commit()

            chunks.append(
                {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "returned_values": chunk_returned_values,
                    "hit_limit": hit_limit,
                    "created_events": chunk_created_events,
                    "created_artifacts": chunk_created_artifacts,
                    "created_mappings": chunk_created_mappings,
                    "advanced_cursor_to": (
                        cur.last_ts.isoformat() if cur.last_ts else None
                    ),
                }
            )

            processed_chunks += 1
            next_start_base = _ensure_utc(cur.last_ts) or end
            start = next_start_base + timedelta(microseconds=1)

    caught_up = start >= final_end
    return {
        "query_name": query_name,
        "start": chunks[0]["start"] if chunks else start.isoformat(),
        "end": chunks[-1]["end"] if chunks else final_end.isoformat(),
        "created_events": created_events,
        "created_artifacts": created_artifacts,
        "created_mappings": created_mappings,
        "cursor_last_ts": cur.last_ts.isoformat() if cur.last_ts else None,
        "strategy": overlong_strategy,
        "caught_up": caught_up,
        "chunks_processed": processed_chunks,
        "chunks_remaining": not caught_up,
        "max_query_range_hours": max_query_range_hours,
        "catchup_chunks_per_run": max_catchup_chunks,
        "chunks": chunks,
    }


def ingest_loki_all(db: Session) -> list[dict[str, Any]]:
    if not settings.loki_enabled:
        return [{"skipped": True, "reason": "KEEN_LOKI_ENABLED=false"}]
    cfg = load_loki_queries(settings.loki_queries_path)
    out: list[dict[str, Any]] = []
    for q in cfg.get("queries", []) or []:
        name = q["name"]
        logql = q["logql"]
        event_defaults = q.get("event", {}) or {}
        try:
            out.append(ingest_loki_once(db, name, logql, event_defaults, q))
        except Exception as e:
            db.rollback()
            out.append({"query_name": name, "error": str(e)})
    return out
