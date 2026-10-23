"""Automatic diary entries for ISMS and risk framework links."""

import json
import uuid
from types import SimpleNamespace

import pytest
from app.core.config import settings
from app.db.models import (
    Artifact,
    Audit,
    AuditEvidence,
    ControlItem,
    Event,
    EventQuestionThread,
    FrameworkClause,
    InterestedParty,
    InterestedPartyName,
    InterestedPartyNature,
    IsmsDocument,
    IsmsEntityClauseLink,
    IsmsSwot,
    Mapping,
    Permission,
    Risk,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
    User,
)
from app.services.errors import DomainError
from app.services.isms.link_diary import MANUAL_RATIONALE, remove_entity_link_diary
from app.services.isms.mutations import _apply_links, _delete_entity_links
from app.services.isms.schemas import IsmsLinksPayload
from sqlalchemy import text

# Imported fixture: the assembled application with real sign-in, permission
# resolution and CSRF, used by the visibility tests below.
from tests.test_assembled_authority_regressions import keen, unsafe  # noqa: F401

FW = "A"


@pytest.fixture
def storage(monkeypatch):
    """Record artifact writes and cleanup requests in memory."""

    state = SimpleNamespace(writes={}, queued=[], fail=False)

    def put_bytes(*, key, data, content_type):
        if state.fail:
            raise OSError("synthetic storage failure")
        assert key not in state.writes, "artifact keys must not repeat"
        state.writes[key] = data
        return SimpleNamespace(
            uri="test://" + key, sha256="0" * 64, size_bytes=len(data)
        )

    monkeypatch.setattr("app.services.isms.link_diary.put_bytes", put_bytes)
    monkeypatch.setattr("app.ingest.common.put_bytes", put_bytes)
    monkeypatch.setattr(
        "app.services.isms.link_diary.queue_object",
        lambda db, uri: state.queued.append(uri),
    )
    return state


@pytest.fixture
def db(contract_db, storage):
    contract_db.add_all(
        [
            ControlItem(framework_slug=FW, ref=ref, type="annex_control", title=ref)
            for ref in ("A.5.1", "A.8.2")
        ]
        + [
            ControlItem(framework_slug=FW, ref="4.1", type="clause", title="Context"),
            FrameworkClause(
                framework_slug=FW, ref="4.1", title="Context", sort_order=1
            ),
        ]
    )
    contract_db.commit()
    return contract_db


def _user(db, name, *codes, role="normal"):
    row = User(username=name, password_hash="unused", role=role, is_active=True)
    db.add(row)
    for code in codes:
        permission = db.query(Permission).filter_by(code=code).one_or_none()
        if permission is None:
            permission = Permission(code=code, description=code)
            db.add(permission)
        row.permissions.append(permission)
    db.flush()
    return row


def _document(db, title="Records policy"):
    row = IsmsDocument(title=title)
    db.add(row)
    db.flush()
    return row


def _asset(db):
    category = RiskCategory(name="Category " + uuid.uuid4().hex[:8])
    db.add(category)
    db.flush()
    subcategory = RiskAssetSubcategory(name="Endpoints", category_id=category.id)
    db.add(subcategory)
    db.flush()
    asset = RiskAsset(
        name="Asset " + uuid.uuid4().hex[:8],
        category_id=category.id,
        subcategory_id=subcategory.id,
    )
    db.add(asset)
    db.flush()
    return asset


def _risk(db, summary="Laptop theft"):
    asset = _asset(db)
    risk = Risk(asset_id=asset.id, threat_summary=summary)
    db.add(risk)
    db.flush()
    return risk


def _party(db, name="Customers"):
    names = InterestedPartyName(name=name + " " + uuid.uuid4().hex[:6])
    nature = InterestedPartyNature(name="Contract " + uuid.uuid4().hex[:6])
    db.add_all([names, nature])
    db.flush()
    row = InterestedParty(name_id=names.id, nature_id=nature.id)
    db.add(row)
    db.flush()
    return row


def _link_party(db, party, user, *controls):
    from app.services.interested_parties import (
        _controls_for_payload,
        _replace_control_links,
    )

    _replace_control_links(
        db, party, FW, _controls_for_payload(db, list(controls), FW), user
    )


def _link(db, record, user, *controls, entity_type="document"):
    _apply_links(
        db, entity_type, record.id, FW, IsmsLinksPayload(controls=list(controls)), user
    )


def _entry(db, entity_type, entity_id):
    return (
        db.query(Event)
        .filter(
            Event.source == "diary",
            Event.external_id == f"isms-link:{entity_type}:{entity_id}",
        )
        .one_or_none()
    )


def _automatic(db, event_id):
    rows = (
        db.query(ControlItem.ref, Mapping.mapped_by)
        .join(Mapping, Mapping.control_item_id == ControlItem.id)
        .filter(
            Mapping.event_id == event_id,
            Mapping.method == "manual",
            Mapping.rationale == MANUAL_RATIONALE,
        )
        .all()
    )
    return dict(rows)


def _revisions(db, event_id, captured_by="keen:isms"):
    return (
        db.query(Artifact)
        .filter_by(event_id=event_id, kind="diary_entry_edit", captured_by=captured_by)
        .all()
    )


def _admin_revision(db, event_id):
    db.add(
        Artifact(
            event_id=event_id,
            kind="diary_entry_edit",
            storage_uri="test://admin-edit",
            sha256="0" * 64,
            content_type="application/json",
            size_bytes=2,
            captured_by="keen:diary",
        )
    )
    db.flush()


def test_first_link_records_an_entry_without_record_content(db, storage):
    editor = _user(db, "editor")
    document = _document(db, "Payroll <b>draft</b> policy")
    _link(db, document, editor, "A.5.1")

    entry = _entry(db, "document", document.id)
    assert (entry.system, entry.action) == ("isms", "isms_link")
    assert entry.summary == "diary: Document linked to framework controls"
    assert entry.normalized_payload == {
        "summary": "Document linked to framework controls",
        "details": {"linked": {FW: "A.5.1"}},
        "links": [{"url": f"/isms-document.html?id={document.id}", "text": "Document"}],
        "author": "editor",
    }
    # The record title reaches neither the event nor its stored artifact.
    assert "Payroll" not in json.dumps(entry.normalized_payload)
    assert all(b"Payroll" not in data for data in storage.writes.values())
    assert _automatic(db, entry.id) == {"A.5.1": "editor"}
    assert _revisions(db, entry.id) == []


def test_clause_links_map_to_the_clause_catalogue_item(db):
    editor = _user(db, "editor")
    document = _document(db)
    clause = db.query(FrameworkClause).filter_by(framework_slug=FW, ref="4.1").one()
    db.add(
        IsmsEntityClauseLink(
            entity_type="document",
            entity_id=document.id,
            framework_slug=FW,
            clause_id=clause.id,
        )
    )
    db.flush()
    # A save without link fields leaves the entry alone.
    _apply_links(db, "document", document.id, FW, IsmsLinksPayload(), editor)
    assert _entry(db, "document", document.id) is None

    _link(db, document, editor, "A.5.1")
    entry = _entry(db, "document", document.id)
    assert set(_automatic(db, entry.id)) == {"A.5.1", "4.1"}
    assert entry.normalized_payload["details"]["linked"] == {FW: "4.1, A.5.1"}


def test_link_changes_revise_the_entry_and_credit_the_editor(db, storage):
    alice, bob = _user(db, "alice"), _user(db, "bob")
    document = _document(db)
    _link(db, document, alice, "A.5.1")
    entry = _entry(db, "document", document.id)

    _link(db, document, bob, "A.5.1", "A.8.2")
    _link(db, document, bob, "A.8.2")
    assert _entry(db, "document", document.id).id == entry.id
    assert entry.normalized_payload["details"]["linked"] == {FW: "A.8.2"}
    assert entry.normalized_payload["author"] == "alice"
    assert _automatic(db, entry.id) == {"A.8.2": "bob"}
    revisions = _revisions(db, entry.id)
    assert [r.meta["edited_by"] for r in revisions] == ["bob", "bob"]
    # Revisions made within the same second still store distinct artifacts.
    assert len({r.storage_uri for r in revisions}) == 2


def test_unchanged_mappings_keep_their_original_author(db):
    alice, bob = _user(db, "alice"), _user(db, "bob")
    document = _document(db)
    _link(db, document, alice, "A.5.1")
    _link(db, document, bob, "A.5.1", "A.8.2")
    entry = _entry(db, "document", document.id)
    assert _automatic(db, entry.id) == {"A.5.1": "alice", "A.8.2": "bob"}


def test_unchanged_links_write_nothing(db, storage):
    editor = _user(db, "editor")
    document = _document(db)
    _link(db, document, editor, "A.5.1")
    writes = dict(storage.writes)
    entry = _entry(db, "document", document.id)
    ids = sorted(str(i) for (i,) in db.query(Mapping.id).filter_by(event_id=entry.id))

    _link(db, document, editor, "A.5.1")
    assert storage.writes == writes
    assert _revisions(db, entry.id) == []
    assert ids == sorted(
        str(i) for (i,) in db.query(Mapping.id).filter_by(event_id=entry.id)
    )


def test_removing_every_link_withdraws_the_mappings(db):
    editor = _user(db, "editor")
    document = _document(db)
    _link(db, document, editor, "A.5.1")
    _link(db, document, editor)
    entry = _entry(db, "document", document.id)
    assert entry is not None
    assert _automatic(db, entry.id) == {}
    assert entry.normalized_payload["details"]["linked"] == {}


def test_risk_entries_carry_no_threat_summary(db):
    from app.services import risks

    editor = _user(db, "editor")
    risk = _risk(db, "Unencrypted backups")
    risks._replace_control_links(
        db, risk, framework=FW, control_values=["A.8.2"], user=editor
    )
    entry = _entry(db, "risk", risk.id)
    assert entry.summary == "diary: Risk linked to framework controls"
    assert "backups" not in json.dumps(entry.normalized_payload)
    assert entry.normalized_payload["links"] == [
        {"url": f"/risk.html?id={risk.id}", "text": "Risk"}
    ]
    assert _automatic(db, entry.id) == {"A.8.2": "editor"}


def test_interested_party_links_count_like_other_records(db, storage):
    from app.services.context import ChangeContext
    from app.services.interested_parties import delete_interested_party

    editor, reviewer = _user(db, "editor"), _user(db, "reviewer")
    party = _party(db)
    _link_party(db, party, editor, "A.5.1", "A.8.2")
    entry = _entry(db, "interested_party", party.id)
    assert (entry.system, entry.action, entry.actor) == ("isms", "isms_link", "editor")
    assert entry.normalized_payload["links"] == [
        {"url": f"/interested_party.html?id={party.id}", "text": "Interested party"}
    ]
    assert entry.normalized_payload["details"] == {"linked": {FW: "A.5.1, A.8.2"}}
    # The entry names no party, so the party's name stays behind its own rule.
    assert "Customers" not in json.dumps(entry.normalized_payload)
    assert _automatic(db, entry.id) == {"A.5.1": "editor", "A.8.2": "editor"}

    _link_party(db, party, reviewer, "A.8.2")
    assert _automatic(db, entry.id) == {"A.8.2": "editor"}
    _link_party(db, party, reviewer)
    assert _automatic(db, entry.id) == {}

    _link_party(db, party, reviewer, "A.5.1")
    delete_interested_party(
        party_id=str(party.id),
        context=ChangeContext(method="DELETE", path="/v1/interested-parties/x"),
        user=reviewer,
        db=db,
    )
    assert _entry(db, "interested_party", party.id) is None
    assert db.query(Mapping).filter_by(event_id=entry.id).count() == 0


def test_deleting_an_asset_from_the_risk_register_withdraws_its_links(db, storage):
    from app.db.models import IsmsEntityControlLink
    from app.services.risks import delete_risk_asset

    editor = _user(db, "editor")
    asset = _asset(db)
    _link(db, asset, editor, "A.5.1", entity_type="asset")
    entry = _entry(db, "asset", asset.id)
    assert _automatic(db, entry.id) == {"A.5.1": "editor"}

    delete_risk_asset(asset_id=str(asset.id), user=editor, db=db)
    assert _entry(db, "asset", asset.id) is None
    assert db.query(Mapping).filter_by(event_id=entry.id).count() == 0
    assert (
        db.query(IsmsEntityControlLink)
        .filter_by(entity_type="asset", entity_id=asset.id)
        .count()
        == 0
    )


def test_deleting_a_record_withdraws_its_entry(db, storage):
    editor = _user(db, "editor")
    document = _document(db)
    _link(db, document, editor, "A.5.1", "A.8.2")
    # Automatic revisions never count as administrator edits.
    _link(db, document, editor, "A.5.1")
    entry_id = _entry(db, "document", document.id).id

    _delete_entity_links(db, "document", document.id, FW)
    assert _entry(db, "document", document.id) is None
    assert db.query(Mapping.id).filter_by(event_id=entry_id).count() == 0
    assert db.query(Artifact.id).filter_by(event_id=entry_id).count() == 0
    assert sorted(storage.queued) == sorted("test://" + key for key in storage.writes)


@pytest.mark.parametrize("reason", ["admin edit", "question thread"])
def test_deletion_keeps_entries_others_rely_on(db, reason):
    editor = _user(db, "editor")
    document = _document(db)
    _link(db, document, editor, "A.5.1")
    entry = _entry(db, "document", document.id)
    if reason == "admin edit":
        _admin_revision(db, entry.id)
    else:
        db.add(
            EventQuestionThread(
                event_id=entry.id, target_type="event", created_by_user_id=editor.id
            )
        )
        db.flush()

    remove_entity_link_diary(db, "document", document.id)
    assert _entry(db, "document", document.id) is not None
    assert _automatic(db, entry.id) == {}


def test_deletion_keeps_entries_referenced_by_another_record(db):
    editor = _user(db, "editor")
    document = _document(db)
    _link(db, document, editor, "A.5.1")
    entry = _entry(db, "document", document.id)
    source = db.query(Artifact).filter_by(event_id=entry.id).one()
    other = Event(
        timestamp=entry.timestamp,
        source="lineage-test",
        summary="derived record",
        external_id=uuid.uuid4().hex,
    )
    db.add(other)
    db.flush()
    db.add(
        Artifact(
            event_id=other.id,
            parent_artifact_id=source.id,
            kind="derived",
            storage_uri="test://derived",
            sha256="0" * 64,
            content_type="application/json",
            size_bytes=2,
        )
    )
    db.flush()

    remove_entity_link_diary(db, "document", document.id)
    assert _entry(db, "document", document.id) is not None


def test_deletion_keeps_audit_held_entries(db):
    if db.get_bind().dialect.name != "postgresql":
        pytest.skip("audit holds are maintained by PostgreSQL triggers")
    editor = _user(db, "editor")
    risk = _risk(db)
    from app.services import risks

    risks._replace_control_links(
        db, risk, framework=FW, control_values=["A.5.1"], user=editor
    )
    entry = _entry(db, "risk", risk.id)
    audit = Audit(title="Sampling audit", framework_slug=FW)
    db.add(audit)
    db.flush()
    db.add(AuditEvidence(audit_id=audit.id, event_id=entry.id))
    db.flush()

    risks.delete_risk(risk_id=str(risk.id), user=editor, db=db)
    assert db.get(Risk, risk.id) is None
    kept = _entry(db, "risk", risk.id)
    assert kept is not None
    assert _automatic(db, kept.id) == {}


def test_deleting_a_swot_item_withdraws_its_entry(db):
    from app.services.context import ChangeContext
    from app.services.isms import swot

    admin = _user(db, "swot-admin", role="admin")
    item = IsmsSwot(title="Strength", kind="strength", status="defined")
    db.add(item)
    db.flush()
    _link(db, item, admin, "A.5.1", entity_type="swot")
    assert _entry(db, "swot", item.id) is not None

    swot.delete_item(
        item_id=str(item.id),
        expected_version=item.version,
        user=admin,
        db=db,
        context=ChangeContext(actor=admin),
    )
    db.expire_all()
    assert _entry(db, "swot", item.id) is None


def _start_manual_purge(db):
    db.execute(
        text(
            "INSERT INTO evidence_purge_jobs (id, mode, automatic, cutoff, status) "
            "VALUES (:id, 'all', false, now(), 'queued')"
        ),
        {"id": uuid.uuid4()},
    )


def test_link_changes_pause_during_a_manual_purge(db):
    if db.get_bind().dialect.name != "postgresql":
        pytest.skip("the evidence purge pause applies on PostgreSQL")
    editor = _user(db, "editor")
    linked, unlinked = _document(db, "Linked"), _document(db, "Unlinked")
    _link(db, linked, editor, "A.5.1")
    entry = _entry(db, "document", linked.id)
    _start_manual_purge(db)

    # An unchanged save writes nothing, so it still succeeds.
    _link(db, linked, editor, "A.5.1")
    for record, controls in ((linked, ["A.8.2"]), (unlinked, ["A.5.1"])):
        with pytest.raises(DomainError) as paused:
            _link(db, record, editor, *controls)
        assert paused.value.status_code == 503
        assert "evidence purge" in paused.value.detail
        assert paused.value.headers == {"Retry-After": "60"}
    # Deleting a record withdraws its entry without waiting for the purge.
    remove_entity_link_diary(db, "document", linked.id)
    assert db.get(Event, entry.id) is None


def test_masked_entries_stay_masked_and_compare_like_for_like(db, storage, monkeypatch):
    monkeypatch.setattr(settings, "event_data_masking", "true")
    editor = _user(db, "editor@example.org")
    document = _document(db)
    _link(db, document, editor, "A.5.1")
    entry = _entry(db, "document", document.id)
    assert "editor@example.org" not in json.dumps(entry.normalized_payload)
    assert entry.actor != "editor@example.org"

    _link(db, document, editor, "A.5.1")
    assert _revisions(db, entry.id) == []

    _link(db, document, editor, "A.8.2")
    (revision,) = _revisions(db, entry.id)
    stored = storage.writes[revision.storage_uri.removeprefix("test://")]
    assert b"editor@example.org" not in stored
    assert "editor@example.org" not in json.dumps(entry.normalized_payload)


def test_administrator_edits_keep_their_text(db):
    editor = _user(db, "editor")
    document = _document(db)
    _link(db, document, editor, "A.5.1")
    entry = _entry(db, "document", document.id)
    _admin_revision(db, entry.id)
    entry.normalized_payload = {
        **entry.normalized_payload,
        "summary": "Administrator explanation",
    }
    db.flush()

    _link(db, document, editor, "A.5.1", "A.8.2")
    db.expire(entry)
    assert entry.normalized_payload["summary"] == "Administrator explanation"
    assert _revisions(db, entry.id) == []
    # The mappings still follow the record's links.
    assert set(_automatic(db, entry.id)) == {"A.5.1", "A.8.2"}


def test_a_revision_that_cannot_be_stored_fails_the_save(db, storage):
    editor = _user(db, "editor")
    document = _document(db)
    _link(db, document, editor, "A.5.1")
    storage.fail = True
    with pytest.raises(OSError):
        _link(db, document, editor, "A.8.2")


PERMISSION_SETS = [
    (),
    ("events.read",),
    ("isms.read",),
    ("isms.manage",),
    ("risk.read",),
    ("risk.manage",),
    ("isms.read", "risk.read"),
    ("interested_parties.read",),
    ("interested_parties.manage",),
    ("isms.read", "interested_parties.read"),
]


@pytest.mark.parametrize("codes", PERMISSION_SETS)
def test_link_entries_follow_the_source_record_read_rules(db, codes):
    from app.security.diary_visibility import (
        diary_filter_condition,
        is_diary_event_visible,
    )
    from app.services.interested_parties import _can_read_interested_parties
    from app.services.isms.access import _can_read_isms
    from app.services.risks import _can_read_risks

    editor = _user(db, "editor")
    document, risk, party = _document(db), _risk(db), _party(db)
    _link(db, document, editor, "A.5.1")
    from app.services import risks

    risks._replace_control_links(
        db, risk, framework=FW, control_values=["A.5.1"], user=editor
    )
    _link_party(db, party, editor, "A.5.1")
    manual = Event(
        timestamp=_entry(db, "risk", risk.id).timestamp,
        source="diary",
        system="manual",
        summary="diary: management review",
        external_id=uuid.uuid4().hex,
    )
    db.add(manual)
    reader = _user(db, "reader-" + uuid.uuid4().hex[:6], *codes)
    db.flush()

    entries = {
        "document": _entry(db, "document", document.id).id,
        "risk": _entry(db, "risk", risk.id).id,
        "interested_party": _entry(db, "interested_party", party.id).id,
        "manual": manual.id,
    }
    expected = {
        "document": _can_read_isms(db, reader),
        "risk": _can_read_risks(db, reader),
        "interested_party": _can_read_interested_parties(db, reader),
        "manual": True,
    }
    listed = {
        event_id
        for (event_id,) in db.query(Event.id).filter(
            Event.id.in_(entries.values()), diary_filter_condition(db, reader)
        )
    }
    for name, event_id in entries.items():
        assert (event_id in listed) is expected[name], name
        assert is_diary_event_visible(db, reader, event_id) is expected[name], name


def test_inactive_and_missing_callers_see_no_diary_entries(db):
    from app.security.diary_visibility import is_diary_event_visible

    editor = _user(db, "editor")
    document = _document(db)
    _link(db, document, editor, "A.5.1")
    entry_id = _entry(db, "document", document.id).id
    inactive = _user(db, "inactive", "isms.read", "risk.read")
    inactive.is_active = False
    db.flush()
    assert is_diary_event_visible(db, inactive, entry_id) is False
    assert is_diary_event_visible(db, None, entry_id) is False
    assert is_diary_event_visible(db, editor, uuid.uuid4()) is False


def test_assembled_reads_follow_the_source_record_rules(keen):  # noqa: F811
    db = keen.db
    control = ControlItem(framework_slug=FW, ref="A.5.9", type="annex_control")
    db.add(control)
    db.commit()
    risk = _risk(db)
    db.commit()

    isms_manager = keen.client(keen.user("isms.manage"))
    created = isms_manager.post(
        "/v1/isms/documents",
        params={"framework": FW},
        json={"title": "Records policy", "controls": ["A.5.9"]},
        headers=unsafe(isms_manager),
    )
    assert created.status_code == 200, created.text
    risk_manager = keen.client(keen.user("risk.manage"))
    linked = risk_manager.patch(
        f"/v1/risks/{risk.id}/controls",
        params={"framework": FW},
        json={"controls": ["A.5.9"]},
        headers=unsafe(risk_manager),
    )
    assert linked.status_code == 200, linked.text
    party_manager = keen.client(keen.user("interested_parties.manage"))
    party = party_manager.post(
        "/v1/interested-parties",
        json={
            "framework": FW,
            "name": "Regulator",
            "nature": "Statutory",
            "controls": ["A.5.9"],
        },
        headers=unsafe(party_manager),
    )
    assert party.status_code == 200, party.text
    document_entry = _entry(db, "document", uuid.UUID(created.json()["id"]))
    risk_entry = _entry(db, "risk", risk.id)
    party_entry = _entry(db, "interested_party", uuid.UUID(party.json()["id"]))

    cases = {
        ("events.read",): set(),
        ("events.read", "isms.read"): {document_entry.id},
        ("events.read", "interested_parties.read"): {party_entry.id},
        ("events.read", "risk.read"): {
            document_entry.id,
            risk_entry.id,
            party_entry.id,
        },
    }
    for codes, visible in cases.items():
        client = keen.client(keen.user(*codes))
        listing = client.get("/v1/events", params={"source": "diary", "framework": FW})
        assert listing.status_code == 200, listing.text
        assert {uuid.UUID(item["id"]) for item in listing.json()["items"]} == visible
        evidence = client.get(f"/v1/controls/{control.id}/evidence")
        assert evidence.status_code == 200, evidence.text
        for entry in (document_entry, risk_entry, party_entry):
            detail = client.get(f"/v1/events/{entry.id}", params={"framework": FW})
            assert detail.status_code == (200 if entry.id in visible else 404)
            assert (str(entry.id) in evidence.text) is (entry.id in visible)


CSV_HEADER = "asset,category,subcategory,threat_summary,risk_types,controls\n"


def test_risk_import_records_entries_for_linked_risks(db, storage):
    from app.services.risk_register import import_register

    importer = _user(db, "importer", role="admin")
    csv_text = (
        CSV_HEADER
        + "Router,Network,Edge,Routing loss,Availability,A.5.1\n"
        + "Switch,Network,Edge,Port loss,Availability,\n"
    )
    import_register(raw=csv_text.encode(), framework=FW, user=importer, db=db)
    imported = {
        risk.threat_summary: risk
        for risk in db.query(Risk).filter(
            Risk.threat_summary.in_(["Routing loss", "Port loss"])
        )
    }
    entry = _entry(db, "risk", imported["Routing loss"].id)
    assert _automatic(db, entry.id) == {"A.5.1": "importer"}
    assert _entry(db, "risk", imported["Port loss"].id) is None


def test_rejected_risk_import_stores_no_artifacts(db, storage):
    from app.services.risk_register import import_register

    importer = _user(db, "importer", role="admin")
    csv_text = (
        CSV_HEADER
        + "Router,Network,Edge,Routing loss,Availability,A.5.1\n"
        + "Switch,Network,Edge,Port loss,Availability,A.9.9\n"
    )
    with pytest.raises(DomainError) as rejected:
        import_register(raw=csv_text.encode(), framework=FW, user=importer, db=db)
    assert rejected.value.status_code == 400
    assert storage.writes == {}


def test_diary_entries_are_immune_to_retention(db):
    from datetime import timedelta

    from app.services import evidence_retention as retention

    editor = _user(db, "editor")
    document = _document(db)
    _link(db, document, editor, "A.5.1")
    entry = _entry(db, "document", document.id)
    ordinary = Event(
        timestamp=retention.now() - timedelta(days=3650),
        source="ordinary-source",
        summary="old record",
        external_id=uuid.uuid4().hex,
    )
    db.add(ordinary)
    db.commit()

    where, params = retention.candidates("all", None, retention.now())
    eligible = {
        uuid.UUID(str(value))
        for value in db.execute(
            text(f"SELECT e.id FROM events e WHERE {where}"), params
        ).scalars()
    }
    assert ordinary.id in eligible
    assert entry.id not in eligible
