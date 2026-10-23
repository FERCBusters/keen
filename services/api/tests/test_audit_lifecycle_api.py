"""Audit editing, locking, sampling and deletion through the assembled application.

Audit managers edit the audits they created; the separate audits.admin
permission covers everyone else's audits and deletion. Completed and archived
audits are locked until reopened. Requests pass through the production
middleware, password sign-in, CSRF and permission resolution.
"""

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from app.db.models import (
    Audit,
    AuditEvidence,
    ControlItem,
    Event,
    Framework,
    IsmsPerson,
    Risk,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
    User,
)
from sqlalchemy import text

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)

FW = "fw-audit"


@pytest.fixture
def aud(keen, monkeypatch):  # noqa: F811 - fixture imported above
    db = keen.db
    db.add(Framework(slug=FW))
    controls = {
        ref: ControlItem(
            framework_slug=FW, type="annex_control", ref=ref, title=f"Control {ref}"
        )
        for ref in ("A.1", "A.2")
    }
    db.add_all(controls.values())
    category = RiskCategory(name="Data")
    db.add(category)
    db.flush()
    subcategory = RiskAssetSubcategory(name="Databases", category_id=category.id)
    db.add(subcategory)
    db.flush()
    asset = RiskAsset(
        name="Payroll", category_id=category.id, subcategory_id=subcategory.id
    )
    db.add(asset)
    db.flush()
    risk = Risk(asset_id=asset.id, threat_summary="Unauthorised export")
    event = Event(
        source="ci",
        summary="Quarterly access review exported",
        timestamp=datetime(2026, 3, 1, 9, 0),
        external_id=uuid.uuid4().hex,
        raw_pointer={},
        normalized_payload={},
    )
    db.add_all([risk, event])
    db.commit()
    owner = keen.user("audits.manage", "events.read", "risk.read")
    owner_client = keen.client(owner)
    created = owner_client.post(
        "/v1/audits",
        json={"title": "Access review", "framework_slug": FW},
        headers=unsafe(owner_client),
    )
    assert created.status_code == 200, created.text
    audit_id = created.json()["id"]

    def call(client, method, path="", **kwargs):
        return client.request(
            method, f"/v1/audits/{audit_id}{path}", headers=unsafe(client), **kwargs
        )

    def detail():
        response = owner_client.get(f"/v1/audits/{audit_id}")
        assert response.status_code == 200, response.text
        return response.json()

    def audit_row():
        db.expire_all()
        return db.get(Audit, uuid.UUID(audit_id))

    return SimpleNamespace(
        keen=keen,
        db=db,
        controls=controls,
        risk=risk,
        event=event,
        owner=owner,
        client=owner_client,
        audit_id=audit_id,
        call=call,
        detail=detail,
        audit_row=audit_row,
        admin=keen.client(keen.user("audits.admin", "events.read")),
        manager=keen.client(keen.user("audits.manage", "events.read")),
        reader=keen.client(keen.user("audits.read")),
    )


# --- Samples --------------------------------------------------------------------


def test_sampling_the_same_event_again_updates_its_one_sample(aud):
    first = aud.call(
        aud.client,
        "POST",
        "/evidence",
        json={
            "event_id": str(aud.event.id),
            "title": "First look",
            "notes": "<p>Reviewed<script>alert(1)</script></p>",
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["title"] == "First look"
    assert "<script>" not in first.json()["notes"]

    again = aud.call(
        aud.client,
        "POST",
        "/evidence",
        json={
            "event_id": str(aud.event.id),
            "title": "Second look",
            "notes": "<p>Confirmed</p>",
            "evidence_url": "https://evidence.example/report",
        },
    )

    assert again.json()["id"] == first.json()["id"]
    (sample,) = aud.detail()["evidence"]
    assert (sample["title"], sample["notes"], sample["evidence_url"]) == (
        "Second look",
        "<p>Confirmed</p>",
        "https://evidence.example/report",
    )


def test_sampling_the_same_record_again_updates_its_one_sample(aud):
    target = {"entity_type": "risk", "entity_id": str(aud.risk.id)}
    first = aud.call(aud.client, "POST", "/evidence", json=target)
    assert first.status_code == 200, first.text
    # A record sample is titled and linked from the record itself.
    assert first.json()["title"] == "CIA Triad risk: Payroll"
    assert first.json()["evidence_url"].startswith(f"/risk.html?id={aud.risk.id}")

    again = aud.call(
        aud.client, "POST", "/evidence", json={**target, "title": "Export risk"}
    )

    assert again.json()["id"] == first.json()["id"]
    (sample,) = aud.detail()["evidence"]
    assert sample["title"] == "Export risk"


def test_a_sample_needs_an_event_a_record_or_a_link(aud):
    empty = aud.call(aud.client, "POST", "/evidence", json={"title": "Nothing"})
    assert empty.status_code == 400
    assert empty.json()["detail"] == (
        "event_id, entity_type/entity_id, or evidence_url is required"
    )

    link = aud.call(
        aud.client,
        "POST",
        "/evidence",
        json={"evidence_url": "https://evidence.example/a"},
    )
    assert (link.json()["title"], link.json()["event_id"]) == (
        "https://evidence.example/a",
        None,
    )
    # A link to an event page samples that event.
    page = aud.call(
        aud.client,
        "POST",
        "/evidence",
        json={"evidence_url": f"/event.html?id={aud.event.id}"},
    )
    assert page.json()["event_id"] == str(aud.event.id)
    assert page.json()["title"] == "Quarterly access review exported"
    assert len(aud.detail()["evidence"]) == 2


def test_samples_of_hidden_or_missing_events_are_refused(aud):
    hidden = Event(
        source="diary",
        system="isms",
        summary="Risk linked to controls",
        timestamp=datetime(2026, 3, 2),
        external_id=f"isms-link:risk:{uuid.uuid4()}",
        raw_pointer={},
        normalized_payload={},
    )
    aud.db.add(hidden)
    aud.db.commit()
    manager = aud.keen.user("audits.manage", "events.read")
    client = aud.keen.client(manager)
    created = client.post(
        "/v1/audits",
        json={"title": "Diary review", "framework_slug": FW},
        headers=unsafe(client),
    ).json()

    for event_id in (hidden.id, uuid.uuid4()):
        refused = client.post(
            f"/v1/audits/{created['id']}/evidence",
            json={"event_id": str(event_id)},
            headers=unsafe(client),
        )
        assert refused.status_code == 404, refused.text
    assert aud.db.query(AuditEvidence).count() == 0


# --- Who may change an audit ----------------------------------------------------


def test_only_the_creator_or_an_audit_administrator_changes_an_audit(aud):
    for client in (aud.manager, aud.reader):
        title = aud.call(client, "PATCH", json={"title": "Taken over"})
        sample = aud.call(
            client, "POST", "/evidence", json={"evidence_url": "https://x.example"}
        )
        assert title.status_code == 403 and sample.status_code == 403
    assert aud.call(aud.manager, "PATCH", json={"title": "x"}).json()["detail"] == (
        "Only the audit creator or an audit administrator can edit this audit"
    )
    assert aud.detail()["title"] == "Access review" and aud.detail()["evidence"] == []

    renamed = aud.call(aud.admin, "PATCH", json={"title": "Access review 2026"})
    assert renamed.status_code == 200, renamed.text
    assert aud.detail()["title"] == "Access review 2026"


def test_completed_audits_are_locked_until_reopened(aud):
    assert (
        aud.call(aud.client, "PATCH", json={"status": "completed"}).status_code == 200
    )

    locked = aud.call(aud.client, "PATCH", json={"title": "Edited later"})
    assert locked.status_code == 409
    assert locked.json()["detail"] == (
        "This audit is completed and cannot be modified. Change its status back to "
        "Open or In progress before editing it."
    )
    sampled = aud.call(
        aud.client, "POST", "/evidence", json={"evidence_url": "https://x.example"}
    )
    assert sampled.json()["detail"].startswith(
        "This audit is completed and cannot be sampled into."
    )
    finding = aud.call(
        aud.client, "POST", "/findings", json={"kind": "ofi", "title": "Late"}
    )
    assert finding.status_code == 409
    archived = aud.call(aud.client, "PATCH", json={"status": "archived"})
    assert archived.status_code == 409
    assert archived.json()["detail"] == (
        "Completed or archived audits can only be reopened to Open or In progress."
    )
    assert aud.detail()["status"] == "completed"

    reopened = aud.call(aud.client, "PATCH", json={"status": "in_progress"})
    assert reopened.json()["status"] == "in_progress"
    assert (
        aud.call(aud.client, "PATCH", json={"title": "Edited later"}).status_code == 200
    )


@pytest.mark.parametrize(
    "fields,detail",
    [
        ({"title": "  "}, "title is required"),
        ({"framework_slug": "bad slug!"}, "Invalid framework_slug"),
        ({"status": "pending"}, "Invalid status"),
        ({"audit_type": "self-assessed"}, "Invalid audit_type"),
        (
            {"start_date": "2026-05-02", "end_date": "2026-05-01"},
            "end_date cannot be before start_date",
        ),
    ],
    ids=["blank-title", "bad-framework", "bad-status", "bad-type", "reversed-dates"],
)
def test_invalid_audit_changes_change_nothing(aud, fields, detail):
    refused = aud.call(aud.client, "PATCH", json=fields)

    assert refused.status_code == 400
    assert refused.json()["detail"] == detail
    current = aud.detail()
    assert (current["title"], current["status"], current["audit_type"]) == (
        "Access review",
        "open",
        "internal",
    )


def test_audit_text_fields_are_sanitised_and_report_notes_are_the_notes(aud):
    changed = aud.call(
        aud.client,
        "PATCH",
        json={
            "audit_type": "EXTERNAL",
            "report_notes": "<p>Scope<img src=x onerror=alert(1)></p>",
            "executive_summary": "<strong>Good</strong><script>steal()</script>",
        },
    )

    assert changed.status_code == 200, changed.text
    row = aud.audit_row()
    assert row.audit_type == "external"
    assert "onerror" not in row.notes and "Scope" in row.notes
    assert (
        "<script>" not in row.executive_summary
        and "<strong>Good</strong>" in row.executive_summary
    )


# --- Deletion -------------------------------------------------------------------


def test_deleting_an_audit_needs_audits_admin_and_queues_its_report(aud):
    uploaded = aud.call(
        aud.client,
        "POST",
        "/report",
        files={"file": ("report.txt", b"final report", "text/plain")},
    )
    assert uploaded.status_code == 200, uploaded.text
    report = aud.audit_row().final_report_storage_uri
    template = Audit(
        title="Monthly review",
        framework_slug=FW,
        status="template",
        schedule_last_created_audit_id=uuid.UUID(aud.audit_id),
        created_by_user_id=aud.owner.id,
    )
    aud.db.add(template)
    aud.db.commit()

    refused = aud.call(aud.client, "DELETE")
    assert refused.status_code == 403
    assert refused.json()["detail"] == "audits.admin permission required"
    as_template = aud.admin.delete(
        f"/v1/audits/{template.id}", headers=unsafe(aud.admin)
    )
    assert as_template.status_code == 400

    assert aud.call(aud.admin, "DELETE").json() == {"ok": True}

    assert aud.audit_row() is None
    aud.db.refresh(template)
    assert template.schedule_last_created_audit_id is None
    queued = aud.db.execute(text("SELECT storage_uri FROM evidence_object_cleanup"))
    assert list(queued.scalars()) == [report]


# --- Attendees and findings -----------------------------------------------------


def test_attendees_name_one_person_or_one_active_user(aud):
    person = IsmsPerson(name="Pat Auditor", email="pat@example.org")
    inactive = User(username="gone", password_hash="unused", is_active=False)
    aud.db.add_all([person, inactive])
    aud.db.commit()
    aud.db.get(User, aud.owner.id).email = "owner@example.org"
    aud.db.commit()

    def add(**body):
        return aud.call(aud.client, "POST", "/attendees", json=body)

    for body, detail in (
        (
            {"person_id": str(person.id), "user_id": str(aud.owner.id)},
            "Select either a person or a Keen user",
        ),
        ({"person_id": str(uuid.uuid4())}, "Unknown Person"),
        ({"user_id": str(inactive.id)}, "Unknown or inactive attendee user"),
        ({"role": "Observer"}, "name is required"),
        ({"name": "Visitor", "email": "not-an-address"}, "Invalid email"),
    ):
        refused = add(**body)
        assert refused.status_code == 400, body
        assert refused.json()["detail"] == detail
    assert aud.detail()["attendees"] == []

    by_user = add(user_id=str(aud.owner.id), role="Lead").json()
    by_person = add(person_id=str(person.id)).json()
    by_name = add(name="Visitor", email="visitor@example.org").json()

    assert (by_user["name"], by_user["email"], by_user["role"]) == (
        aud.owner.username,
        "owner@example.org",
        "Lead",
    )
    assert (by_person["name"], by_person["email"]) == ("Pat Auditor", "pat@example.org")
    assert (by_name["name"], by_name["email"], by_name["user_id"]) == (
        "Visitor",
        "visitor@example.org",
        None,
    )


def test_people_in_the_register_need_isms_read_access_to_attend(aud):
    person = IsmsPerson(name="Pat Auditor", email="pat@example.org")
    aud.db.add(person)
    aud.db.commit()
    # This manager cannot read the people register, so cannot name its people.
    blind_user = aud.keen.user("audits.manage")
    blind = aud.keen.client(blind_user)
    own = blind.post(
        "/v1/audits",
        json={"title": "Blind audit", "framework_slug": FW},
        headers=unsafe(blind),
    ).json()
    refused = blind.post(
        f"/v1/audits/{own['id']}/attendees",
        json={"person_id": str(person.id)},
        headers=unsafe(blind),
    )
    assert refused.status_code == 400
    assert refused.json()["detail"] == "Unknown Person"
    assert "Pat" not in refused.text
    named = blind.post(
        f"/v1/audits/{own['id']}/attendees",
        json={"name": "Pat Auditor"},
        headers=unsafe(blind),
    )
    assert named.status_code == 200, named.text


def test_a_new_audit_arrives_with_its_scope_and_attendees(aud):
    person = IsmsPerson(name="Pat Auditor", email="pat@example.org")
    aud.db.add(person)
    aud.db.commit()
    payload = {
        "title": "Surveillance audit",
        "framework_slug": FW,
        "audit_type": "external",
        "start_date": "2026-06-01",
        "end_date": "2026-06-03",
        "controls": ["A.1"],
        "attendees": [
            {"user_id": str(aud.owner.id), "role": "Lead auditor"},
            {"person_id": str(person.id), "role": "Auditee"},
            {
                "name": "Jo External",
                "email": "jo@example.org",
                "role": "Certification body",
            },
        ],
    }
    created = aud.client.post("/v1/audits", json=payload, headers=unsafe(aud.client))
    assert created.status_code == 200, created.text
    detail = aud.client.get(f"/v1/audits/{created.json()['id']}").json()
    assert sorted(
        (a["name"], a["email"] or "", a["role"], a["person_id"] or "")
        for a in detail["attendees"]
    ) == sorted(
        [
            (aud.owner.username, "", "Lead auditor", ""),
            ("Pat Auditor", "pat@example.org", "Auditee", str(person.id)),
            ("Jo External", "jo@example.org", "Certification body", ""),
        ]
    )
    assert [c["ref"] for c in detail["scoped_controls"]] == ["A.1"]
    assert (detail["audit_type"], detail["start_date"]) == ("external", "2026-06-01")

    # One bad attendee refuses the whole audit; nothing is left half made.
    before = aud.db.query(Audit).count()
    for attendee, message in (
        ({"person_id": str(uuid.uuid4())}, "Unknown Person"),
        ({"role": "Observer"}, "name is required"),
    ):
        refused = aud.client.post(
            "/v1/audits",
            json={**payload, "attendees": [payload["attendees"][0], attendee]},
            headers=unsafe(aud.client),
        )
        assert refused.status_code == 400
        assert refused.json()["detail"] == message
    assert aud.db.query(Audit).count() == before
    too_many = aud.client.post(
        "/v1/audits",
        json={**payload, "attendees": [{"name": "x"}] * 101},
        headers=unsafe(aud.client),
    )
    assert too_many.status_code == 422


def test_findings_keep_to_the_audit_framework(aud):
    other = ControlItem(
        framework_slug="fw-elsewhere", type="annex_control", ref="Z.9", title="Z"
    )
    aud.db.add(other)
    aud.db.commit()
    created = aud.call(
        aud.client,
        "POST",
        "/findings",
        json={"kind": "minor_nc", "title": "Gap", "control": "A.1"},
    ).json()
    path = f"/findings/{created['id']}"
    assert created["control"]["ref"] == "A.1"

    for body, status, detail in (
        ({"kind": "catastrophe"}, 400, "Invalid finding kind"),
        ({"status": "pending"}, 400, "Invalid status"),
        ({"control": "Z.9"}, 404, "Control not found: Z.9"),
        ({"title": " "}, 400, "title is required"),
    ):
        refused = aud.call(aud.client, "PATCH", path, json=body)
        assert refused.status_code == status, body
        assert refused.json()["detail"] == detail

    moved = aud.call(
        aud.client, "PATCH", path, json={"control": "A.2", "kind": "MAJOR_NC"}
    ).json()
    assert (moved["control"]["ref"], moved["kind"]) == ("A.2", "major_nc")
    cleared = aud.call(aud.client, "PATCH", path, json={"control": None}).json()
    assert cleared["control"] is None
    for missing in ("not-a-uuid", str(uuid.uuid4())):
        response = aud.call(
            aud.client, "PATCH", f"/findings/{missing}", json={"title": "x"}
        )
        assert response.status_code in (400, 404)


# --- Sample, attendee and finding identifiers ------------------------------------


def test_sample_edits_need_a_real_sample_and_change_only_named_fields(aud):
    # Sample edits and removals refuse malformed or unknown identifiers, and a
    # partial edit changes only the fields it names.
    sample = aud.call(
        aud.client,
        "POST",
        "/evidence",
        json={"evidence_url": "https://evidence.example/a", "title": "Link"},
    ).json()
    for method in ("PATCH", "DELETE"):
        kwargs = {"json": {"title": "x"}} if method == "PATCH" else {}
        malformed = aud.call(aud.client, method, "/evidence/not-a-uuid", **kwargs)
        assert malformed.status_code == 400
        assert malformed.json()["detail"] == "evidence_id must be a UUID"
        unknown = aud.call(aud.client, method, f"/evidence/{uuid.uuid4()}", **kwargs)
        assert unknown.status_code == 404
        assert unknown.json()["detail"] == "Evidence not found"
    path = f"/evidence/{sample['id']}"
    changed = aud.call(
        aud.client,
        "PATCH",
        path,
        json={"evidence_url": "https://evidence.example/b", "title": " Renamed "},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["evidence_url"] == "https://evidence.example/b"
    assert changed.json()["title"] == "Renamed"
    cleared = aud.call(aud.client, "PATCH", path, json={"title": "  "}).json()
    assert cleared["title"] is None
    assert cleared["evidence_url"] == "https://evidence.example/b"
    assert (
        aud.call(aud.client, "PATCH", path, json={"notes": "<p>Seen</p>"}).json()[
            "notes"
        ]
        == "<p>Seen</p>"
    )
    assert aud.call(aud.client, "DELETE", path).status_code == 200
    assert aud.detail()["evidence"] == []


def test_resampling_a_record_with_notes_keeps_one_sample_with_the_new_notes(aud):
    target = {"entity_type": "risk", "entity_id": str(aud.risk.id)}
    first = aud.call(aud.client, "POST", "/evidence", json=target).json()
    again = aud.call(
        aud.client, "POST", "/evidence", json={**target, "notes": "<p>Checked</p>"}
    )
    assert again.status_code == 200, again.text
    assert again.json()["id"] == first["id"]
    (sample,) = aud.detail()["evidence"]
    assert sample["notes"] == "<p>Checked</p>"


def test_attendee_and_finding_removal_refuse_malformed_identifiers(aud):
    for path, detail in (
        ("/attendees/not-a-uuid", "attendee_id must be a UUID"),
        ("/findings/not-a-uuid", "finding_id must be a UUID"),
    ):
        refused = aud.call(aud.client, "DELETE", path)
        assert refused.status_code == 400
        assert refused.json()["detail"] == detail
    invalid_kind = aud.call(
        aud.client, "POST", "/findings", json={"kind": "catastrophe", "title": "Gap"}
    )
    assert invalid_kind.status_code == 400
    assert invalid_kind.json()["detail"] == "Invalid finding kind"
    assert aud.detail()["findings"] == []


def test_domain_operations_refuse_callers_without_audit_authority(aud):
    # The service layer enforces audit permissions on its own, so a caller
    # that bypasses the HTTP dependencies is still refused.
    from app.services.audits import access, operations
    from app.services.audits.schemas import AuditCreatePayload
    from app.services.errors import DomainError

    reader = aud.keen.user("audits.read")
    with pytest.raises(DomainError) as refused:
        operations.create_audit(
            payload=AuditCreatePayload(title="Blocked", framework_slug=FW),
            db=aud.db,
            user=reader,
        )
    assert refused.value.status_code == 403
    assert aud.db.query(Audit).filter_by(title="Blocked").count() == 0
    with pytest.raises(DomainError) as denied:
        access.require_audit_admin(aud.db, reader)
    assert denied.value.status_code == 403
    audit_id = uuid.UUID(aud.audit_id)
    for user in (None, "owner", object()):
        assert access.can_read_audits(aud.db, user) is False
        assert access.can_manage_audits(aud.db, user) is False
        assert access.is_audit_attendee(aud.db, user, audit_id) is False
    retired = aud.keen.user("audits.admin")
    retired.is_active = False
    aud.db.commit()
    assert access.can_read_audits(aud.db, retired) is False
    assert access.can_manage_audits(aud.db, retired) is False
    assert access.is_audit_attendee(aud.db, retired, audit_id) is False
    with pytest.raises(DomainError) as unknown:
        access.require_sample_source_access(
            aud.db, aud.owner, "ledger_entry", uuid.uuid4()
        )
    assert unknown.value.status_code == 403


def test_sample_references_describe_missing_records_without_leaking(aud):
    # Every sampled record type resolves to a 404 when required and to a
    # neutral placeholder otherwise; half-specified references are refused.
    from app.services.audits.samples import (
        audit_sample_entity_reference,
        entity_href,
    )
    from app.services.audits.validation import AUDIT_SAMPLE_ENTITY_LABELS
    from app.services.errors import DomainError

    assert entity_href("ledger_entry", uuid.uuid4(), FW) == "/audits.html"
    assert (
        audit_sample_entity_reference(
            aud.db, framework=FW, entity_type=None, entity_id=None
        )
        is None
    )
    assert (
        audit_sample_entity_reference(
            aud.db, framework=FW, entity_type="  ", entity_id=uuid.uuid4()
        )
        is None
    )
    with pytest.raises(DomainError) as half:
        audit_sample_entity_reference(
            aud.db, framework=FW, entity_type="risk", entity_id=None
        )
    assert half.value.status_code == 400
    with pytest.raises(DomainError) as half_id:
        audit_sample_entity_reference(
            aud.db, framework=FW, entity_type="", entity_id=uuid.uuid4()
        )
    assert half_id.value.status_code == 400
    for entity_type, label in AUDIT_SAMPLE_ENTITY_LABELS.items():
        missing = uuid.uuid4()
        with pytest.raises(DomainError) as gone:
            audit_sample_entity_reference(
                aud.db, framework=FW, entity_type=entity_type, entity_id=missing
            )
        assert gone.value.status_code == 404, entity_type
        placeholder = audit_sample_entity_reference(
            aud.db,
            framework=FW,
            entity_type=entity_type,
            entity_id=missing,
            required=False,
        )
        assert placeholder["title"] == label
        assert placeholder["subtitle"] == "This sampled item no longer exists."
        assert placeholder["href"] == entity_href(entity_type, missing, FW)
        assert placeholder["controls"] == []


def test_swot_scope_refuses_unknown_items_and_keeps_the_current_scope(aud):
    from app.db.models import AuditSwotScope, IsmsSwot

    item = IsmsSwot(title="Known", kind="threat", significance=3, status="defined")
    aud.db.add(item)
    aud.db.commit()
    manager = aud.keen.client(aud.keen.user("audits.admin", "isms.manage"))
    accepted = aud.call(manager, "PUT", "/scope", json={"swot_ids": [str(item.id)]})
    assert accepted.status_code == 200, accepted.text
    refused = aud.call(
        manager,
        "PUT",
        "/scope",
        json={"swot_ids": [str(item.id), str(uuid.uuid4())]},
    )
    assert refused.status_code == 400
    assert refused.json()["detail"] == "Unknown SWOT scope item"
    aud.db.expire_all()
    assert [
        s.swot_id
        for s in aud.db.query(AuditSwotScope).filter_by(
            audit_id=uuid.UUID(aud.audit_id)
        )
    ] == [item.id]


def test_resampling_an_event_without_details_keeps_notes_and_link(aud):
    # A repeat sample of the same event with no notes or link leaves those
    # fields as they were; the title falls back to the event summary.
    first = aud.call(
        aud.client,
        "POST",
        "/evidence",
        json={
            "event_id": str(aud.event.id),
            "title": "First look",
            "notes": "<p>Reviewed</p>",
            "evidence_url": "https://evidence.example/report",
        },
    ).json()
    again = aud.call(
        aud.client, "POST", "/evidence", json={"event_id": str(aud.event.id)}
    )
    assert again.status_code == 200, again.text
    assert again.json()["id"] == first["id"]
    (sample,) = aud.detail()["evidence"]
    assert (sample["title"], sample["notes"], sample["evidence_url"]) == (
        aud.event.summary,
        "<p>Reviewed</p>",
        "https://evidence.example/report",
    )
