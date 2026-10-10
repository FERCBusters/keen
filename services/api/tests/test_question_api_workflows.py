"""Question ownership, unread state and administrative transitions through HTTP."""

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, Mock

# Imported fixtures are discovered by pytest by name.
from tests.test_management_api_workflows import api as api

import pytest
from app.api.routes import questions
from app.db.models import Event, EventQuestionPost, EventQuestionThread, User
from app.worker import tasks

from tests.test_management_api_workflows import request


@pytest.fixture
def question_api(api, monkeypatch):
    # Only outbound delivery is replaced; access checks and question writes run.
    monkeypatch.setattr(questions, "_broadcast_open_count", AsyncMock())
    monkeypatch.setattr(questions, "_broadcast_user_unread_count", AsyncMock())
    monkeypatch.setattr(tasks.send_question_webhooks_task, "delay", Mock())
    monkeypatch.setattr(tasks.send_question_reply_webhooks_task, "delay", Mock())
    event = Event(
        source="keen-agent",
        external_id="question-event",
        timestamp=datetime(2026, 1, 1),
        summary="Agent alert",
    )
    stranger = User(
        username="stranger", password_hash="unused", role="normal", is_active=True
    )
    api.db.add_all([event, stranger])
    api.db.commit()
    api.reader.effective_permission_codes = {"events.read", "question.create"}
    stranger.effective_permission_codes = {"events.read", "question.create"}
    api.event = event
    api.stranger = stranger
    return api


def test_question_reply_unread_mark_seen_and_delete(question_api):
    api = question_api
    api.identity["user"] = api.reader
    event_path = f"events/{api.event.id}/questions"
    created = request(api, "POST", event_path, {"body": "  Why did this happen?  "})
    thread = created["thread_id"]
    assert (
        request(api, "GET", event_path)["threads"][0]["posts"][0]["body"]
        == "Why did this happen?"
    )
    assert len(request(api, "GET", "me/questions")["items"]) == 1
    api.identity["user"] = api.admin
    request(
        api, "POST", f"questions/{thread}/posts", {"body": "Here is the explanation"}
    )
    api.identity["user"] = api.reader
    assert request(api, "GET", "me/questions/summary")["unread_count"] == 1
    result = request(api, "POST", event_path + "/mark-seen", {})
    assert result["marked"] == 1 and result["unread_count"] == 0
    assert request(api, "POST", event_path + "/mark-seen", {})["marked"] == 0
    api.identity["user"] = api.admin
    request(api, "DELETE", f"questions/{thread}")
    assert request(api, "GET", event_path)["threads"] == []
    assert api.db.query(EventQuestionPost).count() == 0


def test_other_readers_cannot_reply_to_or_delete_someone_elses_question(question_api):
    api = question_api
    api.identity["user"] = api.reader
    created = request(
        api, "POST", f"events/{api.event.id}/questions", {"body": "Explain"}
    )
    api.identity["user"] = api.stranger
    assert request(api, "GET", "me/questions")["items"] == []
    request(
        api,
        "POST",
        f"questions/{created['thread_id']}/posts",
        {"body": "Unauthorised"},
        status=403,
    )
    request(api, "DELETE", f"questions/{created['thread_id']}", status=403)
    assert api.db.query(EventQuestionPost).count() == 1


@pytest.mark.parametrize("status", ["unanswered", "reviewing", "answered"])
def test_admin_status_transitions_are_persisted(question_api, status):
    api = question_api
    thread = request(
        api, "POST", f"events/{api.event.id}/questions", {"body": "Question"}
    )["thread_id"]
    result = request(api, "PATCH", f"admin/questions/{thread}", {"status": status})
    assert result["status"] == status
    assert api.db.get(EventQuestionThread, uuid.UUID(thread)).status == status
    request(
        api, "PATCH", f"admin/questions/{thread}", {"status": "invalid"}, status=400
    )
    assert api.db.get(EventQuestionThread, uuid.UUID(thread)).status == status


@pytest.mark.parametrize("body", ["", "   "])
def test_blank_questions_do_not_create_orphan_threads(question_api, body):
    api = question_api
    request(api, "POST", f"events/{api.event.id}/questions", {"body": body}, status=400)
    assert api.db.query(EventQuestionThread).count() == 0
    assert api.db.query(EventQuestionPost).count() == 0


def test_question_creation_requires_both_event_read_and_question_create(question_api):
    api = question_api
    api.identity["user"] = api.reader
    for permissions in [{"events.read"}, {"question.create"}, set()]:
        api.reader.effective_permission_codes = permissions
        request(
            api,
            "POST",
            f"events/{api.event.id}/questions",
            {"body": "Question"},
            status=403,
        )
    assert api.db.query(EventQuestionThread).count() == 0
