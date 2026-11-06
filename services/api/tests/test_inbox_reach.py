"""Who an inbox notification reaches, and how, through the assembled app.

A notification is emailed only once its reader can open the record; until
then it waits unread in Keen, even for someone who chose email only. ISMS
readers keep seeing minutes they were told about after leaving a meeting's
attendees; people who were only attendees do not.
"""

import uuid
from datetime import timedelta

from app.db.models import InboxItem, QuizRound, SecurityNotification
from app.services import inbox, me
from app.services.schedules import today_for_schedule

from tests.test_assembled_authority_regressions import keen as keen
from tests.test_assembled_authority_regressions import unsafe
from tests.test_inbox import _items, _meeting, _unread
from tests.test_inbox import people as people
from tests.test_quizzes_api import world as world


def _choose_delivery(app, account, delivery, address):
    account.email = address
    app.db.commit()
    client = app.client(account)
    response = client.patch(
        "/v1/me/preferences",
        json={"notification_delivery": delivery},
        headers=unsafe(client),
    )
    assert (response.status_code, response.json()) == (
        200,
        {
            "use_local_timezone": False,
            "timezone": None,
            "theme": "ink",
            "date_format": "default",
            "auto_apply_filters": False,
            "source_colors": {},
            "default_visualisation": None,
            "landing_page": "/",
            "default_framework": None,
            "notification_delivery": "email",
            "notification_email": True,
        },
    )
    return client


def test_a_notification_waits_unread_until_its_reader_can_open_it(world, monkeypatch):
    monkeypatch.setattr(inbox, "smtp_configured", lambda: True)
    monkeypatch.setattr(me, "smtp_configured", lambda: True)
    ada = world.students[0]
    client = _choose_delivery(world.keen, ada.user, "email", "ada@example.test")
    quiz = world.ready(people=[ada])
    emailed = world.db.query(SecurityNotification).count()
    opens = today_for_schedule() + timedelta(days=3)
    started = world.post(
        world.manager,
        f"/v1/quizzes/{quiz['id']}/rounds",
        {
            "opens_on": opens.isoformat(),
            "due_on": (opens + timedelta(days=7)).isoformat(),
        },
    )
    assert started.status_code == 201, started.text
    round_id = uuid.UUID(started.json()["id"])

    # Ada is told, but nothing is emailed while the round has not opened, so
    # the item is not marked as read by email.
    (item,) = world.db.query(InboxItem).filter_by(
        user_id=ada.user.id, subject_id=round_id
    )
    assert (item.kind, item.read_at) == ("quiz_round", None)
    assert world.db.query(SecurityNotification).count() == emailed
    assert _items(client)["items"] == []

    row = world.db.get(QuizRound, round_id)
    row.opens_on = today_for_schedule()
    world.db.commit()
    (shown,) = _items(client)["items"]
    assert (shown["title"], shown["read_at"]) == ("Security basics", None)
    assert _unread(client) == 1


def test_isms_readers_keep_minutes_they_were_told_about(people):
    app, who = people
    reader = app.user("isms.read")
    manager = app.client(who["manager"])
    meeting = _meeting(
        manager,
        title="Security forum",
        date="2026-11-02",
        agenda_minutes_notes="<p>Agreed the access review.</p>",
        attendee_user_ids=[str(reader.id), str(who["attendee"].id)],
    )
    reader_client = app.client(reader)
    attendee_client = app.client(who["attendee"])
    assert [i["title"] for i in _items(reader_client)["items"]] == ["Security forum"]
    assert [i["title"] for i in _items(attendee_client)["items"]] == ["Security forum"]

    # Both leave the attendee list; only the ISMS reader can still open it.
    _meeting(
        manager,
        method="PATCH",
        path=f"/v1/isms/meetings/{meeting['id']}",
        attendee_user_ids=[],
    )
    (shown,) = _items(reader_client)["items"]
    assert (shown["summary"], shown["title"]) == ("Minutes added", "Security forum")
    assert _items(attendee_client)["items"] == []
    assert _unread(attendee_client) == 0
