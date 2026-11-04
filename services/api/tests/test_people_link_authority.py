"""Authority that follows a People record's account link stays with those who hold it.

An account receives some authority through the People record linked to it:
the quizzes the person sits and the audits the person attends. Anyone who can
edit People records can move that link, so moving it, or deleting the record,
requires the permission over what the record carries
(``services.people.LINK_AUTHORITIES``).

Quiz guards are exercised in tests/test_quizzes_api.py through the same
function. The inventory below names every function in the application that
selects by a People record's account, through ``IsmsPerson.user_id`` or
``person_for_user()``. A new one fails
``test_every_reader_of_the_people_link_is_reviewed`` until it is listed with the
guard that covers it, or the reason it needs none. Code that only displays the
account of a record already in hand, such as an audit's attendee list, is not a
rule and is not listed.
"""

import ast
import pathlib
from datetime import date

from app.db.models import (
    Audit,
    AuditAttendee,
    AuditFinding,
    Framework,
    IsmsPerson,
)

from tests.conftest import contract_db, contract_postgres_engine  # noqa: F401
from tests.test_assembled_authority_regressions import keen, unsafe  # noqa: F401

APP = pathlib.Path(__file__).resolve().parents[1] / "app"

# Every function that resolves authority or recipients from a People record's
# account, and what keeps a moved link from moving authority to its mover.
REVIEWED_READERS = {
    # The guard and the provisioning lookups themselves.
    ("services/people.py", "person_for_user"): "lookup",
    ("services/people.py", "ensure_person_for_user"): "lookup",
    ("services/assurance.py", "_save_person"): "the guarded link change",
    # Audits: moving the link needs audits.admin.
    ("services/audits/access.py", "attends"): "attends audits",
    # Quizzes: moving the link needs quiz.manage.
    ("services/quizzes/access.py", "own_person"): "sits quizzes",
    ("services/quizzes/attempts.py", "my_quizzes"): "sits quizzes",
    ("services/quizzes/grading.py", "_own_attempt"): "sits quizzes",
    ("services/quizzes/audience.py", "_holders"): "sits quizzes",
    ("services/quizzes/audience.py", "audience_members"): "sits quizzes",
    ("services/quizzes/audience.py", "_role_holder_counts"): "sits quizzes",
    ("services/inbox.py", "_quiz_views"): "sits quizzes",
    # Meetings: whoever edits People records holds ISMS read access, which
    # reads every meeting, and can edit meeting attendees directly.
    ("services/isms/access.py", "meeting_ids_for"): "isms managers read meetings",
    ("services/isms/personal.py", "my_isms_meetings"): "isms managers read meetings",
    # Notification recipients: an item is shown only while its account can
    # open the record, so recipients follow the rules above.
    ("services/inbox.py", "_person_accounts"): "follows record access",
}


def readers_of_the_people_link(source: str, filename: str):
    """(file, function) pairs that read IsmsPerson.user_id or person_for_user()."""
    found = set()
    tree = ast.parse(source)

    def walk(node, scope):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, child.name)
                continue
            reads = (
                isinstance(child, ast.Attribute)
                and child.attr == "user_id"
                and isinstance(child.value, ast.Name)
                and child.value.id == "IsmsPerson"
            ) or (
                isinstance(child, ast.Call)
                and (
                    (
                        isinstance(child.func, ast.Name)
                        and child.func.id == "person_for_user"
                    )
                    or (
                        isinstance(child.func, ast.Attribute)
                        and child.func.attr == "person_for_user"
                    )
                )
            )
            if reads:
                found.add((filename, scope or "<module>"))
            walk(child, scope)

    walk(tree, None)
    return found


def _application_readers():
    found = set()
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP).as_posix()
        found |= readers_of_the_people_link(path.read_text(), rel)
    return found


def test_every_reader_of_the_people_link_is_reviewed():
    found = _application_readers()
    unreviewed = sorted(found - set(REVIEWED_READERS))
    assert not unreviewed, (
        "These functions resolve authority through a People record's account. "
        "Add each to services.people.LINK_AUTHORITIES (so moving the link needs "
        "that authority) and to REVIEWED_READERS, or record why it needs no guard: "
        f"{unreviewed}"
    )
    stale = sorted(set(REVIEWED_READERS) - found)
    assert not stale, f"Remove entries that no longer read the link: {stale}"


def test_the_inventory_detects_a_new_reader():
    # The defect this guards: audit read access through the People link was
    # added without anyone guarding who may move the link.
    source = (
        "def attends(user):\n"
        "    people = select(IsmsPerson.id).where(IsmsPerson.user_id == user.id)\n"
        "    return AuditAttendee.person_id.in_(people)\n"
    )
    assert readers_of_the_people_link(source, "services/new.py") == {
        ("services/new.py", "attends")
    }


def test_every_guarded_kind_has_a_link_authority():
    from app.services.people import LINK_AUTHORITIES

    carried = {carries for carries, _, _ in LINK_AUTHORITIES}
    reasons = set(REVIEWED_READERS.values())
    assert {"sits quizzes", "attends audits"} <= carried
    assert reasons & {"sits quizzes", "attends audits"} <= carried


def _send(client, method, path, body=None):
    headers = unsafe(client) if method != "GET" else {}
    kwargs = {"json": body} if body is not None else {}
    return client.request(method, path, headers=headers, **kwargs)


def _audit_with_external_attendee(app):
    db = app.db
    db.add(Framework(slug="LINK"))
    owner = app.user("audits.admin")
    attendee = IsmsPerson(name="External auditor", email="auditor@example.test")
    db.add(attendee)
    db.flush()
    audit = Audit(
        title="Confidential supplier audit",
        framework_slug="LINK",
        status="open",
        start_date=date.today(),
        created_by_user_id=owner.id,
    )
    db.add(audit)
    db.flush()
    db.add_all(
        [
            AuditAttendee(audit_id=audit.id, person_id=attendee.id, name=attendee.name),
            AuditFinding(
                audit_id=audit.id,
                kind="major_nc",
                title="Synthetic confidential finding",
                description="details",
            ),
        ]
    )
    db.commit()
    return audit, attendee


def _own_person(app, account):
    row = IsmsPerson(name=account.username, user_id=account.id)
    app.db.add(row)
    app.db.commit()
    return row


def test_an_isms_or_risk_manager_cannot_take_an_attendees_audits(
    keen,  # noqa: F811 - fixture imported above
):
    audit, attendee = _audit_with_external_attendee(keen)
    for code in ("isms.manage", "risk.manage"):
        manager = keen.user(code)
        own = _own_person(keen, manager)
        client = keen.client(manager)
        assert _send(client, "GET", f"/v1/audits/{audit.id}").status_code == 403
        # Freeing the manager's own link is an ordinary People edit.
        freed = _send(client, "PATCH", f"/v1/isms/people/{own.id}", {"name": own.name})
        assert freed.status_code == 200, freed.text
        moved = _send(
            client,
            "PATCH",
            f"/v1/isms/people/{attendee.id}",
            {
                "name": attendee.name,
                "email": attendee.email,
                "user_id": str(manager.id),
            },
        )
        assert moved.status_code == 403, moved.text
        assert moved.json()["detail"] == (
            "Changing the Keen account of someone who attends audits needs audits.admin"
        )
        keen.db.expire_all()
        assert keen.db.get(IsmsPerson, attendee.id).user_id is None
        assert _send(client, "GET", f"/v1/audits/{audit.id}").status_code == 403
        assert _send(client, "GET", "/v1/me/audits").json()["items"] == []
        deleted = _send(client, "DELETE", f"/v1/isms/people/{attendee.id}")
        assert deleted.status_code == 403
        assert deleted.json()["detail"] == (
            "Deleting someone who attends audits needs audits.admin"
        )


def test_an_audit_administrator_may_link_an_attendee_to_their_account(
    keen,  # noqa: F811 - fixture imported above
):
    audit, attendee = _audit_with_external_attendee(keen)
    account = keen.user()
    admin = keen.user("isms.manage", "audits.admin")
    client = keen.client(admin)
    moved = _send(
        client,
        "PATCH",
        f"/v1/isms/people/{attendee.id}",
        {"name": attendee.name, "user_id": str(account.id)},
    )
    assert moved.status_code == 200, moved.text
    reader = keen.client(account)
    opened = _send(reader, "GET", f"/v1/audits/{audit.id}")
    assert opened.status_code == 200
    assert opened.json()["findings"][0]["title"] == "Synthetic confidential finding"


def test_people_without_quizzes_or_audits_are_edited_by_isms_managers(
    keen,  # noqa: F811 - fixture imported above
):
    manager = keen.user("isms.manage")
    account = keen.user()
    person = IsmsPerson(name="Staff member")
    keen.db.add(person)
    keen.db.commit()
    client = keen.client(manager)
    linked = _send(
        client,
        "PATCH",
        f"/v1/isms/people/{person.id}",
        {"name": person.name, "user_id": str(account.id)},
    )
    assert linked.status_code == 200, linked.text
    assert _send(client, "DELETE", f"/v1/isms/people/{person.id}").status_code == 204


def test_audit_editors_see_the_account_a_person_attends_through(
    keen,  # noqa: F811 - fixture imported above
):
    # A People record linked before it attends anything is an ordinary People
    # edit. Adding that person to an audit gives the linked account read
    # access, so the audit shows which account it is.
    audit, _ = _audit_with_external_attendee(keen)
    manager = keen.user("isms.manage")
    linked = _own_person(keen, manager)
    editor = keen.client(keen.user("audits.admin"))
    added = _send(
        editor,
        "POST",
        f"/v1/audits/{audit.id}/attendees",
        {"person_id": str(linked.id), "name": linked.name},
    )
    assert added.status_code == 200, added.text
    assert added.json()["user_id"] is None
    assert added.json()["person_username"] == manager.username
    shown = _send(editor, "GET", f"/v1/audits/{audit.id}").json()["attendees"]
    assert {(a["name"], a["person_username"]) for a in shown} == {
        ("External auditor", None),
        (manager.username, manager.username),
    }
    assert (
        _send(keen.client(manager), "GET", f"/v1/audits/{audit.id}").status_code == 200
    )


def test_quiz_managers_see_the_account_a_person_sits_quizzes_through(
    keen,  # noqa: F811 - fixture imported above
):
    manager = keen.user("isms.manage")
    linked = _own_person(keen, manager)
    unlinked = IsmsPerson(name="Visitor")
    keen.db.add(unlinked)
    keen.db.commit()
    client = keen.client(keen.user("quiz.manage"))
    listed = _send(client, "GET", "/v1/quizzes/assignees")
    assert listed.status_code == 200, listed.text
    accounts = {p["id"]: p["username"] for p in listed.json()["people"]}
    assert accounts[str(linked.id)] == manager.username
    assert accounts[str(unlinked.id)] == ""
