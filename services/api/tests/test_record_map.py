"""The Map through the assembled application.

The graph is checked for what it relates, for showing each reader only the
records they can open, for following the features that are switched on, and
for the evidence it adds on request.
"""

import uuid
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from app.core.config import settings
from app.db.models import (
    AffectedLink,
    Audit,
    AuditAttendee,
    AuditFinding,
    AuditScopedClause,
    AuditScopedControl,
    AuditScopedIsmsDocument,
    AuditSwotScope,
    BookStackSectionEvidence,
    ControlClauseLink,
    ControlItem,
    Event,
    Framework,
    FrameworkClause,
    IsmsDocument,
    IsmsEffectivenessMeasure,
    IsmsEntityClauseLink,
    IsmsEntityControlLink,
    IsmsLegalItem,
    IsmsLegalItemLink,
    IsmsMeeting,
    IsmsMeetingAttendee,
    IsmsMeetingLink,
    IsmsMeetingPerson,
    IsmsObjective,
    IsmsOrgNode,
    IsmsPerson,
    IsmsPersonAsset,
    IsmsSwot,
    IsmsSwotLink,
    IsmsVendor,
    Mapping,
    PestleClauseRelevance,
    PestleControlRelevance,
    PestleItem,
    PestleRelevanceLevel,
    Risk,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
    RiskClauseLink,
    RiskControlLink,
    Story,
    StoryClauseLink,
    StoryControlLink,
    StoryItem,
)
from mosp.services.errors import DomainError

from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)

HOSTILE = '<img src=x onerror="alert(1)">'
EVERY_KIND = [
    "control",
    "clause",
    "risk",
    "pestle_item",
    "document",
    "objective",
    "asset",
    "person",
    "vendor",
    "swot",
    "legal_item",
    "effectiveness_measure",
    "meeting",
    "org_node",
    "story",
    "audit",
    "source",
]


def _event(db, source, *controls):
    row = Event(
        source=source,
        system="ci",
        action="deploy",
        outcome="success",
        severity=2,
        summary="Deploy",
        timestamp=datetime(2026, 3, 1, 12, 0),
        external_id=uuid.uuid4().hex,
        raw_pointer={},
        normalized_payload={},
    )
    db.add(row)
    db.flush()
    for control in controls:
        db.add(Mapping(event_id=row.id, control_item_id=control.id, method="rule"))


@pytest.fixture
def world(keen, monkeypatch):  # noqa: F811 - fixture imported above
    """One record of every kind in framework A, related in every way."""
    monkeypatch.setattr(settings, "aggregate_cache_ttl_seconds", 0)
    monkeypatch.setattr(settings, "enabled_frameworks", "")
    db = keen.db
    owner = keen.user()
    db.add_all([Framework(slug="A"), Framework(slug="B")])
    db.flush()

    def add(row):
        db.add(row)
        db.flush()
        return row

    logging = add(
        ControlItem(framework_slug="A", ref="A.1", type="annex_control", title=HOSTILE)
    )
    change = add(
        ControlItem(framework_slug="A", ref="A.2", type="annex_control", title="Change")
    )
    unused = add(
        ControlItem(framework_slug="A", ref="A.3", type="annex_control", title="Unused")
    )
    elsewhere = add(
        ControlItem(framework_slug="B", ref="B.1", type="annex_control", title="Other")
    )
    context = add(FrameworkClause(framework_slug="A", ref="4.1", title="Context"))
    planning = add(FrameworkClause(framework_slug="A", ref="6.1", title="Planning"))
    lonely = add(FrameworkClause(framework_slug="A", ref="9.9", title="Lonely"))
    for control, clause in ((logging, context), (unused, lonely), (change, planning)):
        add(
            ControlClauseLink(
                control_item_id=control.id,
                clause_id=clause.id,
                applicability="applicable",
            )
        )

    category = add(RiskCategory(name="Category"))
    sub = add(RiskAssetSubcategory(name="Sub", category_id=category.id))
    vendor = add(IsmsVendor(name="Hosting Ltd"))
    team = add(IsmsOrgNode(name="Engineering", node_type="team"))
    role = add(IsmsOrgNode(name="SRE", node_type="role", parent_id=team.id))
    asset = add(
        RiskAsset(
            name="Payroll",
            category_id=category.id,
            subcategory_id=sub.id,
            vendor_id=vendor.id,
            owner_org_node_id=team.id,
        )
    )
    person = add(
        IsmsPerson(
            name="Ada", position="Engineer", org_node_id=role.id, user_id=owner.id
        )
    )
    add(IsmsPersonAsset(person_id=person.id, asset_id=asset.id))
    risk = add(
        Risk(asset_id=asset.id, threat_summary="Outage", risk_owner_user_id=owner.id)
    )
    unnamed = add(Risk(asset_id=asset.id, threat_summary=""))
    add(
        RiskControlLink(risk_id=risk.id, control_item_id=logging.id, framework_slug="A")
    )
    add(RiskClauseLink(risk_id=risk.id, clause_id=context.id, framework_slug="A"))
    add(
        RiskControlLink(
            risk_id=risk.id, control_item_id=elsewhere.id, framework_slug="B"
        )
    )
    add(AffectedLink(risk_id=risk.id, person_id=person.id))
    add(AffectedLink(risk_id=risk.id, vendor_id=vendor.id))

    levels = {
        code: add(PestleRelevanceLevel(code=code, label=code.title(), sort_order=n))
        for n, code in enumerate(("na", "high"))
    }
    factor = add(
        PestleItem(
            framework_slug="A",
            type="Legal",
            lens="External",
            item="New regulation",
            overall_relevance_id=levels["high"].id,
        )
    )
    add(
        PestleItem(
            framework_slug="B",
            type="Legal",
            lens="External",
            item="Another framework's factor",
            overall_relevance_id=levels["high"].id,
        )
    )
    add(
        PestleControlRelevance(
            pestle_item_id=factor.id,
            control_item_id=change.id,
            relevance_id=levels["high"].id,
        )
    )
    add(
        PestleControlRelevance(
            pestle_item_id=factor.id,
            control_item_id=unused.id,
            relevance_id=levels["na"].id,
        )
    )
    add(
        PestleClauseRelevance(
            pestle_item_id=factor.id,
            clause_id=planning.id,
            relevance_id=levels["high"].id,
        )
    )
    add(AffectedLink(pestle_item_id=factor.id, asset_id=asset.id))

    document = add(IsmsDocument(title="Backup policy", document_type="policy"))
    objective = add(IsmsObjective(requirement="Recover", goal=""))
    strength = add(IsmsSwot(title="Skilled team", kind="strength"))
    add(IsmsSwot(title="On-call rota", kind="strength", parent_id=strength.id))
    law = add(IsmsLegalItem(title="Privacy Act", jurisdiction="AU"))
    measure = add(
        IsmsEffectivenessMeasure(framework_slug="A", summary="", metric_key="uptime")
    )
    add(IsmsEffectivenessMeasure(framework_slug="B", summary="Another framework's"))
    meeting = add(IsmsMeeting(title="", date=date(2026, 2, 3)))
    private = add(IsmsMeeting(title="Board", date=date(2026, 2, 4)))
    for kind, row in (
        ("document", document),
        ("objective", objective),
        ("swot", strength),
        ("legal_item", law),
        ("effectiveness_measure", measure),
        ("meeting", meeting),
        ("org_node", team),
        ("asset", asset),
        ("access_control_matrix", document),
    ):
        add(
            IsmsEntityControlLink(
                entity_type=kind,
                entity_id=row.id,
                framework_slug="A",
                control_item_id=logging.id,
            )
        )
    add(
        IsmsEntityClauseLink(
            entity_type="document",
            entity_id=document.id,
            framework_slug="A",
            clause_id=context.id,
        )
    )
    add(
        BookStackSectionEvidence(
            document_id=document.id,
            target_control_id=change.id,
            page_id=1,
            permalink="https://wiki.example/p",
            page_title="Backups",
            storage_uri="local://x",
            sha256="0" * 64,
        )
    )
    for kind, row in (("objective", objective), ("risk", risk), ("person", person)):
        add(IsmsSwotLink(swot_id=strength.id, target_type=kind, target_id=row.id))
    for kind, row in (("document", document), ("swot", strength)):
        add(IsmsLegalItemLink(item_id=law.id, target_type=kind, target_id=row.id))
    add(IsmsLegalItemLink(item_id=law.id, target_type="swot", target_id=uuid.uuid4()))
    add(
        IsmsMeetingLink(
            meeting_id=meeting.id, link_type="isms_document", document_id=document.id
        )
    )
    add(
        IsmsMeetingPerson(
            meeting_id=meeting.id,
            person_id=person.id,
            attendance_type="attendee",
            name="Ada",
        )
    )
    add(
        IsmsMeetingAttendee(
            meeting_id=meeting.id, user_id=owner.id, attendance_type="attendee"
        )
    )

    story = add(Story(title="Outage review", created_by_user_id=owner.id))
    add(StoryItem(story_id=story.id, kind="risk", entity_id=risk.id, label="Risk"))
    add(StoryItem(story_id=story.id, kind="note", label="A note"))
    add(
        StoryItem(
            story_id=story.id, kind="event", entity_id=uuid.uuid4(), label="Event"
        )
    )
    add(
        StoryControlLink(
            story_id=story.id, control_item_id=change.id, framework_slug="A"
        )
    )
    add(StoryClauseLink(story_id=story.id, clause_id=planning.id, framework_slug="A"))

    audit = add(Audit(title="Internal audit", framework_slug="A"))
    add(Audit(title="Another framework's audit", framework_slug="B"))
    closed = add(Audit(title="Closed-door audit", framework_slug="A"))
    add(AuditScopedControl(audit_id=audit.id, control_item_id=logging.id))
    add(AuditScopedClause(audit_id=audit.id, clause_id=context.id))
    add(AuditScopedIsmsDocument(audit_id=audit.id, document_id=document.id))
    add(AuditSwotScope(audit_id=audit.id, swot_id=strength.id))
    add(
        AuditFinding(
            audit_id=audit.id,
            kind="minor_nc",
            title="Gap",
            control_item_id=change.id,
        )
    )
    add(AuditAttendee(audit_id=audit.id, person_id=person.id, name="Ada"))
    add(AuditAttendee(audit_id=audit.id, name="A guest"))

    _event(db, "jenkins", logging, change)
    _event(db, "jenkins", logging)
    _event(db, "github", unused)
    db.commit()
    return SimpleNamespace(
        keen=keen,
        db=db,
        owner=owner,
        admin=keen.client(keen.user(role="admin")),
        logging=logging,
        change=change,
        unused=unused,
        context=context,
        risk=risk,
        unnamed=unnamed,
        strength=strength,
        meeting=meeting,
        private=private,
        audit=audit,
        closed=closed,
        document=document,
    )


def read(client, query="", expect=200, path="/v1/map"):
    response = client.get(f"{path}?framework=A{query}")
    assert response.status_code == expect, response.text
    return response.json()


def labels(data, kind):
    return sorted(node["label"] for node in data["nodes"] if node["kind"] == kind)


def relations(data):
    """Each link as (source label, relation, target label, value)."""
    names = {node["id"]: node["label"] for node in data["nodes"]}
    return sorted(
        (names[link["source"]], link["relation"], names[link["target"]], link["value"])
        for link in data["links"]
        if link["source"] in names and link["target"] in names
    )


def switch(admin, **changes):
    version = admin.get("/v1/admin/features").json()["version"]
    response = admin.put(
        "/v1/admin/features",
        json={"version": version, "changes": changes},
        headers=unsafe(admin),
    )
    assert response.status_code == 200, response.text


def test_the_map_relates_every_kind_of_record_without_events(world):
    data = read(world.admin)
    assert data["framework"] == "A"
    assert (data["truncated"], data["notes"]) == (False, [])
    assert [kind["key"] for kind in data["kinds"]] == EVERY_KIND
    counts = {kind["key"]: kind["count"] for kind in data["kinds"]}
    # The controls and clauses nothing refers to are left out, as are the
    # records of another framework and every event and source.
    assert labels(data, "control") == ["A.1", "A.2"]
    assert labels(data, "clause") == ["4.1", "6.1"]
    assert (counts["control"], counts["source"], counts["audit"]) == (2, 0, 2)
    assert labels(data, "pestle_item") == ["New regulation"]
    assert labels(data, "effectiveness_measure") == ["uptime"]
    # A record with no name of its own takes one from what it is.
    assert labels(data, "risk") == ["Outage", "Payroll"]
    assert labels(data, "objective") == ["Recover"]
    assert labels(data, "meeting") == ["Board", "Meeting minutes"]
    assert relations(data) == [
        ("4.1", "applies", "A.1", 1),
        ("6.1", "applies", "A.2", 1),
        ("Ada", "member", "SRE", 1),
        ("Ada", "uses", "Payroll", 1),
        ("Backup policy", "mapped", "4.1", 1),
        ("Backup policy", "mapped", "A.1", 1),
        ("Backup policy", "mapped", "A.2", 1),
        ("Engineering", "mapped", "A.1", 1),
        ("Internal audit", "attends", "Ada", 1),
        ("Internal audit", "finding", "A.2", 1),
        ("Internal audit", "scope", "4.1", 1),
        ("Internal audit", "scope", "A.1", 1),
        ("Internal audit", "scope", "Backup policy", 1),
        ("Internal audit", "scope", "Skilled team", 1),
        ("Meeting minutes", "attends", "Ada", 1),
        ("Meeting minutes", "mapped", "A.1", 1),
        ("Meeting minutes", "refers", "Backup policy", 1),
        ("New regulation", "affects", "Payroll", 1),
        ("New regulation", "relevant", "6.1", 1),
        ("New regulation", "relevant", "A.2", 1),
        ("On-call rota", "part", "Skilled team", 1),
        ("Outage", "affects", "Ada", 1),
        ("Outage", "affects", "Hosting Ltd", 1),
        ("Outage", "asset", "Payroll", 1),
        ("Outage", "mitigates", "4.1", 1),
        ("Outage", "mitigates", "A.1", 1),
        ("Outage review", "mapped", "6.1", 1),
        ("Outage review", "mapped", "A.2", 1),
        ("Outage review", "story", "Outage", 1),
        ("Payroll", "asset", "Payroll", 1),
        ("Payroll", "mapped", "A.1", 1),
        ("Payroll", "owned", "Engineering", 1),
        ("Payroll", "supplied", "Hosting Ltd", 1),
        ("Privacy Act", "mapped", "A.1", 1),
        ("Privacy Act", "related", "Backup policy", 1),
        ("Privacy Act", "related", "Skilled team", 1),
        ("Recover", "mapped", "A.1", 1),
        ("SRE", "part", "Engineering", 1),
        ("Skilled team", "mapped", "A.1", 1),
        ("Skilled team", "related", "Ada", 1),
        ("Skilled team", "related", "Outage", 1),
        ("Skilled team", "related", "Recover", 1),
        ("uptime", "mapped", "A.1", 1),
    ]
    # A node names its record, says what it is and where it opens. Text is
    # data: the page writes it as text.
    by_label = {node["label"]: node for node in data["nodes"]}
    assert by_label["A.1"] == {
        "id": f"control:{world.logging.id}",
        "kind": "control",
        "label": "A.1",
        "detail": HOSTILE,
        "url": f"/control.html?id={world.logging.id}",
    }
    assert by_label["Engineering"]["url"] == "/isms.html?tab=org"
    assert by_label["Meeting minutes"]["detail"] == "2026-02-03"


def test_a_long_name_is_shortened_and_a_large_map_keeps_its_hubs(world, monkeypatch):
    from app.services import record_map

    world.document.title = "Policy " + "x" * 200
    world.db.commit()
    data = read(world.admin)
    long = next(node for node in data["nodes"] if node["label"].startswith("Policy x"))
    assert (len(long["label"]), long["label"][-1]) == (80, "…")

    monkeypatch.setattr(record_map, "MAX_NODES", 3)
    data = read(world.admin)
    assert data["truncated"] is True
    # The best connected records stay, with the links between them.
    assert labels(data, "control") == ["A.1"]
    assert len(data["nodes"]) == 3
    kept = {node["id"] for node in data["nodes"]}
    assert data["links"]
    assert all(
        link["source"] in kept and link["target"] in kept for link in data["links"]
    )


def test_each_reader_sees_the_records_they_can_open(world):
    keen = world.keen  # noqa: F811
    # Without a permission a person sees what is theirs: the risk they own and
    # the meeting and audit they attend, with the controls those refer to.
    owner = read(keen.client(world.owner))
    assert [kind["key"] for kind in owner["kinds"]] == [
        "control",
        "clause",
        "risk",
        "meeting",
        "audit",
    ]
    assert labels(owner, "risk") == ["Outage"]
    assert labels(owner, "meeting") == ["Meeting minutes"]
    assert labels(owner, "audit") == ["Internal audit"]
    assert relations(owner) == [
        ("4.1", "applies", "A.1", 1),
        ("Internal audit", "finding", "A.2", 1),
        ("Internal audit", "scope", "4.1", 1),
        ("Internal audit", "scope", "A.1", 1),
        ("Meeting minutes", "mapped", "A.1", 1),
        ("Outage", "mitigates", "4.1", 1),
        ("Outage", "mitigates", "A.1", 1),
    ]
    # Someone with nothing of their own sees no records, and so no catalogue.
    stranger = read(keen.client(keen.user()))
    assert (stranger["nodes"], stranger["links"]) == ([], [])

    # Each permission adds its own records.
    isms = read(keen.client(keen.user("isms.read")))
    assert labels(isms, "meeting") == ["Board", "Meeting minutes"]
    assert labels(isms, "risk") == [] and labels(isms, "story") == []
    assert ("Privacy Act", "related", "Skilled team", 1) in relations(isms)
    assert not any("Outage" in row for row in relations(isms))
    risks = read(keen.client(keen.user("risk.read")))
    assert labels(risks, "risk") == ["Outage", "Payroll"]
    assert labels(risks, "pestle_item") == ["New regulation"]
    assert labels(risks, "audit") == []
    pestle = read(keen.client(keen.user("pestle.read")))
    assert labels(pestle, "pestle_item") == ["New regulation"]
    assert labels(pestle, "risk") == [] and labels(pestle, "asset") == []
    stories = read(keen.client(keen.user("stories.read")))
    assert relations(stories) == [
        ("6.1", "applies", "A.2", 1),
        ("Outage review", "mapped", "6.1", 1),
        ("Outage review", "mapped", "A.2", 1),
    ]
    audits = read(keen.client(keen.user("audits.read")))
    assert labels(audits, "audit") == ["Closed-door audit", "Internal audit"]
    assert labels(audits, "document") == []

    anonymous = keen.client().get("/v1/map")
    assert (anonymous.status_code, anonymous.json()) == (
        401,
        {"detail": "Not authenticated"},
    )


def test_the_map_follows_the_features_that_are_switched_on(world):
    switch(world.admin, swot=False, controls=False, audits=False)
    data = read(world.admin)
    keys = [kind["key"] for kind in data["kinds"]]
    assert not {"swot", "control", "audit"} & set(keys)
    assert "legal_item" in keys
    assert not any(
        "Skilled team" in row or "A.1" in row or "Internal audit" in row
        for row in relations(data)
    )
    assert ("Outage", "mitigates", "4.1", 1) in relations(data)

    switch(world.admin, map=False)
    for path in ("/v1/map", "/v1/map/neighbours"):
        refused = world.admin.get(f"{path}?node=risk:{world.risk.id}")
        assert (refused.status_code, refused.json()) == (
            404,
            {"detail": "This feature is disabled by an administrator."},
        )


def test_evidence_sources_join_the_map_weighted_by_their_events(world, monkeypatch):
    from app.services import record_map

    data = read(world.admin, "&evidence=true")
    assert labels(data, "source") == ["GitHub", "Jenkins"]
    # A control with evidence joins the map though no record refers to it.
    assert labels(data, "control") == ["A.1", "A.2", "A.3"]
    assert [row for row in relations(data) if row[1] == "evidence"] == [
        ("GitHub", "evidence", "A.3", 1),
        ("Jenkins", "evidence", "A.1", 2),
        ("Jenkins", "evidence", "A.2", 1),
    ]
    source = next(node for node in data["nodes"] if node["label"] == "Jenkins")
    assert source == {
        "id": "source:jenkins",
        "kind": "source",
        "label": "Jenkins",
        "detail": "2 mapped events",
        "url": "/source.html?source=jenkins",
    }

    # Evidence needs events.read, and both of its features.
    reader = world.keen.client(world.keen.user("isms.read"))
    assert labels(read(reader, "&evidence=true"), "source") == []
    switch(world.admin, sources=False)
    off = read(world.admin, "&evidence=true")
    assert "source" not in [kind["key"] for kind in off["kinds"]]
    assert labels(off, "source") == []
    switch(world.admin, sources=True)

    # A count that cannot be made is said, and the rest of the map stands.
    def refused(**kwargs):
        raise DomainError(400, "The pattern took too long")

    monkeypatch.setattr(record_map, "graph_control_source", refused)
    data = read(world.admin, "&evidence=true")
    assert data["notes"] == [
        "Evidence sources are not shown: The pattern took too long"
    ]
    assert labels(data, "source") == [] and labels(data, "risk") == [
        "Outage",
        "Payroll",
    ]


def test_a_records_neighbours_reach_the_catalogue_and_its_evidence(world):
    def near(node, client=world.admin, expect=200):
        return read(client, f"&node={node}", expect, "/v1/map/neighbours")

    # A control's neighbours are its clauses, records and evidence sources.
    data = near(f"control:{world.unused.id}")
    assert relations(data) == [
        ("9.9", "applies", "A.3", 1),
        ("GitHub", "evidence", "A.3", 1),
    ]
    data = near(f"control:{world.logging.id}")
    assert ("Jenkins", "evidence", "A.1", 2) in relations(data)
    assert ("Outage", "mitigates", "A.1", 1) in relations(data)
    # The neighbours are the records one link away. Their own links come too,
    # so the page can join them to what it already shows, but not their
    # neighbours, and a source's count is the control's own.
    assert "A.2" not in labels(data, "control")
    shown = {node["id"] for node in data["nodes"]}
    beyond = [
        link for link in data["links"] if not {link["source"], link["target"]} <= shown
    ]
    assert f"control:{world.change.id}" in {link["target"] for link in beyond}
    assert all({link["source"], link["target"]} & shown for link in data["links"])
    assert not any(
        link["relation"] == "evidence" and link in beyond for link in data["links"]
    )
    # A source's neighbours are the controls it supplies.
    assert relations(near("source:jenkins")) == [
        ("Jenkins", "evidence", "A.1", 2),
        ("Jenkins", "evidence", "A.2", 1),
    ]
    # A record with nothing related is returned alone.
    alone = near(f"risk:{world.unnamed.id}")
    assert [node["label"] for node in alone["nodes"]] == ["Payroll", "Payroll"]
    assert relations(alone) == [("Payroll", "asset", "Payroll", 1)]
    lonely = near(f"meeting:{world.private.id}")
    assert ([node["label"] for node in lonely["nodes"]], lonely["links"]) == (
        ["Board"],
        [],
    )

    # A record the reader cannot open is not available, as an unknown one.
    owner = world.keen.client(world.owner)
    assert labels(near(f"risk:{world.risk.id}", owner), "risk") == ["Outage"]
    for node, client in (
        (f"risk:{world.unnamed.id}", owner),
        (f"meeting:{world.private.id}", owner),
        (f"audit:{world.closed.id}", owner),
        (f"control:{uuid.uuid4()}", world.admin),
        ("source:nothing", world.admin),
        ("source:jenkins", owner),
    ):
        assert near(node, client, 404) == {"detail": "Record not available"}
    for node in ("event:" + str(uuid.uuid4()), "risk:", "risk:not-a-uuid", "nonsense"):
        assert near(node, expect=400) == {
            "detail": "node names a kind of record and its id"
        }
    missing = world.admin.get("/v1/map/neighbours")
    assert missing.status_code == 422
