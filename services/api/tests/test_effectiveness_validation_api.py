"""Effectiveness measures and their metric entries through the assembled app.

A measure's summary names what is measured and its description explains how;
it carries an owner, a pass threshold (operator, target value and unit), the
Keen source expected to supply its values and may carry a metric key that
metric webhooks address it by. A metric entry records
one observation: a value or a qualitative result, its period and where it
came from. Requests pass through the production middleware, password sign-in,
CSRF and the ISMS permission checks.
"""

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from app.db.models import Event, Framework

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import keen, unsafe  # noqa: F401

MEASURES = "/v1/isms/effectiveness-measures"


@pytest.fixture
def isms(keen):  # noqa: F811 - fixture imported above
    keen.db.add(Framework(slug="A"))
    keen.db.commit()
    admin = keen.client(keen.user(role="admin"))

    def send(method, path, body=None):
        return admin.request(
            method,
            path,
            params={"framework": "A"},
            **({"json": body} if body is not None else {}),
            headers=unsafe(admin),
        )

    def measure(**fields):
        response = send(
            "POST",
            MEASURES,
            {
                "summary": "Backups restore",
                **fields,
            },
        )
        assert response.status_code == 200, response.text
        return response.json()

    return SimpleNamespace(keen=keen, db=keen.db, send=send, measure=measure)


def _refused(response, detail):
    assert (response.status_code, response.json()["detail"]) == (400, detail)


def test_metric_keys_are_simple_names_unique_regardless_of_case(isms):
    first = isms.measure(metric_key="backup.restore:weekly")
    assert first["metric_key"] == "backup.restore:weekly"

    _refused(
        isms.send(
            "POST",
            MEASURES,
            {
                "summary": "Other",
                "metric_key": "BACKUP.Restore:Weekly",
            },
        ),
        "metric_key already exists",
    )
    for key in ("backup restore", "backup/restore", "backup?x=1"):
        _refused(
            isms.send(
                "POST",
                MEASURES,
                {
                    "summary": "Other",
                    "metric_key": key,
                },
            ),
            "metric_key may only contain letters, numbers, dot, underscore, colon and hyphen",
        )

    # A measure keeps its own key when edited; it cannot take another's.
    path = f"{MEASURES}/{first['id']}"
    kept = isms.send(
        "PATCH", path, {"metric_key": "Backup.Restore:Weekly", "summary": "Weekly"}
    )
    assert kept.status_code == 200, kept.text
    assert kept.json()["metric_key"] == "Backup.Restore:Weekly"
    second = isms.measure(metric_key="patching")
    _refused(
        isms.send(
            "PATCH",
            f"{MEASURES}/{second['id']}",
            {"metric_key": "backup.restore:WEEKLY"},
        ),
        "metric_key already exists",
    )
    # A blank key removes it, so any number of measures can go without one.
    for measure in (second, first):
        cleared = isms.send(
            "PATCH", f"{MEASURES}/{measure['id']}", {"metric_key": "  "}
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["metric_key"] is None
    assert isms.measure(metric_key="")["metric_key"] is None


def test_owners_thresholds_and_links_are_checked(isms):
    retired = isms.keen.user()
    retired.is_active = False
    isms.db.commit()
    for owner in (uuid.uuid4(), retired.id):
        _refused(
            isms.send(
                "POST",
                MEASURES,
                {
                    "summary": "M",
                    "owner_user_id": str(owner),
                },
            ),
            "Unknown owner",
        )
    _refused(
        isms.send(
            "POST",
            MEASURES,
            {
                "summary": "M",
                "threshold_operator": "approximately",
            },
        ),
        "Invalid threshold operator",
    )
    _refused(
        isms.send(
            "POST",
            MEASURES,
            {
                "summary": "M",
                "clauses": [str(uuid.uuid4())],
            },
        ),
        "Effectiveness measures link controls only; clauses are inferred from those controls",
    )
    owner = isms.keen.user()
    saved = isms.measure(
        owner_user_id=str(owner.id), threshold_operator="gte", target_value=95
    )
    assert (
        saved["owner_user_id"],
        saved["threshold_operator"],
        saved["target_value"],
    ) == (
        str(owner.id),
        "gte",
        95,
    )
    # An edit that leaves the owner alone is not checked again.
    owner.is_active = False
    isms.db.commit()
    renamed = isms.send("PATCH", f"{MEASURES}/{saved['id']}", {"summary": "Renamed"})
    assert renamed.status_code == 200, renamed.text
    _refused(
        isms.send(
            "PATCH", f"{MEASURES}/{saved['id']}", {"owner_user_id": str(owner.id)}
        ),
        "Unknown owner",
    )


def _entries(isms, measure):
    return f"{MEASURES}/{measure['id']}/metrics"


def test_a_metric_entry_needs_a_result_and_a_sensible_period(isms):
    measure = isms.measure()
    path = _entries(isms, measure)
    _refused(
        isms.send("POST", path, {"notes": "Nothing measured"}),
        "metric_value or qualitative_value is required",
    )
    _refused(
        isms.send(
            "POST",
            path,
            {
                "metric_value": 1,
                "period_start": "2026-02-01",
                "period_end": "2026-01-31",
            },
        ),
        "period_end must be after period_start",
    )
    _refused(
        isms.send(
            "POST", path, {"metric_value": 1, "source_event_id": str(uuid.uuid4())}
        ),
        "Unknown source event",
    )
    qualitative = isms.send("POST", path, {"qualitative_value": "Restore succeeded"})
    assert qualitative.status_code == 200, qualitative.text
    zero = isms.send(
        "POST",
        path,
        {"metric_value": 0, "period_start": "2026-01-01", "period_end": "2026-01-01"},
    )
    assert zero.status_code == 200, zero.text
    assert zero.json()["metric_value"] == 0

    # Editing only the notes keeps the recorded result.
    entry = f"/v1/isms/effectiveness-metrics/{zero.json()['id']}"
    noted = isms.send("PATCH", entry, {"notes": "Checked by the service desk"})
    assert noted.status_code == 200, noted.text
    assert noted.json()["metric_value"] == 0
    _refused(
        isms.send("PATCH", entry, {"metric_value": None}),
        "metric_value or qualitative_value is required",
    )


def test_metric_sources_are_existing_evidence_sources_or_other(isms):
    isms.db.add(
        Event(
            source="google_workspace",
            summary="Admin login",
            external_id=uuid.uuid4().hex,
            timestamp=datetime(2026, 1, 1),
        )
    )
    isms.db.commit()
    measure = isms.measure()
    path = _entries(isms, measure)

    def stored(source_type):
        response = isms.send(
            "POST", path, {"metric_value": 1, "source_type": source_type}
        )
        assert response.status_code == 200, response.text
        return response.json()["source_type"]

    assert stored("") == "other"
    assert stored("OTHER") == "other"
    assert stored("google_workspace") == "google_workspace"
    # Spelling variants name the same collected source.
    assert stored("Google-Workspace") == "google_workspace"
    _refused(
        isms.send("POST", path, {"metric_value": 1, "source_type": "spreadsheet"}),
        "Invalid metric source type; choose an existing Keen source or other",
    )


def test_a_measure_needs_a_summary_and_a_known_expected_source(isms):
    _refused(isms.send("POST", MEASURES, {"description": "How"}), "summary is required")
    _refused(
        isms.send("POST", MEASURES, {"summary": "   ", "description": "How"}),
        "summary is required",
    )
    # The method and metric definition columns are gone: the API ignores the
    # old names instead of storing them anywhere.
    saved = isms.measure(effectiveness_measure="legacy", metric="legacy")
    assert "effectiveness_measure" not in saved and "metric" not in saved
    assert saved["source_type"] == "other"
    assert saved["display"] == "Backups restore"

    _refused(
        isms.send("POST", MEASURES, {"summary": "M", "source_type": "spreadsheet"}),
        "Invalid metric source type; choose an existing Keen source or other",
    )
    isms.db.add(
        Event(
            source="google_workspace",
            summary="Admin login",
            external_id=uuid.uuid4().hex,
            timestamp=datetime(2026, 1, 1),
        )
    )
    isms.db.commit()
    fed = isms.measure(source_type="Google-Workspace")
    assert fed["source_type"] == "google_workspace"
    # An edit that leaves the source alone keeps it; a blank edit returns to other.
    renamed = isms.send("PATCH", f"{MEASURES}/{fed['id']}", {"summary": "Renamed"})
    assert renamed.json()["source_type"] == "google_workspace"
    cleared = isms.send("PATCH", f"{MEASURES}/{fed['id']}", {"source_type": ""})
    assert cleared.json()["source_type"] == "other"


def test_metric_entries_inherit_the_measure_source_unless_told_otherwise(isms):
    isms.db.add(
        Event(
            source="google_workspace",
            summary="Admin login",
            external_id=uuid.uuid4().hex,
            timestamp=datetime(2026, 1, 1),
        )
    )
    isms.db.commit()
    measure = isms.measure(source_type="google_workspace")
    path = _entries(isms, measure)
    inherited = isms.send("POST", path, {"metric_value": 1})
    assert inherited.status_code == 200, inherited.text
    assert inherited.json()["source_type"] == "google_workspace"
    explicit = isms.send("POST", path, {"metric_value": 1, "source_type": "other"})
    assert explicit.json()["source_type"] == "other"
    # Editing an entry's value leaves its recorded source alone.
    entry = f"/v1/isms/effectiveness-metrics/{explicit.json()['id']}"
    edited = isms.send("PATCH", entry, {"metric_value": 2})
    assert edited.json()["source_type"] == "other"


def test_each_value_is_judged_against_the_target_in_the_same_unit(isms):
    measure = isms.measure(threshold_operator="gte", target_value=95, target_unit="%")
    path = _entries(isms, measure)

    def status(body):
        response = isms.send("POST", path, body)
        assert response.status_code == 200, response.text
        return response.json()["threshold_status"]

    # The unit defaults to the measure's, so a bare value is comparable.
    assert status({"metric_value": 96}) == "met"
    assert status({"metric_value": 95}) == "met"
    assert status({"metric_value": 94.9}) == "outside"
    # A value in another unit, or a qualitative result, cannot be judged.
    assert status({"metric_value": 200, "metric_unit": "count"}) is None
    assert status({"qualitative_value": "Pass"}) is None
    latest = isms.send("GET", f"{MEASURES}/{measure['id']}").json()
    assert latest["latest_threshold_status"] is None
    assert latest["latest_entry"]["threshold_status"] is None
    assert [e["threshold_status"] for e in latest["metric_entries"]][1:] == [
        None,
        "outside",
        "met",
        "met",
    ]
    # Without a target nothing is judged.
    untargeted = isms.measure()
    bare = isms.send("POST", _entries(isms, untargeted), {"metric_value": 1})
    assert bare.json()["threshold_status"] is None


def test_a_retired_expected_source_falls_back_to_other_for_new_entries(isms):
    event = Event(
        source="google_workspace",
        summary="Admin login",
        external_id=uuid.uuid4().hex,
        timestamp=datetime(2026, 1, 1),
    )
    isms.db.add(event)
    isms.db.commit()
    measure = isms.measure(source_type="google_workspace", metric_key="gw.logins")
    # The source's evidence is purged, so it no longer collects evidence.
    isms.db.delete(event)
    isms.db.commit()
    assert isms.send("GET", f"{MEASURES}/{measure['id']}").json()["source_type"] == (
        "google_workspace"
    )
    recorded = isms.send("POST", _entries(isms, measure), {"metric_value": 1})
    assert recorded.status_code == 200, recorded.text
    assert recorded.json()["source_type"] == "other"
    # Naming the retired source explicitly is still refused.
    _refused(
        isms.send(
            "POST",
            _entries(isms, measure),
            {"metric_value": 1, "source_type": "google_workspace"},
        ),
        "Invalid metric source type; choose an existing Keen source or other",
    )
