"""Each person's inbox, through the assembled application.

People hear when an audit they attend starts, when they are asked to sit a
quiz round and when minutes appear for a meeting they attended; never about
their own changes, and once per record. The inbox shows an item only while
its reader can open the record, and someone recorded on a meeting can read it
without ISMS access.
"""

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from app.core.datetime_utils import utc_now_naive
from app.db.models import (
    Audit,
    AuditAttendee,
    Framework,
    InboxItem,
    IsmsPerson,
    QuizRound,
    User,
)
from app.security.rich_text import rich_text_has_text
from app.services import audit_schedules, features, inbox
from app.services.quizzes import rounds as rounds_service
from app.services.schedules import today_for_schedule
from app.worker import tasks

# Imported fixtures: the assembled application, and the quiz world built on it.
from tests.test_assembled_authority_regressions import keen, unsafe  # noqa: F401
from tests.test_quizzes_api import world  # noqa: F401

FW = "fw-inbox"


def _items(client, **params):
    response = client.get("/v1/me/inbox", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _unread(client):
    response = client.get("/v1/me/inbox/summary")
    assert response.status_code == 200, response.text
    return response.json()["unread_count"]


def _post(client, path, body=None):
    return client.post(path, json=body or {}, headers=unsafe(client))


@pytest.fixture
def people(keen):  # noqa: F811 - fixture imported above
    db = keen.db
    db.add(Framework(slug=FW))
    db.commit()
    manager = keen.user("audits.manage", "isms.manage")
    attendee = keen.user()
    linked = keen.user()
    reader = keen.user("audits.read")
    stranger = keen.user()
    retired = keen.user()
    retired.is_active = False
    person = IsmsPerson(name="Linked person", user_id=linked.id)
    reader_person = IsmsPerson(name="Reading person", user_id=reader.id)
    no_account = IsmsPerson(name="Visitor")
    db.add_all([person, reader_person, no_account])
    db.commit()
    return keen, {
        "manager": manager,
        "attendee": attendee,
        "linked": linked,
        "reader": reader,
        "stranger": stranger,
        "retired": retired,
        "person": person,
        "reader_person": reader_person,
        "no_account": no_account,
    }


# --- Audits ----------------------------------------------------------------------


def _audit(client, attendees, **fields):
    response = client.post(
        "/v1/audits",
        json={
            "title": "Access review",
            "framework_slug": FW,
            "start_date": "2026-11-09",
            "end_date": "2026-11-13",
            "attendees": attendees,
            **fields,
        },
        headers=unsafe(client),
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_attendees_of_a_new_audit_hear_about_it_except_its_creator(people):
    app, who = people
    manager = app.client(who["manager"])
    audit_id = _audit(
        manager,
        [
            {"user_id": str(who["attendee"].id), "role": "Lead"},
            {"person_id": str(who["person"].id)},
            {"person_id": str(who["reader_person"].id)},
            {"person_id": str(who["no_account"].id)},
            {"user_id": str(who["manager"].id)},
            {"name": "External auditor", "email": "auditor@example.test"},
        ],
    )

    attendee = app.client(who["attendee"])
    (item,) = _items(attendee)["items"]
    assert item == {
        "id": item["id"],
        "kind": "audit_started",
        "summary": "New audit",
        "title": "Access review",
        "href": f"/audit.html?id={audit_id}",
        "date_label": "Starts",
        "date": "2026-11-09",
        "actor": who["manager"].username,
        "created_at": item["created_at"],
        "read_at": None,
    }
    assert _unread(attendee) == 1
    # A People record's account hears too, and sees the audit once it can read it.
    assert [i["title"] for i in _items(app.client(who["reader"]))["items"]] == [
        "Access review"
    ]
    rows = app.db.query(InboxItem).filter_by(subject_id=uuid.UUID(audit_id))
    assert {row.user_id for row in rows} == {
        who["attendee"].id,
        who["linked"].id,
        who["reader"].id,
    }
    # The linked account can open the audit only through audits.read.
    assert _items(app.client(who["linked"]))["items"] == []
    assert _unread(app.client(who["linked"])) == 0
    for account in ("manager", "stranger"):
        assert _items(app.client(who[account]))["items"] == []


def test_someone_added_to_an_audit_under_way_hears_once(people):
    app, who = people
    manager = app.client(who["manager"])
    audit_id = _audit(manager, [])

    def add(**attendee):
        response = _post(manager, f"/v1/audits/{audit_id}/attendees", attendee)
        assert response.status_code == 200, response.text
        return response.json()["id"]

    first = add(user_id=str(who["attendee"].id))
    attendee = app.client(who["attendee"])
    assert _unread(attendee) == 1
    removed = manager.delete(
        f"/v1/audits/{audit_id}/attendees/{first}", headers=unsafe(manager)
    )
    assert removed.status_code == 200, removed.text
    add(user_id=str(who["attendee"].id))
    add(user_id=str(who["manager"].id))
    assert _unread(attendee) == 1
    assert len(_items(attendee)["items"]) == 1
    assert _items(manager)["items"] == []
    # An attendee removed from the audit no longer sees it.
    app.db.query(AuditAttendee).filter_by(user_id=who["attendee"].id).delete()
    app.db.commit()
    assert _items(attendee)["items"] == []


def test_a_finished_audit_tells_nobody(people):
    app, who = people
    db = app.db
    audit = Audit(title="Last year", framework_slug=FW, status="completed")
    db.add(audit)
    db.flush()
    row = AuditAttendee(audit_id=audit.id, user_id=who["attendee"].id, name="A")
    assert inbox.audit_started(db, audit, [row], None) == 0
    audit.status = "template"
    assert inbox.audit_started(db, audit, [row], None) == 0
    assert db.query(InboxItem).count() == 0


def test_a_scheduled_audit_tells_its_attendees(people, monkeypatch):
    app, who = people
    db = app.db
    monkeypatch.setattr(audit_schedules, "smtp_configured", lambda: False)
    today = today_for_schedule()
    template = Audit(
        title="Monthly access review",
        framework_slug=FW,
        status="template",
        audit_type="internal",
        start_date=today,
        end_date=today,
        schedule_recurrence="monthly",
        schedule_interval=1,
        schedule_date_rule="exact",
        schedule_next_run_date=today,
        created_by_user_id=who["manager"].id,
    )
    db.add(template)
    db.flush()
    db.add_all(
        [
            AuditAttendee(audit_id=template.id, user_id=who["attendee"].id, name="A"),
            AuditAttendee(audit_id=template.id, user_id=who["manager"].id, name="M"),
            AuditAttendee(audit_id=template.id, user_id=who["retired"].id, name="R"),
        ]
    )
    db.commit()

    result = audit_schedules.materialize_due_scheduled_audits(db, today=today)

    (created,) = result["created"]
    (item,) = _items(app.client(who["attendee"]))["items"]
    assert item["href"] == f"/audit.html?id={created['audit_id']}"
    assert item["actor"] is None
    # Nobody made a scheduled audit, so its creator hears too; a retired account does not.
    assert len(_items(app.client(who["manager"]))["items"]) == 1
    assert db.query(InboxItem).filter_by(user_id=who["retired"].id).count() == 0


# --- Reading and marking ---------------------------------------------------------------


def test_items_are_marked_read_one_at_a_time_or_all_at_once(people):
    app, who = people
    manager = app.client(who["manager"])
    attendee = app.client(who["attendee"])
    for title in ("First", "Second", "Third"):
        _audit(manager, [{"user_id": str(who["attendee"].id)}], title=title)
    items = _items(attendee)["items"]
    assert [i["title"] for i in items] == ["Third", "Second", "First"]
    assert _items(attendee)["unread_count"] == 3

    marked = _post(attendee, f"/v1/me/inbox/{items[1]['id']}/read")
    assert marked.status_code == 200, marked.text
    assert marked.json() == {"unread_count": 2}
    # Marking it again changes nothing.
    assert _post(attendee, f"/v1/me/inbox/{items[1]['id']}/read").json() == {
        "unread_count": 2
    }
    unread = _items(attendee, unread="true")["items"]
    assert [i["title"] for i in unread] == ["Third", "First"]
    assert _items(attendee)["items"][1]["read_at"] is not None

    # Nobody marks another person's item, and unknown ones are not found.
    for client, item_id in (
        (app.client(who["stranger"]), items[0]["id"]),
        (attendee, str(uuid.uuid4())),
        (attendee, "not-an-id"),
    ):
        refused = _post(client, f"/v1/me/inbox/{item_id}/read")
        assert refused.status_code == 404
        assert refused.json()["detail"] == "Notification not found"
    assert _unread(attendee) == 2

    everything = _post(attendee, "/v1/me/inbox/read-all")
    assert everything.json() == {"marked": 2, "unread_count": 0}
    assert _unread(attendee) == 0
    assert _items(attendee, unread="true")["items"] == []
    assert len(_items(attendee)["items"]) == 3
    # Marking needs the session's CSRF token, like every change.
    assert attendee.post("/v1/me/inbox/read-all").status_code == 403
    assert app.client().get("/v1/me/inbox").status_code == 401


def test_an_item_hides_while_its_record_is_gone_or_switched_off(people):
    app, who = people
    manager = app.client(who["manager"])
    attendee = app.client(who["attendee"])
    kept = _audit(manager, [{"user_id": str(who["attendee"].id)}], title="Kept")
    removed = _audit(manager, [{"user_id": str(who["attendee"].id)}], title="Removed")
    app.db.delete(app.db.get(Audit, uuid.UUID(removed)))
    app.db.commit()
    assert [i["href"] for i in _items(attendee)["items"]] == [f"/audit.html?id={kept}"]
    assert _unread(attendee) == 1

    admin = app.user(role="admin")
    features.update(app.db, admin, 0, {"audits": False})
    assert _items(attendee) == {"items": [], "unread_count": 0}


def test_old_items_are_removed_by_the_daily_housekeeping(people, monkeypatch):
    app, who = people
    db = app.db
    now = utc_now_naive()
    subject = uuid.uuid4()

    def item(kind, *, age, read_age=None):
        return InboxItem(
            user_id=who["attendee"].id,
            kind=kind,
            subject_id=subject,
            created_at=now - timedelta(days=age),
            read_at=None if read_age is None else now - timedelta(days=read_age),
        )

    db.add_all(
        [
            item("audit_started", age=100, read_age=91),
            item("quiz_round", age=100, read_age=89),
            item("meeting_minutes", age=366),
        ]
    )
    db.commit()
    db.add(
        InboxItem(user_id=who["linked"].id, kind="audit_started", subject_id=subject)
    )
    db.commit()

    class Session:
        def __enter__(self):
            return db

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(tasks, "SessionLocal", Session)
    assert tasks.inbox_housekeeping_task() == {"removed": 2}
    assert {row.kind for row in db.query(InboxItem)} == {"quiz_round", "audit_started"}


def test_telling_someone_twice_keeps_one_item(people):
    app, who = people
    db = app.db
    subject = uuid.uuid4()
    accounts = [who["attendee"].id, who["linked"].id, None]
    assert (
        inbox.notify(
            db, kind="audit_started", subject_id=subject, user_ids=accounts, actor=None
        )
        == 2
    )
    assert (
        inbox.notify(
            db, kind="audit_started", subject_id=subject, user_ids=accounts, actor=None
        )
        == 0
    )
    assert (
        inbox.notify(
            db, kind="audit_started", subject_id=subject, user_ids=[], actor=None
        )
        == 0
    )
    assert db.query(InboxItem).filter_by(subject_id=subject).count() == 2


# --- Quizzes ---------------------------------------------------------------------


def test_people_asked_to_sit_a_round_hear_about_it(world):  # noqa: F811
    ada, ben, cleo, dev = world.students
    quiz = world.ready(people=[ada, ben])
    round_ = world.open_round(quiz)

    (item,) = _items(world.student_client(ada))["items"]
    assert (item["kind"], item["summary"], item["title"]) == (
        "quiz_round",
        "Quiz to take",
        "Security basics",
    )
    assert item["href"] == f"/quiz-take.html?round={round_['id']}"
    assert (item["date_label"], item["date"]) == ("Due", round_["due_on"])
    assert _items(world.student_client(cleo))["items"] == []
    assert _items(world.manager)["items"] == []

    # The audience changes: newcomers hear once, those already in do not hear again.
    world.put(
        world.manager,
        f"/v1/quizzes/{quiz['id']}/audience",
        {"person_ids": [str(p.person.id) for p in (ada, ben, cleo)]},
    )
    assert len(_items(world.student_client(cleo))["items"]) == 1
    assert len(_items(world.student_client(ada))["items"]) == 1
    told = world.db.query(InboxItem).filter_by(subject_id=uuid.UUID(round_["id"]))
    assert {row.user_id for row in told} == {s.user.id for s in (ada, ben, cleo)}

    # Once the round closes the item leaves the inbox.
    closed = world.patch(
        world.manager,
        f"/v1/quizzes/{quiz['id']}/rounds/{round_['id']}",
        {"status": "closed"},
    )
    assert closed.status_code == 200, closed.text
    assert _items(world.student_client(ada)) == {"items": [], "unread_count": 0}


def test_a_round_reaches_the_inbox_once_it_opens(world):  # noqa: F811
    ada = world.students[0]
    quiz = world.ready(people=[ada])
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
    client = world.student_client(ada)
    assert _items(client)["items"] == []
    row = world.db.get(QuizRound, uuid.UUID(started.json()["id"]))
    row.opens_on = today_for_schedule()
    world.db.commit()
    assert len(_items(client)["items"]) == 1

    admin = world.keen.user(role="admin")
    features.update(world.db, admin, 0, {"quizzes": False})
    assert _items(client)["items"] == []


def test_a_quiz_manager_in_the_audience_is_not_told_about_their_own_round(
    world,  # noqa: F811
):
    manager = IsmsPerson(name="Manager", user_id=world.manager_user.id)
    world.db.add(manager)
    world.db.commit()
    quiz = world.ready(people=[world.students[0]])
    world.put(
        world.manager,
        f"/v1/quizzes/{quiz['id']}/audience",
        {"person_ids": [str(world.students[0].person.id), str(manager.id)]},
    )
    world.open_round(quiz)
    assert _items(world.manager)["items"] == []
    assert len(_items(world.student_client(world.students[0]))["items"]) == 1


# --- Meetings ----------------------------------------------------------------------


def _meeting(client, status=200, method="POST", path="/v1/isms/meetings", **body):
    response = client.request(
        method,
        path,
        params={"framework": FW},
        json=body,
        headers=unsafe(client),
    )
    assert response.status_code == status, response.text
    return response.json()


def test_attendees_hear_once_when_minutes_first_appear(people):
    app, who = people
    manager = app.client(who["manager"])
    meeting = _meeting(
        manager,
        title="Management review",
        date="2026-10-30",
        agenda_minutes_notes="<p><br></p><p>&nbsp;</p>",
        attendee_user_ids=[str(who["attendee"].id), str(who["manager"].id)],
        apology_user_ids=[str(who["stranger"].id)],
        attendee_person_ids=[str(who["person"].id)],
    )
    path = f"/v1/isms/meetings/{meeting['id']}"
    attendee = app.client(who["attendee"])
    assert _items(attendee)["items"] == []

    _meeting(manager, method="PATCH", path=path, agenda_minutes_notes="<p>Agreed.</p>")
    (item,) = _items(attendee)["items"]
    assert (item["kind"], item["summary"], item["title"], item["href"]) == (
        "meeting_minutes",
        "Minutes added",
        "Management review",
        f"/isms-meeting.html?id={meeting['id']}",
    )
    assert (item["date_label"], item["date"]) == ("Meeting on", "2026-10-30")
    assert len(_items(app.client(who["linked"]))["items"]) == 1
    # Apologies and the person who added the minutes are not told.
    assert _items(app.client(who["stranger"]))["items"] == []
    assert _items(manager)["items"] == []

    # Later edits tell nobody again; someone added afterwards hears once.
    _meeting(manager, method="PATCH", path=path, agenda_minutes_notes="<p>Changed.</p>")
    _meeting(
        manager,
        method="PATCH",
        path=path,
        attendee_user_ids=[str(who["attendee"].id), str(who["reader"].id)],
    )
    assert len(_items(attendee)["items"]) == 1
    assert len(_items(app.client(who["reader"]))["items"]) == 1
    assert app.db.query(InboxItem).filter_by(kind="meeting_minutes").count() == 3


def test_minutes_recorded_with_a_new_meeting_reach_its_attendees(people):
    app, who = people
    manager = app.client(who["manager"])
    _meeting(
        manager,
        title="Kick-off",
        date="2026-10-01",
        agenda_minutes_notes="<p>Scope agreed.</p>",
        attendee_user_ids=[str(who["attendee"].id)],
    )
    assert len(_items(app.client(who["attendee"]))["items"]) == 1

    admin = app.user(role="admin")
    features.update(app.db, admin, 0, {"meetings": False})
    assert _items(app.client(who["attendee"]))["items"] == []


def test_people_on_a_meeting_read_it_without_isms_access(people):
    app, who = people
    manager = app.client(who["manager"])
    document = manager.post(
        "/v1/isms/documents",
        params={"framework": FW},
        json={"title": "Security policy", "content_html": "<p>Confidential text</p>"},
        headers=unsafe(manager),
    )
    assert document.status_code == 200, document.text
    meeting = _meeting(
        manager,
        title="Review",
        date="2026-10-30",
        agenda_minutes_notes="<p>Agreed.</p>",
        attendee_user_ids=[str(who["attendee"].id)],
        apology_person_ids=[str(who["person"].id)],
        links=[
            {"link_type": "isms_document", "document_id": document.json()["id"]},
            {
                "link_type": "external_url",
                "url": "https://example.test/notes",
                "title": "Notes",
            },
        ],
    )
    path = f"/v1/isms/meetings/{meeting['id']}"

    for account in ("attendee", "linked"):
        response = app.client(who[account]).get(path, params={"framework": FW})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["agenda_minutes_notes"] == "<p>Agreed.</p>"
        document_link, url_link = body["links"]
        assert document_link["document"] == {
            "id": document.json()["id"],
            "title": "Security policy",
            "display": "Security policy",
        }
        assert url_link["url"] == "https://example.test/notes"
        assert "Confidential" not in response.text

    # An ISMS reader still sees the whole record.
    full = manager.get(path, params={"framework": FW}).json()
    assert full["links"][0]["document"]["content_html"] == "<p>Confidential text</p>"

    # Anyone else is refused alike, whether or not the meeting exists.
    stranger = app.client(who["stranger"])
    for target in (path, f"/v1/isms/meetings/{uuid.uuid4()}", "/v1/isms/meetings/x"):
        refused = stranger.get(target, params={"framework": FW})
        assert refused.status_code == 403, target
        assert refused.json()["detail"] == "isms.read permission required"
    assert manager.get(f"/v1/isms/meetings/{uuid.uuid4()}").status_code == 404
    assert stranger.get("/v1/isms/meetings").status_code == 403
    retired = app.db.get(User, who["attendee"].id)
    retired.is_active = False
    app.db.commit()
    assert (
        inbox.notify(
            app.db,
            kind="meeting_minutes",
            subject_id=uuid.UUID(meeting["id"]),
            user_ids=[retired.id],
            actor=None,
        )
        == 0
    )


def test_my_meetings_include_those_recorded_through_a_people_record(people):
    app, who = people
    manager = app.client(who["manager"])
    meeting = _meeting(
        manager,
        title="Supplier review",
        date="2026-09-01",
        attendee_person_ids=[str(who["person"].id)],
        apology_person_ids=[str(who["person"].id)],
    )
    response = app.client(who["linked"]).get(
        "/v1/me/isms-meetings", params={"framework": FW}
    )
    assert response.status_code == 200, response.text
    (item,) = response.json()["items"]
    assert item["id"] == meeting["id"]
    assert item["my_attendance_types"] == ["attendee", "apology"]
    assert item["my_attendance_label"] == "Attendee, Apology"


def test_rich_text_counts_as_minutes_only_with_words():
    for empty in (
        None,
        "",
        "<p></p>",
        "<p><br></p>",
        "<p>&nbsp;</p>",
        "<ul><li> </li></ul>",
    ):
        assert not rich_text_has_text(empty), empty
    for words in ("Agreed", "<p>Agreed</p>", "<ul><li>1</li></ul>", "&amp;"):
        assert rich_text_has_text(words), words


def test_a_scheduled_round_tells_everyone_in_its_audience(world):  # noqa: F811
    manager = IsmsPerson(name="Manager", user_id=world.manager_user.id)
    world.db.add(manager)
    world.db.commit()
    ada = world.students[0]
    tomorrow = today_for_schedule() + timedelta(days=1)
    quiz = world.ready(
        people=[ada, SimpleNamespace(person=manager)],
        started=True,
        start_date=tomorrow.isoformat(),
        schedule_recurrence="weekly",
        due_days=3,
    )
    assert quiz["rounds"] == []
    [created] = rounds_service.materialize_due_quiz_rounds(world.db, today=tomorrow)[
        "created"
    ]
    told = world.db.query(InboxItem).filter_by(
        subject_id=uuid.UUID(created["round_id"])
    )
    # Nobody opened a scheduled round, so its manager hears too.
    assert {(row.user_id, row.actor_user_id) for row in told} == {
        (ada.user.id, None),
        (world.manager_user.id, None),
    }
