"""Audit reads through the assembled application.

Covers attendee access, the audit list and attendee directory, scope maps and
coverage, the reverse "which audits sampled this" lookups, and how each kind
of sampled record is described inside an audit.
"""

import uuid
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from app.db.models import (
    Audit,
    AuditAttendee,
    AuditEvidence,
    AuditScopedClause,
    AuditScopedControl,
    AuditScopedIsmsDocument,
    BookStackSectionEvidence,
    ControlClauseLink,
    ControlItem,
    Event,
    Framework,
    FrameworkClause,
    InterestedParty,
    InterestedPartyControlLink,
    InterestedPartyName,
    InterestedPartyNature,
    IsmsAccessControlMatrixEntry,
    IsmsApplicationConfigurationEntry,
    IsmsBusinessProcess,
    IsmsDocument,
    IsmsEffectivenessMeasure,
    IsmsEffectivenessMetricEntry,
    IsmsEntityControlLink,
    IsmsMeeting,
    IsmsObjective,
    IsmsOrgNode,
    IsmsSwot,
    Mapping,
    PestleClauseRelevance,
    PestleItem,
    PestleRelevanceLevel,
    Risk,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
    RiskControlLink,
)

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)

HOSTILE = '<img src=x onerror="alert(1)">'


def _get(client, path, expect=200, **params):
    response = client.get(path, params=params)
    assert response.status_code == expect, response.text
    return response.json()


@pytest.fixture
def audits(keen):  # noqa: F811 - fixture imported above
    db = keen.db
    db.add_all([Framework(slug="A"), Framework(slug="B")])
    owner = keen.user("audits.manage")
    attendee = keen.user()
    retired = keen.user()
    retired.is_active = False
    db.flush()
    control = ControlItem(
        framework_slug="A", ref="A.5.1", type="annex_control", title="Policies"
    )
    other_control = ControlItem(
        framework_slug="A", ref="A.5.2", type="annex_control", title="Roles"
    )
    out_of_scope = ControlItem(
        framework_slug="A",
        ref="A.5.3",
        type="annex_control",
        title="Duties",
        in_scope=False,
    )
    parent = FrameworkClause(
        framework_slug="A", ref="5", title="Leadership", sort_order=1
    )
    db.add_all([control, other_control, out_of_scope, parent])
    db.flush()
    leaf = FrameworkClause(
        framework_slug="A",
        ref="5.1",
        title="Commitment",
        sort_order=2,
        parent_clause_id=parent.id,
    )
    leaf2 = FrameworkClause(
        framework_slug="A",
        ref="5.2",
        title="Policy",
        sort_order=3,
        parent_clause_id=parent.id,
    )
    document = IsmsDocument(title="Security policy", document_type="policy")
    other_document = IsmsDocument(title="Acceptable use", document_type="policy")
    db.add_all([leaf, leaf2, document, other_document])
    db.flush()
    db.add(
        ControlClauseLink(
            control_item_id=control.id, clause_id=leaf.id, applicability="applicable"
        )
    )
    completed = Audit(
        title=f"Annual review {HOSTILE}",
        framework_slug="A",
        status="completed",
        start_date=date.today() - timedelta(days=30),
        end_date=date.today() - timedelta(days=29),
        created_by_user_id=owner.id,
    )
    open_audit = Audit(
        title="Supplier audit",
        framework_slug="A",
        status="open",
        created_by_user_id=owner.id,
    )
    other_framework = Audit(
        title="Other framework",
        framework_slug="B",
        status="open",
        created_by_user_id=owner.id,
    )
    template = Audit(
        title="Monthly check",
        framework_slug="A",
        status="template",
        start_date=date.today() + timedelta(days=5),
        schedule_recurrence="monthly",
        schedule_next_run_date=date.today() + timedelta(days=5),
        created_by_user_id=owner.id,
    )
    db.add_all([completed, open_audit, other_framework, template])
    db.flush()
    db.add_all(
        [
            AuditScopedControl(audit_id=completed.id, control_item_id=control.id),
            AuditScopedControl(audit_id=completed.id, control_item_id=other_control.id),
            AuditScopedClause(audit_id=completed.id, clause_id=parent.id),
            AuditScopedIsmsDocument(audit_id=completed.id, document_id=document.id),
            AuditScopedControl(audit_id=template.id, control_item_id=other_control.id),
            AuditAttendee(
                audit_id=completed.id,
                user_id=attendee.id,
                name="Attendee",
                role="Observer",
            ),
            AuditAttendee(audit_id=template.id, user_id=attendee.id, name="Attendee"),
        ]
    )
    db.commit()
    return SimpleNamespace(
        keen=keen,
        db=db,
        owner=owner,
        attendee=attendee,
        retired=retired,
        control=control,
        other_control=other_control,
        parent=parent,
        leaf=leaf,
        leaf2=leaf2,
        document=document,
        other_document=other_document,
        completed=completed,
        open_audit=open_audit,
        other_framework=other_framework,
        template=template,
    )


def test_attendees_read_their_own_audits_only(audits):
    app = audits.keen
    attendee = app.client(audits.attendee)
    detail = _get(attendee, f"/v1/audits/{audits.completed.id}")
    assert detail["title"] == f"Annual review {HOSTILE}"
    assert [a["name"] for a in detail["attendees"]] == ["Attendee"]
    _get(attendee, f"/v1/audits/{audits.completed.id}/scope-map")
    for audit in (audits.open_audit, audits.other_framework):
        response = _get(attendee, f"/v1/audits/{audit.id}", expect=403)
        assert response["detail"] == "Audit access required"
        _get(attendee, f"/v1/audits/{audit.id}/scope-map", expect=403)
    # Attendance does not open the audit register itself.
    _get(attendee, "/v1/audits", expect=403)
    _get(attendee, "/v1/audits/users", expect=403)

    mine = _get(attendee, "/v1/me/audits")
    assert [item["id"] for item in mine["items"]] == [
        str(audits.completed.id)
    ]  # no templates
    assert mine["items"][0]["my_attendee"]["role"] == "Observer"
    assert _get(app.client(app.user()), "/v1/me/audits")["items"] == []
    assert app.client().get("/v1/me/audits").status_code == 401
    _get(attendee, "/v1/audits/not-a-uuid", expect=400)
    _get(attendee, f"/v1/audits/{uuid.uuid4()}", expect=404)


def test_the_audit_register_lists_and_filters_audits(audits):
    reader = audits.keen.client(audits.keen.user("audits.read"))
    listed = _get(reader, "/v1/audits")
    assert {item["id"] for item in listed["items"]} == {
        str(audits.completed.id),
        str(audits.open_audit.id),
        str(audits.other_framework.id),
    }
    assert [i["id"] for i in _get(reader, "/v1/audits", framework="B")["items"]] == [
        str(audits.other_framework.id)
    ]
    assert [i["id"] for i in _get(reader, "/v1/audits", q="supplier")["items"]] == [
        str(audits.open_audit.id)
    ]
    assert [
        i["id"] for i in _get(reader, "/v1/audits", status="completed")["items"]
    ] == [str(audits.completed.id)]
    assert [
        i["id"] for i in _get(reader, "/v1/audits", status="template")["items"]
    ] == [str(audits.template.id)]
    page = _get(reader, "/v1/audits", limit=1, offset=1)
    assert page["total"] == 3 and len(page["items"]) == 1
    clamped = _get(reader, "/v1/audits", limit=900, offset=-4)
    assert (clamped["limit"], clamped["offset"]) == (500, 0)
    _get(reader, "/v1/audits", framework="A B", expect=400)
    _get(audits.keen.client(audits.keen.user()), "/v1/audits", expect=403)

    manager = audits.keen.client(audits.owner)
    users = _get(manager, "/v1/audits/users")["items"]
    names = [u["username"] for u in users]
    assert audits.attendee.username in names and audits.retired.username not in names
    assert names == sorted(names)


def test_scope_maps_relate_scoped_clauses_and_controls(audits):
    reader = audits.keen.client(audits.keen.user("audits.read"))
    scope = _get(reader, f"/v1/audits/{audits.completed.id}/scope-map")
    assert [c["ref"] for c in scope["controls"]] == ["A.5.1", "A.5.2"]
    assert [c["ref"] for c in scope["clauses"]] == ["5"]
    assert [d["title"] for d in scope["documents"]] == ["Security policy"]
    # Only links between scoped clauses and scoped controls count.
    assert scope["links"] == []
    assert scope["summary"] == {
        "clause_count": 1,
        "control_count": 2,
        "document_count": 1,
        "relationship_count": 0,
        "controls_linked_to_selected_clauses": 0,
        "controls_in_scope_without_selected_clause_link": 2,
        "clauses_without_selected_control_link": 1,
    }
    audits.db.add(
        AuditScopedClause(audit_id=audits.completed.id, clause_id=audits.leaf.id)
    )
    audits.db.commit()
    scope = _get(reader, f"/v1/audits/{audits.completed.id}/scope-map")
    assert [(link["clause_ref"], link["control_ref"]) for link in scope["links"]] == [
        ("5.1", "A.5.1")
    ]
    assert scope["summary"]["controls_linked_to_selected_clauses"] == 1
    detail = _get(reader, f"/v1/audits/{audits.completed.id}")
    assert [c["ref"] for c in detail["scoped_clauses"]] == ["5", "5.1"]
    assert [d["title"] for d in detail["scoped_documents"]] == ["Security policy"]


def test_scope_coverage_combines_completed_and_scheduled_audits(audits):
    reader = audits.keen.client(audits.keen.user("audits.read"))
    window = {
        "framework": "A",
        "start": (date.today() - timedelta(days=60)).isoformat(),
        "end": (date.today() + timedelta(days=60)).isoformat(),
    }
    both = _get(reader, "/v1/audits/scope-coverage", **window)
    assert both["mode"] == "both"
    assert both["source_audit_count"] == 2
    assert both["scheduled_occurrence_count"] == 2  # monthly, inside the window
    included = {c["ref"] for c in both["included"]["controls"]}
    assert included == {"A.5.1", "A.5.2"}
    # Out-of-scope controls are not counted as gaps or coverage.
    assert both["summary"]["controls"] == {"total": 2, "included": 2, "gaps": 0}
    # A scoped parent clause covers its leaf clauses.
    assert {c["ref"] for c in both["included"]["clauses"]} == {"5.1", "5.2"}
    assert both["summary"]["clauses"] == {"total": 2, "included": 2, "gaps": 0}
    assert [d["title"] for d in both["gaps"]["documents"]] == ["Acceptable use"]

    completed = _get(reader, "/v1/audits/scope-coverage", mode="completed", **window)
    assert completed["scheduled_occurrence_count"] == 0
    scheduled = _get(reader, "/v1/audits/scope-coverage", mode="SCHEDULED", **window)
    assert scheduled["source_audit_count"] == 1
    assert {c["ref"] for c in scheduled["included"]["controls"]} == {"A.5.2"}
    assert {c["ref"] for c in scheduled["gaps"]["controls"]} == {"A.5.1"}

    for params, detail in (
        ({"mode": "sometimes"}, "mode must be completed, scheduled or both"),
        ({"start": "2026-02-01", "end": "2026-01-01"}, "end cannot be before start"),
        (
            {"start": "2000-01-01", "end": "2026-01-01"},
            "Coverage window cannot exceed 10 years",
        ),
    ):
        response = _get(
            reader, "/v1/audits/scope-coverage", expect=400, framework="A", **params
        )
        assert response["detail"] == detail
    default = _get(reader, "/v1/audits/scope-coverage", framework="A")
    assert default["end"] == date.today().isoformat()


def test_reverse_lookups_list_audits_that_sampled_an_item(audits):
    db = audits.db
    event = Event(
        source="jenkins",
        system="ci",
        action="deploy",
        outcome="success",
        severity=2,
        summary="Deploy",
        timestamp=datetime(2026, 3, 1, 12, 0),
        external_id="evt-1",
        raw_pointer={},
        normalized_payload={},
    )
    db.add(event)
    db.flush()
    db.add(Mapping(event_id=event.id, control_item_id=audits.control.id, method="rule"))
    risk = _risk(db)
    db.add_all(
        [
            AuditEvidence(
                audit_id=audits.completed.id,
                event_id=event.id,
                notes=HOSTILE,
                added_by_user_id=audits.owner.id,
            ),
            AuditEvidence(
                audit_id=audits.open_audit.id,
                entity_type="risk",
                entity_id=risk.id,
                title="Risk sample",
            ),
        ]
    )
    db.commit()

    app = audits.keen
    reader = app.client(app.user("audits.read", "events.read"))
    by_event = _get(reader, f"/v1/audits/by-event/{event.id}")
    assert [item["audit"]["id"] for item in by_event["items"]] == [
        str(audits.completed.id)
    ]
    assert by_event["items"][0]["evidence"]["notes"] == HOSTILE
    assert (
        by_event["items"][0]["evidence"]["added_by_username"] == audits.owner.username
    )
    _get(reader, f"/v1/audits/by-event/{uuid.uuid4()}", expect=404)
    _get(reader, "/v1/audits/by-event/nope", expect=400)
    # Event lookups also need event access.
    no_events = app.client(app.user("audits.read"))
    _get(no_events, f"/v1/audits/by-event/{event.id}", expect=403)

    by_entity = _get(reader, f"/v1/audits/by-entity/risk/{risk.id}")
    assert by_entity["entity_type"] == "risk"
    assert [item["evidence"]["title"] for item in by_entity["items"]] == ["Risk sample"]
    assert _get(reader, f"/v1/audits/by-entity/risk/{uuid.uuid4()}")["items"] == []
    _get(reader, f"/v1/audits/by-entity/spaceship/{risk.id}", expect=400)
    _get(reader, "/v1/audits/by-entity/risk/nope", expect=400)

    detail = _get(reader, f"/v1/audits/{audits.completed.id}")
    (evidence,) = detail["evidence"]
    assert evidence["event"]["id"] == str(event.id)
    assert [c["ref"] for c in evidence["controls"]] == ["A.5.1"]


def test_reverse_lookups_follow_the_sampled_records_feature(audits):
    db = audits.db
    risk = _risk(db)
    db.add(
        AuditEvidence(
            audit_id=audits.open_audit.id,
            entity_type="risk",
            entity_id=risk.id,
            title="Risk sample",
        )
    )
    db.commit()
    app = audits.keen
    reader = app.client(app.user("audits.read"))
    path = f"/v1/audits/by-entity/risk/{risk.id}"
    assert [i["evidence"]["title"] for i in _get(reader, path)["items"]] == [
        "Risk sample"
    ]

    admin = app.client(app.user(role="admin"))
    switched = admin.put(
        "/v1/admin/features",
        json={"version": 0, "changes": {"risk_cia": False}},
        headers=unsafe(admin),
    )
    assert switched.status_code == 200, switched.text

    off = _get(reader, path, expect=404)
    assert off["detail"] == "This feature is disabled by an administrator."
    assert _get(reader, f"/v1/audits/by-entity/RISK/{risk.id}", expect=404) == off
    # A former short name is refused rather than looked up past the switch.
    refused = _get(reader, f"/v1/audits/by-entity/cia_risk/{risk.id}", expect=400)
    assert refused["detail"] == "Unsupported sampled entity type"


@pytest.mark.parametrize(
    "name",
    [
        "cia_risk",
        "pestle",
        "org",
        "access",
        "app_config",
        "effectiveness",
        "metric",
        "interested-party",
        "Risk",
    ],
)
def test_reverse_lookups_accept_only_the_stored_entity_names(audits, name):
    reader = audits.keen.client(audits.keen.user("audits.read"))

    refused = _get(reader, f"/v1/audits/by-entity/{name}/{uuid.uuid4()}", expect=400)

    assert refused["detail"] == "Unsupported sampled entity type"


def _risk(db):
    category = RiskCategory(name="Data")
    db.add(category)
    db.flush()
    subcategory = RiskAssetSubcategory(category_id=category.id, name="Databases")
    db.add(subcategory)
    db.flush()
    asset = RiskAsset(
        name="Payroll", category_id=category.id, subcategory_id=subcategory.id
    )
    db.add(asset)
    db.flush()
    risk = Risk(
        asset_id=asset.id,
        risk_types=["Confidentiality"],
        threat_summary="Unauthorised export " * 20,
    )
    db.add(risk)
    db.flush()
    return risk


def test_sampled_records_are_described_with_their_controls(audits):
    db = audits.db
    control, fw = audits.control, "A"

    def link(entity_type, row):
        db.add(
            IsmsEntityControlLink(
                entity_type=entity_type,
                entity_id=row.id,
                framework_slug=fw,
                control_item_id=control.id,
            )
        )

    risk = _risk(db)
    db.add(
        RiskControlLink(risk_id=risk.id, framework_slug=fw, control_item_id=control.id)
    )
    high = PestleRelevanceLevel(code="high", label="High", sort_order=1)
    db.add(high)
    db.flush()
    pestle = PestleItem(
        framework_slug=fw,
        type="Legal",
        lens="External",
        item=HOSTILE,
        overall_relevance_id=high.id,
    )
    name, nature = (
        InterestedPartyName(name="Customers"),
        InterestedPartyNature(name="Data subjects"),
    )
    db.add_all([pestle, name, nature])
    db.flush()
    db.add(
        PestleClauseRelevance(
            pestle_item_id=pestle.id, clause_id=audits.leaf.id, relevance_id=high.id
        )
    )
    party = InterestedParty(
        framework_slug=fw, name_id=name.id, nature_id=nature.id, note="Retail"
    )
    swot = IsmsSwot(title="Strong team", kind="strength", description="Experienced")
    objective = IsmsObjective(
        requirement="Protect data", goal="No breaches", metric="Incidents"
    )
    node = IsmsOrgNode(name="Security officer", node_type="role")
    process = IsmsBusinessProcess(name="Payroll run")
    measure = IsmsEffectivenessMeasure(
        framework_slug=fw, summary="Coverage", description="Count"
    )
    meeting = IsmsMeeting(title="Management review", date=date(2026, 2, 1))
    db.add_all([party, swot, objective, node, process, measure, meeting])
    db.flush()
    db.add(
        InterestedPartyControlLink(
            interested_party_id=party.id, framework_slug=fw, control_item_id=control.id
        )
    )
    metric = IsmsEffectivenessMetricEntry(
        measure_id=measure.id,
        metric_value=96.5,
        metric_unit="%",
        qualitative_value="On track",
    )
    access = IsmsAccessControlMatrixEntry(
        task_action="Deploy to production", status="Approved"
    )
    app_config = IsmsApplicationConfigurationEntry(
        source_type="org_node",
        org_node_id=node.id,
        business_process_id=process.id,
        value="High",
    )
    section = BookStackSectionEvidence(
        document_id=audits.document.id,
        target_clause_id=audits.leaf.id,
        page_id=7,
        permalink="https://wiki.example/page/7",
        page_title="Password policy",
        revision_count=3,
        storage_uri="local://snapshots/7",
        sha256="a" * 64,
    )
    db.add_all([metric, access, app_config, section])
    for entity_type, row in (
        ("swot", swot),
        ("objective", objective),
        ("document", audits.document),
        ("org_node", node),
        ("effectiveness_measure", measure),
        ("meeting", meeting),
    ):
        link(entity_type, row)
    asset = db.get(RiskAsset, risk.asset_id)
    link("asset", asset)
    db.flush()

    samples = {
        "risk": risk,
        "pestle_item": pestle,
        "interested_party": party,
        "isms_swot": swot,
        "isms_objective": objective,
        "isms_document": audits.document,
        "bookstack_section": section,
        "isms_org_node": node,
        "isms_asset": asset,
        "isms_access_control_matrix": access,
        "isms_application_configuration": app_config,
        "isms_effectiveness_measure": measure,
        "isms_effectiveness_metric": metric,
        "isms_meeting": meeting,
    }
    for entity_type, row in samples.items():
        db.add(
            AuditEvidence(
                audit_id=audits.open_audit.id, entity_type=entity_type, entity_id=row.id
            )
        )
    db.add(
        AuditEvidence(
            audit_id=audits.open_audit.id, entity_type="risk", entity_id=uuid.uuid4()
        )
    )
    db.commit()

    reader = audits.keen.client(audits.keen.user("audits.read"))
    detail = _get(reader, f"/v1/audits/{audits.open_audit.id}")
    entities = {}
    for evidence in detail["evidence"]:
        entity = evidence.get("entity") or {}
        entities.setdefault(entity.get("type"), []).append(
            (entity, evidence["controls"])
        )

    def one(entity_type):
        (pair,) = [p for p in entities[entity_type] if not p[0]["missing"]]
        return pair

    risk_entity, risk_controls = one("risk")
    assert risk_entity["title"] == "CIA Triad risk: Payroll"
    assert risk_entity["subtitle"].endswith("…") and len(risk_entity["subtitle"]) == 220
    assert risk_entity["href"] == f"/risk.html?id={risk.id}&framework=A"
    assert [c["ref"] for c in risk_controls] == ["A.5.1"]
    missing = [p for p in entities["risk"] if p[0]["missing"]]
    assert missing[0][0]["subtitle"] == "This sampled item no longer exists."

    expectations = {
        "pestle_item": (f"PESTLE(E): {HOSTILE}", "Legal · External", ["A.5.1"]),
        "interested_party": (
            "Interested party: Customers — Data subjects",
            "Retail",
            ["A.5.1"],
        ),
        "isms_swot": ("SWOT strength: Strong team", "Experienced", ["A.5.1"]),
        "isms_objective": ("ISMS objective: No breaches", "Incidents", ["A.5.1"]),
        "isms_document": ("ISMS document: Security policy", "policy", ["A.5.1"]),
        "bookstack_section": (
            "BookStack policy: Password policy",
            f"Revision 3 · SHA-256 {'a' * 64}",
            ["A.5.1"],
        ),
        "isms_org_node": ("Organisation chart: Security officer", "role", ["A.5.1"]),
        "isms_asset": ("ISMS asset: Payroll", "Data / Databases", ["A.5.1"]),
        "isms_access_control_matrix": (
            "Access control matrix: Deploy to production",
            "Approved",
            [],
        ),
        "isms_application_configuration": (
            "Application configuration: Security officer",
            "Payroll run · High",
            [],
        ),
        "isms_effectiveness_measure": (
            "ISMS effectiveness measure: Coverage",
            "Count",
            ["A.5.1"],
        ),
        "isms_effectiveness_metric": (
            "ISMS metric: Coverage",
            "96.5 % On track",
            ["A.5.1"],
        ),
        "isms_meeting": ("ISMS meeting: Management review", "2026-02-01", ["A.5.1"]),
    }
    for entity_type, (title, subtitle, refs) in expectations.items():
        entity, controls = one(entity_type)
        assert (entity["title"], entity["subtitle"]) == (title, subtitle), entity_type
        assert [c["ref"] for c in controls] == refs, entity_type
    assert one("isms_objective")[0]["href"] == "/isms.html?framework=A&tab=objectives"
    assert (
        one("bookstack_section")[0]["href"]
        == f"/api/v1/isms/bookstack-sections/{section.id}/snapshot"
    )
    assert (
        one("isms_meeting")[0]["href"]
        == f"/isms-meeting.html?id={meeting.id}&framework=A"
    )


def test_records_from_another_framework_are_not_described(audits):
    db = audits.db
    high = PestleRelevanceLevel(code="high", label="High", sort_order=1)
    db.add(high)
    db.flush()
    pestle = PestleItem(
        framework_slug="B",
        type="Legal",
        lens="External",
        item="Elsewhere",
        overall_relevance_id=high.id,
    )
    section = BookStackSectionEvidence(
        document_id=audits.document.id,
        target_control_id=audits.control.id,
        page_id=8,
        permalink="https://wiki.example/page/8",
        page_title="Elsewhere",
        storage_uri="local://snapshots/8",
        sha256="b" * 64,
    )
    db.add_all([pestle, section])
    db.flush()
    # The audit uses framework B, so A-targeted snapshots and items are missing.
    for entity_type, row in (("pestle_item", pestle), ("bookstack_section", section)):
        db.add(
            AuditEvidence(
                audit_id=audits.other_framework.id,
                entity_type=entity_type,
                entity_id=row.id,
            )
        )
    db.commit()
    reader = audits.keen.client(audits.keen.user("audits.read"))
    detail = _get(reader, f"/v1/audits/{audits.other_framework.id}")
    states = {e["entity"]["type"]: e["entity"]["missing"] for e in detail["evidence"]}
    assert states == {"pestle_item": False, "bookstack_section": True}
