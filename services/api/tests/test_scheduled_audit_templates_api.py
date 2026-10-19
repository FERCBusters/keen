"""Scheduled audit templates through the assembled application.

A template describes a recurring audit: its first date, how it recurs and when
it stops, its attendees and its scope. Saving one validates the schedule as a
whole, the calendar lists each template's occurrences in a requested window,
and edits move the next run when the template's date changes.
"""

import uuid
from datetime import date

import pytest
from app.db.models import AuditScopedClause, AuditScopedControl, AuditScopedIsmsDocument

# Imported fixtures: the assembled application and an audit workspace.
from tests.test_assembled_authority_regressions import keen, unsafe  # noqa: F401
from tests.test_audit_query_api_workflows import audits  # noqa: F401

ROOT = "/v1/audits/scheduled"


def _template(**changes):
    return {
        "title": "Quarterly access review",
        "framework_slug": "A",
        "start_date": "2027-01-15",
        "schedule_recurrence": "monthly",
        "schedule_interval": 1,
        **changes,
    }


def _send(audits, method, path, body=None, user=None):  # noqa: F811 - fixture imported above
    client = audits.keen.client(user or audits.owner)
    return client.request(
        method,
        path,
        **({"json": body} if body is not None else {}),
        headers=unsafe(client),
    )


def _created(audits, **changes):  # noqa: F811 - fixture imported above
    response = _send(audits, "POST", ROOT, _template(**changes))
    assert response.status_code == 200, response.text
    return response.json()


REFUSED = [
    (
        "ends-before-it-starts",
        {"schedule_until_date": "2027-01-14"},
        "schedule_until_date cannot be before start_date",
    ),
    (
        "weekly-first-weekday",
        {
            "schedule_recurrence": "weekly",
            "schedule_date_rule": "first_monday_of_month",
            "schedule_anchor_month": 3,
        },
        "First weekday schedules can recur monthly, quarterly or yearly",
    ),
    (
        "first-weekday-without-month",
        {"schedule_date_rule": "first_monday_of_month"},
        "schedule_anchor_month is required for first weekday schedules",
    ),
    ("unknown-rule", {"schedule_date_rule": "last_day"}, "Invalid schedule_date_rule"),
    ("unknown-type", {"audit_type": "surprise"}, "Invalid audit_type"),
]


@pytest.mark.parametrize(
    "changes,detail",
    [(changes, detail) for _, changes, detail in REFUSED],
    ids=[name for name, _, _ in REFUSED],
)
def test_schedules_that_cannot_run_are_refused(audits, changes, detail):  # noqa: F811 - fixture imported above
    before = _send(audits, "GET", ROOT).json()["total"]

    refused = _send(audits, "POST", ROOT, _template(**changes))

    assert (refused.status_code, refused.json()["detail"]) == (400, detail)
    assert _send(audits, "GET", ROOT).json()["total"] == before


def test_templates_need_audit_management(audits):  # noqa: F811 - fixture imported above
    refused = _send(audits, "POST", ROOT, _template(), user=audits.attendee)
    assert (refused.status_code, refused.json()["detail"]) == (
        403,
        "audits.manage or audits.admin permission required",
    )


def test_a_first_weekday_template_starts_on_that_weekday_of_its_month(audits):  # noqa: F811 - fixture imported above
    row = _created(
        audits,
        schedule_date_rule="first_monday_of_month",
        schedule_anchor_month=3,
        schedule_recurrence="quarterly",
    )
    # 1 March 2027 is the first Monday of the anchor month.
    assert (row["start_date"], row["schedule_next_run_date"]) == (
        "2027-03-01",
        "2027-03-01",
    )
    assert (row["schedule_date_rule"], row["schedule_anchor_month"]) == (
        "first_monday_of_month",
        3,
    )
    exact = _created(audits, schedule_anchor_month=3)
    # An exact date ignores the anchor month.
    assert (exact["start_date"], exact["schedule_anchor_month"]) == ("2027-01-15", None)


def test_attendees_are_active_people_named_from_their_account(audits):  # noqa: F811 - fixture imported above
    audits.attendee.email = "attendee@example.com"
    audits.db.commit()
    for user_id in (audits.retired.id, uuid.uuid4()):
        refused = _send(
            audits,
            "POST",
            ROOT,
            _template(attendees=[{"user_id": str(user_id)}]),
        )
        assert (refused.status_code, refused.json()["detail"]) == (
            400,
            "Unknown or inactive attendee user",
        )
    unnamed = _send(audits, "POST", ROOT, _template(attendees=[{"role": "Observer"}]))
    assert unnamed.status_code == 400, unnamed.text

    row = _created(
        audits,
        attendees=[
            {"user_id": str(audits.attendee.id), "role": "Lead auditor"},
            {"name": "External assessor", "email": "assessor@example.org"},
        ],
    )

    people = sorted((a["name"], a["email"], a["role"]) for a in row["attendees"])
    assert people == [
        ("External assessor", "assessor@example.org", None),
        (audits.attendee.username, "attendee@example.com", "Lead auditor"),
    ]
    # The template keeps the address the attendee had when it was saved.
    audits.attendee.email = "moved@example.com"
    audits.db.commit()
    listed = _send(audits, "GET", ROOT).json()["items"]
    (saved,) = [item for item in listed if item["id"] == row["id"]]
    assert sorted(a["email"] for a in saved["attendees"]) == [
        "assessor@example.org",
        "attendee@example.com",
    ]


def test_the_calendar_lists_occurrences_in_the_requested_window(audits):  # noqa: F811 - fixture imported above
    monthly = _created(audits, title="Monthly ACCESS review", start_date="2027-01-02")
    _created(audits, title="Yearly supplier review", schedule_recurrence="yearly")

    def calendar(**params):
        client = audits.keen.client(audits.owner)
        return client.get(ROOT, params=params)

    listed = calendar(start="2027-01-01", end="2027-04-30", q="access")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert [item["id"] for item in body["items"]] == [monthly["id"]]
    assert [o["date"] for o in body["occurrences"]] == [
        "2027-01-02",
        "2027-02-02",
        "2027-03-02",
        "2027-04-02",
    ]
    assert {o["audit_id"] for o in body["occurrences"]} == {monthly["id"]}

    # Without an end date the window is the rest of the start's month.
    february = calendar(start="2027-02-01", q="access").json()["occurrences"]
    assert [o["date"] for o in february] == ["2027-02-02"]

    backwards = calendar(start="2027-03-01", end="2027-02-01")
    assert (backwards.status_code, backwards.json()["detail"]) == (
        400,
        "end cannot be before start",
    )


def test_editing_a_template_updates_its_details_scope_and_people(audits):  # noqa: F811 - fixture imported above
    row = _created(audits, controls=[str(audits.control.id)])
    path = f"{ROOT}/{row['id']}"

    edited = _send(
        audits,
        "PATCH",
        path,
        {
            "audit_type": "external",
            "end_date": "2027-01-20",
            "report_notes": "<p>Scope notes<script>bad()</script></p>",
            "executive_summary": "<p>Summary</p>",
            "controls": [str(audits.other_control.id)],
            "clauses": [str(audits.leaf.id)],
            "documents": [str(audits.document.id)],
            "attendees": [{"name": "Replacement auditor"}],
        },
    )

    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert (body["framework_slug"], body["audit_type"], body["end_date"]) == (
        "A",
        "external",
        "2027-01-20",
    )
    # Rich text is sanitised: the script element goes, its text stays inert.
    assert body["report_notes"] == "<p>Scope notesbad()</p>"
    assert body["executive_summary"] == "<p>Summary</p>"
    assert [a["name"] for a in body["attendees"]] == ["Replacement auditor"]
    audit_id = uuid.UUID(row["id"])
    db = audits.db
    db.expire_all()
    # The scoped clause brings its linked control into scope with the chosen one.
    assert {
        r.control_item_id
        for r in db.query(AuditScopedControl).filter_by(audit_id=audit_id)
    } == {audits.other_control.id, audits.control.id}
    assert [
        r.clause_id for r in db.query(AuditScopedClause).filter_by(audit_id=audit_id)
    ] == [audits.leaf.id]
    assert [
        r.document_id
        for r in db.query(AuditScopedIsmsDocument).filter_by(audit_id=audit_id)
    ] == [audits.document.id]

    # A field left out of an edit keeps its value.
    moved = _send(audits, "PATCH", path, {"framework_slug": "B"}).json()
    retitled = _send(audits, "PATCH", path, {"title": "Renamed review"}).json()
    assert moved["framework_slug"] == "B"
    assert (retitled["title"], retitled["framework_slug"], retitled["audit_type"]) == (
        "Renamed review",
        "B",
        "external",
    )


def test_a_new_start_date_moves_the_next_run(audits):  # noqa: F811 - fixture imported above
    row = _created(audits)
    path = f"{ROOT}/{row['id']}"

    moved = _send(audits, "PATCH", path, {"start_date": "2027-02-10"}).json()

    assert (moved["start_date"], moved["schedule_next_run_date"]) == (
        "2027-02-10",
        "2027-02-10",
    )
    for body, detail in [
        ({"start_date": None}, "start_date is required"),
        (
            {"schedule_until_date": "2027-02-01"},
            "schedule_until_date cannot be before the next run date",
        ),
    ]:
        refused = _send(audits, "PATCH", path, body)
        assert (refused.status_code, refused.json()["detail"]) == (400, detail)
    unchanged = _send(audits, "GET", ROOT).json()["items"]
    assert [i["schedule_next_run_date"] for i in unchanged if i["id"] == row["id"]] == [
        "2027-02-10"
    ]


def test_only_templates_are_edited_or_deleted_as_schedules(audits):  # noqa: F811 - fixture imported above
    path = f"{ROOT}/{audits.open_audit.id}"
    for method, body in [("PATCH", {"title": "Changed"}), ("DELETE", None)]:
        refused = _send(audits, method, path, body, user=audits.keen.user(role="admin"))
        assert (refused.status_code, refused.json()["detail"]) == (
            400,
            "Audit is not a scheduled template",
        )
    audits.db.expire_all()
    assert audits.open_audit.title == "Supplier audit"
    assert audits.open_audit.start_date is None or isinstance(
        audits.open_audit.start_date, date
    )
