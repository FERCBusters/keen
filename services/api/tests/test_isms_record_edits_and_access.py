"""ISMS record edits, overview sections and access predicates at their edges.

Covers who may read a meeting, moving an access matrix row to another service,
saving a document type, an organisation node's descriptive fields, a metric
entry that names its source event, minutes that outgrow their limit once
sanitised, overview sections, relationship lookups of unknown kinds, control
requirements with measures switched off, and the SWOT structure lock.
Requests pass through the assembled application unless a test says otherwise.
"""

import uuid
from datetime import datetime

import pytest
from app.db.models import (
    Event,
    IsmsAccessControlMatrixEntry,
    IsmsDocument,
    IsmsEffectivenessMetricEntry,
    IsmsMeeting,
    IsmsOrgNode,
)
from app.services import features
from app.services.errors import DomainError

# Imported fixtures: the assembled application, a populated ISMS and a
# catalogue with linked policies and measures.
from tests.test_assembled_authority_regressions import keen  # noqa: F401
from tests.test_control_requirements import lab  # noqa: F401
from tests.test_crosswalk_resolver import statements_of
from tests.test_isms_overview_api_workflows import _send, isms  # noqa: F401

# --- Meetings ---------------------------------------------------------------------


def test_meeting_reads_follow_isms_access_or_a_place_on_the_meeting(isms):  # noqa: F811
    from app.services.isms.access import can_read_meeting

    db, app = isms.db, isms.keen
    attended = uuid.UUID(isms.meeting["id"])
    elsewhere = uuid.UUID(
        isms.manage(
            "POST",
            "/v1/isms/meetings",
            {"title": "Supplier review", "date": "2026-04-01"},
        )["id"]
    )
    reader = app.user("isms.read")
    retired_reader = app.user("isms.read")
    retired_reader.is_active = False
    db.commit()

    # ISMS readers read every meeting; others only the ones they are on.
    assert can_read_meeting(db, reader, attended) is True
    assert can_read_meeting(db, reader, elsewhere) is True
    assert can_read_meeting(db, isms.member, attended) is True
    assert can_read_meeting(db, isms.member, elsewhere) is False
    assert can_read_meeting(db, app.user(), attended) is False
    # A retired account and no account at all read nothing, ISMS access or not.
    assert can_read_meeting(db, retired_reader, attended) is False
    assert can_read_meeting(db, None, attended) is False


def test_minutes_that_outgrow_their_limit_once_sanitised_are_refused(isms):  # noqa: F811
    path = f"/v1/isms/meetings/{isms.meeting['id']}"
    # Fifty thousand characters, each bare ampersand stored as "&amp;".
    swollen = "<p>" + "&" * 49993 + "</p>"
    assert len(swollen) == 50000

    refused = isms.manage("PATCH", path, {"agenda_minutes_notes": swollen}, status=400)

    assert refused["detail"] == "agenda/minutes/notes is too long"
    isms.db.expire_all()
    stored = isms.db.get(IsmsMeeting, uuid.UUID(isms.meeting["id"]))
    assert stored.agenda_minutes_notes == isms.meeting["agenda_minutes_notes"]
    fits = "<p>" + "&" * 9998 + "</p>"
    saved = isms.manage("PATCH", path, {"agenda_minutes_notes": fits})
    assert saved["agenda_minutes_notes"] == "<p>" + "&amp;" * 9998 + "</p>"


# --- Record edits -----------------------------------------------------------------


def test_a_matrix_row_moves_to_another_service(isms):  # noqa: F811
    path = f"/v1/isms/access-control-matrix/{isms.entry['id']}"

    moved = isms.manage("PATCH", path, {"service_asset_id": isms.unowned_asset["id"]})

    assert (moved["service_asset_id"], moved["service"]["asset"]) == (
        isms.unowned_asset["id"],
        "Kiosk",
    )
    # The fields the edit leaves out keep their values.
    assert moved["task_action"] == "Deploy"
    assert [r["id"] for r in moved["roles"]] == [isms.role["id"]]
    isms.db.expire_all()
    row = isms.db.get(IsmsAccessControlMatrixEntry, uuid.UUID(isms.entry["id"]))
    assert str(row.service_asset_id) == isms.unowned_asset["id"]
    reader = isms.keen.client(isms.keen.user("isms.read"))
    assert _send(reader, "GET", path) == moved


def test_a_document_type_is_saved_in_lower_case(isms):  # noqa: F811
    path = f"/v1/isms/documents/{isms.document['id']}"

    typed = isms.manage("PATCH", path, {"document_type": "  Procedure "})
    untyped = isms.manage("PATCH", path, {"document_type": ""})

    assert typed["document_type"] == "procedure"
    assert typed["title"] == "Security policy"
    # A blank type is a policy.
    assert untyped["document_type"] == "policy"
    isms.db.expire_all()
    assert isms.db.get(IsmsDocument, uuid.UUID(isms.document["id"])).document_type == (
        "policy"
    )


def test_an_organisation_node_edit_changes_its_type_description_and_order(isms):  # noqa: F811
    path = f"/v1/isms/org-nodes/{isms.role['id']}"

    edited = isms.manage(
        "PATCH",
        path,
        {"node_type": "committee", "description": "Meets monthly", "sort_order": 4},
    )

    assert (edited["node_type"], edited["description"], edited["sort_order"]) == (
        "committee",
        "Meets monthly",
        4,
    )
    # Its name and members stay as they were.
    assert edited["name"] == "Security officer"
    assert [p["user"]["id"] for p in edited["people"]] == [str(isms.member.id)]
    isms.db.expire_all()
    row = isms.db.get(IsmsOrgNode, uuid.UUID(isms.role["id"]))
    assert (row.node_type, row.description, row.sort_order) == (
        "committee",
        "Meets monthly",
        4,
    )
    refused = isms.manage("PATCH", path, {"node_type": "  "}, status=400)
    assert refused["detail"] == "node_type is required"


def test_a_metric_entry_names_the_event_it_came_from(isms):  # noqa: F811
    event = Event(
        source="ci",
        summary="Nightly restore test",
        timestamp=datetime(2026, 3, 1, 2, 0),
        external_id=uuid.uuid4().hex,
        raw_pointer={},
        normalized_payload={},
    )
    isms.db.add(event)
    isms.db.commit()
    measure = isms.manage(
        "POST", "/v1/isms/effectiveness-measures", {"summary": "Restores"}
    )

    entry = isms.manage(
        "POST",
        f"/v1/isms/effectiveness-measures/{measure['id']}/metrics",
        {"metric_value": 1, "source_event_id": str(event.id)},
    )

    assert (entry["source_event_id"], entry["source_event_url"]) == (
        str(event.id),
        f"/event.html?id={event.id}",
    )
    isms.db.expire_all()
    stored = isms.db.get(IsmsEffectivenessMetricEntry, uuid.UUID(entry["id"]))
    assert stored.source_event_id == event.id


# --- Overview ---------------------------------------------------------------------


def test_a_blank_section_reads_the_whole_overview(isms):  # noqa: F811
    reader = isms.keen.client(isms.keen.user("isms.read"))

    summary = _send(reader, "GET", "/v1/isms/summary", section="   ")
    meta = _send(reader, "GET", "/v1/isms/meta", section="   ")

    assert summary["section"] == meta["section"] == "all"
    assert {"objectives", "documents", "meetings", "assets"} <= set(summary)
    assert {"users", "controls", "clauses", "documents"} <= set(meta)


def test_a_summary_section_lists_only_its_own_register(isms):  # noqa: F811
    reader = isms.keen.client(isms.keen.user("isms.read"))

    documents = _send(reader, "GET", "/v1/isms/summary", section="documents")

    assert documents["section"] == "documents"
    assert [d["id"] for d in documents["documents"]] == [isms.document["id"]]
    assert not {
        "objectives",
        "org_nodes",
        "assets",
        "access_control_matrix",
        "effectiveness_measures",
        "meetings",
    } & set(documents)
    # Counts still describe every register.
    assert documents["counts"]["objectives"] == 2


# --- Relationships ----------------------------------------------------------------


def test_relationship_lookups_refuse_unknown_record_kinds(isms):  # noqa: F811
    from app.services.isms.related_records import resolve_target, search_targets

    reader = isms.keen.user("isms.read")
    target = uuid.UUID(isms.document["id"])
    for lookup in (
        lambda: resolve_target(isms.db, reader, "ledger", target),
        lambda: resolve_target(isms.db, reader, "ledger", target, required=False),
        lambda: search_targets(isms.db, reader, "ledger", "Security"),
    ):
        with pytest.raises(DomainError) as refused:
            lookup()
        assert (refused.value.status_code, refused.value.detail) == (
            400,
            "Unknown relationship type",
        )


def test_control_requirements_count_only_policies_while_measures_are_off(lab):  # noqa: F811
    app, controls = lab
    features.update(app.db, app.user(role="admin"), 0, {"effectiveness": False})
    reader = app.client(app.user("isms.read"))

    response = reader.get("/v1/isms/control-requirements", params={"framework": "A"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["policies"], body["measures"]) == (True, False)
    empty = {"measures": 0, "met": 0, "judged": 0, "unrecorded": 0}
    assert body["items"] == {
        str(controls["A.1"].id): {"policies": 2, **empty},
        str(controls["A.2"].id): {"policies": 2, **empty},
        str(controls["A.3"].id): {"policies": 1, **empty},
    }


# --- SWOT structure lock ----------------------------------------------------------


def test_a_swot_structure_edit_takes_the_structure_lock(contract_db):
    from app.services.isms.swot import _lock

    _, statements = statements_of(contract_db, lambda: _lock(contract_db))
    assert statements == [
        "SAVEPOINT sa_savepoint_1",
        "SELECT pg_advisory_xact_lock_shared(%(key)s)",
        "SELECT pg_advisory_xact_lock(724103610)",
    ]
