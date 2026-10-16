"""Clause and control catalogue reads through the assembled application.

Catalogue reads are available to any signed-in user; mapped evidence needs
events.read and is redacted, deduplicated across crosswalks and paginated.
"""

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from app.core.config import settings
from app.db.models import (
    ControlClauseLink,
    ControlItem,
    CrossFrameworkControlLink,
    EntityChangelog,
    Event,
    Framework,
    FrameworkClause,
    Mapping,
)
from app.security.redaction import REDACTED

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import keen  # noqa: F401

HOSTILE = '<img src=x onerror="alert(1)">'


@pytest.fixture
def catalogue(keen, monkeypatch, tmp_path):  # noqa: F811 - fixture imported above
    monkeypatch.setattr(settings, "enabled_frameworks", "")
    links = tmp_path / "control_links.yml"
    links.write_text("{}\n")
    monkeypatch.setattr(settings, "control_links_path", str(links))
    db = keen.db
    db.add_all([Framework(slug="A"), Framework(slug="B")])
    db.flush()
    root = FrameworkClause(framework_slug="A", ref="4", title="Context", sort_order=1)
    leadership = FrameworkClause(
        framework_slug="A", ref="5", title="Leadership", sort_order=2
    )
    db.add_all([root, leadership])
    db.flush()
    child10 = FrameworkClause(
        framework_slug="A",
        ref="4.10",
        title="Later child",
        parent_clause_id=root.id,
        sort_order=1,
        meta={
            "evidence_mappings": [
                {
                    "title": "  Statement of Applicability  ",
                    "url": "https://docs.example/soa",
                },
                {"label": "", "href": "/event.html?id=1"},
                {"title": "Duplicate", "url": "https://docs.example/soa"},
                {"title": "No URL"},
                "https://docs.example/plain",
            ],
            "upstream_url": "https://standards.example/4.10",
        },
    )
    child2 = FrameworkClause(
        framework_slug="A",
        ref="4.2",
        title=HOSTILE,
        parent_clause_id=root.id,
        sort_order=1,
        meta={"evidence_urls": "https://docs.example/a\n\n https://docs.example/b "},
    )
    other = FrameworkClause(framework_slug="B", ref="1", title="Other framework")
    db.add_all([child10, child2, other])
    db.flush()
    logging = ControlItem(
        framework_slug="A",
        ref="A.8.15",
        type="annex_control",
        title="Logging",
        meta={
            "justification": "risk_assessment",
            "upstream_url": "javascript:alert(1)",
        },
    )
    access = ControlItem(
        framework_slug="A", ref="A.5.15", type="annex_control", title="Access control"
    )
    custom = ControlItem(
        framework_slug="A", ref="C-2", type="custom", title=HOSTILE, in_scope=False
    )
    monitoring = ControlItem(
        framework_slug="B", ref="B.1", type="annex_control", title="Monitoring"
    )
    db.add_all([logging, access, custom, monitoring])
    db.flush()
    db.add_all(
        [
            ControlClauseLink(
                control_item_id=logging.id,
                clause_id=child2.id,
                applicability="applicable",
            ),
            ControlClauseLink(
                control_item_id=access.id,
                clause_id=child2.id,
                applicability="partially_applicable",
            ),
            ControlClauseLink(
                control_item_id=logging.id,
                clause_id=child10.id,
                applicability="applicable",
            ),
            # A link to another framework's clause is not part of this control's view.
            ControlClauseLink(
                control_item_id=logging.id,
                clause_id=other.id,
                applicability="applicable",
            ),
            CrossFrameworkControlLink(
                source_control_id=logging.id, target_control_id=monitoring.id
            ),
        ]
    )
    db.commit()
    return SimpleNamespace(
        keen=keen,
        db=db,
        root=root,
        leadership=leadership,
        child10=child10,
        child2=child2,
        logging=logging,
        access=access,
        custom=custom,
        monitoring=monitoring,
    )


def test_catalogue_reads_need_a_signed_in_user(catalogue):
    anonymous = catalogue.keen.client()
    clause = catalogue.child2.id
    control = catalogue.logging.id
    for path in (
        "/v1/clauses",
        f"/v1/clauses/{clause}",
        f"/v1/clauses/{clause}/controls",
        f"/v1/clauses/{clause}/changelog",
        "/v1/controls",
        f"/v1/controls/{control}",
        f"/v1/controls/{control}/changelog",
        f"/v1/controls/{control}/evidence",
    ):
        assert anonymous.get(path).status_code == 401, path
    signed_in = catalogue.keen.client(catalogue.keen.user())
    assert signed_in.get(f"/v1/controls/{control}").status_code == 200
    response = signed_in.get(f"/v1/controls/{control}/evidence")
    assert response.status_code == 403
    assert response.json()["detail"] == "events.read permission required"


def test_clauses_are_listed_as_a_naturally_sorted_tree(catalogue):
    client = catalogue.keen.client(catalogue.keen.user())
    listed = client.get("/v1/clauses", params={"framework": "A"}).json()
    assert listed["framework"] == "A" and listed["total"] == 4
    refs = [item["ref"] for item in listed["items"]]
    assert refs == ["4", "4.2", "4.10", "5"]
    tree = listed["tree"]
    assert [node["ref"] for node in tree] == ["4", "5"]
    assert [child["ref"] for child in tree[0]["children"]] == ["4.2", "4.10"]
    by_ref = {item["ref"]: item for item in listed["items"]}
    assert by_ref["4"]["child_count"] == 2
    assert by_ref["4.2"]["control_link_count"] == 2
    assert by_ref["4.2"]["parent_ref"] == "4"
    assert by_ref["4.2"]["title"] == HOSTILE
    assert by_ref["5"]["control_link_count"] == 0

    # Evidence mappings accept several stored shapes and drop duplicates.
    assert by_ref["4.10"]["evidence_mappings"] == [
        {"title": "Statement of Applicability", "url": "https://docs.example/soa"},
        {"title": "/event.html?id=1", "url": "/event.html?id=1"},
        {"title": "https://docs.example/plain", "url": "https://docs.example/plain"},
    ]
    assert by_ref["4.10"]["evidence_count"] == 3
    assert by_ref["4.10"]["upstream_url"] == "https://standards.example/4.10"
    assert by_ref["4.2"]["evidence_urls"] == [
        "https://docs.example/a",
        "https://docs.example/b",
    ]
    assert by_ref["5"]["evidence_mappings"] == []

    page = client.get(
        "/v1/clauses", params={"framework": "A", "limit": 2, "offset": 1}
    ).json()
    assert [item["ref"] for item in page["items"]] == ["4.2", "4.10"]
    # A page whose parent is elsewhere is rooted at its own items.
    assert [node["ref"] for node in page["tree"]] == ["4.2", "4.10"]
    clamped = client.get(
        "/v1/clauses", params={"framework": "A", "limit": 0, "offset": -1}
    ).json()
    assert (clamped["limit"], clamped["offset"]) == (5000, 0)
    assert (
        client.get("/v1/clauses", params={"framework": "missing"}).json()["items"] == []
    )


def test_clause_detail_controls_and_changelog(catalogue):
    client = catalogue.keen.client(catalogue.keen.user())
    clause = catalogue.child2
    detail = client.get(f"/v1/clauses/{clause.id}").json()
    assert detail["ref"] == "4.2" and detail["parent_id"] == str(catalogue.root.id)
    # The detail reports the same link count as the clause list.
    assert detail["control_link_count"] == 2
    assert detail["evidence_urls"] == [
        "https://docs.example/a",
        "https://docs.example/b",
    ]

    controls = client.get(f"/v1/clauses/{clause.id}/controls").json()
    assert controls["clause"]["ref"] == "4.2"
    assert controls["clause"]["control_link_count"] == 2
    assert [(c["ref"], c["applicability"]) for c in controls["items"]] == [
        ("A.5.15", "partially_applicable"),
        ("A.8.15", "applicable"),
    ]
    assert controls["items"][1]["justification"] == "Result of Risk Assessment"
    page = client.get(
        f"/v1/clauses/{clause.id}/controls", params={"limit": 1, "offset": 1}
    ).json()
    assert [c["ref"] for c in page["items"]] == ["A.8.15"] and page["total"] == 2

    catalogue.db.add(
        EntityChangelog(
            entity_type="clause",
            entity_id=clause.id,
            action="updated",
            summary="Updated evidence",
            changes=[],
            changed_by_username="auditor",
        )
    )
    catalogue.db.commit()
    changelog = client.get(f"/v1/clauses/{clause.id}/changelog").json()
    assert [item["summary"] for item in changelog["items"]] == ["Updated evidence"]

    for suffix in ("", "/controls", "/changelog"):
        assert client.get(f"/v1/clauses/not-a-uuid{suffix}").status_code == 400
        assert client.get(f"/v1/clauses/{uuid.uuid4()}{suffix}").status_code == 404


def test_controls_are_listed_and_described_per_framework(catalogue):
    client = catalogue.keen.client(catalogue.keen.user())
    listed = client.get("/v1/controls", params={"framework": "A"}).json()
    assert [item["ref"] for item in listed["items"]] == ["A.5.15", "A.8.15", "C-2"]
    by_ref = {item["ref"]: item for item in listed["items"]}
    assert by_ref["C-2"]["title"] == HOSTILE and by_ref["C-2"]["in_scope"] is False
    # Only absolute web links are offered as upstream guidance.
    assert by_ref["A.8.15"]["upstream_url"] is None
    page = client.get(
        "/v1/controls", params={"framework": "A", "limit": 1, "offset": 2}
    ).json()
    assert [item["ref"] for item in page["items"]] == ["C-2"] and page["total"] == 3

    detail = client.get(f"/v1/controls/{catalogue.logging.id}").json()
    assert detail["justification"] == "Result of Risk Assessment"
    assert [(c["ref"], c["parent_ref"]) for c in detail["clauses"]] == [
        ("4.2", "4"),
        ("4.10", "4"),
    ]
    assert client.get("/v1/controls/not-a-uuid").status_code == 400
    assert client.get(f"/v1/controls/{uuid.uuid4()}").status_code == 404
    assert client.get(f"/v1/controls/{uuid.uuid4()}/changelog").status_code == 404
    assert client.get("/v1/controls/nope/changelog").status_code == 400
    assert (
        client.get(f"/v1/controls/{catalogue.logging.id}/changelog").json()["items"]
        == []
    )


def _mapped_event(db, *controls, minutes=0, **kw):
    event = Event(
        source=kw.pop("source", "jenkins"),
        system="ci",
        action="deploy",
        outcome="success",
        severity=2,
        summary=kw.pop("summary", "Deploy"),
        timestamp=datetime(2026, 3, 1, 12, 0) + timedelta(minutes=minutes),
        external_id=uuid.uuid4().hex,
        raw_pointer=kw.pop("raw_pointer", {}),
        normalized_payload={},
        **kw,
    )
    db.add(event)
    db.flush()
    for offset, control in enumerate(controls):
        db.add(
            Mapping(
                event_id=event.id,
                control_item_id=control.id,
                method="rule",
                rationale=f"rule {control.ref}",
                mapped_at=datetime(2026, 3, 2)
                + timedelta(minutes=minutes, seconds=offset),
            )
        )
    db.flush()
    return event


def test_control_evidence_is_redacted_inherited_and_deduplicated(catalogue):
    db = catalogue.db
    secret_url = "https://builder:hunter2@ci.example/job/1?token=abc123&page=2"
    direct = _mapped_event(
        db,
        catalogue.monitoring,
        minutes=1,
        raw_pointer={"url": secret_url, "password": "hunter2"},
        summary=HOSTILE,
    )
    inherited = _mapped_event(db, catalogue.logging, minutes=2)
    both = _mapped_event(db, catalogue.logging, catalogue.monitoring, minutes=3)
    _mapped_event(db, catalogue.access, minutes=4)  # not linked to B.1
    db.commit()

    client = catalogue.keen.client(catalogue.keen.user("events.read"))
    evidence = client.get(f"/v1/controls/{catalogue.monitoring.id}/evidence").json()
    assert evidence["total"] == 3
    by_event = {item["event_id"]: item for item in evidence["items"]}
    assert set(by_event) == {str(direct.id), str(inherited.id), str(both.id)}

    first = by_event[str(direct.id)]
    assert first["summary"] == HOSTILE
    assert first["inherited"] is False and first["inherited_from"] is None
    assert "hunter2" not in str(first) and "abc123" not in str(first)
    assert first["raw_pointer"]["password"] == REDACTED
    assert first["source_url"].startswith(f"https://{REDACTED}@ci.example/job/1")

    crosswalk = by_event[str(inherited.id)]
    assert crosswalk["inherited"] is True
    assert crosswalk["inherited_from"] == {
        "control_id": str(catalogue.logging.id),
        "framework": "A",
        "ref": "A.8.15",
    }
    # An event mapped directly and through the crosswalk appears once, as direct.
    assert by_event[str(both.id)]["inherited"] is False
    assert by_event[str(both.id)]["rationale"] == "rule B.1"

    page = client.get(
        f"/v1/controls/{catalogue.monitoring.id}/evidence",
        params={"limit": 1, "offset": 1},
    ).json()
    assert page["total"] == 3 and len(page["items"]) == 1
    newest_first = [item["event_id"] for item in evidence["items"]]
    assert page["items"][0]["event_id"] == newest_first[1]
    assert client.get("/v1/controls/nope/evidence").status_code == 400
    assert client.get(f"/v1/controls/{uuid.uuid4()}/evidence").status_code == 404
