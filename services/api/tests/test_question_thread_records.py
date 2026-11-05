"""Question threads over their lifetime: after the asking account is removed,
with files on a reply, when deleted while linked to collected evidence, when
badge counts cannot be read, and when stored for a record type Keen does not
support.

Requests run through the assembled application with real sign-in, CSRF and
permission resolution; the realtime publish and Celery queue are recorders.
"""

from app.db.models import (
    Artifact,
    EventQuestionPost,
    EventQuestionPostAttachment,
    EventQuestionThread,
)
from app.services import questions
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

# Imported fixtures: question targets and recorders on the assembled application.
from tests.test_assembled_authority_regressions import keen, unsafe  # noqa: F401
from tests.test_question_targets import PDF, PNG, qa  # noqa: F401


def _event_question(qa, client, files=None, body="See attached"):  # noqa: F811
    return client.post(
        f"/v1/events/{qa.event.id}/questions",
        data={"body": body},
        files=[("attachments", f) for f in files or []] or None,
        headers=unsafe(client),
    )


def test_questions_outlive_the_account_that_asked_them(qa):  # noqa: F811
    author, client = qa.member("question.create", "events.read", "risk.read")
    evidence = _event_question(qa, client, body="Who ran this?").json()["thread_id"]
    risk = qa.ask(client, "risk", "Who owns this?").json()["thread_id"]
    removed = qa.admin.delete(f"/v1/users/{author.id}", headers=unsafe(qa.admin))
    assert removed.status_code == 200, removed.text
    administrator = qa.admin.get("/v1/me").json()["user"]
    qa.published.clear()

    answered = qa.reply(qa.admin, evidence, "The release pipeline")
    qa.reply(qa.admin, risk, "The data owner")

    assert answered.json()["status"] == "answered"
    # Nobody is told about an unread reply; administrators see the new count.
    assert qa.latest("user") is None
    assert qa.latest("admin")["open_count"] == 0
    (listed,) = qa.admin.get(f"/v1/events/{qa.event.id}/questions").json()["threads"]
    (asked,) = qa.admin.get(qa.path("risk")).json()["threads"]
    for thread, question, answer in (
        (listed, "Who ran this?", "The release pipeline"),
        (asked, "Who owns this?", "The data owner"),
    ):
        assert thread["created_by_user_id"] is None
        assert [
            (p["body"], p["author_user_id"] is None, p["author_username"])
            for p in thread["posts"]
        ] == [(question, True, ""), (answer, False, administrator)]
    deleted = qa.admin.delete(f"/v1/questions/{risk}", headers=unsafe(qa.admin))
    assert deleted.json() == {"thread_id": risk, "deleted": True}
    assert qa.latest("user") is None


def test_a_reply_can_carry_attachments(qa):  # noqa: F811
    _, client = qa.member("question.create", "events.read")
    thread_id = _event_question(qa, client, body="Is this expected?").json()[
        "thread_id"
    ]

    replied = qa.reply(
        client,
        thread_id,
        "Here is the report",
        files=[
            ("attachments", ("report.pdf", PDF, "application/pdf")),
            ("attachments", ("photo.png", PNG, "image/png")),
        ],
    )

    assert replied.status_code == 200, replied.text
    (thread,) = client.get(f"/v1/events/{qa.event.id}/questions").json()["threads"]
    first, reply = thread["posts"]
    assert first["attachments"] == []
    assert sorted((a["filename"], a["content_type"]) for a in reply["attachments"]) == [
        ("photo.png", "image/png"),
        ("report.pdf", "application/pdf"),
    ]
    stored = qa.rows(Artifact, kind="question_attachment")
    assert sorted(a.size_bytes for a in stored) == sorted([len(PDF), len(PNG)])
    assert {a.event_id for a in stored} == {qa.event.id}
    assert qa.queued.replied.call_count == 1


def test_deleting_a_question_keeps_collected_evidence_it_points_at(qa):  # noqa: F811
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
    asked = _event_question(qa, client, [("report.pdf", PDF, "application/pdf")])
    thread_id = asked.json()["thread_id"]
    (post,) = qa.rows(EventQuestionPost)
    # A link to collected evidence, as an older release or an import could hold.
    qa.db.add(EventQuestionPostAttachment(post_id=post.id, artifact_id=collected.id))
    qa.db.commit()
    (uploaded,) = [a.storage_uri for a in qa.rows(Artifact, kind="question_attachment")]

    deleted = qa.admin.delete(f"/v1/questions/{thread_id}", headers=unsafe(qa.admin))

    assert deleted.status_code == 200, deleted.text
    assert qa.rows(EventQuestionPostAttachment) == []
    assert [a.id for a in qa.rows(Artifact)] == [collected.id]
    queue = qa.db.execute(text("SELECT storage_uri FROM evidence_object_cleanup"))
    assert list(queue.scalars()) == [uploaded]


def test_replies_are_saved_when_badge_counts_cannot_be_read(qa, monkeypatch):  # noqa: F811
    author, client = qa.member("question.create", "risk.read")
    thread_id = qa.ask(client, "risk").json()["thread_id"]

    def unavailable(*args):
        raise OperationalError("SELECT count(*)", {}, Exception("connection lost"))

    monkeypatch.setattr(questions, "_count_open_questions", unavailable)
    monkeypatch.setattr(questions, "_count_unread_replies_for_user", unavailable)
    qa.published.clear()

    answered = qa.reply(qa.admin, thread_id, "Planned work")
    followed = qa.reply(client, thread_id, "Thanks")

    assert (answered.status_code, followed.status_code) == (200, 200)
    assert followed.json()["status"] == "unanswered"
    # Without counts nothing is pushed; the replies stand.
    assert qa.published == []
    (thread,) = qa.rows(EventQuestionThread)
    assert [p.body for p in thread.posts] == [
        "Why is this rated high?",
        "Planned work",
        "Thanks",
    ]


def test_a_thread_of_an_unsupported_record_type_stops_the_personal_list(qa):  # noqa: F811
    author, client = qa.member("question.create", "risk.read", "events.read")
    risk = qa.ask(client, "risk").json()["thread_id"]
    _event_question(qa, client, body="Expected?")
    qa.revoke(author, "events.read")
    # A record the author can no longer see is left out of their list.
    (item,) = client.get("/v1/me/questions").json()["items"]
    assert (item["thread_id"], item["target_type"]) == (risk, "risk")

    stored = qa.db.get(EventQuestionThread, risk)
    stored.target_type = "control"
    qa.db.commit()

    refused = client.get("/v1/me/questions")

    # Keen cannot tell whether the author may see such a record, so it refuses
    # the list rather than show or silently drop the thread.
    assert (refused.status_code, refused.json()["detail"]) == (
        400,
        "Unsupported question target type",
    )
