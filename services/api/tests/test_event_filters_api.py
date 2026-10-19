"""Event list, facet and export filters through the assembled application.

Events are filtered by system, actor, free text, control (by reference or ID,
within the chosen framework) and clause. The homepage ticker shows the newest
arrivals with a bounded, redacted summary. Requests come from a user who can
read events.
"""

import csv
import io
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from app.core.datetime_utils import utc_now_naive
from app.db.models import (
    ControlClauseLink,
    ControlItem,
    Event,
    Framework,
    FrameworkClause,
    Mapping,
)

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import keen, unsafe  # noqa: F401

FW = "fw-filters"


@pytest.fixture
def events(keen):  # noqa: F811 - fixture imported above
    db = keen.db
    db.add_all([Framework(slug=FW), Framework(slug="fw-elsewhere")])
    access = ControlItem(framework_slug=FW, ref="A.5.15", type="annex_control")
    backup = ControlItem(framework_slug=FW, ref="A.8.13", type="annex_control")
    foreign = ControlItem(
        framework_slug="fw-elsewhere", ref="A.5.15", type="annex_control"
    )
    clause = FrameworkClause(framework_slug=FW, ref="9.2", title="Internal audit")
    db.add_all([access, backup, foreign, clause])
    db.flush()
    db.add(
        ControlClauseLink(
            control_item_id=backup.id, clause_id=clause.id, applicability="applicable"
        )
    )

    def event(summary, *controls, **fields):
        row = Event(
            source=fields.pop("source", "ci"),
            summary=summary,
            external_id=uuid.uuid4().hex,
            timestamp=fields.pop("timestamp", datetime(2026, 3, 1)),
            **fields,
        )
        db.add(row)
        db.flush()
        for control in controls:
            db.add(Mapping(event_id=row.id, control_item_id=control.id, method="rule"))
        return row

    rows = {
        "login": event(
            "Admin login reviewed", access, system="Okta-Prod", actor="Alice Smith"
        ),
        "backup": event(
            "Nightly backup & restore", backup, system="backup-01", actor="cron"
        ),
        "foreign": event(
            "Access reviewed elsewhere", foreign, system="okta-test", actor="alice"
        ),
        "plain": event("Unmapped heartbeat", system="monitor", actor="robot"),
        "budget": event("R&D budget approved", system="finance", actor="cfo"),
        "users": event("userXname rotated", system="iam", actor="robot"),
    }
    db.commit()
    reader = keen.client(keen.user("events.read"))

    def listed(**params):
        response = reader.get("/v1/events", params={"framework": FW, **params})
        assert response.status_code == 200, response.text
        return sorted(item["summary"] for item in response.json()["items"])

    return SimpleNamespace(
        db=db,
        keen=keen,
        reader=reader,
        rows=rows,
        listed=listed,
        access=access,
        foreign=foreign,
        clause=clause,
    )


@pytest.mark.parametrize(
    "params,expected",
    [
        ({"system": "okta"}, ["Access reviewed elsewhere", "Admin login reviewed"]),
        ({"actor": "ALICE"}, ["Access reviewed elsewhere", "Admin login reviewed"]),
        ({"q": "backup"}, ["Nightly backup & restore"]),
        # Search operators are read as ordinary text.
        ({"q": "backup & restore"}, ["Nightly backup & restore"]),
        ({"q": "!heartbeat|"}, ["Unmapped heartbeat"]),
        # Punctuation is part of a substring, and LIKE wildcards match literally.
        ({"q": "R&D"}, ["R&D budget approved"]),
        ({"q": "user_name"}, []),
        ({"q": "100%"}, []),
        ({"control": "A.5.15"}, ["Admin login reviewed"]),
        ({"control": "A.9.99"}, []),
        ({"clause": "9.2"}, ["Nightly backup & restore"]),
        (
            {"unmapped": "true"},
            [
                "Access reviewed elsewhere",
                "R&D budget approved",
                "Unmapped heartbeat",
                "userXname rotated",
            ],
        ),
    ],
)
def test_list_filters_select_the_matching_events(events, params, expected):
    assert events.listed(**params) == expected


def test_control_ids_from_another_framework_match_nothing(events):
    assert events.listed(control=str(events.foreign.id)) == []
    assert events.listed(control=str(events.access.id)) == ["Admin login reviewed"]
    assert events.listed(clause=str(events.clause.id)) == ["Nightly backup & restore"]


def test_facets_apply_the_same_filters(events):
    response = events.reader.get(
        "/v1/events/facets",
        params={"framework": FW, "system": "okta", "actor": "alice", "q": "reviewed"},
    )

    assert response.status_code == 200, response.text
    sources = {item["source"]: item["count"] for item in response.json()["sources"]}
    assert sources == {"ci": 2}
    for control in ("A.9.99", str(events.foreign.id)):
        unknown = events.reader.get(
            "/v1/events/facets", params={"framework": FW, "control": control}
        ).json()
        assert sum(item["count"] for item in unknown["sources"]) == 0, control
    known = events.reader.get(
        "/v1/events/facets", params={"framework": FW, "control": str(events.access.id)}
    ).json()
    assert {item["source"]: item["count"] for item in known["sources"]} == {"ci": 1}


def test_exports_for_an_unknown_control_are_empty(events):
    as_csv = events.reader.get(
        "/v1/events/export", params={"framework": FW, "control": "A.9.99"}
    )
    assert as_csv.status_code == 200
    (header,) = list(csv.reader(io.StringIO(as_csv.text)))
    assert header[:3] == ["id", "timestamp", "source"]

    as_json = events.reader.get(
        "/v1/events/export",
        params={"framework": FW, "control": "A.9.99", "format": "json"},
    )
    assert as_json.json() == {"total": 0, "returned": 0, "items": []}

    filtered = events.reader.get(
        "/v1/events/export",
        params={"framework": FW, "system": "okta", "actor": "alice", "format": "json"},
    ).json()
    assert filtered["total"] == 2


def test_the_ticker_shows_newer_arrivals_with_bounded_summaries(events):
    long_summary = "Deploy " + "x" * 300 + " password=hunter2"
    newest = events.db.query(Event).filter_by(summary="Unmapped heartbeat").one()
    newest.summary = long_summary
    newest.created_at = utc_now_naive() + timedelta(seconds=5)
    events.db.commit()

    response = events.reader.get("/v1/evidence/latest", params={"limit": 2})

    assert response.status_code == 200, response.text
    first = response.json()["events"][0]
    assert first["id"] == str(newest.id)
    assert len(first["summary"]) == 218 and first["summary"].endswith("…")
    later = events.reader.get(
        "/v1/evidence/latest",
        params={"after": (newest.created_at + timedelta(seconds=1)).isoformat()},
    ).json()
    assert later["events"] == []


def test_incidents_need_their_own_permissions(events):
    event_id = events.rows["login"].id
    create = events.reader.post(
        f"/v1/events/{event_id}/incident",
        json={"title": "Suspicious login"},
        headers=unsafe(events.reader),
    )
    assert (create.status_code, create.json()["detail"]) == (
        403,
        "incident.create permission required",
    )
    remove = events.reader.delete(
        f"/v1/events/{event_id}/incidents/{uuid.uuid4()}", headers=unsafe(events.reader)
    )
    assert (remove.status_code, remove.json()["detail"]) == (
        403,
        "incident.delete permission required",
    )
    deleter = events.keen.client(events.keen.user("events.read", "incident.delete"))
    bad = deleter.delete(
        "/v1/events/not-a-uuid/incidents/also-not", headers=unsafe(deleter)
    )
    assert (bad.status_code, bad.json()["detail"]) == (
        400,
        "event_id and incident_id must be UUIDs",
    )


def test_incident_operations_check_permissions_themselves(events, monkeypatch):
    from app.schemas import IncidentCreatePayload
    from app.services import events as event_operations
    from app.services.context import ChangeContext
    from app.services.errors import DomainError

    # The incident webhook is configured; the reader still lacks permission.
    monkeypatch.setattr(event_operations, "incident_webhook_enabled", lambda: True)
    reader = events.keen.user("events.read")
    context = ChangeContext(actor=reader)
    event_id = str(events.rows["login"].id)

    with pytest.raises(DomainError) as created:
        event_operations.create_event_incident(
            event_id=event_id,
            payload=IncidentCreatePayload(title="Suspicious login"),
            context=context,
            db=events.db,
            event_url="/event.html",
        )
    with pytest.raises(DomainError) as deleted:
        event_operations.delete_event_incident(
            event_id=event_id,
            incident_id=str(uuid.uuid4()),
            context=context,
            db=events.db,
        )

    assert (created.value.status_code, deleted.value.status_code) == (403, 403)
