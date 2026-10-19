"""Dashboard statistics when a framework inherits evidence from another.

A crosswalk lets evidence mapped to a source framework's control count for a
target control. While inheritance applies, the summary and control statistics
use live queries; date windows keep controls without evidence in the list.
Requests pass the assembled application as a user who can read events.
"""

from datetime import datetime

import pytest
from app.core.config import settings
from app.db.models import (
    ControlItem,
    CrossFrameworkControlLink,
    Event,
    Framework,
    ManagedConfiguration,
    Mapping,
)

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import keen  # noqa: F401


@pytest.fixture
def crosswalk(keen, monkeypatch):  # noqa: F811 - fixture imported above
    monkeypatch.setattr(settings, "aggregate_cache_ttl_seconds", 0)
    monkeypatch.setattr(settings, "enabled_frameworks", "")
    db = keen.db
    db.add_all(
        [Framework(slug="SRC", name="Source"), Framework(slug="TGT", name="Target")]
    )
    source = ControlItem(framework_slug="SRC", ref="S.1", type="control")
    inheriting = ControlItem(framework_slug="TGT", ref="T.1", type="control")
    direct = ControlItem(
        framework_slug="TGT", ref="T.2", type="control", in_scope=False
    )
    idle = ControlItem(framework_slug="TGT", ref="T.3", type="control")
    db.add_all([source, inheriting, direct, idle])
    db.flush()
    db.add(
        CrossFrameworkControlLink(
            source_control_id=source.id, target_control_id=inheriting.id
        )
    )
    selection = ManagedConfiguration(
        name="organisation-frameworks",
        version=1,
        document={"enabled": ["SRC", "TGT"], "default": "TGT"},
    )
    db.add(selection)

    def event(day, month, *controls):
        row = Event(
            source="ci",
            summary=f"Build {month}-{day}",
            external_id=f"build-{month}-{day}",
            timestamp=datetime(2026, month, day),
        )
        db.add(row)
        db.flush()
        for control in controls:
            db.add(Mapping(event_id=row.id, control_item_id=control.id, method="rule"))
        return row

    event(5, 1, source)
    event(10, 2, source, inheriting)  # counted once for the target control
    event(20, 1, direct)
    event(25, 1)
    db.commit()
    client = keen.client(keen.user("events.read"))
    return client, selection, db


def controls(client, **params):
    response = client.get("/v1/stats/controls", params={"framework": "TGT", **params})
    assert response.status_code == 200, response.text
    body = response.json()
    return body["stats_source"], {
        item["ref"]: (item["evidence_count"], item["last_evidence"])
        for item in body["items"]
    }


def test_inherited_evidence_counts_once_for_each_target_control(crosswalk):
    client, _, _ = crosswalk

    summary = client.get("/v1/stats/summary", params={"framework": "TGT"}).json()

    assert summary["events"] == {"total": 4, "mapped": 3, "unmapped": 1}
    assert summary["controls"] == {
        "total": 3,
        "in_scope": 2,
        "out_of_scope": 1,
        "with_evidence_in_scope": 1,
        "without_evidence_in_scope": 1,
        "with_evidence_all": 2,
        "without_evidence_all": 1,
    }
    source, counts = controls(client)
    assert source == "live_query"
    assert counts == {
        "T.1": (2, "2026-02-10T00:00:00"),
        "T.2": (1, "2026-01-20T00:00:00"),
        "T.3": (0, None),
    }


def test_a_date_window_counts_only_evidence_inside_it(crosswalk):
    client, _, _ = crosswalk

    source, counts = controls(client, start_date="2026-02-01", end_date="2026-02-28")

    assert source == "live_query"
    assert counts == {
        "T.1": (1, "2026-02-10T00:00:00"),
        "T.2": (0, None),
        "T.3": (0, None),
    }
    _, january = controls(client, end_date="2026-01-31")
    assert january["T.1"] == (1, "2026-01-05T00:00:00")


def test_disabling_the_source_framework_stops_inheritance(crosswalk):
    client, selection, db = crosswalk
    selection.document = {"enabled": ["TGT"], "default": "TGT"}
    db.commit()

    summary = client.get("/v1/stats/summary", params={"framework": "TGT"}).json()

    assert summary["events"]["mapped"] == 2
    assert summary["controls"]["with_evidence_all"] == 2
    _, counts = controls(client, start_date="2026-01-01")
    assert counts["T.1"] == (1, "2026-02-10T00:00:00")
