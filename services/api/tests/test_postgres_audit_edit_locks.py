"""Every audit edit holds the audit's row lock until it commits.

An edit that overlaps another edit of the same audit waits for it and then
works from what it saved: an edit never lands in an audit that was completed
meanwhile, and two scope replacements never merge. Scheduled runs take the
same lock on each template, so overlapping runs create one audit per run
date and a template edit made during a run keeps its new schedule. The
changes run on separate PostgreSQL connections, so these tests skip on SQLite.
"""

import io
import uuid
from datetime import date

import pytest
from app.core.config import settings
from app.db.models import (
    Audit,
    AuditAttendee,
    AuditEvidence,
    AuditFinding,
    AuditScopedControl,
    ControlItem,
    User,
)
from app.services import audit_schedules as schedules
from app.services.audits import operations, reports, scheduling
from app.services.audits import scope as audit_scope
from app.services.audits.schemas import (
    AuditAttendeePayload,
    AuditControlsPayload,
    AuditEvidencePayload,
    AuditEvidenceUpdatePayload,
    AuditFindingPayload,
    AuditFindingUpdatePayload,
    AuditScopePayload,
    AuditUpdatePayload,
    ScheduledAuditUpdatePayload,
)

# Imported fixture: two changes on separate connections, the first held at a
# chosen call while the second starts.
from tests.test_postgres_concurrent_replacement import race  # noqa: F401

FRAMEWORK = "iso27001"
TODAY = date(2026, 4, 10)  # a Friday


def control(race):  # noqa: F811 - fixture imported above
    with race.session() as db:
        row = ControlItem(
            framework_slug=FRAMEWORK,
            ref="A." + uuid.uuid4().hex[:8],
            type="annex_control",
            title="Access control",
        )
        db.add(row)
        db.commit()
        return str(row.id)


def in_progress_audit(race):  # noqa: F811 - fixture imported above
    """An audit with one of everything an edit can change."""
    with race.session() as db:
        audit = Audit(
            title="Access review",
            framework_slug=FRAMEWORK,
            status="in_progress",
            created_by_user_id=race.user_id,
        )
        db.add(audit)
        db.flush()
        evidence = AuditEvidence(
            audit_id=audit.id,
            evidence_url="https://evidence.example/access-review",
            title="Access review export",
            added_by_user_id=race.user_id,
        )
        attendee = AuditAttendee(audit_id=audit.id, name="Lead auditor")
        finding = AuditFinding(audit_id=audit.id, kind="ofi", title="Stale accounts")
        db.add_all([evidence, attendee, finding])
        db.commit()
        return {
            "audit": str(audit.id),
            "evidence": str(evidence.id),
            "attendee": str(attendee.id),
            "finding": str(finding.id),
        }


def contents(race, audit_id):  # noqa: F811 - fixture imported above
    """Everything about an audit that its edits change, apart from its status."""
    aid = uuid.UUID(audit_id)
    with race.session() as db:
        audit = db.get(Audit, aid)
        return {
            "title": audit.title,
            "report": audit.final_report_storage_uri,
            "scope": sorted(
                str(row.control_item_id)
                for row in db.query(AuditScopedControl).filter_by(audit_id=aid)
            ),
            "samples": sorted(
                (str(row.id), row.notes)
                for row in db.query(AuditEvidence).filter_by(audit_id=aid)
            ),
            "attendees": sorted(
                str(row.id) for row in db.query(AuditAttendee).filter_by(audit_id=aid)
            ),
            "findings": sorted(
                (str(row.id), row.title)
                for row in db.query(AuditFinding).filter_by(audit_id=aid)
            ),
        }


def complete(audit_id):
    def change(db, user):
        operations.update_audit(
            audit_id=audit_id,
            payload=AuditUpdatePayload(status="completed"),
            db=db,
            user=user,
        )

    return change


def edit(name, ids, control_id):
    """One audit edit by name, against the rows in_progress_audit made."""
    audit_id = ids["audit"]
    calls = {
        "rename": lambda db, user: operations.update_audit(
            audit_id=audit_id,
            payload=AuditUpdatePayload(title="Renamed review"),
            db=db,
            user=user,
        ),
        "replace scope": lambda db, user: operations.replace_audit_scope(
            audit_id=audit_id,
            payload=AuditScopePayload(controls=[control_id]),
            db=db,
            user=user,
        ),
        "replace controls": lambda db, user: operations.replace_audit_controls(
            audit_id=audit_id,
            payload=AuditControlsPayload(controls=[control_id]),
            db=db,
            user=user,
        ),
        "add sample": lambda db, user: operations.add_audit_evidence(
            audit_id=audit_id,
            payload=AuditEvidencePayload(
                evidence_url="https://evidence.example/joiners", title="Joiners"
            ),
            db=db,
            user=user,
        ),
        "edit sample": lambda db, user: operations.update_audit_evidence(
            audit_id=audit_id,
            evidence_id=ids["evidence"],
            payload=AuditEvidenceUpdatePayload(notes="<p>Checked</p>"),
            db=db,
            user=user,
        ),
        "remove sample": lambda db, user: operations.delete_audit_evidence(
            audit_id=audit_id, evidence_id=ids["evidence"], db=db, user=user
        ),
        "add attendee": lambda db, user: operations.add_attendee(
            audit_id=audit_id,
            payload=AuditAttendeePayload(name="Observer"),
            db=db,
            user=user,
        ),
        "remove attendee": lambda db, user: operations.delete_attendee(
            audit_id=audit_id, attendee_id=ids["attendee"], db=db, user=user
        ),
        "add finding": lambda db, user: operations.add_finding(
            audit_id=audit_id,
            payload=AuditFindingPayload(kind="minor_nc", title="Shared account"),
            db=db,
            user=user,
        ),
        "edit finding": lambda db, user: operations.update_finding(
            audit_id=audit_id,
            finding_id=ids["finding"],
            payload=AuditFindingUpdatePayload(title="Dormant accounts"),
            db=db,
            user=user,
        ),
        "remove finding": lambda db, user: operations.delete_finding(
            audit_id=audit_id, finding_id=ids["finding"], db=db, user=user
        ),
        "upload report": lambda db, user: reports.save_final_report(
            audit_id=audit_id,
            db=db,
            user=user,
            filename="report.txt",
            content_type="text/plain",
            content=io.BytesIO(b"final report"),
        ),
    }
    return calls[name]


EDITS = [
    "rename",
    "replace scope",
    "replace controls",
    "add sample",
    "edit sample",
    "remove sample",
    "add attendee",
    "remove attendee",
    "add finding",
    "edit finding",
    "remove finding",
    "upload report",
]


def refused(action="modified"):
    return (
        409,
        f"This audit is completed and cannot be {action}. "
        "Change its status back to Open or In progress before editing it.",
    )


@pytest.mark.parametrize("name", EDITS)
def test_an_edit_overlapping_the_audits_completion_waits_and_is_refused(race, name):  # noqa: F811 - fixture imported above
    ids = in_progress_audit(race)
    control_id = control(race)
    before = contents(race, ids["audit"])

    outcome = race.run(
        complete(ids["audit"]),
        edit(name, ids, control_id),
        # The completion holds here, after reading the audit and before it commits.
        pause_at=(operations, "validate_dates"),
    )

    action = "sampled into" if name == "add sample" else "modified"
    assert outcome == ("done", refused(action))
    with race.session() as db:
        assert db.get(Audit, uuid.UUID(ids["audit"])).status == "completed"
    assert contents(race, ids["audit"]) == before
    assert race.stored(f"audit-reports/{ids['audit']}") == set()


def test_overlapping_scope_replacements_keep_the_later_scope_whole(race):  # noqa: F811 - fixture imported above
    ids = in_progress_audit(race)
    original, first, second = control(race), control(race), control(race)
    with race.session() as db:
        operations.replace_audit_controls(
            audit_id=ids["audit"],
            payload=AuditControlsPayload(controls=[original]),
            db=db,
            user=db.get(User, race.user_id),
        )

    def replace(control_id):
        def change(db, user):
            operations.replace_audit_scope(
                audit_id=ids["audit"],
                payload=AuditScopePayload(controls=[control_id]),
                db=db,
                user=user,
            )

        return change

    outcome = race.run(
        replace(first),
        replace(second),
        # The first replacement holds here, with the earlier scope deleted and
        # its own not yet written.
        pause_at=(audit_scope, "utcnow"),
    )

    assert outcome == ("done", "done")
    assert contents(race, ids["audit"])["scope"] == [second]


def test_a_template_deletion_overlapping_a_report_upload_leaves_no_report_behind(race):  # noqa: F811 - fixture imported above
    with race.session() as db:
        template = Audit(
            title="Quarterly review",
            framework_slug=FRAMEWORK,
            status="template",
            created_by_user_id=race.user_id,
        )
        db.add(template)
        db.commit()
        template_id = str(template.id)
    prefix = f"audit-reports/{template_id}"
    upload = edit("upload report", {"audit": template_id}, None)
    with race.session() as db:
        upload(db, db.get(User, race.user_id))

    def delete(db, user):
        scheduling.delete_scheduled_audit(audit_id=template_id, db=db, user=user)

    outcome = race.run(upload, delete, pause_at=(reports, "put_file"))

    assert outcome == ("done", "done")
    with race.session() as db:
        assert db.get(Audit, uuid.UUID(template_id)) is None
    files = race.stored(prefix)
    assert len(files) == 2 and race.queued(prefix) == files


# --- Scheduled runs -------------------------------------------------------------------


@pytest.fixture
def template(race, monkeypatch):  # noqa: F811 - fixture imported above
    monkeypatch.setattr(settings, "scheduled_audit_create_days_ahead", 0)
    with race.session() as db:
        row = Audit(
            title="Monthly access review " + uuid.uuid4().hex[:6],
            framework_slug=FRAMEWORK,
            status="template",
            start_date=date(2026, 3, 10),
            schedule_recurrence="monthly",
            schedule_interval=1,
            schedule_date_rule="exact",
            schedule_next_run_date=TODAY,
            created_by_user_id=race.user_id,
        )
        db.add(row)
        db.commit()
        return row.id


def materialise(results):
    def change(db, user):
        results.append(schedules.materialize_due_scheduled_audits(db, today=TODAY))

    return change


def runs_of(race, template_id):  # noqa: F811 - fixture imported above
    with race.session() as db:
        return sorted(
            (audit.title, audit.start_date)
            for audit in db.query(Audit).filter(Audit.status == "open")
            if (audit.meta or {}).get("scheduled_from_template_id") == str(template_id)
        )


@pytest.mark.parametrize("missed", [False, True], ids=["due-today", "missed-run"])
def test_overlapping_runs_create_one_audit_for_a_run_date(race, template, missed):  # noqa: F811 - fixture imported above
    if missed:
        # The run first moves past the missed March date, then creates today's
        # audit; both steps happen under the one lock.
        with race.session() as db:
            db.get(Audit, template).schedule_next_run_date = date(2026, 3, 10)
            db.commit()
    first, second = [], []

    outcome = race.run(
        materialise(first),
        materialise(second),
        pause_at=(schedules, "_copy_template_scope_and_attendees"),
    )

    assert outcome == ("done", "done")
    assert [run["run_date"] for run in first[0]["created"]] == ["2026-04-10"]
    skipped = [run["skipped_run_dates"] for run in first[0]["skipped_past"]]
    assert skipped == ([["2026-03-10"]] if missed else [])
    assert second[0]["created"] == second[0]["skipped_past"] == []
    ((title, start),) = runs_of(race, template)
    assert title.endswith("(2026-04-10)") and start == TODAY
    with race.session() as db:
        saved = db.get(Audit, template)
        # 10 May 2026 is a Sunday, so the next run is Friday 8 May.
        assert (saved.schedule_last_run_date, saved.schedule_next_run_date) == (
            TODAY,
            date(2026, 5, 8),
        )


def test_a_template_edit_during_a_run_keeps_its_new_schedule(race, template):  # noqa: F811 - fixture imported above
    results = []
    moved = date(2026, 6, 15)

    def reschedule(db, user):
        scheduling.update_scheduled_audit(
            audit_id=str(template),
            payload=ScheduledAuditUpdatePayload(start_date=moved),
            db=db,
            user=user,
        )

    outcome = race.run(
        materialise(results),
        reschedule,
        pause_at=(schedules, "_copy_template_scope_and_attendees"),
    )

    assert outcome == ("done", "done")
    assert [run["run_date"] for run in results[0]["created"]] == ["2026-04-10"]
    with race.session() as db:
        saved = db.get(Audit, template)
        assert (saved.start_date, saved.schedule_next_run_date) == (moved, moved)
        assert saved.schedule_last_run_date == TODAY


def test_a_run_waits_for_a_template_edit_and_follows_its_schedule(race, template):  # noqa: F811 - fixture imported above
    results = []
    moved = date(2026, 6, 15)

    def reschedule(db, user):
        scheduling.update_scheduled_audit(
            audit_id=str(template),
            payload=ScheduledAuditUpdatePayload(start_date=moved),
            db=db,
            user=user,
        )

    outcome = race.run(
        reschedule,
        materialise(results),
        # The edit holds here, after reading the template and before it commits.
        pause_at=(scheduling, "apply_schedule_fields"),
    )

    assert outcome == ("done", "done")
    # The run saw the template as the edit left it: nothing is due until June.
    assert results[0]["created"] == []
    assert runs_of(race, template) == []
    with race.session() as db:
        assert db.get(Audit, template).schedule_next_run_date == moved


def test_a_template_made_an_ordinary_audit_during_a_run_is_not_run(race, template):  # noqa: F811 - fixture imported above
    results = []

    def make_ordinary(db, user):
        operations.update_audit(
            audit_id=str(template),
            payload=AuditUpdatePayload(status="open"),
            db=db,
            user=user,
        )

    outcome = race.run(
        make_ordinary,
        materialise(results),
        pause_at=(operations, "validate_dates"),
    )

    assert outcome == ("done", "done")
    assert results[0]["created"] == []
    assert runs_of(race, template) == []


def test_deleting_a_template_during_a_run_keeps_the_audit_it_created(race, template):  # noqa: F811 - fixture imported above
    results = []

    def delete(db, user):
        scheduling.delete_scheduled_audit(audit_id=str(template), db=db, user=user)

    outcome = race.run(
        materialise(results),
        delete,
        pause_at=(schedules, "_copy_template_scope_and_attendees"),
    )

    assert outcome == ("done", "done")
    assert [run["run_date"] for run in results[0]["created"]] == ["2026-04-10"]
    with race.session() as db:
        assert db.get(Audit, template) is None
    assert len(runs_of(race, template)) == 1
