from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core.cache import cache_delete_prefix, cache_get_json, cache_set_json
from app.core.config import settings
from app.core.valkey import get_valkey
from app.db.models import (
    ControlEvidenceStats,
    ControlItem,
    Event,
    Mapping,
)

_EMPTY_SENTINEL = "__empty__"
_CACHE_PREFIX = "keen:stats:v1:control_evidence"
_EVENT_COUNTS_CACHE_NAMESPACE = "stats:framework-event-counts"
_EVENT_COUNTS_CACHE_VERSION = "celery-count-v1"


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _safe_framework_part(framework: str) -> str:
    return "".join(
        ch if ch.isalnum() or ch in ("-", "_", ":", ".") else "_"
        for ch in str(framework or "default")
    )


def control_evidence_stats_cache_key(framework: str) -> str:
    """Return the Valkey hash key used to mirror per-control aggregate stats."""

    return f"{_CACHE_PREFIX}:{_safe_framework_part(framework)}"


def clear_control_evidence_stats_cache(framework: str | None = None) -> int:
    """Best-effort deletion of mirrored control evidence stats from Valkey."""

    try:
        client = get_valkey()
        if framework:
            return int(client.delete(control_evidence_stats_cache_key(framework)) or 0)
        deleted = 0
        batch: list[str] = []
        for key in client.scan_iter(f"{_CACHE_PREFIX}:*"):
            batch.append(str(key))
            if len(batch) >= 500:
                deleted += int(client.delete(*batch) or 0)
                batch = []
        if batch:
            deleted += int(client.delete(*batch) or 0)
        return deleted
    except Exception:
        return 0


def clear_stats_caches(framework: str | None = None) -> dict[str, int]:
    """Clear cached aggregate stats used by dashboards and event counters."""

    return {
        "aggregate_cache_keys_deleted": cache_delete_prefix(),
        "control_evidence_stats_cache_keys_deleted": clear_control_evidence_stats_cache(
            framework
        ),
    }


def _decode_cached_payload(
    raw: dict[str, str],
) -> dict[uuid.UUID, dict[str, Any]] | None:
    if not raw:
        return None
    stats: dict[uuid.UUID, dict[str, Any]] = {}
    for control_id, payload in raw.items():
        if control_id == _EMPTY_SENTINEL:
            continue
        try:
            parsed = json.loads(payload)
            stats[uuid.UUID(str(control_id))] = {
                "evidence_count": int(parsed.get("evidence_count") or 0),
                "last_evidence": parsed.get("last_evidence"),
            }
        except Exception:
            return None
    return stats


def _load_from_cache(framework: str) -> dict[uuid.UUID, dict[str, Any]] | None:
    ttl = int(getattr(settings, "aggregate_cache_ttl_seconds", 0) or 0)
    if ttl <= 0:
        return None
    try:
        return _decode_cached_payload(
            get_valkey().hgetall(control_evidence_stats_cache_key(framework))
        )
    except Exception:
        return None


def _store_in_cache(framework: str, stats: dict[uuid.UUID, dict[str, Any]]) -> None:
    ttl = int(getattr(settings, "aggregate_cache_ttl_seconds", 0) or 0)
    if ttl <= 0:
        return
    try:
        key = control_evidence_stats_cache_key(framework)
        client = get_valkey()
        payload = {
            str(control_id): json.dumps(
                {
                    "evidence_count": int(item.get("evidence_count") or 0),
                    "last_evidence": item.get("last_evidence"),
                },
                sort_keys=True,
                separators=(",", ":"),
                default=_json_default,
            )
            for control_id, item in stats.items()
        }
        if not payload:
            payload = {_EMPTY_SENTINEL: "1"}
        pipe = client.pipeline(transaction=False)
        pipe.delete(key)
        pipe.hset(key, mapping=payload)
        pipe.expire(key, ttl)
        pipe.execute()
    except Exception:
        return


def get_control_evidence_stats_by_id(
    db: Session, framework: str
) -> dict[uuid.UUID, dict[str, Any]]:
    """Return per-control evidence aggregate stats for a framework.

    The durable source of truth is the Postgres ``control_evidence_stats`` table,
    which is maintained by database triggers. Valkey mirrors the table briefly to
    keep hot dashboard loads cheap; callers must tolerate Valkey misses.
    """

    cached = _load_from_cache(framework)
    if cached is not None:
        return cached

    rows = (
        db.query(
            ControlEvidenceStats.control_item_id,
            ControlEvidenceStats.evidence_count,
            ControlEvidenceStats.last_evidence,
        )
        .join(ControlItem, ControlItem.id == ControlEvidenceStats.control_item_id)
        .filter(ControlItem.framework_slug == framework)
        .all()
    )
    stats: dict[uuid.UUID, dict[str, Any]] = {}
    for control_id, evidence_count, last_evidence in rows:
        stats[control_id] = {
            "evidence_count": int(evidence_count or 0),
            "last_evidence": last_evidence.isoformat() if last_evidence else None,
        }

    _store_in_cache(framework, stats)
    return stats


def _framework_event_counts_cache_parts(framework: str) -> dict[str, str]:
    return {
        "framework": framework or settings.default_framework_slug or "default",
        "counter_version": _EVENT_COUNTS_CACHE_VERSION,
    }


def _normalise_event_counts(total_events: int, mapped_events: int) -> dict[str, int]:
    total_int = int(total_events or 0)
    mapped_int = int(mapped_events or 0)
    return {
        "total_events": total_int,
        "mapped_events": mapped_int,
        "unmapped_events": max(0, total_int - mapped_int),
    }


def _compute_framework_event_counts(
    db: Session, framework: str, *, total_events: int | None = None
) -> dict[str, int]:
    """Count mapped/unmapped events directly from events and mappings.

    This intentionally does not read the legacy ``global_event_stats`` or
    ``framework_event_stats`` trigger-maintained tables. The result is cached
    for ``KEEN_AGGREGATE_CACHE_TTL_SECONDS`` and periodically warmed by Celery.
    """

    total = (
        int(total_events)
        if total_events is not None
        else int(db.query(func.count(Event.id)).scalar() or 0)
    )
    mapped = (
        db.query(func.count(func.distinct(Mapping.event_id)))
        .join(ControlItem, ControlItem.id == Mapping.control_item_id)
        .filter(ControlItem.framework_slug == framework)
        .scalar()
        or 0
    )
    return _normalise_event_counts(total, int(mapped or 0))


def _store_framework_event_counts(framework: str, counts: dict[str, int]) -> None:
    cache_set_json(
        _EVENT_COUNTS_CACHE_NAMESPACE,
        _framework_event_counts_cache_parts(framework),
        _normalise_event_counts(
            int(counts.get("total_events") or 0),
            int(counts.get("mapped_events") or 0),
        ),
    )


def get_framework_event_counts(db: Session, framework: str) -> dict[str, int]:
    """Return cached framework-scoped mapped/unmapped event counts.

    ``total_events`` is the total evidence universe visible to authenticated
    users. ``mapped_events`` is the number of distinct events mapped to at least
    one control in ``framework``. Therefore ``unmapped_events`` means events
    with no mapping in this framework, even if they are mapped in another
    framework.
    """

    cached = cache_get_json(
        _EVENT_COUNTS_CACHE_NAMESPACE, _framework_event_counts_cache_parts(framework)
    )
    if isinstance(cached, dict):
        try:
            return _normalise_event_counts(
                int(cached.get("total_events") or 0),
                int(cached.get("mapped_events") or 0),
            )
        except Exception:
            pass

    counts = _compute_framework_event_counts(db, framework)
    _store_framework_event_counts(framework, counts)
    return counts


def refresh_framework_event_counts_cache(
    db: Session, framework: str | None = None
) -> dict[str, dict[str, int]]:
    """Refresh cached global mapped/unmapped counters for one or all frameworks."""

    total_events = int(db.query(func.count(Event.id)).scalar() or 0)
    frameworks: list[str]
    if framework:
        frameworks = [framework]
    else:
        rows = db.query(ControlItem.framework_slug).distinct().all()
        frameworks = sorted(
            {str(slug) for (slug,) in rows if slug}
            | {settings.default_framework_slug or "default"}
        )

    refreshed: dict[str, dict[str, int]] = {}
    for slug in frameworks:
        counts = _compute_framework_event_counts(db, slug, total_events=total_events)
        _store_framework_event_counts(slug, counts)
        refreshed[slug] = counts
    return refreshed


def rebuild_control_evidence_stats(db: Session, *, clear_cache: bool = True) -> int:
    """Recompute the durable aggregate table from mappings/events.

    This is primarily a repair/maintenance hook. Normal operation is handled by
    database triggers attached to the mappings table.
    """

    db.execute(text("TRUNCATE control_evidence_stats"))
    result = db.execute(text("""
            INSERT INTO control_evidence_stats (
                control_item_id,
                evidence_count,
                last_evidence,
                updated_at
            )
            SELECT
                m.control_item_id,
                COUNT(*)::integer AS evidence_count,
                MAX(e.timestamp) AS last_evidence,
                NOW() AS updated_at
            FROM mappings m
            JOIN events e ON e.id = m.event_id
            GROUP BY m.control_item_id
            """))
    if clear_cache:
        clear_stats_caches()
    return int(result.rowcount or 0)


def rebuild_framework_event_stats(
    db: Session, *, clear_cache: bool = True
) -> dict[str, int]:
    """Compatibility wrapper: refresh the Valkey event counter cache.

    Older callers used this after bulk remapping. The Postgres trigger-maintained
    counter tables are no longer the source of truth for global mapped/unmapped
    counters, so this now just refreshes the cached live counts.
    """

    if clear_cache:
        clear_stats_caches()
    refreshed = refresh_framework_event_counts_cache(db)
    return {"framework_event_counter_cache": len(refreshed)}
