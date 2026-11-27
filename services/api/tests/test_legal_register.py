"""The legal register through sign-in, middleware and its domain operations:
items and their categories, framework links, related records, assessments,
the export, audit sampling, questions and assessment reminders."""

import csv
import io
import uuid
from datetime import date, datetime, timedelta

import pytest
from app.core.config import settings
from app.db.models import (
    AuditEvidence,
    ControlItem,
    EntityChangelog,
    Event,
    Framework,
    FrameworkClause,
    IsmsDocument,
    IsmsLegalAssessment,
    IsmsLegalCategory,
    IsmsLegalItem,
    IsmsLegalItemLink,
    Risk,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
)
from app.services.isms import legal_register as service
from app.services.schedules import today_for_schedule

from tests.test_assembled_authority_regressions import keen as keen  # noqa: F401
from tests.test_assembled_authority_regressions import unsafe

# Imported fixture: SMTP classes replaced by a recorder, with mail configured.
from tests.test_mailer import smtp  # noqa: F401

BASE = "/v1/isms/legal-register"
HOSTILE = "<img src=x onerror=alert(1)>"
ISO = "iso_27001_2022"


def create(client, expect=200, framework=None, **values):
    response = client.post(
        BASE + (f"?framework={framework}" if framework else ""),
        json={"title": "Data Protection Act 2018", **values},
        headers=unsafe(client),
    )
    assert response.status_code == expect, response.text
    return response.json()


def patch(client, item, expect=200, framework=None, **values):
    response = client.patch(
        f"{BASE}/{item['id']}" + (f"?framework={framework}" if framework else ""),
        json={"expected_version": item["version"], **values},
        headers=unsafe(client),
    )
    assert response.status_code == expect, response.text
    return response.json()


def assess(client, item, expect=200, **values):
    response = client.post(
        f"{BASE}/{item['id']}/assessments",
        json={
            "assessed_on": "2026-06-01",
            "outcome": "compliant",
            "expected_version": item["version"],
            **values,
        },
        headers=unsafe(client),
    )
    assert response.status_code == expect, response.text
    return response.json()


def framework(db, slug, control="A.5.31", clause="4.2"):
    db.add(Framework(slug=slug, name=slug))
    db.flush()
    control_row = ControlItem(
        framework_slug=slug, ref=control, title="Legal requirements", type="annex"
    )
    clause_row = FrameworkClause(framework_slug=slug, ref=clause, title="Parties")
    db.add_all([control_row, clause_row])
    db.commit()
    return control_row, clause_row


def related(db):
    """A document, an asset and a risk an item can relate to."""
    category = RiskCategory(name="Category " + uuid.uuid4().hex[:6])
    db.add(category)
    db.flush()
    sub = RiskAssetSubcategory(name="Sub", category_id=category.id)
    db.add(sub)
    db.flush()
    asset = RiskAsset(name="Payroll", category_id=category.id, subcategory_id=sub.id)
    document = IsmsDocument(title="Privacy policy", document_type="policy")
    db.add_all([asset, document])
    db.flush()
    risk = Risk(asset_id=asset.id, threat_summary="Regulator fine")
    db.add(risk)
    db.commit()
    return document, asset, risk


# --- Access -------------------------------------------------------------------------


def test_reading_and_changing_need_isms_permissions_and_csrf(keen):  # noqa: F811
    manager = keen.client(keen.user("isms.manage"))
    reader = keen.client(keen.user("isms.read"))
    item = create(manager)
    paths = [
        BASE,
        BASE + "/meta",
        BASE + "/export.csv",
        BASE + "/choices/document",
        f"{BASE}/{item['id']}",
    ]
    for path in paths:
        refused = keen.client().get(path)
        assert (refused.status_code, refused.json()) == (
            401,
            {"detail": "Not authenticated"},
        ), path
        refused = keen.client(keen.user()).get(path)
        assert (refused.status_code, refused.json()) == (
            403,
            {"detail": "isms.read permission required"},
        ), path
        assert reader.get(path).status_code == 200, path
    category = keen.db.query(IsmsLegalCategory).first()
    target = uuid.uuid4()
    writes = [
        ("post", BASE, {"title": "blocked"}),
        ("patch", f"{BASE}/{item['id']}", {"title": "blocked", "expected_version": 1}),
        ("delete", f"{BASE}/{item['id']}?expected_version=1", None),
        ("post", BASE + "/categories", {"name": "blocked"}),
        ("patch", f"{BASE}/categories/{category.id}", {"name": "blocked"}),
        ("delete", f"{BASE}/categories/{category.id}", None),
        (
            "post",
            f"{BASE}/{item['id']}/links",
            {"target_type": "asset", "target_id": str(target), "expected_version": 1},
        ),
        (
            "delete",
            f"{BASE}/{item['id']}/links/asset/{target}?expected_version=1",
            None,
        ),
        (
            "post",
            f"{BASE}/{item['id']}/assessments",
            {
                "assessed_on": "2026-01-01",
                "outcome": "compliant",
                "expected_version": 1,
            },
        ),
        (
            "delete",
            f"{BASE}/{item['id']}/assessments/{target}?expected_version=1",
            None,
        ),
    ]
    for method, path, body in writes:
        kwargs = {"json": body} if body is not None else {}
        refused = getattr(reader, method)(path, headers=unsafe(reader), **kwargs)
        assert (refused.status_code, refused.json()) == (
            403,
            {"detail": "isms.manage permission required"},
        ), (method, path)
        refused = getattr(manager, method)(path, **kwargs)
        assert (refused.status_code, refused.json()) == (
            403,
            {"detail": "CSRF token missing or invalid"},
        ), (method, path)
    # Nothing a refused request named was stored or removed.
    assert [row.title for row in keen.db.query(IsmsLegalItem)] == [item["title"]]
    assert keen.db.query(IsmsLegalCategory).filter_by(name="blocked").count() == 0
    assert keen.db.get(IsmsLegalCategory, category.id) is not None


def test_the_domain_operations_check_access_themselves(keen):  # noqa: F811
    # The operations are also called by other features, so each refuses a
    # caller without ISMS access whatever route it arrived by.
    outsider = keen.user("events.read")
    with pytest.raises(service.DomainError) as refused:
        service.list_items(user=outsider, db=keen.db)
    assert (refused.value.status_code, refused.value.detail) == (
        403,
        "ISMS access required",
    )
    reader = keen.user("isms.read")
    with pytest.raises(service.DomainError) as refused:
        service.save_category(
            payload=service.CategoryPayload(name="x"), user=reader, db=keen.db
        )
    assert refused.value.status_code == 403
    assert service.list_items(user=reader, db=keen.db)["total"] == 0


def test_a_risk_manager_manages_the_register_as_other_isms_registers(keen):  # noqa: F811
    client = keen.client(keen.user("risk.manage"))
    item = create(client, title="Bribery Act 2010")
    assert client.get(f"{BASE}/{item['id']}").json()["title"] == "Bribery Act 2010"


# --- Items --------------------------------------------------------------------------


def test_an_item_keeps_its_fields_link_scope_and_category(keen):  # noqa: F811
    owner = keen.user()
    manager = keen.client(keen.user("isms.manage"))
    reader = keen.client(keen.user("isms.read"))
    seeded = keen.db.query(IsmsLegalCategory).count()
    item = create(
        manager,
        title="  Data   Protection\nAct 2018 ",
        url="https://www.legislation.gov.uk/ukpga/2018/12/contents",
        category_name="data protection AND privacy",
        jurisdiction="United Kingdom",
        authority="Information Commissioner's Office",
        requirement=HOSTILE,
        applicability="We process customer personal data.",
        in_scope=False,
        owner_user_id=str(owner.id),
        next_assessment_date="2027-01-31",
    )
    # A category is matched whatever its case; no second one is added.
    assert keen.db.query(IsmsLegalCategory).count() == seeded
    assert item["category"]["name"] == "Data protection and privacy"
    assert item["category_id"] == item["category"]["id"]
    # A title is one line, since it reaches email subjects and calendars.
    assert item["title"] == "Data Protection Act 2018"
    assert item["display"] == item["title"]
    assert item["url"] == "https://www.legislation.gov.uk/ukpga/2018/12/contents"
    assert item["requirement"] == HOSTILE
    assert (item["in_scope"], item["compliance_status"]) == (False, "not_assessed")
    assert item["owner"]["username"] == owner.username
    assert item["owner_user_id"] == str(owner.id)
    assert (item["last_assessed_date"], item["next_assessment_date"]) == (
        None,
        "2027-01-31",
    )
    assert (item["version"], item["links"], item["assessments"]) == (1, [], [])
    assert reader.get(f"{BASE}/{item['id']}").json() == item

    changed = patch(
        manager,
        item,
        url=None,
        in_scope=True,
        category_name="Trade sanctions",
        owner_user_id=None,
        next_assessment_date=None,
        applicability="",
    )
    assert (changed["url"], changed["in_scope"], changed["version"]) == (None, True, 2)
    assert changed["category"]["name"] == "Trade sanctions"
    assert keen.db.query(IsmsLegalCategory).count() == seeded + 1
    assert (changed["owner"], changed["owner_user_id"]) == (None, None)
    assert changed["next_assessment_date"] is None
    # Fields a save does not name stay as they were.
    assert changed["requirement"] == HOSTILE
    assert changed["jurisdiction"] == "United Kingdom"
    # Naming neither an id nor a name leaves the item without a category.
    cleared = patch(manager, changed, category_name="  ")
    assert (cleared["category"], cleared["category_id"]) == (None, None)
    by_id = patch(manager, cleared, category_id=item["category_id"])
    assert by_id["category"]["name"] == "Data protection and privacy"
    actions = [
        row.action
        for row in keen.db.query(EntityChangelog)
        .filter_by(entity_type="isms_legal_item")
        .order_by(EntityChangelog.changed_at, EntityChangelog.id)
    ]
    assert sorted(actions) == ["created", "updated", "updated", "updated"]
    history = reader.get(f"/v1/isms/legal-item/{item['id']}/changelog").json()
    assert history["total"] == 4
    first = history["items"][-1]
    assert first["entity_title"] == "Data Protection Act 2018"


def test_an_item_refuses_values_it_cannot_keep(keen):  # noqa: F811
    manager = keen.client(keen.user("isms.manage"))
    inactive = keen.user()
    inactive.is_active = False
    keen.db.commit()
    for values, status, detail in [
        ({"title": "   "}, 400, "title is required"),
        ({"category_id": str(uuid.uuid4())}, 400, "Unknown legal register category"),
        ({"owner_user_id": str(uuid.uuid4())}, 400, "Unknown active owner"),
        ({"owner_user_id": str(inactive.id)}, 400, "Unknown active owner"),
        ({"in_scope": None}, 400, "in_scope cannot be empty"),
        ({"controls": ["missing"]}, 400, "Unknown control: missing"),
        ({"clauses": ["missing"]}, 400, "Unknown clause: missing"),
    ]:
        refused = create(manager, expect=status, **values)
        assert refused == {"detail": detail}, values
    for values, kind in [
        ({"url": "javascript:alert(1)"}, "value_error"),
        ({"url": "https://user:secret@example.test/"}, "value_error"),
        ({"in_scope": "yes"}, "bool_type"),
        ({"title": ""}, "string_too_short"),
        ({"title": "x" * 257}, "string_too_long"),
        ({"surprise": 1}, "extra_forbidden"),
        ({"next_assessment_date": "soon"}, "date_from_datetime_parsing"),
    ]:
        refused = create(manager, expect=422, **values)
        assert refused["detail"][0]["type"] == kind, values
    refused = manager.post(BASE, json={"requirement": "x"}, headers=unsafe(manager))
    assert (refused.status_code, refused.json()) == (
        400,
        {"detail": "title is required"},
    )
    assert keen.db.query(IsmsLegalItem).count() == 0
    refused = manager.get(BASE + "/not-a-uuid")
    assert refused.status_code == 422
    refused = manager.get(f"{BASE}/{uuid.uuid4()}")
    assert (refused.status_code, refused.json()) == (
        404,
        {"detail": "Legal register item not found"},
    )


def test_a_change_needs_the_version_it_was_read_at(keen):  # noqa: F811
    manager = keen.client(keen.user("isms.manage"))
    item = create(manager)
    newer = patch(manager, item, authority="ICO")
    stale = {"detail": "This legal register item changed. Reload before saving."}
    assert patch(manager, item, expect=409, authority="Other") == stale
    refused = manager.patch(
        f"{BASE}/{item['id']}", json={"authority": "x"}, headers=unsafe(manager)
    )
    assert (refused.status_code, refused.json()) == (409, stale)
    assert assess(manager, item, expect=409) == stale
    refused = manager.delete(
        f"{BASE}/{item['id']}?expected_version=1", headers=unsafe(manager)
    )
    assert (refused.status_code, refused.json()) == (409, stale)
    assert manager.get(f"{BASE}/{item['id']}").json()["authority"] == "ICO"
    deleted = manager.delete(
        f"{BASE}/{item['id']}?expected_version={newer['version']}",
        headers=unsafe(manager),
    )
    assert (deleted.status_code, deleted.json()) == (200, {"ok": True})
    assert keen.db.query(IsmsLegalItem).count() == 0
    last = (
        keen.db.query(EntityChangelog)
        .filter_by(entity_type="isms_legal_item", action="deleted")
        .one()
    )
    assert last.entity_id == uuid.UUID(item["id"])


# --- Controls and clauses -------------------------------------------------------------


def test_a_new_item_starts_with_the_control_for_legal_requirements(keen):  # noqa: F811
    manager = keen.client(keen.user("isms.manage"))
    control, clause = framework(keen.db, ISO)
    other = ControlItem(framework_slug=ISO, ref="A.5.1", title="Policies", type="annex")
    keen.db.add(other)
    framework(keen.db, "legal-plain", control="L.1", clause="1.1")
    keen.db.commit()
    meta = manager.get(f"{BASE}/meta?framework={ISO}").json()
    assert [c["ref"] for c in meta["default_controls"]] == ["A.5.31"]
    assert (meta["framework"], meta["can_manage"]) == (ISO, True)
    assert meta["link_types"] == ["document", "asset", "risk"]
    assert [s["code"] for s in meta["statuses"]] == [
        "not_assessed",
        "compliant",
        "partially_compliant",
        "non_compliant",
    ]
    assert [s["label"] for s in meta["outcomes"]] == [
        "Compliant",
        "Partially compliant",
        "Non-compliant",
    ]
    # A framework without such a control starts an item with none.
    plain = manager.get(f"{BASE}/meta?framework=legal-plain").json()
    assert plain["default_controls"] == []
    assert (
        keen.client(keen.user("isms.read")).get(BASE + "/meta").json()["can_manage"]
        is False
    )

    started = create(manager, framework=ISO)
    assert [c["ref"] for c in started["controls"]] == ["A.5.31"]
    assert (started["control_count"], started["clause_count"]) == (1, 0)
    # A save that names its controls, even none, is taken at its word.
    assert create(manager, framework=ISO, controls=[])["controls"] == []
    chosen = create(manager, framework=ISO, controls=["A.5.1"], clauses=["4.2"])
    assert [c["ref"] for c in chosen["controls"]] == ["A.5.1"]
    assert [c["ref"] for c in chosen["clauses"]] == ["4.2"]
    assert create(manager, framework="legal-plain")["controls"] == []
    # An update that names no controls leaves them; one that does replaces them.
    kept = patch(manager, started, framework=ISO, authority="ICO")
    assert [c["ref"] for c in kept["controls"]] == ["A.5.31"]
    emptied = patch(manager, kept, framework=ISO, controls=[])
    assert emptied["controls"] == []

    listed = manager.get(f"{BASE}?framework={ISO}&control=A.5.1&clause=4.2").json()
    assert [row["id"] for row in listed["items"]] == [chosen["id"]]
    assert manager.get(f"{BASE}?framework={ISO}&control=A.5.31").json()["total"] == 0
    refused = manager.get(f"{BASE}?framework={ISO}&control=nope")
    assert (refused.status_code, refused.json()) == (
        400,
        {"detail": "Unknown control: nope"},
    )
    refused = manager.get(f"{BASE}?framework={ISO}&clause=nope")
    assert (refused.status_code, refused.json()) == (
        400,
        {"detail": "Unknown clause: nope"},
    )
    # The control and the clause list the item from their own side.
    seen = manager.get(f"/v1/controls/{other.id}/isms?framework={ISO}").json()
    assert [row["id"] for row in seen["legal_items"]] == [chosen["id"]]
    assert seen["legal_items"][0]["title"] == chosen["title"]
    assert "controls" not in seen["legal_items"][0]
    seen = manager.get(f"/v1/clauses/{clause.id}/isms?framework={ISO}").json()
    assert [row["id"] for row in seen["legal_items"]] == [chosen["id"]]
    assert seen["total"] == 1


# --- Related documents, assets and risks ----------------------------------------------


def test_related_records_show_from_both_sides_with_the_readers_access(keen):  # noqa: F811
    document, asset, risk = related(keen.db)
    both = keen.client(keen.user("isms.manage", "risk.read"))
    isms_only = keen.client(keen.user("isms.manage"))
    links = [
        {"target_type": "document", "target_id": str(document.id)},
        {"target_type": "asset", "target_id": str(asset.id)},
        {"target_type": "risk", "target_id": str(risk.id)},
    ]
    # A person who cannot read a record cannot relate an item to it.
    refused = create(isms_only, expect=404, links=links)
    assert refused == {"detail": "Related item not available"}
    assert keen.db.query(IsmsLegalItem).count() == 0

    item = create(both, links=[*links, links[0]])
    assert [(link["target_type"], link["title"]) for link in item["links"]] == [
        ("asset", "Payroll"),
        ("document", "Privacy policy"),
        ("risk", "Regulator fine"),
    ]
    assert item["links"][1]["url"] == f"/isms-document.html?id={document.id}"
    created = (
        keen.db.query(EntityChangelog)
        .filter_by(entity_type="isms_legal_item", action="created")
        .one()
    )
    # The history names the kinds of record, never a record's own title.
    assert "Regulator fine" not in str(created.changes)
    assert "Regulator fine" not in str(created.summary)
    # The risk is withheld from a reader who cannot open it.
    seen = isms_only.get(f"{BASE}/{item['id']}").json()
    assert [link["target_type"] for link in seen["links"]] == ["asset", "document"]

    for kind, row in (("document", document), ("asset", asset), ("risk", risk)):
        listed = both.get(f"{BASE}?target_type={kind}&target_id={row.id}").json()
        assert [found["id"] for found in listed["items"]] == [item["id"]], kind
    refused = isms_only.get(f"{BASE}?target_type=risk&target_id={risk.id}")
    assert (refused.status_code, refused.json()) == (
        404,
        {"detail": "Related item not available"},
    )
    for query in ("target_type=asset", f"target_id={asset.id}", "target_type=person"):
        suffix = f"&target_id={asset.id}" if query == "target_type=person" else ""
        refused = both.get(f"{BASE}?{query}{suffix}")
        assert (refused.status_code, refused.json()) == (
            400,
            {"detail": "Choose a relationship type and item"},
        ), query

    found = both.get(BASE + "/choices/document?q=privacy").json()["items"]
    assert [(c["target_id"], c["title"]) for c in found] == [
        (str(document.id), "Privacy policy")
    ]
    assert both.get(BASE + "/choices/document?q=zzz").json() == {"items": []}
    assert isms_only.get(BASE + "/choices/risk").json() == {"items": []}
    refused = both.get(BASE + "/choices/person")
    assert (refused.status_code, refused.json()) == (
        400,
        {"detail": "Unknown relationship type"},
    )


def test_relationships_are_added_and_removed_one_at_a_time(keen):  # noqa: F811
    document, asset, risk = related(keen.db)
    manager = keen.client(keen.user("isms.manage"))
    item = create(manager)
    path = f"{BASE}/{item['id']}/links"

    def link(kind, row, version):
        return manager.post(
            path,
            json={
                "target_type": kind,
                "target_id": str(row.id),
                "expected_version": version,
            },
            headers=unsafe(manager),
        )

    # An existing item's relationships change through their own actions.
    refused = patch(manager, item, expect=400, links=[])
    assert refused == {
        "detail": "Use relationship actions for an existing legal register item"
    }
    added = link("document", document, 1)
    assert (added.status_code, added.json()) == (200, {"ok": True, "version": 2})
    # Linking the same record again keeps one relationship.
    again = link("document", document, 2)
    assert again.json() == {"ok": True, "version": 3}
    assert keen.db.query(IsmsLegalItemLink).count() == 1
    refused = link("asset", asset, 1)
    assert refused.status_code == 409
    refused = link("risk", risk, 3)
    assert (refused.status_code, refused.json()) == (
        404,
        {"detail": "Related item not available"},
    )
    refused = manager.post(
        path,
        json={
            "target_type": "person",
            "target_id": str(asset.id),
            "expected_version": 3,
        },
        headers=unsafe(manager),
    )
    assert refused.status_code == 422
    assert link("asset", asset, 3).json()["version"] == 4
    removed = manager.delete(
        f"{path}/document/{document.id}?expected_version=4", headers=unsafe(manager)
    )
    assert (removed.status_code, removed.json()) == (200, {"ok": True, "version": 5})
    refused = manager.delete(
        f"{path}/person/{document.id}?expected_version=5", headers=unsafe(manager)
    )
    assert refused.status_code == 422
    seen = manager.get(f"{BASE}/{item['id']}").json()
    assert [(row["target_type"], row["title"]) for row in seen["links"]] == [
        ("asset", "Payroll")
    ]
    entries = (
        keen.db.query(EntityChangelog)
        .filter(EntityChangelog.action.like("relationship_%"))
        .all()
    )
    assert sorted(entry.action for entry in entries) == [
        "relationship_added",
        "relationship_added",
        "relationship_added",
        "relationship_removed",
    ]
    # A removal's own path names its target; the history records the collection.
    assert {entry.request_path for entry in entries} == {path}
    # A record deleted elsewhere drops out of the item without an error.
    keen.db.query(Risk).delete()
    keen.db.query(RiskAsset).filter_by(id=asset.id).delete()
    keen.db.commit()
    assert manager.get(f"{BASE}/{item['id']}").json()["links"] == []


def test_an_item_holds_a_bounded_number_of_relationships(keen, monkeypatch):  # noqa: F811
    document, asset, _ = related(keen.db)
    manager = keen.client(keen.user("isms.manage"))
    item = create(manager, links=[{"target_type": "asset", "target_id": str(asset.id)}])
    monkeypatch.setattr(service, "MAX_LINKS", 1)
    refused = manager.post(
        f"{BASE}/{item['id']}/links",
        json={
            "target_type": "document",
            "target_id": str(document.id),
            "expected_version": 1,
        },
        headers=unsafe(manager),
    )
    assert (refused.status_code, refused.json()) == (
        400,
        {"detail": "A legal register item supports up to 1 relationships"},
    )
    assert keen.db.query(IsmsLegalItemLink).count() == 1


# --- Assessments ----------------------------------------------------------------------


def test_assessments_set_the_items_status_and_dates(keen):  # noqa: F811
    assessor = keen.user()
    account = keen.user("isms.manage")
    manager = keen.client(account)
    item = create(manager, next_assessment_date="2026-06-01")
    first = assess(
        manager,
        item,
        outcome="partially_compliant",
        notes="  Retention schedule missing.  ",
        next_assessment_date="2026-12-01",
    )
    assert (first["compliance_status"], first["last_assessed_date"]) == (
        "partially_compliant",
        "2026-06-01",
    )
    assert (first["next_assessment_date"], first["version"]) == ("2026-12-01", 2)
    (entry,) = first["assessments"]
    assert (entry["outcome"], entry["notes"]) == (
        "partially_compliant",
        "Retention schedule missing.",
    )
    # The person saving an assessment is its assessor unless another is named.
    assert entry["assessed_by"]["username"] == account.username

    # An assessment that names no next date leaves the one the item has, and an
    # earlier-dated one never replaces the newest as the item's status.
    earlier = assess(
        manager,
        first,
        assessed_on="2026-03-01",
        outcome="non_compliant",
        assessed_by_user_id=str(assessor.id),
    )
    assert (earlier["compliance_status"], earlier["last_assessed_date"]) == (
        "partially_compliant",
        "2026-06-01",
    )
    assert earlier["next_assessment_date"] == "2026-12-01"
    assert [a["assessed_on"] for a in earlier["assessments"]] == [
        "2026-06-01",
        "2026-03-01",
    ]
    assert earlier["assessments"][1]["assessed_by"]["username"] == assessor.username
    cleared = assess(
        manager,
        earlier,
        assessed_on="2026-07-01",
        outcome="compliant",
        next_assessment_date=None,
    )
    assert (cleared["compliance_status"], cleared["next_assessment_date"]) == (
        "compliant",
        None,
    )
    listed = manager.get(BASE + "?status=compliant").json()
    assert [row["id"] for row in listed["items"]] == [item["id"]]
    assert manager.get(BASE + "?status=non_compliant").json()["total"] == 0
    assessed = (
        keen.db.query(EntityChangelog)
        .filter_by(entity_type="isms_legal_item", action="assessed")
        .count()
    )
    assert assessed == 3

    # Removing an assessment recorded in error returns the item to the newest left.
    newest = cleared["assessments"][0]["id"]
    path = f"{BASE}/{item['id']}/assessments"
    refused = manager.delete(
        f"{path}/{newest}?expected_version=1", headers=unsafe(manager)
    )
    assert refused.status_code == 409
    refused = manager.delete(
        f"{path}/{uuid.uuid4()}?expected_version=4", headers=unsafe(manager)
    )
    assert (refused.status_code, refused.json()) == (
        404,
        {"detail": "Assessment not found"},
    )
    after = manager.delete(
        f"{path}/{newest}?expected_version=4", headers=unsafe(manager)
    ).json()
    assert (after["compliance_status"], after["last_assessed_date"]) == (
        "partially_compliant",
        "2026-06-01",
    )
    assert after["version"] == 5
    for assessment in after["assessments"]:
        after = manager.delete(
            f"{path}/{assessment['id']}?expected_version={after['version']}",
            headers=unsafe(manager),
        ).json()
    assert (after["compliance_status"], after["last_assessed_date"]) == (
        "not_assessed",
        None,
    )
    assert keen.db.query(IsmsLegalAssessment).count() == 0
    # Another item's assessment cannot be removed through this one.
    other = assess(manager, create(manager, title="Other"))
    refused = manager.delete(
        f"{path}/{other['assessments'][0]['id']}?expected_version={after['version']}",
        headers=unsafe(manager),
    )
    assert refused.status_code == 404
    assert keen.db.query(IsmsLegalAssessment).count() == 1


def test_an_assessment_refuses_what_it_cannot_record(keen, monkeypatch):  # noqa: F811
    manager = keen.client(keen.user("isms.manage"))
    item = create(manager)
    today = today_for_schedule()
    for values, status, detail in [
        (
            {"assessed_on": (today + timedelta(days=2)).isoformat()},
            400,
            "An assessment cannot be dated in the future",
        ),
        (
            {"next_assessment_date": "2026-05-31"},
            400,
            "The next assessment date must be on or after the assessment date",
        ),
        ({"assessed_by_user_id": str(uuid.uuid4())}, 400, "Unknown active assessor"),
    ]:
        assert assess(manager, item, expect=status, **values) == {"detail": detail}
    for values, kind in [
        ({"outcome": "not_assessed"}, "literal_error"),
        ({"outcome": None}, "literal_error"),
        ({"assessed_on": None}, "date_type"),
        ({"notes": "x" * 20001}, "string_too_long"),
    ]:
        refused = assess(manager, item, expect=422, **values)
        assert refused["detail"][0]["type"] == kind, values
    assert keen.db.query(IsmsLegalAssessment).count() == 0
    # Tomorrow is accepted: the assessor's date may be ahead of the server's.
    ahead = assess(manager, item, assessed_on=(today + timedelta(days=1)).isoformat())
    monkeypatch.setattr(service, "MAX_ASSESSMENTS", 1)
    refused = assess(manager, ahead, expect=400)
    assert refused == {"detail": "A legal register item keeps up to 1 assessments"}


# --- Categories -----------------------------------------------------------------------


def test_categories_are_seeded_and_managed(keen):  # noqa: F811
    manager = keen.client(keen.user("isms.manage"))
    names = [c["name"] for c in manager.get(BASE + "/meta").json()["categories"]]
    assert names == sorted(names, key=str.lower)
    assert {"Data protection and privacy", "Corporate governance", "Labour law"} <= set(
        names
    )
    added = manager.post(
        BASE + "/categories",
        json={"name": " Export  control ", "description": " Sanctions. "},
        headers=unsafe(manager),
    )
    assert added.status_code == 200, added.text
    category = added.json()
    assert (category["name"], category["description"], category["item_count"]) == (
        "Export control",
        "Sanctions.",
        0,
    )
    path = f"{BASE}/categories/{category['id']}"
    for method, target in (("post", BASE + "/categories"), ("patch", path)):
        name = "export CONTROL" if method == "post" else "labour law"
        refused = getattr(manager, method)(
            target, json={"name": name}, headers=unsafe(manager)
        )
        assert (refused.status_code, refused.json()) == (
            409,
            {"detail": "A category with this name already exists"},
        ), method
    refused = manager.post(
        BASE + "/categories", json={"name": "   "}, headers=unsafe(manager)
    )
    assert (refused.status_code, refused.json()) == (
        400,
        {"detail": "name is required"},
    )
    # Renaming keeps a description the change does not name.
    renamed = manager.patch(
        path, json={"name": "Export controls"}, headers=unsafe(manager)
    ).json()
    assert (renamed["name"], renamed["description"]) == (
        "Export controls",
        "Sanctions.",
    )
    described = manager.patch(
        path,
        json={"name": "Export controls", "description": None},
        headers=unsafe(manager),
    ).json()
    assert described["description"] == ""
    item = create(manager, category_id=category["id"])
    found = manager.get(f"{BASE}?category_id={category['id']}").json()
    assert [row["id"] for row in found["items"]] == [item["id"]]
    counts = {
        c["name"]: c["item_count"]
        for c in manager.get(BASE + "/meta").json()["categories"]
    }
    assert (counts["Export controls"], counts["Labour law"]) == (1, 0)
    assert (
        manager.patch(
            path, json={"name": "Export controls"}, headers=unsafe(manager)
        ).json()["item_count"]
        == 1
    )
    refused = manager.delete(path, headers=unsafe(manager))
    assert (refused.status_code, refused.json()) == (
        409,
        {"detail": "Move this category's items to another category before deleting it"},
    )
    patch(manager, item, category_id=None)
    removed = manager.delete(path, headers=unsafe(manager))
    assert (removed.status_code, removed.json()) == (200, {"ok": True})
    refused = manager.delete(path, headers=unsafe(manager))
    assert (refused.status_code, refused.json()) == (
        404,
        {"detail": "Category not found"},
    )
    refused = manager.patch(path, json={"name": "Gone"}, headers=unsafe(manager))
    assert refused.status_code == 404


# --- The register and its export ------------------------------------------------------


def test_the_register_filters_pages_and_exports(keen):  # noqa: F811
    owner = keen.user()
    manager = keen.client(keen.user("isms.manage"))
    framework(keen.db, "legal-export", control="L.1", clause="1.1")
    today = today_for_schedule()
    overdue = create(
        manager,
        framework="legal-export",
        title="bribery Act 2010",
        category_name="Corporate governance",
        authority="Serious Fraud Office",
        owner_user_id=str(owner.id),
        next_assessment_date=(today - timedelta(days=1)).isoformat(),
        controls=["L.1"],
        clauses=["1.1"],
    )
    later = create(
        manager,
        title="Companies Act 2006",
        jurisdiction="United Kingdom",
        next_assessment_date=(today + timedelta(days=30)).isoformat(),
    )
    out = create(
        manager,
        title="=HYPERLINK(1)",
        requirement="line one\n=cmd",
        in_scope=False,
        next_assessment_date=(today - timedelta(days=5)).isoformat(),
    )
    assess(manager, later, outcome="non_compliant")

    def ids(query=""):
        answer = manager.get(f"{BASE}?{query}")
        assert answer.status_code == 200, answer.text
        return [row["id"] for row in answer.json()["items"]]

    # Titles sort without regard to case.
    assert ids() == [out["id"], overdue["id"], later["id"]]
    assert ids("q=fraud") == [overdue["id"]]
    assert ids("q=kingdom") == [later["id"]]
    assert ids("in_scope=false") == [out["id"]]
    assert ids("in_scope=true") == [overdue["id"], later["id"]]
    assert ids(f"owner_user_id={owner.id}") == [overdue["id"]]
    assert ids("status=non_compliant") == [later["id"]]
    # An item out of scope is never due, however old its date.
    assert ids("due=true") == [overdue["id"]]
    assert ids("due=false") == [out["id"], later["id"]]
    listed = manager.get(BASE).json()["items"]
    assert [row["assessment_due"] for row in listed] == [False, True, False]
    assert "links" not in listed[0] and "controls" not in listed[0]
    page = manager.get(BASE + "?limit=1&offset=1").json()
    assert (page["total"], page["limit"], page["offset"]) == (3, 1, 1)
    assert [row["id"] for row in page["items"]] == [overdue["id"]]
    refused = manager.get(BASE + "?status=unknown")
    assert (refused.status_code, refused.json()) == (
        400,
        {"detail": "Unknown compliance status"},
    )
    assert manager.get(BASE + "?limit=0").status_code == 422
    # Called directly, the operation still bounds its page.
    direct = service.list_items(
        user=keen.user("isms.read"), db=keen.db, limit=5000, offset=-3
    )
    assert (direct["limit"], direct["offset"], len(direct["items"])) == (200, 0, 3)

    exported = manager.get(BASE + "/export.csv?framework=legal-export")
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/csv")
    assert (
        exported.headers["content-disposition"]
        == "attachment; filename=keen-legal-register.csv"
    )
    rows = list(csv.DictReader(io.StringIO(exported.text)))
    assert list(rows[0]) == list(service.CSV_COLUMNS)
    by_title = {row["title"]: row for row in rows}
    assert len(rows) == 3
    bribery = by_title["bribery Act 2010"]
    assert (bribery["category"], bribery["owner"], bribery["in_scope"]) == (
        "Corporate governance",
        owner.username,
        "yes",
    )
    assert (bribery["controls"], bribery["clauses"]) == ("L.1", "1.1")
    assert (bribery["compliance_status"], bribery["last_assessed_date"]) == (
        "Not assessed",
        "",
    )
    companies = by_title["Companies Act 2006"]
    assert (companies["compliance_status"], companies["last_assessed_date"]) == (
        "Non-compliant",
        "2026-06-01",
    )
    assert (companies["category"], companies["owner"], companies["url"]) == ("", "", "")
    # Text a spreadsheet would read as a formula is written as text.
    hostile = by_title["'=HYPERLINK(1)"]
    assert (hostile["in_scope"], hostile["requirement"]) == ("no", "line one\n'=cmd")
    # The export takes the register's filters.
    filtered = manager.get(BASE + "/export.csv?in_scope=false")
    assert [row["title"] for row in csv.DictReader(io.StringIO(filtered.text))] == [
        "'=HYPERLINK(1)"
    ]
    refused = manager.get(BASE + "/export.csv?status=unknown")
    assert refused.status_code == 400


# --- Audit sampling and questions -----------------------------------------------------


def test_an_item_is_sampled_into_an_audit_with_its_controls(keen):  # noqa: F811
    client = keen.client(keen.user("isms.manage", "audits.manage", "isms.read"))
    framework(keen.db, "legal-audit", control="L.1")
    item = create(
        client,
        framework="legal-audit",
        requirement="Keep   records\nfor six years.",
        controls=["L.1"],
    )
    audit = client.post(
        "/v1/audits",
        json={"title": "Legal review", "framework_slug": "legal-audit"},
        headers=unsafe(client),
    ).json()
    sample = client.post(
        f"/v1/audits/{audit['id']}/evidence",
        json={"entity_type": "isms_legal_item", "entity_id": item["id"]},
        headers=unsafe(client),
    )
    assert sample.status_code == 200, sample.text
    assert keen.db.query(AuditEvidence).one().entity_type == "isms_legal_item"
    detail = client.get(f"/v1/audits/{audit['id']}").json()
    (evidence,) = detail["evidence"]
    entity = evidence["entity"]
    assert (entity["kind"], entity["title"], entity["subtitle"]) == (
        "Legal register item",
        "Legal register: Data Protection Act 2018",
        "Keep records for six years.",
    )
    assert entity["href"] == (
        f"/legal-register.html?id={item['id']}&framework=legal-audit"
    )
    assert [c["ref"] for c in evidence["controls"]] == ["L.1"]
    # Someone without ISMS access cannot sample an item they cannot read.
    auditor = keen.client(keen.user("audits.manage"))
    own = auditor.post(
        "/v1/audits",
        json={"title": "Own review", "framework_slug": "legal-audit"},
        headers=unsafe(auditor),
    ).json()
    keen.db.query(IsmsLegalItem).filter_by(id=item["id"]).update({"version": 1})
    keen.db.commit()
    assert client.delete(
        f"{BASE}/{item['id']}?expected_version=1", headers=unsafe(client)
    ).json() == {"ok": True}
    refused = auditor.post(
        f"/v1/audits/{own['id']}/evidence",
        json={"entity_type": "isms_legal_item", "entity_id": item["id"]},
        headers=unsafe(auditor),
    )
    assert refused.status_code == 404
    # The sample of a deleted item says so and still names what it was.
    gone = client.get(f"/v1/audits/{audit['id']}").json()["evidence"][0]["entity"]
    assert (gone["missing"], gone["subtitle"]) == (
        True,
        "This sampled item no longer exists.",
    )


def test_questions_are_asked_about_an_item_by_those_who_read_it(keen):  # noqa: F811
    manager = keen.client(keen.user("isms.manage", "question.create"))
    outsider = keen.client(keen.user("question.create"))
    item = create(manager, category_name="Labour law")
    bare = create(manager, title="Uncategorised")
    path = f"/v1/questions/entities/isms_legal_item/{item['id']}"
    asked = manager.post(
        path, json={"body": "Who confirmed this applies?"}, headers=unsafe(manager)
    )
    assert asked.status_code == 200, asked.text
    (thread,) = manager.get(path).json()["threads"]
    assert (thread["target_type"], thread["target_id"]) == (
        "isms_legal_item",
        item["id"],
    )
    assert thread["target_label"] == (
        "Legal register item: Labour law - Data Protection Act 2018"
    )
    assert thread["target_url"] == (
        f"/legal-register.html?id={item['id']}&tab=questions&thread={thread['id']}"
    )
    asked = manager.post(
        f"/v1/questions/entities/isms_legal_item/{bare['id']}",
        json={"body": "Which category?"},
        headers=unsafe(manager),
    )
    assert asked.status_code == 200, asked.text
    mine = manager.get("/v1/me/questions").json()["items"]
    assert "Legal register item: Legal register - Uncategorised" in [
        row["target_label"] for row in mine
    ]
    refused = outsider.post(path, json={"body": "x"}, headers=unsafe(outsider))
    assert (refused.status_code, refused.json()) == (
        403,
        {"detail": "ISMS access required"},
    )
    refused = manager.post(
        f"/v1/questions/entities/isms_legal_item/{uuid.uuid4()}",
        json={"body": "x"},
        headers=unsafe(manager),
    )
    assert (refused.status_code, refused.json()) == (
        404,
        {"detail": "Legal register item not found"},
    )


# --- The feature switch ---------------------------------------------------------------


def test_the_register_is_withdrawn_when_its_feature_is_off(keen, smtp):  # noqa: F811
    admin = keen.client(keen.user(role="admin"))
    manager = keen.client(keen.user("isms.manage", "question.create"))
    item = create(manager)
    version = admin.get("/v1/admin/features").json()["version"]
    switched = admin.put(
        "/v1/admin/features",
        json={"version": version, "changes": {"legal_register": False}},
        headers=unsafe(admin),
    )
    assert switched.status_code == 200, switched.text
    disabled = {"detail": "This feature is disabled by an administrator."}
    for path in (
        BASE,
        BASE + "/meta",
        f"{BASE}/{item['id']}",
        f"/v1/isms/legal-item/{item['id']}/changelog",
        f"/v1/questions/entities/isms_legal_item/{item['id']}",
    ):
        refused = manager.get(path)
        assert (refused.status_code, refused.json()) == (404, disabled), path
    refused = manager.post(BASE, json={"title": "x"}, headers=unsafe(manager))
    assert (refused.status_code, refused.json()) == (404, disabled)
    assert service.send_assessment_reminders(keen.db) == {"enabled": False, "sent": 0}
    # Other ISMS registers stay available.
    assert manager.get("/v1/isms/swot").status_code == 200


# --- Reminders ------------------------------------------------------------------------


def test_owners_are_reminded_once_of_each_assessment_date(keen, smtp, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "reminder_days_before", 7)
    today = date(2026, 11, 26)
    owner = keen.user()
    owner.email = "owner@example.test"
    silent = keen.user()
    inactive = keen.user()
    inactive.email = "gone@example.test"
    inactive.is_active = False
    keen.db.commit()
    manager = keen.client(keen.user("isms.manage"))

    def item(title, due, **values):
        return create(
            manager,
            title=title,
            next_assessment_date=(today + timedelta(days=due)).isoformat(),
            **values,
        )

    soon = item("Soon", 7, owner_user_id=str(owner.id))
    overdue = item("Overdue", -3, owner_user_id=str(owner.id))
    item("Later", 8, owner_user_id=str(owner.id))
    item("Out of scope", 1, owner_user_id=str(owner.id), in_scope=False)
    item("Unowned", 1)
    item("No address", 1, owner_user_id=str(silent.id))
    create(manager, title="No date", owner_user_id=str(owner.id))
    keen.db.query(IsmsLegalItem).filter_by(title="No address").update(
        {"owner_user_id": silent.id}
    )
    stale = item("Inactive owner", 1)
    keen.db.query(IsmsLegalItem).filter_by(id=stale["id"]).update(
        {"owner_user_id": inactive.id}
    )
    keen.db.commit()

    result = service.send_assessment_reminders(keen.db, today=today)
    assert result == {
        "enabled": True,
        "sent": 2,
        "window_start": "2026-11-26",
        "window_end": "2026-12-03",
    }
    sent = {}
    for client in smtp.instances:
        (message,) = client.messages
        assert client.recipients == ["owner@example.test"]
        sent[message["Subject"]] = (
            message.get_body(preferencelist=("plain",)).get_content(),
            list(message.iter_attachments())[0],
        )
    assert sorted(sent) == [
        "Keen Alerts: Legal register assessment due 2026-11-23 - Overdue",
        "Keen Alerts: Legal register assessment due 2026-12-03 - Soon",
    ]
    body, invite = sent["Keen Alerts: Legal register assessment due 2026-12-03 - Soon"]
    assert body.startswith(f"Hello {owner.username},\n\n")
    assert "a legal register item you own is due soon." in body
    assert "Item: Soon\nAssessment due: 2026-12-03\n" in body
    assert f"https://keen.example/legal-register.html?id={soon['id']}" in body
    assert invite.get_filename() == "keen-legal-assessment.ics"
    ics = invite.get_content()
    assert f"UID:keen-legal-assessment-{soon['id']}@keen" in ics
    assert "DTSTART;VALUE=DATE:20261203" in ics and "SUMMARY:Assess: Soon" in ics
    late, _ = sent["Keen Alerts: Legal register assessment due 2026-11-23 - Overdue"]
    assert "a legal register item you own is overdue." in late

    def reminded(title):
        keen.db.expire_all()
        return (
            keen.db.query(IsmsLegalItem).filter_by(title=title).one().reminded_at
            is not None
        )

    assert reminded("Soon") and reminded("Overdue")
    # An item whose owner has no address, or is inactive, stays due a reminder.
    assert not reminded("No address") and not reminded("Inactive owner")
    assert not reminded("Later") and not reminded("Out of scope")
    # Nobody is reminded of the same date twice.
    smtp.instances.clear()
    assert service.send_assessment_reminders(keen.db, today=today)["sent"] == 0
    assert smtp.instances == []
    # A new date, a new owner or an assessment that sets the next date each make
    # the item due a reminder again; other changes do not.
    current = manager.get(f"{BASE}/{soon['id']}").json()
    current = patch(
        manager,
        current,
        authority="ICO",
        owner_user_id=str(owner.id),
        next_assessment_date=current["next_assessment_date"],
    )
    assert reminded("Soon")
    current = patch(
        manager, current, next_assessment_date=(today + timedelta(days=2)).isoformat()
    )
    assert not reminded("Soon")
    late_item = manager.get(f"{BASE}/{overdue['id']}").json()
    late_item = assess(manager, late_item, next_assessment_date="2026-11-30")
    assert not reminded("Overdue")
    assert service.send_assessment_reminders(keen.db, today=today)["sent"] == 2
    patch(manager, manager.get(f"{BASE}/{soon['id']}").json(), owner_user_id=None)
    assert not reminded("Soon")
    # With reminders switched off, nothing is sent.
    monkeypatch.setattr(settings, "reminder_days_before", 0)
    assert service.send_assessment_reminders(keen.db, today=today) == {
        "enabled": False,
        "sent": 0,
    }


def test_a_date_cleared_while_reminders_run_sends_nothing(keen, smtp, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "reminder_days_before", 2)
    owner = keen.user()
    owner.email = "owner@example.test"
    keen.db.commit()
    manager = keen.client(keen.user("isms.manage"))
    today = date(2026, 11, 26)
    item = create(
        manager, owner_user_id=str(owner.id), next_assessment_date="2026-11-27"
    )
    real = service.deliver_once

    def cleared_first(db, **kwargs):
        db.query(IsmsLegalItem).filter_by(id=item["id"]).update(
            {"next_assessment_date": None}
        )
        db.commit()
        return real(db, **kwargs)

    monkeypatch.setattr(service, "deliver_once", cleared_first)
    assert service.send_assessment_reminders(keen.db, today=today)["sent"] == 0
    assert smtp.instances == []
    keen.db.expire_all()
    assert keen.db.get(IsmsLegalItem, uuid.UUID(item["id"])).reminded_at is None


def test_the_reminder_email_greets_an_unnamed_owner(smtp):  # noqa: F811
    from app.services import mailer as keen_mailer

    keen_mailer.send_legal_assessment_reminder_email(
        to_email="owner@example.test",
        owner_name=" ",
        title="Bribery Act 2010",
        due_on="2026-04-10",
        overdue=False,
        item_url="https://keen.example/legal-register.html?id=1",
        uid="keen-legal-assessment-1",
    )
    (client,) = smtp.instances
    (message,) = client.messages
    body = message.get_body(preferencelist=("plain",)).get_content()
    assert body.startswith("Hello there,\n\nThe assessment of a legal register item")
    assert body.endswith("because you are the item's owner.\n")


# --- Automatic diary entries ----------------------------------------------------------


def test_linking_controls_records_one_diary_entry_for_the_item(keen):  # noqa: F811
    manager = keen.client(keen.user("isms.manage"))
    framework(keen.db, "legal-diary", control="L.1")
    item = create(manager, framework="legal-diary", controls=["L.1"])
    entries = keen.db.query(Event).filter_by(source="diary").all()
    assert len(entries) == 1
    assert "Legal register item" in entries[0].summary
    assert f"/legal-register.html?id={item['id']}" in str(entries[0].normalized_payload)
    assert isinstance(entries[0].timestamp, datetime)
    deleted = manager.delete(
        f"{BASE}/{item['id']}?expected_version=1&framework=legal-diary",
        headers=unsafe(manager),
    )
    assert deleted.status_code == 200, deleted.text
    # Deleting the item withdraws the entry that recorded its links.
    assert keen.db.query(Event).filter_by(source="diary").count() == 0


# --- Concurrent saves -----------------------------------------------------------------


def test_a_category_name_taken_by_a_concurrent_save_is_never_duplicated(  # noqa: F811
    keen, monkeypatch
):
    # Two saves can both find a name free. The unique index refuses the second
    # insert; an item then takes the category the first save added, and a
    # category save is told the name is taken.
    manager = keen.client(keen.user("isms.manage"))
    real = service._category_by_name
    seeded = keen.db.query(IsmsLegalCategory).count()

    def free_once():
        calls = []

        def lookup(db, name, **kwargs):
            calls.append(name)
            return None if len(calls) == 1 else real(db, name, **kwargs)

        return lookup

    monkeypatch.setattr(service, "_category_by_name", free_once())
    item = create(manager, category_name="Labour law")
    assert item["category"]["name"] == "Labour law"
    assert keen.db.query(IsmsLegalCategory).count() == seeded

    for method, path in (
        ("post", BASE + "/categories"),
        ("patch", f"{BASE}/categories/{item['category_id']}"),
    ):
        monkeypatch.setattr(service, "_category_by_name", free_once())
        name = "labour LAW" if method == "post" else "fire safety"
        refused = getattr(manager, method)(
            path, json={"name": name}, headers=unsafe(manager)
        )
        assert (refused.status_code, refused.json()) == (
            409,
            {"detail": "A category with this name already exists"},
        ), method
    keen.db.expire_all()
    assert keen.db.query(IsmsLegalCategory).count() == seeded
    assert keen.db.get(IsmsLegalCategory, uuid.UUID(item["category_id"])).name == (
        "Labour law"
    )
