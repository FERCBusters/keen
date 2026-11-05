"""Audit sampling, scope and coverage rules decided at their edges.

Covers resampling an event that has no summary, coverage and scope maps with
nothing to relate, how sampled metric entries and undescribed sample types are
described, scheduled template edits that name only documents or only clauses,
clearing a SWOT scope while SWOT is switched off, rich text that outgrows its
limit once sanitised, and the template authority check in the service layer.
Requests pass through the assembled application unless a test says otherwise.
"""

import uuid
from datetime import date, datetime

import pytest
from app.db.models import (
    Audit,
    AuditEvidence,
    AuditScopedClause,
    AuditScopedControl,
    AuditScopedIsmsDocument,
    AuditSwotScope,
    Event,
    IsmsEffectivenessMeasure,
    IsmsEffectivenessMetricEntry,
    IsmsEntityClauseLink,
    IsmsEntityControlLink,
    IsmsMeeting,
    IsmsSwot,
)
from app.services import features
from app.services.errors import DomainError

# Imported fixtures: the assembled application and two audit workspaces.
from tests.test_assembled_authority_regressions import keen  # noqa: F401
from tests.test_audit_lifecycle_api import aud  # noqa: F401
from tests.test_audit_query_api_workflows import _get, audits  # noqa: F401
from tests.test_crosswalk_resolver import statements_of
from tests.test_scheduled_audit_templates_api import ROOT, _created, _send

DISABLED = "This feature is disabled by an administrator."


# --- Samples --------------------------------------------------------------------


def test_resampling_an_event_without_a_summary_keeps_the_sample_title(aud):  # noqa: F811
    quiet = Event(
        source="ci",
        summary="",
        timestamp=datetime(2026, 3, 2, 9, 0),
        external_id=uuid.uuid4().hex,
        raw_pointer={},
        normalized_payload={},
    )
    aud.db.add(quiet)
    aud.db.commit()
    first = aud.call(
        aud.client,
        "POST",
        "/evidence",
        json={"event_id": str(quiet.id), "title": "Backup log"},
    )
    assert first.status_code == 200, first.text

    # Neither the request nor the event offers a title, so the sample keeps its own.
    again = aud.call(
        aud.client,
        "POST",
        "/evidence",
        json={"event_id": str(quiet.id), "notes": "<p>Checked</p>"},
    )

    assert again.status_code == 200, again.text
    assert again.json()["id"] == first.json()["id"]
    (sample,) = aud.detail()["evidence"]
    assert (sample["title"], sample["notes"]) == ("Backup log", "<p>Checked</p>")


def test_sampled_metric_entries_are_described_by_the_parts_they_record(audits):  # noqa: F811
    db = audits.db
    measure = IsmsEffectivenessMeasure(
        framework_slug="A", summary="Restore tests", description="Monthly"
    )
    db.add(measure)
    db.flush()
    db.add(
        IsmsEntityControlLink(
            entity_type="effectiveness_measure",
            entity_id=measure.id,
            framework_slug="A",
            control_item_id=audits.control.id,
        )
    )
    counted = IsmsEffectivenessMetricEntry(measure_id=measure.id, metric_value=12)
    judged = IsmsEffectivenessMetricEntry(
        measure_id=measure.id, qualitative_value="Restored cleanly"
    )
    both = IsmsEffectivenessMetricEntry(
        measure_id=measure.id, metric_value=3, qualitative_value="Partial"
    )
    db.add_all([counted, judged, both])
    db.flush()
    for row in (counted, judged, both):
        db.add(
            AuditEvidence(
                audit_id=audits.open_audit.id,
                entity_type="isms_effectiveness_metric",
                entity_id=row.id,
            )
        )
    db.commit()

    reader = audits.keen.client(audits.keen.user("audits.read"))
    detail = _get(reader, f"/v1/audits/{audits.open_audit.id}")

    described = {
        e["entity"]["id"]: (e["entity"]["title"], e["entity"]["subtitle"])
        for e in detail["evidence"]
    }
    # Only the parts an entry records are named, each once.
    assert described == {
        str(counted.id): ("ISMS metric: Restore tests", "12"),
        str(judged.id): ("ISMS metric: Restore tests", "Restored cleanly"),
        str(both.id): ("ISMS metric: Restore tests", "3 Partial"),
    }
    # Each entry brings the controls of its measure.
    assert {tuple(c["ref"] for c in e["controls"]) for e in detail["evidence"]} == {
        ("A.5.1",)
    }


def test_a_sample_type_without_a_description_is_never_described_as_another(
    audits,  # noqa: F811
    monkeypatch,
):
    from app.services.audits import samples

    meeting = IsmsMeeting(title="Board review", date=date(2026, 2, 1))
    audits.db.add(meeting)
    audits.db.commit()
    # A type accepted for sampling before anyone has written its description.
    monkeypatch.setitem(
        samples.AUDIT_SAMPLE_ENTITY_LABELS, "isms_minutes", "ISMS minutes"
    )

    with pytest.raises(DomainError) as missing:
        samples.audit_sample_entity_reference(
            audits.db, framework="A", entity_type="isms_minutes", entity_id=meeting.id
        )
    placeholder = samples.audit_sample_entity_reference(
        audits.db,
        framework="A",
        entity_type="isms_minutes",
        entity_id=meeting.id,
        required=False,
    )

    assert (missing.value.status_code, missing.value.detail) == (
        404,
        "Sampled entity not found",
    )
    # The meeting sharing its identifier is not offered in its place.
    assert placeholder == {
        "type": "isms_minutes",
        "id": str(meeting.id),
        "kind": "ISMS minutes",
        "title": "ISMS minutes",
        "subtitle": "This sampled item no longer exists.",
        "href": "/audits.html",
        "controls": [],
        "missing": True,
    }


# --- Coverage and scope maps ----------------------------------------------------


def test_coverage_of_a_window_without_audits_lists_every_item_as_a_gap(audits):  # noqa: F811
    reader = audits.keen.client(audits.keen.user("audits.read", "isms.read"))

    coverage, statements = statements_of(
        audits.db,
        lambda: _get(
            reader,
            "/v1/audits/scope-coverage",
            framework="A",
            start="2001-01-01",
            end="2001-12-31",
        ),
    )

    assert (coverage["source_audit_count"], coverage["scheduled_occurrence_count"]) == (
        0,
        0,
    )
    assert coverage["included"] == {"controls": [], "clauses": [], "documents": []}
    assert {c["ref"] for c in coverage["gaps"]["controls"]} == {"A.5.1", "A.5.2"}
    assert {c["ref"] for c in coverage["gaps"]["clauses"]} == {"5.1", "5.2"}
    assert {d["title"] for d in coverage["gaps"]["documents"]} == {
        "Security policy",
        "Acceptable use",
    }
    # With no audit to draw on, no audit's scope is read.
    assert any("FROM framework_clauses" in s for s in statements)
    assert [s for s in statements if "audit_scoped_" in s] == []


def test_a_scope_map_without_scoped_clauses_looks_up_no_relationships(audits):  # noqa: F811
    audits.db.add(
        AuditScopedControl(
            audit_id=audits.open_audit.id, control_item_id=audits.control.id
        )
    )
    audits.db.commit()
    reader = audits.keen.client(audits.keen.user("audits.read"))

    scope, statements = statements_of(
        audits.db,
        lambda: _get(reader, f"/v1/audits/{audits.open_audit.id}/scope-map"),
    )

    assert [c["ref"] for c in scope["controls"]] == ["A.5.1"]
    assert (scope["clauses"], scope["links"]) == ([], [])
    assert scope["summary"] == {
        "clause_count": 0,
        "control_count": 1,
        "document_count": 0,
        "relationship_count": 0,
        "controls_linked_to_selected_clauses": 0,
        "controls_in_scope_without_selected_clause_link": 1,
        "clauses_without_selected_control_link": 0,
    }
    # A.5.1's link to clause 5.1 is never read without a scoped clause.
    assert any("audit_scoped_controls" in s for s in statements)
    assert [s for s in statements if "control_clause_links" in s] == []


# --- Scheduled template scope -----------------------------------------------------


def _template_scope(audits, template_id):  # noqa: F811
    db = audits.db
    db.expire_all()
    audit_id = uuid.UUID(template_id)
    return (
        {
            r.control_item_id
            for r in db.query(AuditScopedControl).filter_by(audit_id=audit_id)
        },
        {r.clause_id for r in db.query(AuditScopedClause).filter_by(audit_id=audit_id)},
        {
            r.document_id
            for r in db.query(AuditScopedIsmsDocument).filter_by(audit_id=audit_id)
        },
    )


def test_scoping_a_templates_documents_adds_their_links_to_the_scope_it_keeps(
    audits,  # noqa: F811
):
    row = _created(
        audits,
        controls=[str(audits.other_control.id)],
        clauses=[str(audits.leaf2.id)],
    )
    # The document covers clause 5.1, which control A.5.1 is linked to.
    audits.db.add(
        IsmsEntityClauseLink(
            entity_type="document",
            entity_id=audits.document.id,
            framework_slug="A",
            clause_id=audits.leaf.id,
        )
    )
    audits.db.commit()

    edited = _send(
        audits, "PATCH", f"{ROOT}/{row['id']}", {"documents": [str(audits.document.id)]}
    )

    assert edited.status_code == 200, edited.text
    controls, clauses, documents = _template_scope(audits, row["id"])
    # The scope already chosen stays; the document brings its clause, and the
    # clause brings its control.
    assert clauses == {audits.leaf2.id, audits.leaf.id}
    assert controls == {audits.other_control.id, audits.control.id}
    assert documents == {audits.document.id}


def test_changing_only_a_templates_clauses_leaves_its_controls_as_they_are(audits):  # noqa: F811
    row = _created(audits, controls=[str(audits.other_control.id)])

    edited = _send(
        audits, "PATCH", f"{ROOT}/{row['id']}", {"clauses": [str(audits.leaf.id)]}
    )

    assert edited.status_code == 200, edited.text
    controls, clauses, documents = _template_scope(audits, row["id"])
    assert clauses == {audits.leaf.id}
    # The edit names no controls, so the clause's linked control A.5.1 waits
    # for an edit that does.
    assert controls == {audits.other_control.id}
    assert documents == set()


def test_templates_are_refused_to_callers_without_audit_management(audits):  # noqa: F811
    # The service checks authority on its own, so a caller that bypasses the
    # HTTP dependencies is still refused.
    from app.services.audits.scheduling import create_scheduled_audit
    from app.services.audits.schemas import ScheduledAuditPayload

    payload = ScheduledAuditPayload(
        title="Bypassed review", framework_slug="A", start_date=date(2027, 1, 15)
    )
    for user in (audits.attendee, audits.keen.user("audits.read")):
        with pytest.raises(DomainError) as refused:
            create_scheduled_audit(payload=payload, db=audits.db, user=user)
        assert (refused.value.status_code, refused.value.detail) == (
            403,
            "audits.manage permission required",
        )
    assert audits.db.query(Audit).filter_by(title="Bypassed review").count() == 0


# --- SWOT scope -------------------------------------------------------------------


def test_clearing_a_swot_scope_needs_isms_access_but_not_the_swot_feature(
    without_implied_permissions,
    aud,  # noqa: F811
):
    db = aud.db
    item = IsmsSwot(title="Known", kind="threat", significance=3, status="defined")
    db.add(item)
    db.commit()
    manager = aud.keen.client(aud.keen.user("audits.admin", "isms.read"))
    chosen = aud.call(manager, "PUT", "/scope", json={"swot_ids": [str(item.id)]})
    assert chosen.status_code == 200, chosen.text

    def scoped():
        db.expire_all()
        return [
            s.swot_id
            for s in db.query(AuditSwotScope).filter_by(
                audit_id=uuid.UUID(aud.audit_id)
            )
        ]

    features.update(db, aud.keen.user(role="admin"), 0, {"swot": False})
    added = aud.call(manager, "PUT", "/scope", json={"swot_ids": [str(item.id)]})
    assert (added.status_code, added.json()["detail"]) == (404, DISABLED)
    # Clearing still needs ISMS access ...
    outsider = aud.keen.client(aud.keen.user("audits.admin"))
    denied = aud.call(outsider, "PUT", "/scope", json={"swot_ids": []})
    assert (denied.status_code, denied.json()["detail"]) == (
        403,
        "ISMS access required",
    )
    assert scoped() == [item.id]

    # ... and empties the scope while SWOT is off.
    cleared = aud.call(manager, "PUT", "/scope", json={"swot_ids": []})

    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["scoped_swot"] == []
    assert scoped() == []


# --- Rich text --------------------------------------------------------------------


def test_report_notes_that_outgrow_their_limit_once_sanitised_are_refused(aud):  # noqa: F811
    before = aud.audit_row().notes
    # Twenty thousand characters, each bare ampersand stored as "&amp;".
    swollen = "<p>" + "&" * 19993 + "</p>"
    assert len(swollen) == 20000

    refused = aud.call(aud.client, "PATCH", json={"report_notes": swollen})

    assert (refused.status_code, refused.json()["detail"]) == (
        400,
        "report_notes is too long",
    )
    assert aud.audit_row().notes == before
    # Text that stays within the limit once sanitised is kept.
    fits = "<p>" + "&" * 3998 + "</p>"
    accepted = aud.call(aud.client, "PATCH", json={"report_notes": fits})
    assert accepted.status_code == 200, accepted.text
    assert aud.audit_row().notes == "<p>" + "&amp;" * 3998 + "</p>"
