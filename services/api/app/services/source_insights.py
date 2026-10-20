"""Activity insights for one evidence source.

The insights describe the rhythm of a source from the events it has already
delivered: how often it usually sends something, whether it has gone quiet,
which days stood out, and which summaries keep repeating. Every figure is
derived from the source's own history, so a daily report and a busy log stream
are each judged against their own baseline. The result supports a human
reading of the source; it makes no claim about the system behind it.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.cache import cached_json, user_cache_scope
from app.core.datetime_utils import utc_now_naive
from app.db.models import Event, Mapping
from app.security.diary_visibility import diary_filter_condition
from app.services.control_inheritance import effective_framework_ids
from app.services.errors import DomainError

DEFAULT_DAYS = 90
MIN_DAYS = 7
MAX_DAYS = 365
# Gap and summary statistics come from the most recent events in the window;
# daily counts are aggregated in the database and cover the whole window.
SAMPLE_SIZE = 5000
MIN_EVENTS_FOR_BASELINE = 3
# A source is quiet when the current silence is at least twice the longest
# gap it normally shows, and at least this long.
QUIET_FLOOR_SECONDS = 3600
SPIKE_MIN_EVENTS = 5
SPIKE_FACTOR = 3
REPEAT_MIN_COUNT = 3
REPEAT_LIMIT = 8
PERIOD_LIMIT = 5

_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)
_HEX = re.compile(r"\b[0-9a-f]{7,}\b", re.I)
_NUMBER = re.compile(r"\d+([.,:]\d+)*")
_SPACE = re.compile(r"\s+")


def summary_pattern(summary: str) -> str:
    """Collapse identifiers and numbers so repeated summaries group together."""
    text = str(summary or "").strip()
    text = _UUID.sub("{id}", text)
    text = _HEX.sub("{hex}", text)
    text = _NUMBER.sub("#", text)
    return _SPACE.sub(" ", text).casefold()[:300]


def humanise_seconds(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "under a minute"
    units = (("day", 86400), ("hour", 3600), ("minute", 60))
    parts = []
    for name, size in units:
        value, seconds = divmod(seconds, size)
        if value:
            parts.append(f"{value} {name}{'' if value == 1 else 's'}")
        if len(parts) == 2:
            break
    return " ".join(parts)


def _percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))
    return ordered[index]


def _day(value) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    return str(value)[:10]


def _clean_days(days) -> int:
    try:
        value = int(days)
    except (TypeError, ValueError):
        raise DomainError(400, "days must be a whole number") from None
    if value < MIN_DAYS or value > MAX_DAYS:
        raise DomainError(400, f"days must be between {MIN_DAYS} and {MAX_DAYS}")
    return value


def source_insights(
    *,
    source_name: str,
    framework: str,
    days: int = DEFAULT_DAYS,
    db: Session,
    user,
    now: datetime | None = None,
) -> dict:
    """Describe a source's recent activity relative to its own history."""
    src = str(source_name or "").strip()
    if not src or len(src) > 64:
        raise DomainError(400, "source must be between 1 and 64 characters")
    window_days = _clean_days(days)
    clock = now or utc_now_naive()
    cache_parts = {
        "user": user_cache_scope(user),
        "source": src,
        "framework": framework,
        "days": window_days,
        # Fresh enough for a page load, shared across reloads within the bucket.
        "bucket": clock.strftime("%Y%m%d%H%M")[:-1],
    }
    return cached_json(
        "sources:insights",
        cache_parts,
        lambda: _compute(src, framework, window_days, db, user, clock),
    )


def _compute(src, framework, window_days, db, user, clock) -> dict:
    visibility = diary_filter_condition(db, user)
    window_start = clock - timedelta(days=window_days)
    in_window = [Event.source == src, Event.timestamp >= window_start, visibility]

    last_seen, lifetime_total = (
        db.query(func.max(Event.timestamp), func.count(Event.id))
        .filter(Event.source == src, visibility)
        .one()
    )
    lifetime_total = int(lifetime_total or 0)

    daily_rows = (
        db.query(func.date(Event.timestamp), func.count(Event.id))
        .filter(*in_window)
        .group_by(func.date(Event.timestamp))
        .all()
    )
    counts_by_day = {_day(day): int(n) for day, n in daily_rows}
    total_in_window = sum(counts_by_day.values())

    sample = (
        db.query(Event.id, Event.timestamp, Event.summary)
        .filter(*in_window)
        .order_by(Event.timestamp.desc(), Event.id.desc())
        .limit(SAMPLE_SIZE)
        .all()
    )
    # Look up mappings for the sampled events only, so the cost follows the
    # sample size rather than the installation's whole mapping table.
    mapped_ids: set = set()
    sample_ids = [row.id for row in sample]
    framework_control_ids = effective_framework_ids(framework, db)
    for start in range(0, len(sample_ids), 1000):
        chunk = sample_ids[start : start + 1000]
        mapped_ids.update(
            event_id
            for (event_id,) in db.query(Mapping.event_id)
            .filter(
                Mapping.event_id.in_(chunk),
                Mapping.control_item_id.in_(framework_control_ids),
            )
            .distinct()
            .all()
        )
    sample = [
        (row.id, row.timestamp, row.summary, row.id in mapped_ids) for row in sample
    ]
    timestamps = sorted(timestamp for _, timestamp, _, _ in sample)

    result = {
        "source": src,
        "framework": framework,
        "days": window_days,
        "window_start": window_start.isoformat(),
        "as_of": clock.isoformat(),
        "lifetime_events": lifetime_total,
        "window_events": total_in_window,
        "sample_size": len(sample),
        "sampled": total_in_window > len(sample),
        "last_seen": last_seen.isoformat() if last_seen else None,
        "since_last_seconds": None,
        "status": "no_events",
        "typical_gap_seconds": None,
        "usual_longest_gap_seconds": None,
        "quiet_threshold_seconds": None,
        "message": "This source has no events yet.",
        "quiet_periods": [],
        "daily": _daily_series(counts_by_day, window_start, clock, window_days),
        "spikes": [],
        "trend": _trend(counts_by_day, clock),
        "repeated_summaries": _repeated(sample),
    }
    if last_seen is None:
        return result

    since_last = max(0.0, (clock - last_seen).total_seconds())
    result["since_last_seconds"] = round(since_last)
    result["spikes"] = _spikes(counts_by_day)

    if len(timestamps) < MIN_EVENTS_FOR_BASELINE:
        result["status"] = "insufficient"
        result["message"] = (
            f"Last event {humanise_seconds(since_last)} ago. Fewer than "
            f"{MIN_EVENTS_FOR_BASELINE} events in the last {window_days} days, "
            "so there is no baseline to compare against yet."
        )
        return result

    gaps = [
        (later - earlier).total_seconds()
        for earlier, later in zip(timestamps, timestamps[1:])
    ]
    typical = statistics.median(gaps)
    usual_longest = _percentile(gaps, 0.9)
    threshold = max(2 * usual_longest, QUIET_FLOOR_SECONDS)
    result["typical_gap_seconds"] = round(typical)
    result["usual_longest_gap_seconds"] = round(usual_longest)
    result["quiet_threshold_seconds"] = round(threshold)
    result["quiet_periods"] = _quiet_periods(timestamps, gaps, threshold)

    rhythm = (
        f"it usually sends one every {humanise_seconds(typical)}"
        if typical >= 60
        else "it usually sends several a minute"
    )
    if since_last > threshold:
        result["status"] = "quiet"
        result["message"] = (
            f"No events for {humanise_seconds(since_last)}; {rhythm}, and gaps "
            f"longer than {humanise_seconds(usual_longest)} are unusual for it."
        )
    else:
        result["status"] = "active"
        result["message"] = f"Last event {humanise_seconds(since_last)} ago; {rhythm}."
    return result


def _daily_series(counts_by_day, window_start, clock, window_days) -> list[dict]:
    first = window_start.date()
    series = []
    for offset in range(window_days + 1):
        day = first + timedelta(days=offset)
        if day > clock.date():
            break
        key = day.isoformat()
        series.append({"date": key, "count": counts_by_day.get(key, 0)})
    return series


def _spikes(counts_by_day) -> list[dict]:
    active = [n for n in counts_by_day.values() if n > 0]
    if len(active) < MIN_EVENTS_FOR_BASELINE:
        return []
    baseline = statistics.median(active)
    threshold = max(SPIKE_FACTOR * baseline, SPIKE_MIN_EVENTS)
    spikes = [
        {
            "date": day,
            "count": n,
            "baseline": round(baseline, 1),
            "ratio": round(n / baseline, 1) if baseline else None,
        }
        for day, n in counts_by_day.items()
        if n >= threshold
    ]
    spikes.sort(key=lambda s: (-s["count"], s["date"]))
    return spikes[:PERIOD_LIMIT]


def _trend(counts_by_day, clock) -> dict:
    today = clock.date()

    def total(start_offset, end_offset):
        return sum(
            counts_by_day.get((today - timedelta(days=d)).isoformat(), 0)
            for d in range(start_offset, end_offset)
        )

    recent, previous = total(0, 7), total(7, 14)
    change = None
    if previous:
        change = round((recent - previous) * 100 / previous)
    return {"recent_7_days": recent, "previous_7_days": previous, "change_pct": change}


def _quiet_periods(timestamps, gaps, threshold) -> list[dict]:
    periods = [
        {
            "start": earlier.isoformat(),
            "end": later.isoformat(),
            "seconds": round(gap),
            "label": humanise_seconds(gap),
        }
        for earlier, later, gap in zip(timestamps, timestamps[1:], gaps)
        if gap > threshold
    ]
    periods.sort(key=lambda p: (-p["seconds"], p["start"]))
    return periods[:PERIOD_LIMIT]


def _repeated(sample) -> list[dict]:
    if not sample:
        return []
    groups: dict[str, dict] = {}
    for event_id, timestamp, summary, mapped in sample:
        key = summary_pattern(summary)
        if not key:
            continue
        group = groups.setdefault(
            key,
            {
                "pattern": key,
                "example": str(summary or "").strip()[:300],
                "example_event_id": str(event_id),
                "count": 0,
                "unmapped_count": 0,
                "first_seen": timestamp,
                "last_seen": timestamp,
            },
        )
        group["count"] += 1
        if not mapped:
            group["unmapped_count"] += 1
            # Prefer an unmapped example so a reviewer lands on something to map.
            if group["unmapped_count"] == 1:
                group["example"] = str(summary or "").strip()[:300]
                group["example_event_id"] = str(event_id)
        group["first_seen"] = min(group["first_seen"], timestamp)
        group["last_seen"] = max(group["last_seen"], timestamp)
    counted = Counter({key: g["count"] for key, g in groups.items()})
    items = []
    for key, count in counted.most_common(REPEAT_LIMIT):
        if count < REPEAT_MIN_COUNT:
            break
        group = groups[key]
        items.append(
            {
                **group,
                "share_pct": round(count * 100 / len(sample)),
                "first_seen": group["first_seen"].isoformat(),
                "last_seen": group["last_seen"].isoformat(),
            }
        )
    return items
