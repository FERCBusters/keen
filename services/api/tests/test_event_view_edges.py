"""Event detail, facets, suggestions and exports at their edges.

A sample export masks addresses only when masking covers samples; the source
facet can be left out; rule facets match the framework exactly even where the
database compares text without case; control suggestions keep diary entries
hidden from readers who cannot open them and need a framework; and an export
whose events are removed while it streams ends with the events that remain.
"""

import json
import uuid
from datetime import datetime

import pytest
from app.core.config import settings
from app.db.models import ControlItem, Event, Framework, Mapping, User
from app.security.diary_visibility import (
    DIARY_SOURCE,
    LINK_ENTRY_SYSTEM,
    RISK_LINK_ENTRY_PREFIX,
)
from app.security.redaction import MASKED_EMAIL, MASKED_IP
from app.services import events as events_service
from app.services.event_filters import EventFilters
from sqlalchemy import create_engine
from sqlalchemy import event as sql_event
from sqlalchemy.orm import Session

from tests.db_helpers import create_sqlite_schema
from tests.test_assembled_authority_regressions import keen as keen

FW = "fw-edges"
DAY = datetime(2026, 3, 2, 9, 0)


@pytest.fixture(autouse=True)
def uncached(monkeypatch):
    monkeypatch.setattr(settings, "aggregate_cache_ttl_seconds", 0)


def _event(db, **columns):
    row = Event(
        source=columns.pop("source", "ci"),
        summary=columns.pop("summary", "Deploy finished"),
        external_id=columns.pop("external_id", uuid.uuid4().hex),
        timestamp=columns.pop("timestamp", DAY),
        **columns,
    )
    db.add(row)
    db.flush()
    return row


# --- Sample exports ---------------------------------------------------------------


def test_a_sample_export_masks_addresses_only_when_masking_covers_samples(
    keen, monkeypatch
):
    row = _event(
        keen.db,
        system="host 192.0.2.10",
        actor="ada@example.org",
        action="login",
        outcome=None,
        summary="ada@example.org signed in from 192.0.2.10",
        raw_pointer={"client": {"address": "198.51.100.7"}, "attempts": 3},
        normalized_payload={"user": "ada@example.org", "ok": True},
    )
    keen.db.commit()
    reader = keen.client(keen.user("events.read"))

    def shown(**params):
        response = reader.get(f"/v1/events/{row.id}", params=params)
        assert response.status_code == 200, response.text
        return response.json()

    monkeypatch.setattr(settings, "event_data_masking", "samples")
    masked = shown(sample_export="true")
    assert masked["system"] == f"host {MASKED_IP}"
    assert masked["actor"] == MASKED_EMAIL
    assert masked["action"] == "login" and masked["outcome"] is None
    assert masked["summary"] == f"{MASKED_EMAIL} signed in from {MASKED_IP}"
    assert masked["raw_pointer"] == {"client": {"address": MASKED_IP}, "attempts": 3}
    assert masked["normalized_payload"] == {"user": MASKED_EMAIL, "ok": True}

    # The event page itself, and samples without masking, keep the values.
    page = shown()
    monkeypatch.setattr(settings, "event_data_masking", "false")
    for unmasked in (page, shown(sample_export="true")):
        assert unmasked["actor"] == "ada@example.org"
        assert unmasked["raw_pointer"]["client"]["address"] == "198.51.100.7"


# --- Facets ---------------------------------------------------------------------


def test_the_source_facet_can_be_left_out_while_controls_are_counted(keen):
    db = keen.db
    db.add(Framework(slug=FW))
    control = ControlItem(framework_slug=FW, type="control", ref="C.1")
    db.add(control)
    db.flush()
    row = _event(db, source="jenkins")
    db.add(Mapping(event_id=row.id, control_item_id=control.id, method="manual"))
    db.commit()
    reader = keen.client(keen.user("events.read"))

    def facets(**params):
        response = reader.get("/v1/events/facets", params={"framework": FW, **params})
        assert response.status_code == 200, response.text
        return response.json()

    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    sql_event.listen(db.get_bind(), "before_cursor_execute", record)
    try:
        without_sources = facets(max_sources=0)
        counted = len(statements)
        with_sources = facets()
    finally:
        sql_event.remove(db.get_bind(), "before_cursor_execute", record)
    assert without_sources["sources"] == []
    assert without_sources["controls"] == [
        {"ref": "C.1", "title": None, "type": "control", "count": 1}
    ]
    assert with_sources["sources"][0] == {"source": "jenkins", "count": 1}

    # Leaving the facet out skips its count, which a first page load needs fast.
    def counts_sources(statement):
        return "GROUP BY events.source" in statement

    assert not any(counts_sources(s) for s in statements[:counted])
    assert any(counts_sources(s) for s in statements[counted:])


def test_rule_facets_match_the_framework_exactly_where_text_compares_without_case(
    monkeypatch,
):
    # SQLite compares LIKE patterns without case, so the database filter alone
    # would also return another framework's rule mappings.
    engine = create_engine("sqlite://")
    create_sqlite_schema(engine)
    try:
        with Session(engine) as db:
            admin = User(username="admin", password_hash="x", role="admin")
            control = ControlItem(framework_slug="FW-CASE", type="control", ref="C.1")
            db.add_all([admin, control])
            db.flush()
            row = _event(db)
            db.add(
                Mapping(
                    event_id=row.id,
                    control_item_id=control.id,
                    method="rule",
                    mapped_by="system",
                    rationale="auto by rule r1 (FW-CASE)",
                )
            )
            db.commit()

            def rules(framework):
                return events_service.event_facets(
                    filters=EventFilters(framework=framework),
                    max_sources=0,
                    max_controls=0,
                    db=db,
                    user=admin,
                )["rules"]

            assert rules("fw-case") == []
            assert [(r["rule_id"], r["count"]) for r in rules("FW-CASE")] == [("r1", 1)]
    finally:
        engine.dispose()


# --- Control suggestions -----------------------------------------------------------


def test_control_suggestions_keep_hidden_diary_entries_hidden_and_need_a_framework(
    keen,
):
    db = keen.db
    entry = _event(
        db,
        source=DIARY_SOURCE,
        system=LINK_ENTRY_SYSTEM,
        external_id=f"{RISK_LINK_ENTRY_PREFIX}{uuid.uuid4()}",
        summary="Risk linked",
    )
    ordinary = _event(db, summary="Backup completed")
    db.commit()
    reader = keen.client(keen.user("events.read"))
    risk_reader = keen.client(keen.user("events.read", "risk.read"))
    path = f"/v1/events/{entry.id}/control-suggestions"

    hidden = reader.get(path, params={"framework": FW})
    assert (hidden.status_code, hidden.json()["detail"]) == (404, "Event not found")
    assert risk_reader.get(path, params={"framework": FW}).status_code == 200

    blank = reader.get(
        f"/v1/events/{ordinary.id}/control-suggestions", params={"framework": "  "}
    )
    assert (blank.status_code, blank.json()["detail"]) == (
        400,
        "framework is required",
    )


# --- Exports --------------------------------------------------------------------


def test_an_export_whose_events_are_removed_while_it_streams_ends_cleanly(
    contract_db,
):
    db = contract_db
    admin = User(username="admin-" + uuid.uuid4().hex[:6], password_hash="x")
    admin.role = "admin"
    db.add(admin)
    rows = [
        _event(db, source="export-edge", timestamp=datetime(2026, 3, 2, 9, n))
        for n in range(3)
    ]
    db.commit()
    export = events_service.export_events(
        filters=EventFilters(framework=FW, sources=("export-edge",)),
        format="json",
        max_rows=100,
        db=db,
        user=admin,
    )
    assert export.attributes["X-Keen-Export-Total"] == "3"
    # Retention removes the newest event after the count, before the stream.
    db.delete(rows[2])
    db.commit()
    body = json.loads("".join(export.content))
    assert (body["total"], body["returned"]) == (3, 3)
    assert [item["id"] for item in body["items"]] == [
        str(rows[1].id),
        str(rows[0].id),
    ]
    assert "incomplete" not in body
