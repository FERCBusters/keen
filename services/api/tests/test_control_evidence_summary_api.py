"""A control's evidence summary through the assembled application.

The summary feeds the control page's Requirements tab: the control's evidence
counted by source, including what it inherits through a crosswalk and its
diary entries. It needs events.read and counts diary entries only where the
caller can see them.
"""

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from app.core.config import settings
from app.db.models import (
    ControlItem,
    CrossFrameworkControlLink,
    Event,
    Framework,
    Mapping,
)
from app.mapping.rules import rule_rationale

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import keen  # noqa: F401

FW = "fw-requirements"
OTHER_FW = "fw-crosswalk"


@pytest.fixture
def summary(keen, monkeypatch):  # noqa: F811 - fixture imported above
    # Every request reads the database directly, so each assertion sees the rows
    # it set up.
    monkeypatch.setattr(settings, "aggregate_cache_ttl_seconds", 0)
    monkeypatch.setattr(settings, "enabled_frameworks", "")
    db = keen.db
    db.add_all([Framework(slug=FW), Framework(slug=OTHER_FW)])
    db.flush()
    access = ControlItem(framework_slug=FW, ref="A.5.15", type="annex_control")
    other = ControlItem(framework_slug=FW, ref="A.8.13", type="annex_control")
    upstream = ControlItem(framework_slug=OTHER_FW, ref="S.1", type="custom")
    db.add_all([access, other, upstream])
    db.flush()
    # The access control inherits the upstream control's evidence.
    db.add(
        CrossFrameworkControlLink(
            source_control_id=upstream.id, target_control_id=access.id
        )
    )

    def event(source, control, *, day, rule=None, system=None, external_id=None):
        row = Event(
            source=source,
            system=system,
            summary=f"{source} event",
            external_id=external_id or uuid.uuid4().hex,
            timestamp=datetime(2026, 3, day, 9, 0),
        )
        db.add(row)
        db.flush()
        db.add(
            Mapping(
                event_id=row.id,
                control_item_id=control.id,
                method="rule" if rule else "manual",
                mapped_by="system" if rule else "auditor",
                rationale=rule_rationale(rule, control.framework_slug)
                if rule
                else "linked",
            )
        )
        return row

    event("jenkins", access, day=1, rule="deploys")
    event("jenkins", access, day=5, rule="deploys")
    event("okta", upstream, day=3, rule="logins")
    event("diary", access, day=2, system="diary")
    # Automatic entries recorded when an ISMS document or a risk is linked.
    event(
        "diary",
        access,
        day=7,
        system="isms",
        external_id=f"isms-link:document:{uuid.uuid4()}",
    )
    event(
        "diary",
        access,
        day=8,
        system="isms",
        external_id=f"isms-link:risk:{uuid.uuid4()}",
    )
    event("jenkins", other, day=9, rule="elsewhere")
    db.commit()

    def read(client, control=access):
        return client.get(f"/v1/controls/{control.id}/evidence/summary")

    return SimpleNamespace(keen=keen, db=db, access=access, other=other, read=read)


def test_sources_follow_the_evidence_list(summary):
    reader = summary.keen.client(summary.keen.user("events.read"))
    response = summary.read(reader)
    assert response.status_code == 200, response.text
    # Two deploys, the inherited sign-in and the manual diary entry; link
    # entries for documents and risks need ISMS and risk read access.
    assert response.json() == {
        "control_id": str(summary.access.id),
        "framework": FW,
        "total": 4,
        "latest": "2026-03-05T09:00:00",
        "sources": [
            {
                "source": "jenkins",
                "count": 2,
                "latest": "2026-03-05T09:00:00",
                "link_entries": 0,
            },
            {
                "source": "diary",
                "count": 1,
                "latest": "2026-03-02T09:00:00",
                "link_entries": 0,
            },
            {
                "source": "okta",
                "count": 1,
                "latest": "2026-03-03T09:00:00",
                "link_entries": 0,
            },
        ],
    }


def test_link_entries_follow_the_callers_record_access(summary):
    isms_reader = summary.keen.client(summary.keen.user("events.read", "isms.read"))
    sources = summary.read(isms_reader).json()["sources"]
    # Sources with equal counts are listed by name.
    assert [row["source"] for row in sources] == ["diary", "jenkins", "okta"]
    diary = sources[0]
    assert (diary["source"], diary["count"], diary["link_entries"]) == ("diary", 2, 1)
    assert diary["latest"] == "2026-03-07T09:00:00"

    admin = summary.keen.client(summary.keen.user(role="admin"))
    body = summary.read(admin).json()
    diary = next(row for row in body["sources"] if row["source"] == "diary")
    assert (diary["count"], diary["link_entries"]) == (3, 2)
    assert body["total"] == 6 and body["latest"] == "2026-03-08T09:00:00"


def test_a_cached_summary_stays_with_its_reader(summary, monkeypatch):
    monkeypatch.setattr(settings, "aggregate_cache_ttl_seconds", 60)
    admin = summary.keen.client(summary.keen.user(role="admin"))
    assert summary.read(admin).json()["total"] == 6
    # Another account receives its own counts, without the link entries.
    reader = summary.keen.client(summary.keen.user("events.read"))
    assert summary.read(reader).json()["total"] == 4


def test_another_control_and_an_empty_control(summary):
    reader = summary.keen.client(summary.keen.user("events.read"))
    body = summary.read(reader, summary.other).json()
    assert body["total"] == 1 and [s["source"] for s in body["sources"]] == ["jenkins"]
    empty = ControlItem(framework_slug=FW, ref="A.9.9", type="annex_control")
    summary.db.add(empty)
    summary.db.commit()
    body = summary.read(reader, empty).json()
    assert body == {
        "control_id": str(empty.id),
        "framework": FW,
        "total": 0,
        "latest": None,
        "sources": [],
    }


def test_the_summary_needs_events_read(summary):
    path = f"/v1/controls/{summary.access.id}/evidence/summary"
    assert summary.keen.client().get(path).status_code == 401
    isms_only = summary.keen.client(summary.keen.user("isms.read", "risk.read"))
    response = isms_only.get(path)
    assert response.status_code == 403
    assert response.json()["detail"] == "events.read permission required"
    reader = summary.keen.client(summary.keen.user("events.read"))
    assert reader.get("/v1/controls/not-a-uuid/evidence/summary").status_code == 400
    assert (
        reader.get(f"/v1/controls/{uuid.uuid4()}/evidence/summary").status_code == 404
    )
    # Writes are not part of the route.
    assert reader.post(path, json={}).status_code in (403, 405)


@pytest.mark.parametrize("listed", [5000, 0])
def test_an_event_mapped_directly_and_by_inheritance_counts_once(
    summary, monkeypatch, listed
):
    # With few matching mappings the evidence events are named by id; beyond
    # the listed limit the mappings remain a subquery. Both count events once.
    from app.services import control_inheritance

    monkeypatch.setattr(control_inheritance, "LISTED_MAPPINGS", listed)
    db = summary.db
    upstream = db.query(ControlItem).filter_by(framework_slug=OTHER_FW, ref="S.1").one()
    both = Event(
        source="okta",
        summary="Sign-in reviewed",
        external_id=uuid.uuid4().hex,
        timestamp=datetime(2026, 3, 4, 9, 0),
    )
    db.add(both)
    db.flush()
    for control in (summary.access, upstream):
        db.add(Mapping(event_id=both.id, control_item_id=control.id, method="manual"))
    db.commit()

    reader = summary.keen.client(summary.keen.user("events.read"))
    response = summary.read(reader)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 5
    assert {row["source"]: row["count"] for row in body["sources"]} == {
        "jenkins": 2,
        "okta": 2,
        "diary": 1,
    }
