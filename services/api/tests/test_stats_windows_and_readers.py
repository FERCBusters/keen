"""Statistics for date windows open at one end, sources sharing a bucket, and
readers who cannot see every event of an inheriting framework.

Control counts to an end date count that day's evidence and nothing later; a
chart window from a start date runs to today; sources sharing a day are each
counted in it; and the home counters of a framework that inherits evidence
are counted for each reader, leaving out diary entries the reader cannot open.
"""

import uuid
from datetime import datetime, timedelta

import pytest
from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.db.models import ControlItem, Event, Framework, Mapping
from app.services import home as home_service
from app.services.context import ChangeContext

from tests.test_assembled_authority_regressions import keen as keen
from tests.test_inherited_dashboard_counts import TGT
from tests.test_inherited_dashboard_counts import crosswalk as crosswalk

FW = "stats-windows"


@pytest.fixture
def counted(keen, monkeypatch):
    monkeypatch.setattr(settings, "aggregate_cache_ttl_seconds", 0)
    db = keen.db
    db.add(Framework(slug=FW))
    control = ControlItem(framework_slug=FW, type="control", ref="C.1")
    db.add(control)
    db.flush()

    def event(when, source="ci"):
        row = Event(
            source=source,
            summary="Deploy",
            external_id=uuid.uuid4().hex,
            timestamp=when,
        )
        db.add(row)
        db.flush()
        db.add(Mapping(event_id=row.id, control_item_id=control.id, method="rule"))
        return row

    db.commit()
    return keen, event, keen.client(keen.user("events.read"))


def test_control_counts_to_an_end_date_include_that_day_only(counted):
    keen, event, reader = counted
    event(datetime(2026, 3, 1, 9, 0))
    event(datetime(2026, 3, 2, 23, 59))
    event(datetime(2026, 3, 3, 0, 0))
    keen.db.commit()

    def counts(**window):
        response = reader.get("/v1/stats/controls", params={"framework": FW, **window})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["stats_source"] == "live_query"
        (item,) = body["items"]
        return item["evidence_count"], item["last_evidence"]

    assert counts(end_date="2026-03-02") == (2, "2026-03-02T23:59:00")
    assert counts(start_date="2026-03-02", end_date="2026-03-02") == (
        1,
        "2026-03-02T23:59:00",
    )
    assert counts(start_date="2026-03-02") == (2, "2026-03-03T00:00:00")


def test_a_chart_window_from_a_start_date_runs_to_today_by_source(counted):
    keen, event, reader = counted
    today = utc_now_naive().replace(hour=1, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)
    event(yesterday, source="jenkins")
    event(yesterday + timedelta(hours=2), source="github")
    event(yesterday + timedelta(hours=3), source="github")
    event(today, source="jenkins")
    keen.db.commit()

    response = reader.get(
        "/v1/charts/event_volume_timeseries",
        params={
            "framework": FW,
            "start_date": yesterday.date().isoformat(),
            "breakdown": "source",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["start_date"], body["end_date"]) == (
        yesterday.date().isoformat(),
        today.date().isoformat(),
    )
    assert body["days"] == 2
    buckets = {item["bucket"]: item for item in body["items"]}
    assert list(buckets) == [yesterday.date().isoformat(), today.date().isoformat()]
    first = buckets[yesterday.date().isoformat()]
    assert (first["total"], first["by_source"]) == (3, {"jenkins": 1, "github": 2})
    assert buckets[today.date().isoformat()]["by_source"] == {"jenkins": 1}
    assert {s["source"]: s["total"] for s in body["sources"]} == {
        "jenkins": 2,
        "github": 2,
    }


def test_home_counters_of_an_inheriting_framework_follow_each_reader(crosswalk):
    db = crosswalk.db

    def counters(user):
        return home_service.home_overview(
            context=ChangeContext(method="GET", path="/v1/home", actor=user),
            framework=TGT,
            db=db,
            user=user,
        )["events"]

    # The events reader cannot open the risk diary entry, so it is not counted.
    assert counters(crosswalk.reader) == {"total": 6, "mapped": 3, "unmapped": 3}
    assert counters(crosswalk.admin) == {"total": 7, "mapped": 4, "unmapped": 3}
