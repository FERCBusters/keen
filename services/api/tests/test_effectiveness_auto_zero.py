"""Monthly "no news is good news" zero entries for effectiveness measures.

The scheduled job records an explicit zero for each closed month without any
metric entry, but only for monthly or as-required measures whose target makes
zero acceptable. It must be idempotent and bounded.
"""

import uuid
from datetime import date, datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from app.core.config import settings
from app.core.datetime_utils import add_months
from app.db.models import (
    EntityChangelog,
    IsmsEffectivenessMeasure,
    IsmsEffectivenessMetricEntry,
    ManagedConfiguration,
)
from app.services import effectiveness_metrics as metrics

TODAY = date(2026, 4, 10)  # last closed month: March 2026


def _measure(
    db,
    *,
    frequency="Monthly",
    target=0.0,
    operator="lte",
    created=datetime(2026, 1, 15),
    **kw,
):
    row = IsmsEffectivenessMeasure(
        framework_slug="A",
        summary=kw.pop("summary", "Unauthorised access events"),
        frequency=frequency,
        target_value=target,
        threshold_operator=operator,
        target_unit=kw.pop("unit", "incidents"),
        created_at=created,
        updated_at=created,
        **kw,
    )
    db.add(row)
    db.flush()
    return row


def _entry(db, measure, **kw):
    row = IsmsEffectivenessMetricEntry(
        measure_id=measure.id, metric_value=kw.pop("value", 1), **kw
    )
    db.add(row)
    db.flush()
    return row


def _entries(db, measure):
    return (
        db.query(IsmsEffectivenessMetricEntry)
        .filter_by(measure_id=measure.id)
        .order_by(IsmsEffectivenessMetricEntry.period_start.nullsfirst())
        .all()
    )


def test_closed_months_without_entries_receive_one_zero(contract_db):
    db = contract_db
    measure = _measure(db)
    # January: an older entry without a period, recorded within the month.
    _entry(db, measure, recorded_at=datetime(2026, 1, 20, 9, 0))
    # February: a weekly entry inside the month counts as coverage.
    _entry(
        db,
        measure,
        period_start=date(2026, 2, 9),
        period_end=date(2026, 2, 15),
        recorded_at=datetime(2026, 2, 16),
    )
    db.commit()

    result = metrics.materialize_missing_monthly_zero_metrics(db, today=TODAY)
    assert result["ok"] is True
    assert (result["today"], result["last_closed_month"]) == ("2026-04-10", "2026-03")
    assert (
        result["checked_periods"],
        result["created"],
        result["skipped_existing"],
    ) == (3, 1, 2)
    (created,) = result["entries"]
    assert (
        created["period_start"] == "2026-03-01"
        and created["period_end"] == "2026-03-31"
    )
    assert created["recorded_at"] == "2026-03-31T23:59:59"
    assert created["metric_value"] == 0 and created["metric_unit"] == "incidents"
    assert created["value_display"] == "0 incidents"
    assert created["source_type"] == "other"
    assert created["source_title"] == "Automatically recorded zero"
    assert created["source_reference"] == "auto-zero:monthly:2026-03"
    assert created["raw_payload"]["auto_generated"] is True
    assert created["raw_payload"]["period"] == "2026-03"
    assert created["period"] == "2026-03-01 → 2026-03-31"

    changelog = (
        db.query(EntityChangelog)
        .filter_by(entity_type="isms_effectiveness_metric")
        .one()
    )
    assert changelog.action == "created" and changelog.request_method == "CELERY"
    assert changelog.changed_by_username is None

    # Running again creates nothing new.
    again = metrics.materialize_missing_monthly_zero_metrics(db, today=TODAY)
    assert (again["created"], again["skipped_existing"]) == (0, 3)
    assert len(_entries(db, measure)) == 3


@pytest.mark.parametrize(
    "frequency,target,operator,eligible",
    [
        ("Monthly", 0.0, "lte", True),
        ("  PER   month ", 0.0, "eq", True),
        ("As required", 0.0, "", True),
        ("When Required", 0.0, "lte", True),
        ("As and when required", 0.0, "eq", True),
        # Nobody measures these, so their months stay blank.
        ("Not required", 0.0, "lte", False),
        ("No longer required", 0.0, "", False),
        ("never REQUIRED", 0.0, "eq", False),
        ("Quarterly", 0.0, "lte", False),
        ("Monthly", 0.0, "gte", False),
        ("Monthly", 0.0, "gt", False),
        ("Monthly", 0.0, "lt", False),
        ("Monthly", None, "", False),
    ],
)
def test_only_zero_is_acceptable_monthly_measures_are_eligible(
    contract_db, frequency, target, operator, eligible
):
    db = contract_db
    measure = _measure(
        db,
        frequency=frequency,
        target=target,
        operator=operator,
        created=datetime(2026, 3, 2),
    )
    db.commit()
    result = metrics.materialize_missing_monthly_zero_metrics(db, today=TODAY)
    assert result["created"] == (1 if eligible else 0)
    if target == 0.0:
        assert result["skipped_ineligible"] == (0 if eligible else 1)
    assert len(_entries(db, measure)) == (1 if eligible else 0)


def test_non_zero_targets_are_never_considered(contract_db):
    measure = _measure(contract_db, target=5.0)
    contract_db.commit()
    result = metrics.materialize_missing_monthly_zero_metrics(contract_db, today=TODAY)
    assert (result["created"], result["skipped_ineligible"]) == (0, 0)
    assert _entries(contract_db, measure) == []


def test_overlapping_periods_and_open_ended_entries_count_as_coverage(contract_db):
    db = contract_db
    measure = _measure(db, created=datetime(2025, 12, 5))
    # Spans December into January.
    _entry(
        db,
        measure,
        period_start=date(2025, 12, 20),
        period_end=date(2026, 1, 5),
        recorded_at=datetime(2026, 1, 6),
    )
    # Open-ended entries starting or ending inside the month.
    _entry(db, measure, period_start=date(2026, 2, 3), recorded_at=datetime(2026, 2, 4))
    _entry(db, measure, period_end=date(2026, 3, 30), recorded_at=datetime(2026, 4, 1))
    db.commit()
    result = metrics.materialize_missing_monthly_zero_metrics(db, today=TODAY)
    assert (result["checked_periods"], result["created"]) == (4, 0)


def test_backfill_is_bounded(contract_db):
    measure = _measure(contract_db, created=datetime(2010, 1, 1))
    contract_db.commit()
    result = metrics.materialize_missing_monthly_zero_metrics(
        contract_db, today=TODAY, max_backfill_months=3
    )
    assert result["created"] == 3
    assert [e.period_start.isoformat() for e in _entries(contract_db, measure)] == [
        "2026-01-01",
        "2026-02-01",
        "2026-03-01",
    ]
    # Earlier months recorded at the end of their own month.
    first = _entries(contract_db, measure)[0]
    assert first.recorded_at == datetime(2026, 1, 31, 23, 59, 59)


def test_nothing_is_recorded_while_effectiveness_is_disabled(contract_db):
    _measure(contract_db)
    contract_db.add(
        ManagedConfiguration(
            name="features", version=1, document={"effectiveness": False}
        )
    )
    contract_db.commit()
    assert metrics.materialize_missing_monthly_zero_metrics(
        contract_db, today=TODAY
    ) == {
        "enabled": False,
        "created": 0,
    }
    assert contract_db.query(IsmsEffectivenessMetricEntry).count() == 0


def test_today_follows_the_configured_timezone(monkeypatch):
    monkeypatch.setattr(settings, "timezone", "Pacific/Kiritimati")
    expected = datetime.now(ZoneInfo("Pacific/Kiritimati")).date()
    assert metrics._today_for_effectiveness_metrics() == expected
    log = Mock()
    monkeypatch.setattr(metrics, "log", log)
    monkeypatch.setattr(settings, "timezone", "Not/A_Zone")
    assert metrics._today_for_effectiveness_metrics() == date.today()
    assert "Invalid KEEN_TIMEZONE" in log.warning.call_args.args[0]


def test_month_arithmetic_clamps_to_the_last_day():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2026, 1, 15), -1) == date(2025, 12, 15)
    assert add_months(date(2026, 11, 30), 14) == date(2028, 1, 30)
    assert metrics._month_end(date(2026, 2, 3)) == date(2026, 2, 28)


@pytest.mark.parametrize(
    "fields,display,period",
    [
        ({"metric_value": 2.5, "metric_unit": "%"}, "2.5 %", ""),
        ({"metric_value": None, "qualitative_value": "Green"}, "Green", ""),
        ({"metric_value": 3, "qualitative_value": "steady"}, "3 steady", ""),
        ({"metric_value": None, "source_title": "Board pack"}, "", ""),
        ({"metric_value": 1, "period_start": date(2026, 1, 1)}, "1", "from 2026-01-01"),
        ({"metric_value": 1, "period_end": date(2026, 1, 31)}, "1", "to 2026-01-31"),
    ],
)
def test_metric_entries_describe_their_value_and_period(fields, display, period):
    row = IsmsEffectivenessMetricEntry(measure_id=uuid.uuid4(), **fields)
    out = metrics._metric_out(row)
    assert out["value_display"] == display
    assert out["period"] == period
    assert out["display"] == (display or fields.get("source_title") or "Metric entry")
    assert out["source_type"] == "other"
