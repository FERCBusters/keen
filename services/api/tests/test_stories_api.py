"""Stories through the assembled application.

A story holds events, risks, ISMS records and notes with a timeline and a flow
chart. Three permissions govern it: stories.read views and comments,
stories.manage creates and edits its holder's own stories, stories.admin edits
any story. Every item is shown only to readers who can read the referenced
record themselves, and adding an item needs that same access.
"""

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from app.core.config import settings
from app.db.models import (
    ControlItem,
    EntityChangelog,
    Event,
    Framework,
    IsmsDocument,
    Mapping,
    Risk,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
    Story,
    StoryItem,
    StoryLink,
)
from app.security.diary_visibility import LINK_ENTRY_SYSTEM, link_entry_external_id
from app.services import features
from app.services import stories as service

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import keen, unsafe  # noqa: F401

FW = "fw-stories"


@pytest.fixture
def world(keen):  # noqa: F811 - fixture imported above
    db = keen.db
    db.add_all([Framework(slug=FW), Framework(slug="fw-other")])
    access = ControlItem(framework_slug=FW, ref="A.5.15", type="annex_control")
    backup = ControlItem(framework_slug=FW, ref="A.8.13", type="annex_control")
    elsewhere = ControlItem(
        framework_slug="fw-other", ref="CC6.1", type="annex_control"
    )
    db.add_all([access, backup, elsewhere])
    category = RiskCategory(name="Data")
    db.add(category)
    db.flush()
    subcategory = RiskAssetSubcategory(name="Databases", category_id=category.id)
    db.add(subcategory)
    db.flush()
    asset = RiskAsset(
        name="Payroll database", category_id=category.id, subcategory_id=subcategory.id
    )
    db.add(asset)
    db.flush()
    risk = Risk(asset_id=asset.id, threat_summary="Unauthorised export")
    document = IsmsDocument(title="Access control policy", document_type="policy")
    login = Event(
        source="cloud-logs",
        summary="Root account sign-in",
        timestamp=datetime(2026, 5, 1, 9, 30),
        external_id=uuid.uuid4().hex,
        raw_pointer={},
        normalized_payload={},
    )
    export = Event(
        source="cloud-logs",
        summary="Bulk export started",
        timestamp=datetime(2026, 5, 1, 9, 45),
        external_id=uuid.uuid4().hex,
        raw_pointer={},
        normalized_payload={},
    )
    db.add_all([risk, document, login, export])
    db.flush()
    # An automatic ISMS link entry in the diary is visible to ISMS readers only.
    diary = Event(
        source="diary",
        system=LINK_ENTRY_SYSTEM,
        summary="Policy linked to A.5.15",
        timestamp=datetime(2026, 5, 1, 10, 0),
        external_id=link_entry_external_id("document", document.id),
        raw_pointer={},
        normalized_payload={},
    )
    db.add(diary)
    db.flush()
    db.add_all(
        [
            Mapping(event_id=login.id, control_item_id=access.id, method="manual"),
            Mapping(event_id=export.id, control_item_id=backup.id, method="manual"),
            Mapping(event_id=export.id, control_item_id=access.id, method="manual"),
            Mapping(event_id=export.id, control_item_id=elsewhere.id, method="manual"),
        ]
    )
    db.commit()

    def client(*codes, role="normal"):
        return keen.client(keen.user(*codes, role=role))

    def create(client, **overrides):
        payload = {"title": "Payroll export incident", **overrides}
        response = client.post(
            f"/v1/stories?framework={FW}", json=payload, headers=unsafe(client)
        )
        assert response.status_code == 200, response.text
        return response.json()

    def item(kind, entity=None, **fields):
        return {"kind": kind, "entity_id": str(entity.id) if entity else None, **fields}

    return SimpleNamespace(
        keen=keen,
        db=db,
        client=client,
        create=create,
        item=item,
        risk=risk,
        document=document,
        login=login,
        export=export,
        diary=diary,
        access=access,
        backup=backup,
    )


def _items(story):
    return {(i["kind"], i["title"]): i for i in story["items"]}


# --- Permissions ----------------------------------------------------------------


def test_reading_and_writing_follow_the_three_permissions(world):
    nobody = world.client()
    reader = world.client("stories.read")
    author = world.client("stories.manage", "events.read")
    other_author = world.client("stories.manage")
    admin = world.client("stories.admin")

    assert nobody.get("/v1/stories").status_code == 403
    assert reader.get("/v1/stories").status_code == 200
    refused = reader.post("/v1/stories", json={"title": "No"}, headers=unsafe(reader))
    assert refused.status_code == 403
    assert "stories.manage" in refused.json()["detail"]

    story = world.create(author, items=[world.item("event", world.login)])
    assert story["can_edit"] is True and story["item_count"] == 1
    assert reader.get(f"/v1/stories/{story['id']}").json()["can_edit"] is False
    assert nobody.get(f"/v1/stories/{story['id']}").status_code == 403

    rename = {"title": "Renamed", "expected_version": story["version"]}
    refused = other_author.patch(
        f"/v1/stories/{story['id']}", json=rename, headers=unsafe(other_author)
    )
    assert refused.status_code == 403
    assert "stories you created" in refused.json()["detail"]
    assert (
        other_author.delete(
            f"/v1/stories/{story['id']}?expected_version={story['version']}",
            headers=unsafe(other_author),
        ).status_code
        == 403
    )
    renamed = admin.patch(
        f"/v1/stories/{story['id']}", json=rename, headers=unsafe(admin)
    )
    assert renamed.status_code == 200, renamed.text
    assert (renamed.json()["title"], renamed.json()["version"]) == ("Renamed", 2)
    # The author's own next save carries a stale version.
    stale = author.patch(
        f"/v1/stories/{story['id']}", json=rename, headers=unsafe(author)
    )
    assert stale.status_code == 409
    listed = admin.get("/v1/stories").json()["items"]
    assert [(s["title"], s["item_count"], s["can_edit"]) for s in listed] == [
        ("Renamed", 1, True)
    ]
    assert (
        admin.delete(
            f"/v1/stories/{story['id']}?expected_version=2", headers=unsafe(admin)
        ).status_code
        == 200
    )
    assert reader.get(f"/v1/stories/{story['id']}").status_code == 404


def test_me_reports_story_permissions_and_the_feature(world):
    manager = world.client("stories.manage")
    flags = manager.get("/v1/me").json()
    assert (
        flags["can_view_stories"],
        flags["can_manage_stories"],
        flags["can_admin_stories"],
    ) == (True, True, False)
    reader = world.client("stories.read").get("/v1/me").json()
    assert (reader["can_view_stories"], reader["can_manage_stories"]) == (True, False)
    features.update(world.db, world.keen.user(role="admin"), 0, {"stories": False})
    assert manager.get("/v1/me").json()["can_view_stories"] is False
    assert manager.get("/v1/stories").status_code == 404


# --- Item visibility ------------------------------------------------------------


def test_each_item_is_shown_only_to_readers_of_its_record(world):
    author = world.client("stories.manage", "events.read", "risk.read", "isms.read")
    story = world.create(
        author,
        items=[
            world.item("event", world.login, note="Where it started"),
            world.item("event", world.diary),
            world.item("risk", world.risk, label="The risk this realised"),
            world.item("document", world.document),
            world.item("note", label="Containment", note="Keys rotated"),
        ],
    )
    assert [i["kind"] for i in story["items"]] == [
        "event",
        "event",
        "risk",
        "document",
        "note",
    ]
    assert all(i["withheld"] is False for i in story["items"])
    assert story["items"][0]["controls"] == [
        {"id": str(world.access.id), "ref": "A.5.15", "title": None}
    ]
    assert story["items"][2]["url"] == f"/risk.html?id={world.risk.id}"
    assert story["items"][3]["title"] == "Access control policy"

    evidence_only = world.client("stories.read", "events.read")
    seen = evidence_only.get(f"/v1/stories/{story['id']}?framework={FW}").json()
    by_kind = {(i["kind"], i["withheld"]): i for i in seen["items"]}
    assert set(by_kind) == {
        ("event", False),
        ("event", True),
        ("risk", True),
        ("document", True),
        ("note", False),
    }
    withheld = by_kind[("risk", True)]
    assert (
        withheld["entity_id"],
        withheld["label"],
        withheld["note"],
        withheld["url"],
        withheld["occurred_at"],
        withheld["title"],
    ) == (None, "", "", None, None, "Risk you cannot view")
    assert "unauthorised" not in str(seen).lower()  # no risk text leaks
    assert by_kind[("note", False)]["note"] == "Keys rotated"
    assert seen["controls"] == [
        {"id": str(world.access.id), "ref": "A.5.15", "title": None}
    ]

    # A reader without evidence access sees no event at all, by placeholder.
    isms_only = world.client("stories.read", "isms.read")
    seen = isms_only.get(f"/v1/stories/{story['id']}?framework={FW}").json()
    assert [(i["kind"], i["withheld"]) for i in seen["items"]] == [
        ("event", True),
        ("event", True),
        ("risk", True),
        ("document", False),
        ("note", False),
    ]
    assert seen["controls"] == []


def test_controls_follow_the_requested_framework(world):
    author = world.client("stories.manage", "events.read")
    story = world.create(author, items=[world.item("event", world.export)])
    assert [c["ref"] for c in story["controls"]] == ["A.5.15", "A.8.13"]
    other = author.get(f"/v1/stories/{story['id']}?framework=fw-other").json()
    assert [c["ref"] for c in other["controls"]] == ["CC6.1"]
    assert other["items"][0]["controls"][0]["ref"] == "CC6.1"


# --- Adding items ---------------------------------------------------------------


def test_adding_an_item_needs_read_access_to_its_record(world):
    author = world.client("stories.manage")
    story = world.create(author)

    def add(client, *items, status=200):
        response = client.post(
            f"/v1/stories/{story['id']}/items",
            json={"items": list(items), "expected_version": story["version"]},
            headers=unsafe(client),
        )
        assert response.status_code == status, response.text
        return response.json()

    refused = add(author, world.item("event", world.login), status=403)
    assert refused["detail"] == "events.read permission required"
    assert add(author, world.item("risk", world.risk), status=404)["detail"] == (
        "Related item not available"
    )
    assert add(author, world.item("document", world.document), status=404)
    missing = add(
        author, world.item("note", label="x", entity_id=str(uuid.uuid4())), status=400
    )
    assert missing["detail"] == "A note has no record"
    assert add(author, world.item("note"), status=400)["detail"] == (
        "A note needs a label or text"
    )
    assert add(author, {"kind": "risk"}, status=400)["detail"] == (
        "Risk items need a record"
    )
    # Nothing above changed the story.
    assert author.get(f"/v1/stories/{story['id']}").json()["version"] == 1

    # The author's own permissions are what count, not the story creator's.
    reader = world.client("stories.read", "events.read")
    add(reader, world.item("event", world.login), status=403)

    able = world.client("stories.admin", "events.read", "risk.read")
    saved = add(able, world.item("event", world.login), world.item("risk", world.risk))
    assert [i["kind"] for i in saved["items"]] == ["event", "risk"]
    assert saved["version"] == 2


def test_disabled_features_withhold_items_and_refuse_additions(world):
    author = world.client("stories.manage", "events.read", "risk.read")
    story = world.create(
        author, items=[world.item("event", world.login), world.item("risk", world.risk)]
    )
    admin = world.keen.user(role="admin")
    features.update(world.db, admin, 0, {"risk_cia": False})
    seen = author.get(f"/v1/stories/{story['id']}").json()
    assert [(i["kind"], i["withheld"]) for i in seen["items"]] == [
        ("event", False),
        ("risk", True),
    ]
    features.update(world.db, admin, 1, {"evidence": False})
    seen = author.get(f"/v1/stories/{story['id']}").json()
    assert [i["withheld"] for i in seen["items"]] == [True, True]
    assert seen["controls"] == []
    without_events = author.post(
        f"/v1/stories/{story['id']}/items",
        json={"items": [world.item("event", world.export)], "expected_version": 1},
        headers=unsafe(author),
    )
    assert without_events.status_code == 404
    assert "disabled" in without_events.json()["detail"]
    features.update(world.db, admin, 2, {"evidence": True})
    refused = author.post(
        f"/v1/stories/{story['id']}/items",
        json={"items": [world.item("risk", world.risk)], "expected_version": 1},
        headers=unsafe(author),
    )
    assert refused.status_code == 404
    assert refused.json()["detail"] == "Related feature is disabled"


def test_duplicate_records_are_kept_once_and_the_story_is_bounded(world, monkeypatch):
    author = world.client("stories.manage", "events.read")
    story = world.create(
        author,
        items=[
            world.item("event", world.login),
            world.item("event", world.login, note="again"),
            world.item("note", label="one"),
            world.item("note", label="one"),
        ],
    )
    # Records appear once; notes are free text and may repeat.
    assert [i["kind"] for i in story["items"]] == ["event", "note", "note"]
    assert story["items"][0]["note"] == ""
    assert [(i["x"], i["y"]) for i in story["items"]] == [
        (40, 40),
        (280, 40),
        (520, 40),
    ]
    monkeypatch.setattr(service, "MAX_ITEMS", 4)
    response = author.post(
        f"/v1/stories/{story['id']}/items",
        json={
            "items": [world.item("event", world.export), world.item("note", label="n")],
            "expected_version": story["version"],
        },
        headers=unsafe(author),
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "A story holds up to 4 items"
    assert author.get(f"/v1/stories/{story['id']}").json()["item_count"] == 3


def test_timeline_order_uses_the_event_time_unless_the_author_sets_one(world):
    author = world.client("stories.manage", "events.read")
    story = world.create(
        author,
        items=[
            world.item("note", label="Decision", occurred_at="2026-05-01T09:40:00Z"),
            world.item("event", world.login),
        ],
    )
    note, login = story["items"]
    assert note["occurred_at"] == "2026-05-01T09:40:00"
    assert login["occurred_at"] == "2026-05-01T09:30:00"
    changed = author.patch(
        f"/v1/stories/{story['id']}/items/{login['id']}",
        json={
            "occurred_at": "2026-05-01T11:00:00+02:00",
            "label": "Attacker signs in",
            "expected_version": story["version"],
        },
        headers=unsafe(author),
    )
    assert changed.status_code == 200, changed.text
    login = changed.json()["items"][1]
    assert (login["occurred_at"], login["label"]) == (
        "2026-05-01T09:00:00",
        "Attacker signs in",
    )
    cleared = author.patch(
        f"/v1/stories/{story['id']}/items/{login['id']}",
        json={"clear_occurred_at": True, "expected_version": 2},
        headers=unsafe(author),
    )
    assert cleared.json()["items"][1]["occurred_at"] == "2026-05-01T09:30:00"
    emptied = author.patch(
        f"/v1/stories/{story['id']}/items/{note['id']}",
        json={"label": "", "expected_version": 3},
        headers=unsafe(author),
    )
    assert emptied.status_code == 400
    unknown = author.patch(
        f"/v1/stories/{story['id']}/items/{uuid.uuid4()}",
        json={"label": "x", "expected_version": 3},
        headers=unsafe(author),
    )
    assert unknown.status_code == 404


# --- Layout -----------------------------------------------------------------------


def test_layout_names_only_the_story_own_items(world):
    author = world.client("stories.manage", "events.read")
    story = world.create(
        author,
        items=[
            world.item("event", world.login),
            world.item("event", world.export),
            world.item("note", label="Impact"),
        ],
    )
    other = world.create(author, items=[world.item("note", label="elsewhere")])
    a, b, c = (i["id"] for i in story["items"])
    foreign = other["items"][0]["id"]

    def layout(body, status=200, version=None):
        response = author.put(
            f"/v1/stories/{story['id']}/layout",
            json={**body, "expected_version": version or story["version"]},
            headers=unsafe(author),
        )
        assert response.status_code == status, response.text
        return response.json()

    assert layout({"positions": [{"item_id": foreign, "x": 1, "y": 1}]}, 400)[
        "detail"
    ].startswith("Position refers")
    assert layout({"links": [{"from_item_id": a, "to_item_id": foreign}]}, 400)[
        "detail"
    ].startswith("Link refers")
    assert layout({"links": [{"from_item_id": a, "to_item_id": a}]}, 400)
    assert layout(
        {
            "links": [
                {"from_item_id": a, "to_item_id": b},
                {"from_item_id": a, "to_item_id": b, "label": "twice"},
            ]
        },
        400,
    )["detail"].startswith("Links between the same items")
    assert layout({"positions": [{"item_id": a, "x": 1.5, "y": 1}]}, 422)
    assert layout({"positions": [{"item_id": a, "x": 30000, "y": 1}]}, 422)
    assert layout({}, 409, version=99)

    saved = layout(
        {
            "positions": [
                {"item_id": a, "x": 10, "y": 20},
                {"item_id": c, "x": 5, "y": 5},
            ],
            "links": [
                {"from_item_id": a, "to_item_id": b, "label": "then"},
                {"from_item_id": b, "to_item_id": c},
            ],
        }
    )
    assert [(i["x"], i["y"]) for i in saved["items"]] == [(10, 20), (280, 40), (5, 5)]
    assert [
        (link["from_item_id"], link["to_item_id"], link["label"])
        for link in saved["links"]
    ] == [
        (a, b, "then"),
        (b, c, ""),
    ]
    # Saving again replaces the links wholesale.
    again = layout(
        {"links": [{"from_item_id": c, "to_item_id": a}]}, version=saved["version"]
    )
    assert [(link["from_item_id"], link["to_item_id"]) for link in again["links"]] == [
        (c, a)
    ]
    # Removing an item removes the links that touch it.
    removed = author.delete(
        f"/v1/stories/{story['id']}/items/{c}?expected_version={again['version']}",
        headers=unsafe(author),
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["links"] == []
    assert [i["id"] for i in removed.json()["items"]] == [a, b]
    assert world.db.query(StoryLink).count() == 0
    assert world.db.query(StoryItem).filter_by(id=uuid.UUID(c)).first() is None


# --- Comments -------------------------------------------------------------------


def test_readers_comment_and_delete_their_own_comments(world):
    author = world.client("stories.manage")
    story = world.create(author)
    reader = world.client("stories.read")
    other = world.client("stories.read")
    admin = world.client("stories.admin")
    path = f"/v1/stories/{story['id']}/comments"

    assert (
        world.client()
        .post(path, json={"body": "x"}, headers=unsafe(world.client()))
        .status_code
        == 403
    )
    posted = reader.post(
        path, json={"body": "  Looks complete.  "}, headers=unsafe(reader)
    )
    assert posted.status_code == 200, posted.text
    comment = posted.json()
    assert (comment["body"], comment["can_delete"]) == ("Looks complete.", True)
    assert (
        reader.post(path, json={"body": "   "}, headers=unsafe(reader)).status_code
        == 400
    )
    listed = other.get(path).json()["items"]
    assert [(c["body"], c["can_delete"]) for c in listed] == [
        ("Looks complete.", False)
    ]
    assert admin.get(path).json()["items"][0]["can_delete"] is True
    # Comments do not bump the story version; its count is reported.
    detail = reader.get(f"/v1/stories/{story['id']}").json()
    assert (detail["version"], detail["comment_count"]) == (1, 1)

    refused = other.delete(f"{path}/{comment['id']}", headers=unsafe(other))
    assert refused.status_code == 403
    assert (
        other.delete(f"{path}/{uuid.uuid4()}", headers=unsafe(other)).status_code == 404
    )
    assert (
        admin.delete(f"{path}/{comment['id']}", headers=unsafe(admin)).status_code
        == 200
    )
    assert reader.get(path).json()["items"] == []
    # Authors remove their own comments without stories.admin.
    own = reader.post(path, json={"body": "Mine"}, headers=unsafe(reader)).json()
    assert (
        reader.delete(f"{path}/{own['id']}", headers=unsafe(reader)).status_code == 200
    )
    assert reader.get(path).json()["items"] == []


# --- Other features -------------------------------------------------------------


def test_sampling_a_story_into_an_audit_needs_story_read_access(world):
    author = world.client("stories.manage", "events.read")
    story = world.create(
        author,
        summary="An export after a root sign-in",
        items=[world.item("event", world.login), world.item("event", world.export)],
    )
    auditor = world.client("audits.admin", "events.read")
    created = auditor.post(
        "/v1/audits",
        json={"title": "Q2", "framework_slug": FW},
        headers=unsafe(auditor),
    )
    assert created.status_code == 200, created.text
    audit_id = created.json()["id"]
    target = {"entity_type": "story", "entity_id": story["id"]}
    refused = auditor.post(
        f"/v1/audits/{audit_id}/evidence", json=target, headers=unsafe(auditor)
    )
    assert refused.status_code == 403
    assert refused.json()["detail"] == "Read access to the sampled source is required"

    reader = world.client("audits.admin", "events.read", "stories.read")
    sampled = reader.post(
        f"/v1/audits/{audit_id}/evidence", json=target, headers=unsafe(reader)
    )
    assert sampled.status_code == 200, sampled.text
    sample = sampled.json()
    assert sample["title"] == "Story: Payroll export incident"
    assert sample["evidence_url"] == f"/story.html?id={story['id']}&framework={FW}"
    entity = sample["entity"]
    assert entity["subtitle"] == "An export after a root sign-in"
    assert [c["ref"] for c in entity["controls"]] == ["A.5.15", "A.8.13"]

    admin = world.client("stories.admin")
    admin.delete(f"/v1/stories/{story['id']}?expected_version=1", headers=unsafe(admin))
    (sample,) = reader.get(f"/v1/audits/{audit_id}").json()["evidence"]
    assert sample["entity"]["missing"] is True


def test_questions_target_stories_readers_can_see(world):
    author = world.client("stories.manage")
    story = world.create(author)
    asker = world.client("stories.read", "question.create")
    path = f"/v1/questions/entities/story/{story['id']}"
    posted = asker.post(
        path, json={"body": "Why was the export allowed?"}, headers=unsafe(asker)
    )
    assert posted.status_code == 200, posted.text
    (thread,) = asker.get(path).json()["threads"]
    assert thread["target_label"] == "Story: open — Payroll export incident"
    assert thread["target_url"].startswith(
        f"/story.html?id={story['id']}&tab=questions&thread="
    )
    stranger = world.client("question.create")
    assert stranger.get(path).status_code == 403
    assert (
        stranger.post(path, json={"body": "x"}, headers=unsafe(stranger)).status_code
        == 403
    )
    unknown = asker.get(f"/v1/questions/entities/story/{uuid.uuid4()}")
    assert unknown.status_code == 404


def test_changes_are_recorded_in_the_story_changelog(world):
    author = world.client("stories.manage", "events.read")
    story = world.create(author, items=[world.item("event", world.login)])
    sid = story["id"]
    author.put(
        f"/v1/stories/{sid}/layout",
        json={"positions": [], "links": [], "expected_version": 1},
        headers=unsafe(author),
    )
    author.post(
        f"/v1/stories/{sid}/comments", json={"body": "noted"}, headers=unsafe(author)
    )
    author.patch(
        f"/v1/stories/{sid}",
        json={"status": "closed", "expected_version": 2},
        headers=unsafe(author),
    )
    log = author.get(f"/v1/stories/{sid}/changelog").json()["items"]
    assert [e["action"] for e in log] == [
        "updated",
        "comment_added",
        "layout_saved",
        "created",
    ]
    assert all(e["entity_title"] == "Payroll export incident" for e in log)
    assert log[0]["summary"].startswith("Updated")
    assert "status" in {c["field"] for c in log[0]["changes"]}
    assert world.client().get(f"/v1/stories/{sid}/changelog").status_code == 403
    author.delete(f"/v1/stories/{sid}?expected_version=3", headers=unsafe(author))
    deleted = world.db.query(EntityChangelog).filter_by(action="deleted").one()
    assert deleted.entity_type == "story" and deleted.entity_id == uuid.UUID(sid)
    assert world.db.query(Story).count() == 0


def test_choices_list_readable_records_for_authors(world):
    author = world.client("stories.manage")
    assert author.get("/v1/stories/choices/event").status_code == 400
    assert author.get("/v1/stories/choices/document").json()["items"] == []
    assert (
        world.client("stories.read").get("/v1/stories/choices/document").status_code
        == 403
    )
    isms_author = world.client("stories.manage", "isms.read")
    found = isms_author.get("/v1/stories/choices/document?q=access").json()["items"]
    assert [(i["title"], i["url"]) for i in found] == [
        ("Access control policy", f"/isms-document.html?id={world.document.id}")
    ]
    assert isms_author.get("/v1/stories/choices/document?q=zzz").json()["items"] == []


def test_list_filters_by_status_and_text(world):
    author = world.client("stories.manage")
    first = world.create(author, title="Phishing wave", summary="Mail campaign")
    second = world.create(author, title="Payroll export incident", status="closed")
    items = author.get("/v1/stories").json()["items"]
    assert [s["title"] for s in items] == [second["title"], first["title"]]
    assert [
        s["id"] for s in author.get("/v1/stories?status=closed").json()["items"]
    ] == [second["id"]]
    assert [s["id"] for s in author.get("/v1/stories?q=campaign").json()["items"]] == [
        first["id"]
    ]
    assert author.get("/v1/stories?status=archived").status_code == 400
    assert (
        author.get("/v1/stories?limit=1&offset=1").json()["items"][0]["id"]
        == first["id"]
    )
    assert settings.default_framework_slug  # the list carries no framework scoping


def test_the_service_checks_permissions_itself(world):
    """Callers outside the HTTP adapters meet the same gates."""
    from app.services.errors import DomainError

    author = world.keen.user("stories.manage")
    reader = world.keen.user("stories.read")
    nobody = world.keen.user()
    story = service.create_story(
        payload=service.StoryPayload(title="Service"),
        framework=FW,
        user=author,
        db=world.db,
        context=None,
    )
    for call, code in (
        (lambda: service.list_stories(user=nobody, db=world.db), 403),
        (
            lambda: service.create_story(
                payload=service.StoryPayload(title="x"),
                framework=FW,
                user=reader,
                db=world.db,
                context=None,
            ),
            403,
        ),
        (lambda: service.choices(kind="risk", q="", user=reader, db=world.db), 403),
        (
            lambda: service.changelog(
                story_id=story["id"], limit=10, offset=0, user=nobody, db=world.db
            ),
            403,
        ),
        (lambda: service._editable_story(world.db, reader, story["id"]), 403),
    ):
        with pytest.raises(DomainError) as refused:
            call()
        assert refused.value.status_code == code
        world.db.rollback()
    # A reader is told which permission edits need; a manager who did not
    # create the story is told about ownership.
    with pytest.raises(DomainError, match="stories.manage permission required"):
        service._editable_story(world.db, reader, story["id"])
    world.db.rollback()
    with pytest.raises(DomainError, match="stories you created"):
        service._editable_story(
            world.db, world.keen.user("stories.manage"), story["id"]
        )
    world.db.rollback()
    assert service.readable_story(world.db, nobody, story["id"]) is None
    assert service.readable_story(world.db, reader, story["id"]).title == "Service"
    assert service.readable_story(world.db, reader, "not-a-uuid") is None


def test_story_edits_change_named_fields_and_keep_items_to_their_action(world):
    # A story edit updates the summary alone when that is all it names, and
    # items cannot be smuggled into a story edit.
    author = world.client("stories.manage", "events.read")
    story = world.create(
        author, summary="Before", items=[world.item("event", world.login)]
    )
    path = f"/v1/stories/{story['id']}"
    refused = author.patch(
        path,
        json={"items": [world.item("event", world.export)], "expected_version": 1},
        headers=unsafe(author),
    )
    assert refused.status_code == 400
    assert "items action" in refused.json()["detail"]
    changed = author.patch(
        path,
        json={"summary": "  After  ", "expected_version": 1},
        headers=unsafe(author),
    )
    assert changed.status_code == 200, changed.text
    assert (changed.json()["summary"], changed.json()["title"]) == (
        "After",
        story["title"],
    )
    assert len(changed.json()["items"]) == 1
    (item,) = changed.json()["items"]
    noted = author.patch(
        f"{path}/items/{item['id']}",
        json={"note": "  Reviewed by the on-call engineer  ", "expected_version": 2},
        headers=unsafe(author),
    )
    assert noted.status_code == 200, noted.text
    assert noted.json()["items"][0]["note"] == "Reviewed by the on-call engineer"
    assert noted.json()["items"][0]["label"] == item["label"]
    # An author-set time with an offset is stored in UTC.
    timed = author.patch(
        f"{path}/items/{item['id']}",
        json={"occurred_at": "2026-05-02T09:30:00+10:00", "expected_version": 3},
        headers=unsafe(author),
    )
    assert timed.status_code == 200, timed.text
    assert timed.json()["items"][0]["occurred_at"].startswith("2026-05-01T23:30:00")
    plain = author.patch(
        f"{path}/items/{item['id']}",
        json={"occurred_at": "2026-05-03T08:00:00", "expected_version": 4},
        headers=unsafe(author),
    )
    assert plain.json()["items"][0]["occurred_at"].startswith("2026-05-03T08:00:00")


def test_an_event_item_needs_an_existing_event(world):
    author = world.client("stories.manage", "events.read")
    story = world.create(author)
    missing = author.post(
        f"/v1/stories/{story['id']}/items",
        json={
            "items": [{"kind": "event", "entity_id": str(uuid.uuid4())}],
            "expected_version": 1,
        },
        headers=unsafe(author),
    )
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Event not found"
    assert world.db.query(StoryItem).count() == 0


def test_a_story_holds_a_bounded_number_of_comments(world):
    from app.db.models import StoryComment

    author = world.client("stories.manage")
    story = world.create(author)
    reader_user = world.keen.user("stories.read")
    reader = world.keen.client(reader_user)
    story_id = uuid.UUID(story["id"])
    for index in range(service.MAX_COMMENTS):
        world.db.add(
            StoryComment(
                story_id=story_id,
                body=f"Comment {index}",
                created_by_user_id=reader_user.id,
            )
        )
    world.db.commit()
    refused = reader.post(
        f"/v1/stories/{story['id']}/comments",
        json={"body": "One more"},
        headers=unsafe(reader),
    )
    assert refused.status_code == 400
    assert str(service.MAX_COMMENTS) in refused.json()["detail"]
    assert (
        world.db.query(StoryComment).filter_by(story_id=story_id).count()
        == service.MAX_COMMENTS
    )


def test_text_and_author_helpers_guard_their_bounds(world):
    from app.services.errors import DomainError

    with pytest.raises(DomainError, match="note is too long"):
        service._text("x" * 11, max_len=10, label="note")
    assert service._text("  a   b  ", max_len=10, label="title") == "a b"
    assert service._username(world.db, None) is None
    author = world.keen.user("stories.manage")
    assert service._username(world.db, author.id) == author.username
