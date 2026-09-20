from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.utils import (
    control_justification as _control_justification,
    control_upstream_url as _control_upstream_url,
    ref_sort_key as _ref_sort_key,
)
from app.core.config import settings
from app.core.cache import cached_json
from app.db.models import ControlItem, Event, Mapping, User
from app.db.session import get_db
from app.security.diary_visibility import diary_filter_condition
from app.services.control_evidence_stats import (
    get_control_evidence_stats_by_id,
    get_framework_event_counts,
)
from app.core.source_meta import get_source_meta, apply_user_source_overrides

router = APIRouter()


def _event_visibility_cache_scope(user) -> str:
    """Cache scope for event/count endpoints after global diary visibility."""
    if isinstance(user, User) and getattr(user, "is_active", False):
        return "authenticated"
    return "anonymous"


@router.get("/v1/stats/summary")
def stats_summary(
    request: Request,
    framework: str = settings.default_framework_slug,
    db: Session = Depends(get_db),
):
    user = getattr(request.state, "user", None)
    cache_parts = {
        "framework": framework,
        "visibility": _event_visibility_cache_scope(user),
        # Bump the cache signature so older Valkey summaries do not continue
        # to show stale mapped/unmapped counters from the legacy trigger-backed
        # global event stats path.
        "counter_version": "celery-event-counts-v1",
    }

    def _load() -> dict:
        def _summary_payload(
            *,
            total_events: int,
            mapped_events: int,
            total_controls: int,
            in_scope_controls: int,
            controls_with_evidence_in_scope: int,
            controls_with_evidence_all: int,
            stats_source: str,
        ) -> dict:
            unmapped_events = max(0, int(total_events) - int(mapped_events))
            out_of_scope_controls = max(0, int(total_controls) - int(in_scope_controls))
            controls_without_evidence_in_scope = max(
                0, int(in_scope_controls) - int(controls_with_evidence_in_scope)
            )
            controls_without_evidence_all = max(
                0, int(total_controls) - int(controls_with_evidence_all)
            )

            return {
                "framework": framework,
                "events": {
                    "total": int(total_events),
                    "mapped": int(mapped_events),
                    "unmapped": int(unmapped_events),
                },
                "controls": {
                    "total": int(total_controls),
                    "in_scope": int(in_scope_controls),
                    "out_of_scope": int(out_of_scope_controls),
                    # Backwards compatible keys now report IN-SCOPE coverage.
                    "with_evidence": int(controls_with_evidence_in_scope),
                    "without_evidence": int(controls_without_evidence_in_scope),
                    # New explicit keys.
                    "with_evidence_in_scope": int(controls_with_evidence_in_scope),
                    "without_evidence_in_scope": int(
                        controls_without_evidence_in_scope
                    ),
                    "with_evidence_all": int(controls_with_evidence_all),
                    "without_evidence_all": int(controls_without_evidence_all),
                },
                "stats_source": stats_source,
            }

        # Fast path for authenticated users: diary events are now globally
        # visible to active users, so these dashboard counters use the global
        # framework event-count cache instead of legacy trigger-maintained
        # Postgres counter tables.
        if isinstance(user, User) and getattr(user, "is_active", False):
            controls = (
                db.query(ControlItem.id, ControlItem.in_scope)
                .filter(ControlItem.framework_slug == framework)
                .all()
            )
            stats_by_id = get_control_evidence_stats_by_id(db, framework)
            controls_with_evidence_all = 0
            controls_with_evidence_in_scope = 0
            in_scope_controls = 0
            for control_id, in_scope in controls:
                if in_scope:
                    in_scope_controls += 1
                evidence_count = int(
                    (stats_by_id.get(control_id) or {}).get("evidence_count") or 0
                )
                if evidence_count > 0:
                    controls_with_evidence_all += 1
                    if in_scope:
                        controls_with_evidence_in_scope += 1

            event_counts = get_framework_event_counts(db, framework)
            return _summary_payload(
                total_events=event_counts["total_events"],
                mapped_events=event_counts["mapped_events"],
                total_controls=len(controls),
                in_scope_controls=in_scope_controls,
                controls_with_evidence_in_scope=controls_with_evidence_in_scope,
                controls_with_evidence_all=controls_with_evidence_all,
                stats_source="cached_event_counts",
            )

        visibility = diary_filter_condition(db, user)

        total_events = db.query(func.count(Event.id)).filter(visibility).scalar() or 0

        mapped_events = (
            db.query(func.count(func.distinct(Mapping.event_id)))
            .join(Event, Event.id == Mapping.event_id)
            .join(ControlItem, ControlItem.id == Mapping.control_item_id)
            .filter(ControlItem.framework_slug == framework)
            .filter(visibility)
            .scalar()
            or 0
        )

        total_controls = (
            db.query(func.count(ControlItem.id))
            .filter(ControlItem.framework_slug == framework)
            .scalar()
            or 0
        )
        in_scope_controls = (
            db.query(func.count(ControlItem.id))
            .filter(
                ControlItem.framework_slug == framework, ControlItem.in_scope == True
            )  # noqa: E712
            .scalar()
            or 0
        )

        # Evidence coverage should be reported for IN-SCOPE controls by default.
        controls_with_evidence_in_scope = (
            db.query(func.count(func.distinct(Mapping.control_item_id)))
            .join(ControlItem, ControlItem.id == Mapping.control_item_id)
            .join(Event, Event.id == Mapping.event_id)
            .filter(
                ControlItem.framework_slug == framework, ControlItem.in_scope == True
            )  # noqa: E712
            .filter(visibility)
            .scalar()
            or 0
        )

        # Also expose "all controls" coverage for completeness.
        controls_with_evidence_all = (
            db.query(func.count(func.distinct(Mapping.control_item_id)))
            .join(ControlItem, ControlItem.id == Mapping.control_item_id)
            .join(Event, Event.id == Mapping.event_id)
            .filter(ControlItem.framework_slug == framework)
            .filter(visibility)
            .scalar()
            or 0
        )

        return _summary_payload(
            total_events=int(total_events),
            mapped_events=int(mapped_events),
            total_controls=int(total_controls),
            in_scope_controls=int(in_scope_controls),
            controls_with_evidence_in_scope=int(controls_with_evidence_in_scope),
            controls_with_evidence_all=int(controls_with_evidence_all),
            stats_source="live_query",
        )

    return cached_json("stats:summary", cache_parts, _load)


@router.get("/v1/stats/controls")
def stats_controls(
    request: Request,
    framework: str = settings.default_framework_slug,
    limit: int = 2000,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db),
):
    user = getattr(request.state, "user", None)
    limit = max(1, min(int(limit or 2000), 5000))
    cache_parts = {
        "framework": framework,
        "limit": limit,
        "start_date": start_date,
        "end_date": end_date,
        "visibility": _event_visibility_cache_scope(user),
    }

    def _load() -> dict:
        def _item(c: ControlItem, evidence_count: int, last_evidence) -> dict:
            if isinstance(last_evidence, datetime):
                last_evidence_value = last_evidence.isoformat()
            else:
                last_evidence_value = last_evidence or None
            return {
                "id": str(c.id),
                "framework": c.framework_slug,
                "type": c.type,
                "ref": c.ref,
                "title": c.title,
                "in_scope": c.in_scope,
                "justification": _control_justification(c),
                "upstream_url": _control_upstream_url(c),
                "evidence_count": int(evidence_count or 0),
                "last_evidence": last_evidence_value,
            }

        # Fast path: diary events are globally visible to active authenticated
        # users, so the durable aggregate table is exact for all such callers
        # without a date window. Date windows still need the precise query below.
        if (
            start_date is None
            and end_date is None
            and isinstance(user, User)
            and getattr(user, "is_active", False)
        ):
            stats_by_id = get_control_evidence_stats_by_id(db, framework)
            controls = (
                db.query(ControlItem)
                .filter(ControlItem.framework_slug == framework)
                .all()
            )
            items = []
            for c in controls:
                stats = stats_by_id.get(c.id, {})
                items.append(
                    _item(
                        c,
                        int(stats.get("evidence_count") or 0),
                        stats.get("last_evidence"),
                    )
                )
            items.sort(key=lambda it: (it["type"], _ref_sort_key(it["ref"])))
            return {"items": items[:limit], "stats_source": "control_evidence_stats"}

        # Optional date window for evidence counts.
        # Important: keep controls with zero evidence in-range.
        event_filters = []
        if start_date:
            event_filters.append(
                Event.timestamp >= datetime.combine(start_date, datetime.min.time())
            )
        if end_date:
            end_excl = datetime.combine(
                end_date + timedelta(days=1), datetime.min.time()
            )
            event_filters.append(Event.timestamp < end_excl)

        # Aggregate evidence by control first, then left-join to the comparatively
        # small controls table. This remains the exact path for date-filtered
        # requests.
        evidence_sq = (
            db.query(
                Mapping.control_item_id.label("control_item_id"),
                func.count(Mapping.event_id).label("evidence_count"),
                func.max(Event.timestamp).label("last_evidence"),
            )
            .join(Event, Event.id == Mapping.event_id)
            .join(ControlItem, ControlItem.id == Mapping.control_item_id)
            .filter(ControlItem.framework_slug == framework)
            .filter(diary_filter_condition(db, user))
            .filter(*event_filters)
            .group_by(Mapping.control_item_id)
            .subquery()
        )

        rows = (
            db.query(
                ControlItem,
                func.coalesce(evidence_sq.c.evidence_count, 0).label("evidence_count"),
                evidence_sq.c.last_evidence.label("last_evidence"),
            )
            .outerjoin(evidence_sq, evidence_sq.c.control_item_id == ControlItem.id)
            .filter(ControlItem.framework_slug == framework)
            .all()
        )

        items = [
            _item(c, evidence_count, last_evidence)
            for c, evidence_count, last_evidence in rows
        ]
        items.sort(key=lambda it: (it["type"], _ref_sort_key(it["ref"])))
        return {"items": items[:limit], "stats_source": "live_query"}

    return cached_json("stats:controls", cache_parts, _load)


@router.get("/v1/charts/event_volume_timeseries")
def stats_events_timeseries(
    request: Request,
    days: int = 120,
    framework: str = settings.default_framework_slug,
    source: Optional[str] = None,
    breakdown: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    # Optional timestamp window (UTC, end exclusive). When provided, it takes
    # precedence over start_date/end_date/days.
    start_ts: Optional[datetime] = None,
    end_ts: Optional[datetime] = None,
    # Bucket size ("day"|"hour"|"minute"|"second").
    interval: str = "day",
    db: Session = Depends(get_db),
):
    """Return event volume over time.

    The histogram is bucketed by `interval` (default: daily). The endpoint
    supports either a date window (start_date/end_date inclusive) or a
    timestamp window (start_ts/end_ts, end exclusive).

    Counts:
    - total: total events
    - mapped: distinct events mapped to the requested framework
    - unmapped: total - mapped

    This endpoint is used by the UI visualisations (histograms).
    """

    user = getattr(request.state, "user", None)

    def _iso_z(dt: datetime) -> str:
        """Format a naive datetime as UTC with a trailing Z."""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def _naive_utc(dt: datetime) -> datetime:
        """Convert an aware datetime to a naive UTC datetime (for DB comparisons)."""
        if dt.tzinfo is None:
            return dt
        return dt.astimezone(timezone.utc).replace(tzinfo=None)

    def _floor(dt: datetime, unit: str) -> datetime:
        unit = (unit or "day").strip().lower()
        if unit == "day":
            return dt.replace(hour=0, minute=0, second=0, microsecond=0)
        if unit == "hour":
            return dt.replace(minute=0, second=0, microsecond=0)
        if unit == "minute":
            return dt.replace(second=0, microsecond=0)
        if unit == "second":
            return dt.replace(microsecond=0)
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)

    def _step_for(unit: str) -> timedelta:
        unit = (unit or "day").strip().lower()
        if unit == "second":
            return timedelta(seconds=1)
        if unit == "minute":
            return timedelta(minutes=1)
        if unit == "hour":
            return timedelta(hours=1)
        return timedelta(days=1)

    # Normalise interval.
    interval_req = (interval or "day").strip().lower()
    if interval_req not in {"day", "hour", "minute", "second"}:
        interval_req = "day"

    # If a timestamp window is provided, it takes precedence.
    # Note: end_ts is treated as an exclusive bound.
    if start_ts is not None or end_ts is not None:
        if end_ts is None:
            end_ts = datetime.utcnow()
        if start_ts is None:
            days = max(1, min(int(days or 120), 3650))
            start_ts = end_ts - timedelta(days=days)
        if start_ts > end_ts:
            start_ts, end_ts = end_ts, start_ts

        # Convert any timezone-aware inputs to naive UTC timestamps.
        start_ts = _naive_utc(start_ts)
        end_ts = _naive_utc(end_ts)

        # Keep the filter bounds as provided (so callers can request partial buckets),
        # but we will floor bucket generation boundaries later.
        start = start_ts
        end_excl = end_ts
        if end_excl <= start:
            end_excl = start + _step_for(interval_req)

        # Keep start_date/end_date in sync for legacy fields.
        start_date = start.date()
        end_date = (end_excl - timedelta(microseconds=1)).date()
    # Otherwise: date window if provided.
    elif start_date or end_date:
        if end_date is None:
            end_date = datetime.utcnow().date()
        if start_date is None:
            # Backfill start from `days` (inclusive window).
            days = max(1, min(int(days or 120), 3650))
            start_date = end_date - timedelta(days=days - 1)
        if start_date > end_date:
            start_date, end_date = end_date, start_date

        start = datetime.combine(start_date, datetime.min.time())
        end_excl = datetime.combine(end_date + timedelta(days=1), datetime.min.time())
    # Fallback: last N days ending "now".
    else:
        days = max(1, min(int(days or 120), 3650))
        end_excl = datetime.utcnow()
        start = end_excl - timedelta(days=days)
        # Bucket boundaries start at midnight for day-level buckets.
        start = _floor(start, interval_req)

    # Enforce a maximum number of buckets by coarsening the interval as needed.
    MAX_BUCKETS = 5000
    interval_eff = interval_req
    start_bucket = _floor(start, interval_eff)
    if end_excl is None:
        # Safety: should not happen (end_excl is always set in the branches above).
        end_excl = datetime.utcnow()
    # end_excl is an exclusive bound; generate buckets up to the last bucket
    # that has any overlap with the window.
    end_bucket = _floor(end_excl - timedelta(microseconds=1), interval_eff)
    if end_bucket <= start_bucket:
        end_bucket = start_bucket + _step_for(interval_eff)

    def _bucket_count(unit: str) -> int:
        step = _step_for(unit)
        seconds = max(0.0, (end_bucket - start_bucket).total_seconds())
        step_s = max(1.0, step.total_seconds())
        return int(seconds // step_s) + 1

    # Coarsen interval (second->minute->hour->day) when the window is too large.
    while _bucket_count(interval_eff) > MAX_BUCKETS and interval_eff != "day":
        interval_eff = {
            "second": "minute",
            "minute": "hour",
            "hour": "day",
        }.get(interval_eff, "day")
        start_bucket = _floor(start, interval_eff)
        end_bucket = _floor(end_excl - timedelta(microseconds=1), interval_eff)
        if end_bucket <= start_bucket:
            end_bucket = start_bucket + _step_for(interval_eff)

    bucket_expr = func.date_trunc(interval_eff, Event.timestamp).label("b")

    total_q = (
        db.query(bucket_expr, func.count(Event.id).label("n"))
        .filter(Event.timestamp >= start)
        .filter(diary_filter_condition(db, user))
    )
    total_q = total_q.filter(Event.timestamp < end_excl)
    if source:
        total_q = total_q.filter(Event.source == source)
    total_rows = total_q.group_by("b").order_by("b").all()
    total_by_bucket: dict[datetime, int] = {}
    for b, n in total_rows:
        if b is None:
            continue
        total_by_bucket[b] = int(n or 0)

    mapped_q = (
        db.query(
            bucket_expr,
            func.count(func.distinct(Event.id)).label("n"),
        )
        .join(Mapping, Mapping.event_id == Event.id)
        .join(ControlItem, ControlItem.id == Mapping.control_item_id)
        .filter(Event.timestamp >= start)
        .filter(ControlItem.framework_slug == framework)
        .filter(diary_filter_condition(db, user))
    )
    mapped_q = mapped_q.filter(Event.timestamp < end_excl)
    if source:
        mapped_q = mapped_q.filter(Event.source == source)
    mapped_rows = mapped_q.group_by("b").order_by("b").all()
    mapped_by_bucket: dict[datetime, int] = {}
    for b, n in mapped_rows:
        if b is None:
            continue
        mapped_by_bucket[b] = int(n or 0)

    # Optional per-source breakdown (used by the stacked histogram).
    bd = (breakdown or "").strip().lower()
    want_source_breakdown = bd in {"source", "sources", "by_source"}

    by_bucket_source: dict[datetime, dict[str, int]] = {}
    totals_by_source: dict[str, int] = {}
    sources_meta = None
    if want_source_breakdown:
        by_q = (
            db.query(
                bucket_expr,
                Event.source.label("src"),
                func.count(Event.id).label("n"),
            )
            .filter(Event.timestamp >= start)
            .filter(diary_filter_condition(db, user))
        )
        by_q = by_q.filter(Event.timestamp < end_excl)
        if source:
            by_q = by_q.filter(Event.source == source)

        for b, src, n in by_q.group_by("b", "src").order_by("b").all():
            if b is None:
                continue
            s = str(src)
            v = int(n or 0)
            if b not in by_bucket_source:
                by_bucket_source[b] = {}
            by_bucket_source[b][s] = v
            totals_by_source[s] = totals_by_source.get(s, 0) + v

        # Attach label/colour, respecting user overrides.
        overrides = (
            getattr(user, "pref_source_colors", None)
            if isinstance(user, User)
            else None
        )
        sources_meta = []
        for src, total in sorted(
            totals_by_source.items(), key=lambda kv: kv[1], reverse=True
        ):
            default_meta = get_source_meta(src)
            meta = apply_user_source_overrides(default_meta, overrides)
            sources_meta.append(
                {
                    "source": src,
                    "label": meta.label,
                    "color": meta.color,
                    "total": int(total),
                }
            )

    # Generate a dense sequence of buckets so the chart doesn't have gaps.
    step = _step_for(interval_eff)
    items = []
    t = start_bucket
    # Use end_bucket (floored) as the inclusive upper bound for bucket starts.
    while t <= end_bucket:
        total = int(total_by_bucket.get(t, 0))
        mapped = int(mapped_by_bucket.get(t, 0))

        if interval_eff == "day":
            bucket_key = t.date().isoformat()
        else:
            bucket_key = _iso_z(t)

        row = {
            "bucket": bucket_key,
            "total": total,
            "mapped": mapped,
            "unmapped": max(0, total - mapped),
        }
        if want_source_breakdown:
            row["by_source"] = by_bucket_source.get(t, {})
        items.append(row)
        t = t + step

    payload = {
        "days": (end_date - start_date).days + 1 if start_date and end_date else None,
        "framework": framework,
        "source": source,
        "breakdown": "source" if want_source_breakdown else None,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "start_ts": _iso_z(start_bucket) if start_bucket else None,
        "end_ts": _iso_z(end_excl) if end_excl else None,
        "interval": interval_eff,
        "interval_requested": interval_req,
        "items": items,
    }
    if sources_meta is not None:
        payload["sources"] = sources_meta
    return payload
