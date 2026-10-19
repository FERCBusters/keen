"""SWOT regressions through password login, middleware and domain operations."""

from app.db.models import (
    EntityChangelog,
    IsmsSwot,
    IsmsSwotLink,
    Risk,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
)

from tests.test_assembled_authority_regressions import keen as keen
from tests.test_assembled_authority_regressions import unsafe


def create(client, **values):
    response = client.post(
        "/v1/isms/swot",
        json={"title": "Pandemic", "kind": "threat", **values},
        headers=unsafe(client),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_swot_permissions_csrf_validation_and_history(keen):
    manager = keen.client(keen.user("isms.manage"))
    reader = keen.client(keen.user("isms.read"))
    denied = keen.client(keen.user())
    anon = keen.client()
    assert anon.get("/v1/isms/swot").status_code == 401
    assert denied.get("/v1/isms/swot").status_code == 403
    assert (
        reader.post(
            "/v1/isms/swot", json={"title": "blocked"}, headers=unsafe(reader)
        ).status_code
        == 403
    )
    assert manager.post("/v1/isms/swot", json={"title": "blocked"}).status_code == 403
    assert (
        manager.post(
            "/v1/isms/swot",
            json={"title": "blocked"},
            headers={**unsafe(manager), "Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    assert keen.db.query(IsmsSwot).count() == 0
    for value in [0, 6, True, "3"]:
        assert (
            manager.post(
                "/v1/isms/swot",
                json={"title": "bad", "significance": value},
                headers=unsafe(manager),
            ).status_code
            == 422
        )
    root = create(manager, title="<img src=x onerror=alert(1)>", significance=5)
    assert reader.get("/v1/isms/swot/" + root["id"]).json()["title"] == root["title"]
    assert reader.get("/v1/isms/swot?kind=threat&significance=5").json()["total"] == 1
    assert reader.get("/v1/isms/swot?kind=opportunity").json()["total"] == 0
    path = "/v1/isms/swot/" + root["id"]
    assert (
        manager.patch(
            path,
            json={"status": "planned", "expected_version": 1},
            headers=unsafe(manager),
        ).status_code
        == 200
    )
    assert (
        manager.patch(
            path,
            json={"status": "completed", "expected_version": 1},
            headers=unsafe(manager),
        ).status_code
        == 409
    )
    assert (
        manager.patch(
            path, json={"kind": None, "expected_version": 2}, headers=unsafe(manager)
        ).status_code
        == 400
    )
    assert (
        manager.patch(
            path,
            json={
                "start_date": "2026-10-20",
                "target_date": "2026-10-01",
                "expected_version": 2,
            },
            headers=unsafe(manager),
        ).status_code
        == 400
    )
    assert reader.get(path).json()["status"] == "planned"
    assert (
        keen.db.query(EntityChangelog).filter_by(entity_type="isms_swot").count() == 2
    )
    assert (
        manager.delete(
            path + "?expected_version=1", headers=unsafe(manager)
        ).status_code
        == 409
    )
    assert (
        manager.delete(
            path + "?expected_version=2", headers=unsafe(manager)
        ).status_code
        == 200
    )


def test_swot_hierarchy_cycles_quadrants_and_delete(keen):
    client = keen.client(keen.user("isms.manage"))
    root = create(client)
    child = create(client, title="Supply chain", parent_id=root["id"])
    path = "/v1/isms/swot/" + root["id"]
    for payload in [
        {"parent_id": child["id"]},
        {"parent_id": root["id"]},
        {"kind": "strength"},
    ]:
        result = client.patch(
            path, json={**payload, "expected_version": 1}, headers=unsafe(client)
        )
        assert result.status_code == 400, result.text
    assert (
        client.delete(path + "?expected_version=1", headers=unsafe(client)).status_code
        == 409
    )
    for depth in range(2, 12):
        child = create(client, title=f"Level {depth + 1}", parent_id=child["id"])
    result = client.post(
        "/v1/isms/swot",
        json={"title": "Too deep", "kind": "threat", "parent_id": child["id"]},
        headers=unsafe(client),
    )
    assert result.status_code == 400, result.text


def test_links_require_target_access_and_do_not_leak_in_history(keen):
    admin = keen.client(keen.user(role="admin"))
    manager = keen.client(keen.user("isms.manage"))
    root = create(admin)
    category = RiskCategory(name="Category")
    keen.db.add(category)
    keen.db.flush()
    sub = RiskAssetSubcategory(name="Sub", category_id=category.id)
    keen.db.add(sub)
    keen.db.flush()
    asset = RiskAsset(name="Asset", category_id=category.id, subcategory_id=sub.id)
    keen.db.add(asset)
    keen.db.flush()
    risk = Risk(asset_id=asset.id, threat_summary="Restricted risk title")
    keen.db.add(risk)
    keen.db.commit()
    rejected = manager.post(
        "/v1/isms/swot",
        json={
            "title": "Restricted draft",
            "links": [{"target_type": "risk", "target_id": str(risk.id)}],
        },
        headers=unsafe(manager),
    )
    assert rejected.status_code == 404
    assert keen.db.query(IsmsSwot).count() == 1
    body = {"target_type": "risk", "target_id": str(risk.id), "expected_version": 1}
    path = "/v1/isms/swot/" + root["id"]
    assert (
        manager.post(path + "/links", json=body, headers=unsafe(manager)).status_code
        == 404
    )
    assert keen.db.query(IsmsSwotLink).count() == 0
    assert (
        admin.post(path + "/links", json=body, headers=unsafe(admin)).status_code == 200
    )
    assert len(admin.get(path).json()["links"]) == 1
    assert manager.get(path).json()["links"] == []
    assert (
        manager.get(f"/v1/isms/swot?target_type=risk&target_id={risk.id}").status_code
        == 404
    )
    assert manager.get("/v1/isms/swot-choices/risk").json()["items"] == []
    history = manager.get(path + "/changelog").text
    assert str(risk.id) not in history and "Restricted risk title" not in history
    assert (
        admin.get(f"/v1/isms/swot?target_type=risk&target_id={risk.id}").json()["total"]
        == 1
    )


def test_swot_questions_and_audit_scope_permissions(keen):
    from app.core.datetime_utils import utc_now_naive
    from app.db.models import Audit

    owner = keen.user("isms.manage", "isms.read", "audits.manage", "question.create")
    client = keen.client(owner)
    root = create(client)
    denied = keen.client(keen.user("question.create", "audits.manage"))
    qpath = f"/v1/questions/entities/isms_swot/{root['id']}"
    assert denied.get(qpath).status_code == 403
    assert (
        denied.post(qpath, json={"body": "blocked"}, headers=unsafe(denied)).status_code
        == 403
    )
    result = client.post(
        qpath, json={"body": "How do we test continuity?"}, headers=unsafe(client)
    )
    assert result.status_code == 200, result.text
    assert len(client.get(qpath).json()["threads"]) == 1
    audit = Audit(
        title="SWOT audit",
        framework_slug="ISO27001:2022",
        status="open",
        audit_type="internal",
        created_by_user_id=owner.id,
        created_at=utc_now_naive(),
        updated_at=utc_now_naive(),
    )
    keen.db.add(audit)
    keen.db.commit()
    scope = f"/v1/audits/{audit.id}/scope"
    payload = {"swot_ids": [root["id"]]}
    assert denied.put(scope, json=payload, headers=unsafe(denied)).status_code == 403
    result = client.put(scope, json=payload, headers=unsafe(client))
    assert result.status_code == 200, result.text
    assert result.json()["scoped_swot"][0]["id"] == root["id"]
    assert (
        client.delete(
            f"/v1/isms/swot/{root['id']}?expected_version=1", headers=unsafe(client)
        ).status_code
        == 409
    )
    # An unrelated scope edit preserves explicit SWOT scope.
    assert (
        client.put(scope, json={}, headers=unsafe(client)).json()["scoped_swot"][0][
            "id"
        ]
        == root["id"]
    )
    audit.status = "completed"
    keen.db.commit()
    assert (
        client.put(scope, json={"swot_ids": []}, headers=unsafe(client)).status_code
        == 409
    )


def test_framework_mappings_sampling_and_objective_dates(keen):
    from app.db.models import (
        AuditEvidence,
        ControlItem,
        Framework,
        FrameworkClause,
    )

    client = keen.client(keen.user("isms.manage", "audits.manage", "isms.read"))
    fw = Framework(slug="swot-test", name="SWOT test")
    keen.db.add(fw)
    keen.db.flush()
    control = ControlItem(
        framework_slug=fw.slug, ref="A.1", title="Continuity", type="annex_control"
    )
    clause = FrameworkClause(framework_slug=fw.slug, ref="6.1", title="Planning")
    keen.db.add_all([control, clause])
    keen.db.commit()
    response = client.post(
        "/v1/isms/swot?framework=swot-test",
        json={
            "title": "Continuity",
            "kind": "strength",
            "controls": ["A.1"],
            "clauses": ["6.1"],
        },
        headers=unsafe(client),
    )
    assert response.status_code == 200, response.text
    item = response.json()
    assert item["controls"][0]["ref"] == "A.1"
    assert (
        client.get("/v1/isms/swot?framework=swot-test&control=A.1&clause=6.1").json()[
            "total"
        ]
        == 1
    )
    assert (
        client.get(f"/v1/controls/{control.id}/isms?framework=swot-test").json()[
            "swot"
        ][0]["id"]
        == item["id"]
    )
    before = keen.db.query(IsmsSwot).count()
    assert (
        client.post(
            "/v1/isms/swot?framework=swot-test",
            json={"title": "Rejected", "controls": ["missing"]},
            headers=unsafe(client),
        ).status_code
        == 400
    )
    assert keen.db.query(IsmsSwot).count() == before
    audit = client.post(
        "/v1/audits",
        json={"title": "Continuity review", "framework_slug": fw.slug},
        headers=unsafe(client),
    ).json()
    sample = client.post(
        f"/v1/audits/{audit['id']}/evidence",
        json={"entity_type": "isms_swot", "entity_id": item["id"]},
        headers=unsafe(client),
    )
    assert sample.status_code == 200, sample.text
    assert keen.db.query(AuditEvidence).count() == 1
    for dates in [
        {"start_date": "2026-11-01", "completion_target_date": "2026-10-01"},
        {"completion_target_date": "Quarterly"},
        {"completion_target_date": "20261101"},
    ]:
        result = client.post(
            "/v1/isms/objectives",
            json={"requirement": "R", "goal": "G", **dates},
            headers=unsafe(client),
        )
        assert result.status_code == 400, result.text
    result = client.post(
        "/v1/isms/objectives",
        json={
            "requirement": "R",
            "goal": "G",
            "start_date": "2026-10-01",
            "completion_target_date": "2026-11-01",
            "status": "under_review",
        },
        headers=unsafe(client),
    )
    assert result.status_code == 200, result.text
    assert client.get("/v1/isms/objectives?status=under_review").json()["total"] == 1
    assert client.get("/v1/isms/objectives?status=planned").json()["total"] == 0


def test_swot_audit_scope_survives_scheduled_instantiation(keen):
    from app.db.models import Audit, AuditSwotScope
    from app.services.audit_schedules import _copy_template_scope_and_attendees

    client = keen.client(keen.user("isms.manage"))
    item = create(client)
    template = Audit(
        title="Template", framework_slug="ISO27001:2022", status="template"
    )
    audit = Audit(title="Occurrence", framework_slug="ISO27001:2022", status="open")
    keen.db.add_all([template, audit])
    keen.db.flush()
    import uuid

    keen.db.add(AuditSwotScope(audit_id=template.id, swot_id=uuid.UUID(item["id"])))
    keen.db.flush()
    _copy_template_scope_and_attendees(keen.db, template=template, audit=audit)
    keen.db.flush()
    assert keen.db.query(AuditSwotScope).filter_by(audit_id=audit.id).count() == 1


def test_swot_mapping_ids_are_framework_scoped_and_preserve_other_frameworks(keen):
    from app.db.models import ControlItem, Framework, FrameworkClause

    client = keen.client(keen.user("isms.manage", "isms.read"))
    records = {}
    for namespace in ("swot-a", "swot-b"):
        keen.db.add(Framework(slug=namespace, name=namespace))
        keen.db.flush()
        control = ControlItem(
            framework_slug=namespace, ref="A.1", title=namespace, type="annex_control"
        )
        clause = FrameworkClause(framework_slug=namespace, ref="6.1", title=namespace)
        keen.db.add_all([control, clause])
        keen.db.flush()
        records[namespace] = {
            "controls": [str(control.id)],
            "clauses": [str(clause.id)],
        }
    keen.db.commit()
    response = client.post(
        "/v1/isms/swot?framework=swot-a",
        json={"title": "Stable team", "kind": "strength", **records["swot-a"]},
        headers=unsafe(client),
    )
    assert response.status_code == 200, response.text
    item = response.json()
    assert item["start_date"] is None and item["target_date"] is None
    path = "/v1/isms/swot/" + item["id"]
    response = client.patch(
        path + "?framework=swot-b",
        json={**records["swot-b"], "expected_version": item["version"]},
        headers=unsafe(client),
    )
    assert response.status_code == 200, response.text
    version = response.json()["version"]
    for field in ("controls", "clauses"):
        response = client.patch(
            path + "?framework=swot-a",
            json={field: records["swot-b"][field], "expected_version": version},
            headers=unsafe(client),
        )
        assert response.status_code == 400, response.text
    for namespace in records:
        current = client.get(path + "?framework=" + namespace).json()
        assert current["version"] == version
        for field in ("controls", "clauses"):
            assert [record["id"] for record in current[field]] == records[namespace][
                field
            ]
    response = client.patch(
        path + "?framework=swot-b",
        json={"controls": [], "clauses": [], "expected_version": version},
        headers=unsafe(client),
    )
    assert response.status_code == 200, response.text
    assert client.get(path + "?framework=swot-b").json()["controls"] == []
    assert (
        client.get(path + "?framework=swot-a").json()["controls"][0]["id"]
        == records["swot-a"]["controls"][0]
    )


def test_draft_relationships_create_atomically_and_deduplicate(keen):
    from uuid import uuid4

    from app.db.models import IsmsObjective

    client = keen.client(keen.user("isms.manage"))
    objective = IsmsObjective(goal="Measurable response")
    keen.db.add(objective)
    keen.db.commit()
    link = {"target_type": "objective", "target_id": str(objective.id)}
    invalid = {"target_type": "risk", "target_id": str(uuid4())}
    before_history = keen.db.query(EntityChangelog).count()
    for extra in [
        {"links": [link, invalid]},
        {"links": [link], "controls": ["missing-control"]},
    ]:
        response = client.post(
            "/v1/isms/swot",
            json={"title": "Atomic draft", **extra},
            headers=unsafe(client),
        )
        assert response.status_code in (400, 404), response.text
        keen.db.expire_all()
        assert keen.db.query(IsmsSwot).count() == 0
        assert keen.db.query(IsmsSwotLink).count() == 0
        assert keen.db.query(EntityChangelog).count() == before_history
    created = create(client, links=[link, link])
    assert len(created["links"]) == 1
    assert created["links"][0]["target_id"] == str(objective.id)
    assert keen.db.query(IsmsSwotLink).count() == 1
    response = client.patch(
        f"/v1/isms/swot/{created['id']}",
        json={"expected_version": 1, "links": []},
        headers=unsafe(client),
    )
    assert response.status_code == 400
    assert keen.db.query(IsmsSwotLink).count() == 1
    history = client.get(f"/v1/isms/swot/{created['id']}/changelog").text
    assert str(objective.id) not in history
    assert objective.goal not in history


def test_draft_relationships_obey_disabled_features_and_payload_bound(keen):
    from app.db.models import IsmsObjective
    from app.services.features import update

    admin = keen.user(role="admin")
    client = keen.client(admin)
    objective = IsmsObjective(goal="Disabled objective")
    keen.db.add(objective)
    keen.db.commit()
    link = {"target_type": "objective", "target_id": str(objective.id)}
    response = client.post(
        "/v1/isms/swot",
        json={"title": "Too many", "links": [link] * 201},
        headers=unsafe(client),
    )
    assert response.status_code == 422
    update(keen.db, admin, 0, {"objectives": False})
    response = client.post(
        "/v1/isms/swot",
        json={"title": "Disabled", "links": [link]},
        headers=unsafe(client),
    )
    assert response.status_code == 404
    assert keen.db.query(IsmsSwot).count() == 0
    assert keen.db.query(IsmsSwotLink).count() == 0


def test_soa_swot_framework_scope_permissions_and_feature_gate(keen):
    from app.db.models import ControlItem, Framework, FrameworkClause

    admin = keen.client(keen.user(role="admin"))
    reader = keen.client(keen.user("isms.read"))
    denied = keen.client(keen.user())
    keen.db.add(Framework(slug="soa-test", name="SoA test"))
    keen.db.flush()
    control = ControlItem(
        framework_slug="soa-test", ref="C1", title="Control", type="annex_control"
    )
    clause = FrameworkClause(framework_slug="soa-test", ref="6.2", title="Planning")
    keen.db.add_all([control, clause])
    keen.db.commit()
    for field, target in (("controls", control), ("clauses", clause)):
        response = admin.post(
            "/v1/isms/swot?framework=soa-test",
            json={"title": field, field: [str(target.id)]},
            headers=unsafe(admin),
        )
        assert response.status_code == 200, response.text
    path = "/v1/statement-of-applicability?framework=soa-test"
    assert keen.client().get(path).status_code == 401
    assert len(reader.get(path).json()["swot_items"]) == 2
    assert (
        reader.get("/v1/statement-of-applicability?framework=other").json()[
            "swot_items"
        ]
        == []
    )
    assert denied.get(path).json()["swot_items"] == []
    response = admin.put(
        "/v1/admin/features",
        json={"version": 0, "changes": {"swot": False}},
        headers=unsafe(admin),
    )
    assert response.status_code == 200, response.text
    assert reader.get(path).json()["swot_items"] == []


def test_a_relationship_is_removed_only_at_the_current_version(keen):
    from app.db.models import IsmsObjective

    manager = keen.client(keen.user("isms.manage"))
    reader = keen.client(keen.user("isms.read"))
    objective = IsmsObjective(goal="Confidential objective goal")
    keen.db.add(objective)
    keen.db.commit()
    item = create(
        manager, links=[{"target_type": "objective", "target_id": str(objective.id)}]
    )
    path = f"/v1/isms/swot/{item['id']}/links/objective/{objective.id}"

    def remove(client, version=None, headers=None):
        params = {} if version is None else {"expected_version": version}
        return client.delete(
            path, params=params, headers=unsafe(client) if headers is None else headers
        )

    # Readers, requests without the token and stale or missing versions are
    # refused, and the relationship stays.
    assert remove(reader, item["version"]).status_code == 403
    assert remove(manager, item["version"], headers={}).status_code == 403
    assert remove(manager).status_code == 422
    stale = remove(manager, item["version"] + 1)
    assert stale.status_code == 409
    assert keen.db.query(IsmsSwotLink).count() == 1
    unknown = manager.delete(
        f"/v1/isms/swot/{item['id']}/links/control/{objective.id}",
        params={"expected_version": item["version"]},
        headers=unsafe(manager),
    )
    assert unknown.status_code == 422

    removed = remove(manager, item["version"])

    assert removed.status_code == 200, removed.text
    assert removed.json() == {"ok": True, "version": item["version"] + 1}
    assert keen.db.query(IsmsSwotLink).count() == 0
    assert reader.get(f"/v1/isms/swot/{item['id']}").json()["links"] == []
    history = reader.get(f"/v1/isms/swot/{item['id']}/changelog").json()
    entry = next(e for e in history["items"] if e["action"] == "relationship_removed")
    assert {c["field"]: c["after"] for c in entry["changes"]} == {
        "relationship_type": "objective",
        "version": item["version"] + 1,
    }
    # The history names the kind of relationship, never the target.
    assert (entry["request_method"], entry["request_path"]) == (
        "DELETE",
        f"/v1/isms/swot/{item['id']}/links",
    )
    text = str(history)
    assert str(objective.id) not in text and objective.goal not in text
