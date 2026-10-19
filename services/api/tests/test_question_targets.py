"""Questions about risks, PESTLE(E) items, SWOT items and evidence.

Requests run through the assembled application: real sign-in, CSRF, route
feature policy and permission resolution. Only the realtime publish and the
Celery queue are replaced, by recorders, so their messages can be checked.
"""

import hashlib
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from app.core.config import settings
from app.db.models import (
    Artifact,
    Event,
    EventQuestionPost,
    EventQuestionPostAttachment,
    EventQuestionThread,
    IsmsOrgNode,
    IsmsOrgNodeUser,
    IsmsSwot,
    Permission,
    PestleItem,
    PestleRelevanceLevel,
    Risk,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
)
from app.realtime.notifications import notification_bus
from app.services import questions
from app.worker import tasks
from sqlalchemy import text

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)

PDF = b"%PDF-1.7\nsynthetic report"
PNG = b"\x89PNG\r\n\x1a\nsynthetic image"
DISABLED = "This feature is disabled by an administrator."
EVERY_READ = ("risk.read", "pestle.read", "isms.read", "events.read")


@pytest.fixture
def qa(keen, monkeypatch, tmp_path):  # noqa: F811 - fixture imported above
    published = []

    async def publish(payload):
        published.append(payload)

    monkeypatch.setattr(notification_bus, "publish", publish)
    queued = SimpleNamespace(asked=Mock(), replied=Mock())
    monkeypatch.setattr(tasks.send_question_webhooks_task, "delay", queued.asked)
    monkeypatch.setattr(
        tasks.send_question_reply_webhooks_task, "delay", queued.replied
    )

    db = keen.db
    category = RiskCategory(name="Data")
    db.add(category)
    db.flush()
    subcategory = RiskAssetSubcategory(name="Databases", category_id=category.id)
    db.add(subcategory)
    db.flush()
    asset = RiskAsset(
        name="Payroll database",
        category_id=category.id,
        subcategory_id=subcategory.id,
    )
    level = PestleRelevanceLevel(code="high", label="High", sort_order=1)
    db.add_all([asset, level])
    db.flush()
    risk = Risk(asset_id=asset.id, threat_summary="Unauthorised export")
    other_risk = Risk(asset_id=asset.id, threat_summary="Ransomware")
    pestle = PestleItem(
        framework_slug=settings.default_framework_slug,
        type="Legal",
        lens="External",
        item="Privacy law reform",
        overall_relevance_id=level.id,
    )
    swot = IsmsSwot(title="Ageing hardware", kind="weakness")
    event = Event(
        source="cloud-logs",
        summary="Root account sign-in",
        timestamp=datetime(2026, 5, 1, 9, 30),
        external_id=uuid.uuid4().hex,
        raw_pointer={},
        normalized_payload={},
    )
    db.add_all([risk, other_risk, pestle, swot, event])
    db.commit()
    admin = keen.client(keen.user(role="admin"))
    ids = {
        "risk": risk.id,
        "pestle_item": pestle.id,
        "isms_swot": swot.id,
        "event": event.id,
    }

    def member(*codes):
        user = keen.user(*codes)
        return user, keen.client(user)

    def path(target, target_id=None):
        return f"/v1/questions/entities/{target}/{target_id or ids[target]}"

    def ask(client, target, body="Why is this rated high?", target_id=None):
        return client.post(
            path(target, target_id), json={"body": body}, headers=unsafe(client)
        )

    def reply(client, thread_id, body="More detail, please", files=None):
        url = f"/v1/questions/{thread_id}/posts"
        if files is not None:
            return client.post(
                url, data={"body": body}, files=files, headers=unsafe(client)
            )
        return client.post(url, json={"body": body}, headers=unsafe(client))

    def switch(**changes):
        version = admin.get("/v1/admin/features").json()["version"]
        response = admin.put(
            "/v1/admin/features",
            json={"version": version, "changes": changes},
            headers=unsafe(admin),
        )
        assert response.status_code == 200, response.text

    def revoke(user, code):
        db.refresh(user)
        user.permissions.remove(db.query(Permission).filter_by(code=code).one())
        user.authz_version += 1
        db.commit()

    def rows(model, **filters):
        db.expire_all()
        return db.query(model).filter_by(**filters).all()

    def latest(scope, user=None):
        for payload in reversed(published):
            if payload["scope"] == scope and (
                user is None or payload["user_id"] == str(user.id)
            ):
                return payload
        return None

    return SimpleNamespace(
        keen=keen,
        db=db,
        tmp_path=tmp_path,
        risk=risk,
        other_risk=other_risk,
        pestle=pestle,
        swot=swot,
        event=event,
        admin=admin,
        published=published,
        queued=queued,
        member=member,
        path=path,
        ask=ask,
        reply=reply,
        switch=switch,
        revoke=revoke,
        rows=rows,
        latest=latest,
    )


def _stored_files(qa):
    return sorted(p for p in (qa.tmp_path / "questions").rglob("*") if p.is_file())


# --- Who may ask about which record ---------------------------------------------


ACCESS = [
    ("risk", "risk.read", True),
    ("risk", "risk.manage", True),
    ("risk", "pestle.read", False),
    ("pestle_item", "pestle.read", True),
    ("pestle_item", "pestle.manage", True),
    ("pestle_item", "risk.read", True),
    ("pestle_item", "isms.read", False),
    ("isms_swot", "isms.read", True),
    ("isms_swot", "isms.manage", True),
    ("isms_swot", "pestle.read", False),
    ("event", "events.read", True),
    ("event", "risk.read", False),
]
REFUSAL = {
    "risk": "risk.read permission or risk ownership required",
    "pestle_item": "pestle.read permission required",
    "isms_swot": "ISMS access required",
    "event": "events.read permission required",
}


@pytest.mark.parametrize(
    "target,permission,allowed",
    ACCESS,
    ids=[f"{target}-{permission}" for target, permission, _ in ACCESS],
)
def test_each_record_needs_read_access_to_that_record(qa, target, permission, allowed):
    _, client = qa.member("question.create", permission)

    asked = qa.ask(client, target, "Who approved this?")
    listed = client.get(qa.path(target))
    seen = client.post(qa.path(target) + "/mark-seen", headers=unsafe(client))

    if allowed:
        assert asked.status_code == 200, asked.text
        (thread,) = listed.json()["threads"]
        assert (thread["id"], thread["target_type"]) == (
            asked.json()["thread_id"],
            target,
        )
        assert [post["body"] for post in thread["posts"]] == ["Who approved this?"]
        assert seen.json() == {"marked": 0, "unread_count": 0}
    else:
        for response in (asked, listed, seen):
            assert response.status_code == 403, response.text
            assert response.json()["detail"] == REFUSAL[target]
        assert qa.rows(EventQuestionThread) == []


def test_reading_questions_needs_record_access_and_asking_needs_question_create(qa):
    _, reader = qa.member("risk.read")
    _, asker = qa.member("risk.read", "question.create")
    qa.ask(asker, "risk", "Is this still accepted?")

    refused = qa.ask(reader, "risk")

    assert refused.status_code == 403
    assert refused.json()["detail"] == "question.create permission required"
    (thread,) = reader.get(qa.path("risk")).json()["threads"]
    assert thread["posts"][0]["body"] == "Is this still accepted?"
    assert len(qa.rows(EventQuestionThread)) == 1


def test_risk_owners_can_ask_about_the_risks_they_own(qa):
    owner, owner_client = qa.member("question.create")
    member, member_client = qa.member("question.create")
    role = IsmsOrgNode(name="Payroll service owners")
    qa.db.add(role)
    qa.db.flush()
    qa.db.add(IsmsOrgNodeUser(org_node_id=role.id, user_id=member.id))
    qa.risk.risk_owner_user_id = owner.id
    qa.other_risk.risk_owner_role_id = role.id
    qa.db.commit()

    for client, own, other in (
        (owner_client, qa.risk, qa.other_risk),
        (member_client, qa.other_risk, qa.risk),
    ):
        assert qa.ask(client, "risk", target_id=own.id).status_code == 200
        refused = qa.ask(client, "risk", target_id=other.id)
        assert refused.status_code == 403
        assert refused.json()["detail"] == REFUSAL["risk"]
    assert sorted(t.target_id for t in qa.rows(EventQuestionThread)) == sorted(
        [qa.risk.id, qa.other_risk.id]
    )


@pytest.mark.parametrize(
    "name,target",
    [
        ("cia_risk", "risk"),
        ("cia_triad_risk", "risk"),
        ("Risk", "risk"),
        ("pestle", "pestle_item"),
        ("pestle_risk", "pestle_item"),
        ("isms-swot", "isms_swot"),
        ("evidence", "event"),
        ("spaceship", "risk"),
    ],
)
def test_question_routes_accept_only_the_stored_target_names(qa, name, target):
    _, client = qa.member("question.create", *EVERY_READ)
    record = {
        "risk": qa.risk.id,
        "pestle_item": qa.pestle.id,
        "isms_swot": qa.swot.id,
        "event": qa.event.id,
    }[target]
    path = f"/v1/questions/entities/{name}/{record}"

    for response in (
        client.get(path),
        client.post(path, json={"body": "Hello?"}, headers=unsafe(client)),
        client.post(path + "/mark-seen", headers=unsafe(client)),
    ):
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "Unsupported question target type"
    assert qa.rows(EventQuestionThread) == []


@pytest.mark.parametrize(
    "target,missing",
    [
        ("risk", "CIA Triad risk not found"),
        ("pestle_item", "PESTLE(E) item not found"),
        ("isms_swot", "SWOT item not found"),
        ("event", "Event not found"),
    ],
)
def test_questions_about_missing_records_are_refused(qa, target, missing):
    _, client = qa.member("question.create", *EVERY_READ)

    unknown = qa.ask(client, target, target_id=uuid.uuid4())
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == missing
    malformed = qa.ask(client, target, target_id="not-a-uuid")
    assert malformed.status_code == 400
    assert malformed.json()["detail"] == "Invalid entity id"
    assert client.get(qa.path(target, "not-a-uuid")).status_code == 400
    assert qa.rows(EventQuestionThread) == []


def test_record_lists_keep_threads_apart_by_type_and_record(qa):
    # A SWOT item that shares the risk's identifier keeps its own questions.
    twin = IsmsSwot(id=qa.risk.id, title="Twin", kind="threat")
    qa.db.add(twin)
    qa.db.commit()
    _, client = qa.member("question.create", *EVERY_READ)
    risk_thread = qa.ask(client, "risk", "About the risk").json()["thread_id"]
    qa.ask(client, "risk", "About another risk", target_id=qa.other_risk.id)
    qa.ask(client, "isms_swot", "About the twin", target_id=twin.id)

    (thread,) = client.get(qa.path("risk")).json()["threads"]

    assert thread["id"] == risk_thread
    assert [post["body"] for post in thread["posts"]] == ["About the risk"]
    (swot_thread,) = client.get(qa.path("isms_swot", twin.id)).json()["threads"]
    assert swot_thread["posts"][0]["body"] == "About the twin"


def test_a_hidden_diary_entry_cannot_be_questioned(qa):
    # A framework-link diary entry about a risk is hidden without risk access.
    entry = Event(
        source="diary",
        system="isms",
        summary="Risk linked to controls",
        timestamp=datetime(2026, 3, 1),
        external_id=f"isms-link:risk:{qa.risk.id}",
        raw_pointer={},
        normalized_payload={},
    )
    qa.db.add(entry)
    qa.db.commit()
    _, blind = qa.member("question.create", "events.read")
    _, sighted = qa.member("question.create", "events.read", "risk.read")
    base = f"/v1/events/{entry.id}/questions"

    for response in (
        blind.post(base, json={"body": "What changed?"}, headers=unsafe(blind)),
        blind.get(base),
        qa.ask(blind, "event", target_id=entry.id),
    ):
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "Event not found"
    assert qa.rows(EventQuestionThread) == []

    asked = sighted.post(base, json={"body": "What changed?"}, headers=unsafe(sighted))
    assert asked.status_code == 200, asked.text
    assert blind.get(base).status_code == 404


# --- Feature switches -----------------------------------------------------------


@pytest.mark.parametrize(
    "target,feature",
    [
        ("risk", "risk_cia"),
        ("pestle_item", "pestle"),
        ("isms_swot", "swot"),
        ("event", "evidence"),
    ],
)
def test_questions_follow_the_switch_for_their_record(qa, target, feature):
    _, client = qa.member("question.create", "question.delete", *EVERY_READ)
    thread_id = qa.ask(client, target).json()["thread_id"]
    assert qa.reply(qa.admin, thread_id, "Answered").status_code == 200

    qa.switch(**{feature: False})

    attempts = [
        client.get(qa.path(target)),
        qa.ask(client, target, "Again"),
        client.post(qa.path(target) + "/mark-seen", headers=unsafe(client)),
        # Thread routes reach the record only through the stored thread.
        qa.reply(client, thread_id, "Follow-up"),
        client.delete(f"/v1/questions/{thread_id}", headers=unsafe(client)),
    ]
    if target == "event":
        base = f"/v1/events/{qa.event.id}/questions"
        attempts += [
            client.get(base),
            client.post(base, json={"body": "Again"}, headers=unsafe(client)),
            client.post(base + "/mark-seen", headers=unsafe(client)),
        ]
    for response in attempts:
        assert response.status_code == 404, response.request.url
        assert response.json()["detail"] == DISABLED
    assert client.get("/v1/me/questions").json() == {"items": []}
    (thread,) = qa.rows(EventQuestionThread)
    assert [post.body for post in thread.posts] == [
        "Why is this rated high?",
        "Answered",
    ]

    qa.switch(**{feature: True})
    (item,) = client.get("/v1/me/questions").json()["items"]
    assert (item["thread_id"], item["unread"]) == (thread_id, True)


def test_route_policy_holds_evidence_questions_back_before_the_body_is_read(
    qa, monkeypatch
):
    _, client = qa.member("question.create", "events.read")
    qa.switch(evidence=False)
    # The route policy answers before the question operation parses the upload.
    parsed = []

    async def question_input(request):
        parsed.append(request.url.path)
        return "", []

    from app.api.routes import questions as routes

    monkeypatch.setattr(routes, "question_input", question_input)

    refused = client.post(
        f"/v1/events/{qa.event.id}/questions",
        data={"body": "Attached"},
        files=[("attachments", ("report.pdf", PDF, "application/pdf"))],
        headers=unsafe(client),
    )

    assert refused.status_code == 404
    assert refused.json()["detail"] == DISABLED
    assert parsed == []


# --- Replies, status and unread replies -----------------------------------------


def test_replies_come_from_admins_or_the_author_and_move_the_status(qa):
    author, client = qa.member("question.create", "risk.read")
    _, stranger = qa.member("question.create", "risk.read", "question.delete")
    thread_id = qa.ask(client, "risk", "Who approved this?").json()["thread_id"]
    assert qa.latest("admin")["open_count"] == 1

    refused = qa.reply(stranger, thread_id, "Me too")
    assert refused.status_code == 403
    assert refused.json()["detail"] == "Only admins or the thread author can reply"
    blank = qa.reply(client, thread_id, "   ")
    assert blank.status_code == 400 and blank.json()["detail"] == "Body is required"

    answered = qa.reply(qa.admin, thread_id, "The security committee")
    assert answered.json()["status"] == "answered"
    assert qa.latest("admin")["open_count"] == 0
    assert qa.latest("user", author)["unread_count"] == 1
    assert client.get("/v1/me/questions/summary").json() == {"unread_count": 1}
    (item,) = client.get("/v1/me/questions").json()["items"]
    assert (item["status"], item["unread"]) == ("answered", True)
    assert item["target_label"] == (
        "CIA Triad risk: Payroll database — Unauthorised export"
    )
    assert (
        item["target_url"] == f"/risk.html?id={qa.risk.id}&thread={thread_id}#questions"
    )

    seen = client.post(qa.path("risk") + "/mark-seen", headers=unsafe(client))
    assert seen.json() == {"marked": 1, "unread_count": 0}
    assert qa.latest("user", author)["unread_count"] == 0
    again = client.post(qa.path("risk") + "/mark-seen", headers=unsafe(client))
    assert again.json() == {"marked": 0, "unread_count": 0}

    reopened = qa.reply(client, thread_id, "Can I see the minutes?")
    assert reopened.json()["status"] == "unanswered"
    assert qa.latest("admin")["open_count"] == 1
    (thread,) = qa.rows(EventQuestionThread)
    assert [post.body for post in thread.posts] == [
        "Who approved this?",
        "The security committee",
        "Can I see the minutes?",
    ]
    # A new reply from the author leaves nothing unread for them.
    assert client.get("/v1/me/questions/summary").json() == {"unread_count": 0}


def test_an_authors_reply_counts_as_reading_the_answer(qa):
    author, client = qa.member("question.create", "risk.read")
    thread_id = qa.ask(client, "risk").json()["thread_id"]
    qa.reply(qa.admin, thread_id, "Answer")
    assert client.get("/v1/me/questions/summary").json() == {"unread_count": 1}

    qa.reply(client, thread_id, "Thanks, one more thing")

    assert client.get("/v1/me/questions/summary").json() == {"unread_count": 0}
    assert qa.latest("user", author)["unread_count"] == 0


def test_marking_one_record_seen_leaves_other_records_unread(qa):
    author, client = qa.member("question.create", "risk.read")
    first = qa.ask(client, "risk").json()["thread_id"]
    second = qa.ask(client, "risk", target_id=qa.other_risk.id).json()["thread_id"]
    qa.reply(qa.admin, first, "Answer one")
    qa.reply(qa.admin, second, "Answer two")
    # The administrator's own view of the record marks nothing for the author.
    qa.admin.post(qa.path("risk") + "/mark-seen", headers=unsafe(qa.admin))

    seen = client.post(qa.path("risk") + "/mark-seen", headers=unsafe(client))

    assert seen.json() == {"marked": 1, "unread_count": 1}
    unread = {
        i["thread_id"]: i["unread"]
        for i in client.get("/v1/me/questions").json()["items"]
    }
    assert unread == {first: False, second: True}


def test_replies_recheck_that_the_author_can_still_see_the_record(qa):
    author, client = qa.member("question.create", "risk.read")
    thread_id = qa.ask(client, "risk").json()["thread_id"]

    qa.revoke(author, "risk.read")

    refused = qa.reply(client, thread_id, "Still there?")
    assert refused.status_code == 403
    assert refused.json()["detail"] == REFUSAL["risk"]
    assert client.get("/v1/me/questions").json() == {"items": []}
    (thread,) = qa.rows(EventQuestionThread)
    assert len(thread.posts) == 1


def test_threads_without_a_reachable_record_refuse_replies(qa):
    author, client = qa.member("question.create", "risk.read")
    thread_id = qa.ask(client, "risk", target_id=qa.other_risk.id).json()["thread_id"]
    orphan = EventQuestionThread(
        target_type="risk",
        target_id=None,
        created_by_user_id=author.id,
        status="unanswered",
    )
    qa.db.add(orphan)
    qa.db.delete(qa.db.get(Risk, qa.other_risk.id))
    qa.db.commit()

    deleted = qa.reply(client, thread_id, "Is it gone?")
    assert deleted.status_code == 404
    assert deleted.json()["detail"] == "CIA Triad risk not found"
    untargeted = qa.reply(client, orphan.id, "Anyone?")
    assert untargeted.status_code == 404
    assert untargeted.json()["detail"] == "Question target not found"
    assert [post.body for post in qa.rows(EventQuestionPost)] == [
        "Why is this rated high?"
    ]
    assert client.get("/v1/me/questions").json() == {"items": []}


def test_reply_requests_are_checked_before_anything_is_stored(qa):
    _, client = qa.member("question.create", "risk.read")
    thread_id = qa.ask(client, "risk").json()["thread_id"]

    for target, status, detail in (
        ("not-a-uuid", 400, "Invalid thread id"),
        (uuid.uuid4(), 404, "Thread not found"),
    ):
        response = qa.reply(client, target)
        assert response.status_code == status
        assert response.json()["detail"] == detail
    attached = qa.reply(
        client,
        thread_id,
        files=[("attachments", ("report.pdf", PDF, "application/pdf"))],
    )
    assert attached.status_code == 400
    assert attached.json()["detail"] == (
        "Attachments are currently only supported for evidence questions"
    )
    assert len(qa.rows(EventQuestionPost)) == 1
    assert qa.rows(Artifact) == []


# --- Outbound notifications -----------------------------------------------------


def test_only_evidence_questions_are_queued_for_outbound_webhooks(qa):
    _, client = qa.member("question.create", "events.read", "risk.read")
    event_thread = client.post(
        f"/v1/events/{qa.event.id}/questions",
        json={"body": "Was this expected?"},
        headers=unsafe(client),
    ).json()
    risk_thread = qa.ask(client, "risk").json()
    qa.reply(qa.admin, risk_thread["thread_id"], "Yes")

    reply = qa.reply(qa.admin, event_thread["thread_id"], "Yes, planned work")

    qa.queued.asked.assert_called_once_with(
        event_thread["thread_id"], event_thread["post_id"]
    )
    qa.queued.replied.assert_called_once_with(
        event_thread["thread_id"], reply.json()["post_id"]
    )


# --- Deletion -------------------------------------------------------------------


def test_deleting_a_question_needs_question_delete_and_sight_of_the_record(qa):
    author, client = qa.member("question.create", "risk.read")
    _, blind = qa.member("question.delete")
    _, moderator = qa.member("question.delete", "risk.read")
    thread_id = qa.ask(client, "risk").json()["thread_id"]
    # Leave the thread both open and unread so deletion changes both badges.
    qa.reply(qa.admin, thread_id, "Answer")
    qa.admin.patch(
        f"/v1/admin/questions/{thread_id}",
        json={"status": "reviewing"},
        headers=unsafe(qa.admin),
    )
    assert qa.latest("admin")["open_count"] == 1
    assert qa.latest("user", author)["unread_count"] == 1

    def delete(who, target=thread_id):
        return who.delete(f"/v1/questions/{target}", headers=unsafe(who))

    own = delete(client)
    assert own.status_code == 403
    assert own.json()["detail"] == "question.delete permission required"
    hidden = delete(blind)
    assert hidden.status_code == 403
    assert hidden.json()["detail"] == REFUSAL["risk"]
    assert len(qa.rows(EventQuestionThread)) == 1
    assert delete(moderator, "not-a-uuid").status_code == 400
    assert delete(moderator, uuid.uuid4()).status_code == 404

    assert delete(moderator).json() == {"thread_id": thread_id, "deleted": True}

    assert qa.rows(EventQuestionThread) == [] and qa.rows(EventQuestionPost) == []
    assert qa.latest("admin")["open_count"] == 0
    assert qa.latest("user", author)["unread_count"] == 0
    assert client.get("/v1/me/questions/summary").json() == {"unread_count": 0}
    assert client.get("/v1/me/questions").json() == {"items": []}


def test_deleting_an_evidence_question_removes_its_attachments(qa):
    collected = Artifact(
        event_id=qa.event.id,
        kind="raw",
        storage_uri="local/collected.json",
        sha256="0" * 64,
        content_type="application/json",
        size_bytes=2,
    )
    qa.db.add(collected)
    qa.db.commit()
    _, client = qa.member("question.create", "events.read")
    asked = client.post(
        f"/v1/events/{qa.event.id}/questions",
        data={"body": "See attached"},
        files=[
            ("attachments", ("report.pdf", PDF, "application/pdf")),
            ("attachments", ("photo.png", PNG, "image/png")),
        ],
        headers=unsafe(client),
    )
    assert asked.status_code == 200, asked.text
    stored = qa.rows(Artifact, kind="question_attachment")
    assert sorted(a.content_type for a in stored) == ["application/pdf", "image/png"]
    assert sorted(p.read_bytes() for p in _stored_files(qa)) == sorted([PDF, PNG])
    uris = sorted(a.storage_uri for a in stored)

    deleted = qa.admin.delete(
        f"/v1/questions/{asked.json()['thread_id']}", headers=unsafe(qa.admin)
    )

    assert deleted.status_code == 200, deleted.text
    assert qa.rows(Artifact, kind="question_attachment") == []
    assert qa.rows(EventQuestionPostAttachment) == []
    queue = qa.db.execute(text("SELECT storage_uri FROM evidence_object_cleanup"))
    assert sorted(queue.scalars()) == uris
    # The event's collected evidence is untouched.
    assert [a.id for a in qa.rows(Artifact)] == [collected.id]


# --- Attachments ----------------------------------------------------------------


def _event_question(qa, client, files):
    return client.post(
        f"/v1/events/{qa.event.id}/questions",
        data={"body": "See attached"},
        files=[("attachments", f) for f in files],
        headers=unsafe(client),
    )


@pytest.mark.parametrize(
    "files,detail",
    [
        (
            [("report.pdf", b"MZ not a PDF", "application/pdf")],
            "Invalid file type: file content does not match declared content-type",
        ),
        (
            # The whole batch is checked before any file is written.
            [("report.pdf", PDF, "application/pdf"), ("photo.png", PDF, "image/png")],
            "Invalid file type: file content does not match declared content-type",
        ),
        (
            [("report.pdf", PDF, "application/pdf")] * 11,
            "At most 10 attachments are allowed",
        ),
    ],
    ids=["mismatched", "one-bad-in-a-batch", "too-many"],
)
def test_rejected_attachments_store_nothing(qa, files, detail):
    _, client = qa.member("question.create", "events.read")

    refused = _event_question(qa, client, files)

    assert refused.status_code == 400, refused.text
    assert refused.json()["detail"] == detail
    assert qa.rows(EventQuestionThread) == [] and qa.rows(Artifact) == []
    assert not (qa.tmp_path / "questions").exists()


def test_attachment_limits_are_inclusive(qa, monkeypatch):
    _, client = qa.member("question.create", "events.read")
    monkeypatch.setattr(questions, "MAX_ATTACHMENT_BYTES", len(PDF) - 1)

    large = _event_question(qa, client, [("report.pdf", PDF, "application/pdf")])
    assert large.status_code == 413
    assert large.json()["detail"] == "Question attachment is too large"
    assert qa.rows(EventQuestionThread) == [] and qa.rows(Artifact) == []

    monkeypatch.setattr(questions, "MAX_ATTACHMENT_BYTES", len(PDF))
    ten = _event_question(qa, client, [("report.pdf", PDF, "application/pdf")] * 10)
    assert ten.status_code == 200, ten.text
    assert len(qa.rows(Artifact, kind="question_attachment")) == 10
    # Empty parts are skipped rather than stored.
    empty = _event_question(qa, client, [("empty.txt", b"", "text/plain")])
    assert empty.status_code == 200
    assert len(qa.rows(Artifact, kind="question_attachment")) == 10


def test_only_evidence_questions_take_attachments(qa):
    _, client = qa.member("question.create", "risk.read")

    refused = client.post(
        qa.path("risk"),
        data={"body": "See attached"},
        files=[("attachments", ("report.pdf", PDF, "application/pdf"))],
        headers=unsafe(client),
    )

    assert refused.status_code == 400
    assert refused.json()["detail"] == (
        "Attachments are currently only supported for evidence questions"
    )
    assert qa.rows(EventQuestionThread) == [] and qa.rows(Artifact) == []


def test_attachments_download_as_files_and_stay_out_of_collected_evidence(qa):
    _, client = qa.member("question.create", "events.read")
    page = b"<script>alert(document.cookie)</script>"
    hostile = "../<b>report</b>.html"
    asked = _event_question(qa, client, [(hostile, page, "text/html")])
    assert asked.status_code == 200, asked.text

    (thread,) = client.get(f"/v1/events/{qa.event.id}/questions").json()["threads"]
    (attachment,) = thread["posts"][0]["attachments"]
    # The uploaded name is kept as data and never used for the download.
    assert attachment["filename"] == hostile
    assert (attachment["content_type"], attachment["size_bytes"]) == (
        "text/html",
        len(page),
    )
    artifact_id = attachment["artifact_id"]
    assert attachment["download_url"] == f"/api/v1/artifacts/{artifact_id}/download"

    download = client.get(f"/v1/artifacts/{artifact_id}/download")

    assert download.status_code == 200
    assert download.content == page
    name = f"artifact-{artifact_id}.html"
    assert download.headers["content-disposition"] == (
        f"attachment; filename=\"{name}\"; filename*=UTF-8''{name}"
    )
    assert download.headers["x-content-type-options"] == "nosniff"
    assert download.headers["x-artifact-sha256"] == hashlib.sha256(page).hexdigest()
    # Discussion material never joins the event's collected evidence.
    assert client.get(f"/v1/events/{qa.event.id}").json()["artifacts"] == []


# --- The administrator queue ----------------------------------------------------


def test_the_admin_queue_shows_open_questions_unless_asked_for_more(qa):
    _, client = qa.member("question.create", *EVERY_READ)
    risk_thread = qa.ask(client, "risk").json()["thread_id"]
    pestle_thread = qa.ask(client, "pestle_item").json()["thread_id"]
    swot_thread = qa.ask(client, "isms_swot").json()["thread_id"]
    for thread_id, status in ((pestle_thread, "reviewing"), (swot_thread, "answered")):
        changed = qa.admin.patch(
            f"/v1/admin/questions/{thread_id}",
            json={"status": status},
            headers=unsafe(qa.admin),
        )
        assert changed.json() == {"thread_id": thread_id, "status": status}

    def queue(**params):
        response = qa.admin.get("/v1/admin/questions", params=params)
        assert response.status_code == 200, response.text
        return response.json()["items"]

    # Most recently updated first.
    assert [(i["thread_id"], i["status"]) for i in queue()] == [
        (pestle_thread, "reviewing"),
        (risk_thread, "unanswered"),
    ]
    assert [i["thread_id"] for i in queue(status="answered")] == [swot_thread]
    everything = {i["thread_id"]: i for i in queue(status="all")}
    assert set(everything) == {risk_thread, pestle_thread, swot_thread}
    assert qa.admin.get("/v1/admin/questions/summary").json() == {"open_count": 2}
    assert qa.latest("admin")["open_count"] == 2

    labels = {
        thread_id: (item["target_label"], item["target_url"])
        for thread_id, item in everything.items()
    }
    assert labels == {
        risk_thread: (
            "CIA Triad risk: Payroll database — Unauthorised export",
            f"/risk.html?id={qa.risk.id}&thread={risk_thread}#questions",
        ),
        pestle_thread: (
            "PESTLE(E) item: Legal / External — Privacy law reform",
            f"/pestle_item.html?id={qa.pestle.id}&tab=questions"
            f"&thread={pestle_thread}#questions",
        ),
        swot_thread: (
            "SWOT item: weakness — Ageing hardware",
            f"/isms-swot.html?id={qa.swot.id}&tab=questions&thread={swot_thread}",
        ),
    }


def test_only_administrators_see_or_change_the_queue(qa):
    _, client = qa.member("question.create", "question.delete", *EVERY_READ)
    thread_id = qa.ask(client, "risk").json()["thread_id"]

    for response in (
        client.get("/v1/admin/questions"),
        client.get("/v1/admin/questions/summary"),
        client.patch(
            f"/v1/admin/questions/{thread_id}",
            json={"status": "answered"},
            headers=unsafe(client),
        ),
    ):
        assert response.status_code == 403, response.text
    (thread,) = qa.rows(EventQuestionThread)
    assert thread.status == "unanswered"
    for target, status in (("not-a-uuid", 400), (uuid.uuid4(), 404)):
        response = qa.admin.patch(
            f"/v1/admin/questions/{target}",
            json={"status": "answered"},
            headers=unsafe(qa.admin),
        )
        assert response.status_code == status


DOCX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@pytest.mark.parametrize(
    "name,data,content_type,accepted",
    [
        ("tiny.txt", b"abc", "text/plain", False),
        ("notes.txt", "café notes".encode(), "text/plain", True),
        # Text that is not UTF-8 is accepted only under a text file name.
        ("notes.txt", b"\xff\xfe\x00 binary", "text/plain", True),
        ("notes.exe", b"\xff\xfe\x00 binary", "text/plain", False),
        (
            "sheet.xls",
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 rest",
            "application/vnd.ms-excel",
            True,
        ),
        ("deck.pptx", b"PK\x03\x04 rest", DOCX, True),
        ("deck.pptx", b"%PDF-1.4 rest", DOCX, False),
        ("photo.gif", b"GIF89a rest", "image/gif", True),
        ("photo.jpg", b"\xff\xd8\xff\xe0 rest", "image/jpeg", True),
        ("tool.exe", b"MZ\x90\x00 rest", "application/x-msdownload", False),
        # Undeclared types are inferred from the file name and checked against it.
        ("report.pdf", b"%PDF-1.4 rest", "application/octet-stream", True),
        ("report.pdf", b"MZ\x90\x00 rest", "application/octet-stream", False),
        ("export.csv", b"a,b\n1,2\n", "application/octet-stream", True),
        ("README", b"plain words", "application/octet-stream", False),
    ],
)
def test_attachments_are_accepted_only_when_their_content_fits_their_type(
    qa, name, data, content_type, accepted
):
    _, client = qa.member("question.create", "events.read")

    response = _event_question(qa, client, [(name, data, content_type)])

    stored = qa.rows(Artifact, kind="question_attachment")
    if accepted:
        assert response.status_code == 200, response.text
        assert [(a.content_type, a.meta["filename"]) for a in stored] == [
            (content_type, name)
        ]
    else:
        assert (response.status_code, response.json()["detail"]) == (
            400,
            "Invalid file type: file content does not match declared content-type",
        )
        assert stored == [] and qa.rows(EventQuestionThread) == []
