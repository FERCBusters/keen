from __future__ import annotations

import logging
from calendar import monthrange
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import IsmsEffectivenessMeasure, IsmsEffectivenessMetricEntry
from app.services.entity_changelog import record_entity_changelog

log = logging.getLogger(__name__)

AUTO_ZERO_SOURCE_TYPE = "other"
AUTO_ZERO_SOURCE_TITLE = "Automatically recorded zero"
AUTO_ZERO_REFERENCE_PREFIX = "auto-zero:monthly"
AUTO_ZERO_MAX_BACKFILL_MONTHS = 120
ZERO_TARGET_OPERATORS = {"", "eq", "lte"}


def utcnow() -> datetime:
    return datetime.utcnow()


def _today_for_effectiveness_metrics() -> date:
    """Return today's date in Keen's configured timezone for date-only periods."""

    tz_name = str(settings.timezone or "Etc/UTC").strip() or "Etc/UTC"
    try:
        return datetime.now(ZoneInfo(tz_name)).date()
    except ZoneInfoNotFoundError:
        log.warning(
            "Invalid KEEN_TIMEZONE=%r; falling back to server-local date for effectiveness metrics",
            tz_name,
        )
    except Exception:
        log.exception(
            "Failed to resolve KEEN_TIMEZONE=%r; falling back to server-local date for effectiveness metrics",
            tz_name,
        )
    return date.today()


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _month_end(d: date) -> date:
    return date(d.year, d.month, monthrange(d.year, d.month)[1])


def _add_months(d: date, months: int) -> date:
    month_idx = (d.month - 1) + months
    year = d.year + month_idx // 12
    month = month_idx % 12 + 1
    day = min(d.day, monthrange(year, month)[1])
    return date(year, month, day)


def _is_monthly_zero_target(measure: IsmsEffectivenessMeasure) -> bool:
    frequency = " ".join(str(measure.frequency or "").strip().lower().split())
    monthly_values = {
        "month",
        "monthly",
        "per month",
        "calendar month",
        "calendar monthly",
    }
    # Treat "as required"/"when required" style measures as eligible too.
    # These still materialise as calendar-month zero entries because the purpose is
    # to make silent closed months explicit in metrics tables and visualisations.
    if frequency not in monthly_values and "required" not in frequency:
        return False
    if measure.target_value is None or float(measure.target_value) != 0.0:
        return False
    # Auto-zero is only safe for zero-is-good measures.  A target of "more than 0"
    # or "less than 0" should not be inferred from silence.
    op = str(measure.threshold_operator or "").strip().lower()
    return op in ZERO_TARGET_OPERATORS


def _metric_reference_for_period(period_start: date) -> str:
    return f"{AUTO_ZERO_REFERENCE_PREFIX}:{period_start:%Y-%m}"


def _metric_out(row: IsmsEffectivenessMetricEntry) -> dict[str, Any]:
    value_display = ""
    if row.metric_value is not None:
        value_display = f"{row.metric_value:g}"
        if row.metric_unit:
            value_display = f"{value_display} {row.metric_unit}"
    if row.qualitative_value:
        value_display = " ".join([value_display, row.qualitative_value]).strip()
    period = ""
    if row.period_start and row.period_end:
        period = f"{row.period_start.isoformat()} → {row.period_end.isoformat()}"
    elif row.period_start:
        period = f"from {row.period_start.isoformat()}"
    elif row.period_end:
        period = f"to {row.period_end.isoformat()}"
    return {
        "id": str(row.id),
        "measure_id": str(row.measure_id),
        "recorded_at": row.recorded_at.isoformat() if row.recorded_at else None,
        "period_start": row.period_start.isoformat() if row.period_start else None,
        "period_end": row.period_end.isoformat() if row.period_end else None,
        "period": period,
        "metric_value": row.metric_value,
        "metric_unit": row.metric_unit or "",
        "qualitative_value": row.qualitative_value or "",
        "value_display": value_display,
        "source_type": row.source_type or AUTO_ZERO_SOURCE_TYPE,
        "source_title": row.source_title or "",
        "source_url": row.source_url,
        "source_reference": row.source_reference or "",
        "source_event_id": str(row.source_event_id) if row.source_event_id else None,
        "notes": row.notes or "",
        "raw_payload": row.raw_payload or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "display": value_display or row.source_title or "Metric entry",
    }


def _entry_exists_for_period(
    db: Session, measure_id, period_start: date, period_end: date
) -> bool:
    next_period_start = period_end + timedelta(days=1)
    recorded_start = datetime.combine(period_start, time.min)
    recorded_next = datetime.combine(next_period_start, time.min)

    return (
        db.query(IsmsEffectivenessMetricEntry.id)
        .filter(IsmsEffectivenessMetricEntry.measure_id == measure_id)
        .filter(
            or_(
                # Exact or overlapping explicit periods count as real coverage for
                # this month, including daily/weekly entries within a monthly measure.
                and_(
                    IsmsEffectivenessMetricEntry.period_start.isnot(None),
                    IsmsEffectivenessMetricEntry.period_end.isnot(None),
                    IsmsEffectivenessMetricEntry.period_start <= period_end,
                    IsmsEffectivenessMetricEntry.period_end >= period_start,
                ),
                and_(
                    IsmsEffectivenessMetricEntry.period_start.isnot(None),
                    IsmsEffectivenessMetricEntry.period_start >= period_start,
                    IsmsEffectivenessMetricEntry.period_start <= period_end,
                ),
                and_(
                    IsmsEffectivenessMetricEntry.period_end.isnot(None),
                    IsmsEffectivenessMetricEntry.period_end >= period_start,
                    IsmsEffectivenessMetricEntry.period_end <= period_end,
                ),
                # Older/manual entries may omit period dates. In that case, treat
                # a recorded_at timestamp inside the month as evidence that the
                # month had a metric and should not receive an inferred zero.
                and_(
                    IsmsEffectivenessMetricEntry.period_start.is_(None),
                    IsmsEffectivenessMetricEntry.period_end.is_(None),
                    IsmsEffectivenessMetricEntry.recorded_at >= recorded_start,
                    IsmsEffectivenessMetricEntry.recorded_at < recorded_next,
                ),
            )
        )
        .first()
        is not None
    )


def materialize_missing_monthly_zero_metrics(
    db: Session,
    *,
    today: date | None = None,
    max_backfill_months: int = AUTO_ZERO_MAX_BACKFILL_MONTHS,
) -> dict[str, Any]:
    """Create zero metric entries for closed monthly zero-target measures.

    This implements the ISMS "no news is good news" convention for controls such
    as "0 unauthorised access events this month".  It is deliberately limited to
    monthly or required/as-required measures whose target is zero and whose
    threshold operator means zero is acceptable (blank, equals or at-most).  The
    job is idempotent because any existing metric entry covering the month
    prevents an auto-zero entry.
    """

    today = today or _today_for_effectiveness_metrics()
    current_month = _month_start(today)
    last_closed_month = _add_months(current_month, -1)
    last_closed_month_end = _month_end(last_closed_month)
    now = utcnow()
    max_months = max(1, int(max_backfill_months or AUTO_ZERO_MAX_BACKFILL_MONTHS))

    rows = (
        db.query(IsmsEffectivenessMeasure)
        .filter(IsmsEffectivenessMeasure.target_value == 0)
        .all()
    )

    checked = 0
    created = 0
    skipped_existing = 0
    skipped_ineligible = 0
    entries: list[dict[str, Any]] = []

    for measure in rows:
        if not _is_monthly_zero_target(measure):
            skipped_ineligible += 1
            continue

        created_at = (
            measure.created_at.date() if measure.created_at else last_closed_month
        )
        period_start = _month_start(created_at)
        earliest_allowed = _add_months(last_closed_month, -(max_months - 1))
        if period_start < earliest_allowed:
            period_start = earliest_allowed

        while period_start <= last_closed_month:
            period_end = _month_end(period_start)
            checked += 1
            if _entry_exists_for_period(db, measure.id, period_start, period_end):
                skipped_existing += 1
                period_start = _add_months(period_start, 1)
                continue

            reference = _metric_reference_for_period(period_start)
            row = IsmsEffectivenessMetricEntry(
                measure_id=measure.id,
                recorded_at=(
                    datetime.combine(last_closed_month_end, time.max).replace(
                        microsecond=0
                    )
                    if period_start == last_closed_month
                    else datetime.combine(period_end, time.max).replace(microsecond=0)
                ),
                period_start=period_start,
                period_end=period_end,
                metric_value=0,
                metric_unit=measure.target_unit or "",
                qualitative_value="",
                source_type=AUTO_ZERO_SOURCE_TYPE,
                source_title=AUTO_ZERO_SOURCE_TITLE,
                source_url=None,
                source_reference=reference,
                notes=(
                    "Automatically recorded by KEEN because this zero-target "
                    "effectiveness measure had no metric entries for the closed period."
                ),
                raw_payload={
                    "auto_generated": True,
                    "generator": "materialize_missing_monthly_zero_metrics",
                    "reason": "no_metric_entries_for_closed_month",
                    "frequency": measure.frequency or "",
                    "target_value": measure.target_value,
                    "threshold_operator": measure.threshold_operator or "",
                    "period": period_start.strftime("%Y-%m"),
                },
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            measure.updated_at = now
            db.add(measure)
            db.flush()
            after = _metric_out(row)
            record_entity_changelog(
                db,
                entity_type="isms_effectiveness_metric",
                entity_id=row.id,
                action="created",
                before=None,
                after=after,
                user=None,
                request_method="CELERY",
                request_path=(
                    "app.worker.tasks.materialize_missing_monthly_zero_metrics_task"
                ),
            )
            entries.append(after)
            created += 1
            period_start = _add_months(period_start, 1)

    db.commit()
    return {
        "ok": True,
        "today": today.isoformat(),
        "last_closed_month": last_closed_month.strftime("%Y-%m"),
        "checked_periods": checked,
        "created": created,
        "skipped_existing": skipped_existing,
        "skipped_ineligible": skipped_ineligible,
        "entries": entries,
    }
