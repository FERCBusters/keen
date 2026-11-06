"""Mapping rules suggested from unmapped events.

The service tests run on SQLite and, when the contract database is configured,
on PostgreSQL, where a run also takes its lock and query budget. The
administrator routes are covered through the assembled application at the end.
"""

import os
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from app.core import source_meta
from app.db.models import (
    ControlItem,
    Event,
    Framework,
    ManagedConfiguration,
    Mapping,
    RuleBackfillJob,
    RuleSuggestion,
    RuleSuggestionRun,
    User,
)
from app.services import rule_suggestions as suggestions
from app.services.errors import DomainError
from app.services.rule_suggestions import SuggestionApplyInput, SuggestionStatusInput
from app.worker import tasks
from app.worker.celery_app import celery_app
from psycopg2 import errors
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from tests.db_helpers import create_sqlite_schema

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)

NOW = datetime(2026, 10, 10, 12, 0, 0)
FW = "fw-suggestions"
OTHER_FW = "fw-suggestions-other"
CONTROLS = (
    (
        "CM-1",
        "Change management",
        "Changes to information processing facilities follow change management.",
    ),
    (
        "AC-1",
        "Access rights",
        "Access rights are provisioned, reviewed and removed in line with policy.",
    ),
    (
        "AU-1",
        "Authentication information",
        "Allocation and management of authentication information is controlled.",
    ),
    ("LG-1", "Logging", "Logs that record activities and faults are produced."),
    ("PH-1", "Physical security monitoring", "Premises are monitored."),
)


@pytest.fixture(autouse=True)
def labels(monkeypatch):
    # Source labels come from the shipped defaults, not a database.
    monkeypatch.setattr(source_meta, "_configured_source_meta", lambda _: {})


@pytest.fixture(params=["sqlite", "postgresql"])
def db(request):
    if request.param == "postgresql":
        if not os.getenv("KEEN_CONTRACT_TEST_DATABASE_URL"):
            pytest.skip("Set KEEN_CONTRACT_TEST_DATABASE_URL to run on PostgreSQL")
        session = request.getfixturevalue("contract_db")
        _catalogue(session)
        yield session
        return
    engine = create_engine("sqlite://")
    create_sqlite_schema(engine)
    with Session(engine) as session:
        _catalogue(session)
        yield session
    engine.dispose()


def _catalogue(session):
    for slug in (FW, OTHER_FW):
        session.add(Framework(slug=slug, name=f"Framework {slug}"))
        for ref, title, description in CONTROLS:
            session.add(
                ControlItem(
                    framework_slug=slug,
                    type="annex_control",
                    ref=ref,
                    title=title,
                    meta={"description": description},
                )
            )
    # A clause is never a suggested target while the framework has controls.
    session.add(
        ControlItem(
            framework_slug=FW, type="clause", ref="4.1", title="Change management"
        )
    )
    session.add(
        ManagedConfiguration(
            name="organisation-frameworks",
            version=1,
            document={"enabled": [FW, OTHER_FW], "default": FW},
        )
    )
    session.flush()


@pytest.fixture
def admin(db):
    row = User(username="suggester", password_hash="x", role="admin", is_active=True)
    db.add(row)
    db.flush()
    return row


def events(db, count, *, source="github", action="PullRequestEvent", **columns):
    rows = [
        Event(
            id=uuid.uuid4(),
            source=source,
            action=action,
            system=columns.get("system", f"acme/repo-{index}"),
            outcome="info",
            summary=columns.get("summary", f"alice opened pull request #{index}"),
            timestamp=columns.get("end", NOW - timedelta(hours=1))
            - timedelta(minutes=index),
            raw_pointer={},
            normalized_payload={},
            external_id=uuid.uuid4().hex,
        )
        for index in range(count)
    ]
    db.add_all(rows)
    db.flush()
    return rows


def mapped(db, rows, ref="LG-1", framework=FW):
    control = db.scalar(
        select(ControlItem).where(
            ControlItem.framework_slug == framework, ControlItem.ref == ref
        )
    )
    for row in rows:
        db.add(
            Mapping(
                event_id=row.id,
                control_item_id=control.id,
                method="manual",
                confidence=1,
                rationale="Reviewed",
            )
        )
    db.flush()


def save_rules(db, *rules):
    row = db.get(ManagedConfiguration, "rules")
    if row is None:
        db.add(ManagedConfiguration(name="rules", version=1, document={"rules": []}))
        db.flush()
        row = db.get(ManagedConfiguration, "rules")
    row.document = {"rules": list(rules)}
    row.version += 1
    db.commit()


def rule(rule_id, *, refs=("AU-1",), enabled=True, **when):
    return {
        "id": rule_id,
        "description": rule_id.replace("_", " "),
        "when": when,
        "map_to": [{"framework": FW, "ref": ref, "kind": "control"} for ref in refs],
        "enabled": enabled,
    }


def refresh(db):
    return suggestions.refresh_suggestions(db, now=NOW)


def open_rows(db):
    db.expire_all()
    return list(
        db.scalars(
            select(RuleSuggestion)
            .where(RuleSuggestion.status == "open")
            .order_by(RuleSuggestion.event_count.desc(), RuleSuggestion.signature)
        )
    )


def chosen(row):
    return [target["ref"] for target in row.rule["map_to"]]


# --- Finding suggestions ----------------------------------------------------------


def test_an_unmapped_activity_becomes_a_rule_for_its_matching_controls(db):
    events(db, 6)

    result = refresh(db)

    assert result == {
        "status": "completed",
        "examined": 6,
        "unmapped": 6,
        "suggested": 1,
    }
    (row,) = open_rows(db)
    assert row.framework_slug == FW
    assert row.keys == [["github", "PullRequestEvent"]]
    assert row.event_count == 6
    assert row.rule["when"] == {"source": "github", "action": "PullRequestEvent"}
    assert row.rule["id"] == "suggested_github_pull_request_event"
    assert row.rule["description"] == "GitHub: PullRequestEvent"
    # "Pull Request Event" is change management; the clause sharing the name
    # and the unrelated controls are left out.
    assert chosen(row) == ["CM-1"]
    assert row.rule["map_to"][0]["kind"] == "control"
    best = row.controls[0]
    assert best["ref"] == "CM-1" and best["selected"]
    assert any("pull request" in reason for reason in best["reasons"])
    assert len(row.samples) == suggestions.SAMPLES_KEPT
    # Samples keep identifiers and times, never event content.
    assert set(row.samples[0]) == {"id", "timestamp"}
    assert row.first_seen < row.last_seen
    run = db.scalar(select(RuleSuggestionRun))
    assert (run.status, run.examined, run.unmapped, run.suggested) == (
        "completed",
        6,
        6,
        1,
    )


def test_mapped_old_private_and_rare_events_are_not_suggested(db):
    mapped(db, events(db, 6, action="PushEvent"))
    events(db, 6, action="ReleaseEvent", end=NOW - timedelta(days=31))
    events(db, 6, source="diary", action="change_management")
    events(db, suggestions.MIN_EVENTS - 1, action="DeleteEvent")
    # The same activity mapped only in another framework is still unmapped here.
    mapped(db, events(db, 6, action="PullRequestEvent"), framework=OTHER_FW)

    result = refresh(db)

    assert result["examined"] == 6 + 4 + 6
    assert [row.keys for row in open_rows(db)] == [[["github", "PullRequestEvent"]]]


def test_one_activity_from_several_sources_becomes_one_rule(db):
    events(db, 6, source="gitlab", action="pull request opened")
    events(db, 7, source="github", action="pull request opened")

    refresh(db)

    (row,) = open_rows(db)
    assert row.keys == [
        ["github", "pull request opened"],
        ["gitlab", "pull request opened"],
    ]
    assert row.event_count == 13
    assert row.rule["when"] == {
        "source_regex": "\\A(?:github|gitlab)\\Z",
        "action": "pull request opened",
    }
    assert row.rule["id"] == "suggested_sources_pull_request_opened"
    assert row.rule["description"] == "GitHub, GitLab: pull request opened"


def test_sources_too_long_for_one_pattern_get_a_rule_each(db, monkeypatch):
    monkeypatch.setattr(suggestions, "combined_pattern", lambda values: None)
    events(db, 6, source="gitlab", action="pull request opened")
    events(db, 7, source="github", action="pull request opened")

    refresh(db)

    assert [row.rule["when"] for row in open_rows(db)] == [
        {"source": "github", "action": "pull request opened"},
        {"source": "gitlab", "action": "pull request opened"},
    ]


def test_the_most_frequent_activities_are_suggested_first(db, monkeypatch):
    monkeypatch.setattr(suggestions, "MAX_OPEN", 1)
    events(db, 6, source="gitlab", action="merge_request")
    events(db, 7)

    assert refresh(db)["suggested"] == 1
    assert [row.keys for row in open_rows(db)] == [[["github", "PullRequestEvent"]]]


def test_a_source_without_activities_gets_a_rule_for_the_whole_source(db):
    events(
        db,
        6,
        source="feeds",
        action=None,
        system=None,
        summary="Password policy changed",
    )
    # Where a source names activities, events without one are left for review.
    events(db, 6, action=None, summary="Password policy changed")
    events(db, 2, action="PushEvent")

    refresh(db)

    (row,) = open_rows(db)
    assert row.keys == [["feeds", ""]]
    assert row.rule["when"] == {"source": "feeds"}
    assert row.rule["id"] == "suggested_feeds"
    assert row.rule["description"] == "Feeds: all events"
    assert chosen(row) == ["AU-1"]


def test_a_weak_match_is_not_suggested(db):
    events(db, 6, action="quuxxy", summary="frobnicated widget", system="x")

    assert refresh(db)["suggested"] == 0
    assert open_rows(db) == []


def test_a_rule_already_covering_the_activity_stops_the_suggestion(db):
    events(db, 6)
    events(db, 6, source="gitlab", action="merge_request")
    events(db, 6, source="forgejo", action="pull_request")
    save_rules(
        db,
        # Other activities of the same source leave these events to suggest.
        rule("pushes", source="github", action="PushEvent"),
        rule("releases", source="github", action_regex="^Release"),
        # Paused, it still describes these events.
        rule("prs", source="github", action="PullRequestEvent", enabled=False),
        rule("merges", source_regex="^git", action_regex="^merge"),
        # Another framework's rule leaves this framework's mapping to be made.
        {
            **rule("forgejo", source="forgejo"),
            "map_to": [{"framework": OTHER_FW, "ref": "CM-1"}],
        },
    )

    refresh(db)

    assert [row.keys for row in open_rows(db)] == [[["forgejo", "pull_request"]]]


def test_saved_rules_for_related_events_count_as_precedent(db):
    events(db, 6, source="aws", action="posture.identity.mfa", system="iam")
    events(db, 6, source="ci", action="build", system="runner")
    save_rules(
        db,
        # Narrower than the activity, so its unmapped events remain.
        rule(
            "root_mfa",
            refs=("AU-1",),
            source="aws",
            action="posture.identity.mfa",
            outcome="fail",
        ),
        rule("ci_builds", refs=("LG-1",), source="ci", system="runner", outcome="ok"),
        # A weaker relation to a control already voted for, and a reference
        # the catalogue does not hold, change nothing.
        rule("aws_any", refs=("AU-1", "ZZ-9"), source="aws", outcome="unknown"),
    )

    refresh(db)

    by_source = {row.keys[0][0]: row for row in open_rows(db)}
    aws = by_source["aws"]
    assert chosen(aws)[0] == "AU-1"
    reasons = next(c["reasons"] for c in aws.controls if c["ref"] == "AU-1")
    assert "Rule “root mfa” maps the same activity to this control" in reasons
    ci = by_source["ci"]
    assert chosen(ci) == ["LG-1"]
    assert ci.controls[0]["reasons"] == [
        "Rule “ci builds” maps other events from runner to this control"
    ]


@pytest.mark.parametrize(
    "when,relation",
    [
        ({"source": "aws", "action": "posture.identity.mfa"}, "activity"),
        ({"source": "azure", "action": "posture.identity.mfa"}, "elsewhere"),
        ({"source": "aws", "action_regex": "^posture\\.identity"}, "pattern"),
        ({"source": "azure", "action_regex": "^posture\\.identity"}, None),
        ({"source": "azure", "action": "posture.identity.root"}, "family"),
        ({"source": "aws", "system": "iam"}, "system"),
        ({"source": "aws", "system": "other"}, "source"),
        ({"source": "aws", "action_regex": "("}, "source"),
        ({"source_regex": "(", "action": "other"}, None),
        ({"source": "azure"}, None),
    ],
)
def test_how_a_saved_rule_relates_to_a_suggestion(when, relation):
    group = suggestions._Group("aws", "posture.identity.mfa")
    for index in range(5):
        group.add(
            SimpleNamespace(id=uuid.uuid4(), system="iam", summary="", timestamp=NOW)
        )
    proposal = suggestions._Proposal([group], {})
    assert suggestions._relation(when, proposal) == relation


def test_reasons_name_each_concept_once_and_say_where_it_was_found():
    concept = lambda term, target, origin: {  # noqa: E731
        "query_term": term,
        "catalogue_term": target,
        "origin": origin,
    }
    item = {
        "matched_terms": ["password"],
        "matched_concepts": [
            concept("password", "authentication information", "mapping name"),
            concept("password", "authentication information", "sample"),
            concept("iam", "access rights", "context"),
            concept("mfa", "secure authentication", "sample"),
            concept("sso", "authentication", "sample"),
        ],
    }
    assert suggestions._wording_reasons(item) == [
        "Catalogue words: password",
        "Related concept: password → authentication information (activity)",
        "Related concept: iam → access rights (source or system)",
        "Related concept: mfa → secure authentication (summaries)",
    ]


def test_a_varied_system_does_not_describe_the_activity():
    group = suggestions._Group("github", "PushEvent")
    for system in ["a", "a", "a", "b", "c"]:
        group.add(
            SimpleNamespace(id=uuid.uuid4(), system=system, summary="", timestamp=NOW)
        )
    assert suggestions._Proposal([group], {}).system == ""
    # Four in five events from one system: that system describes the activity.
    for _ in range(5):
        group.add(
            SimpleNamespace(id=uuid.uuid4(), system="a", summary="", timestamp=NOW)
        )
    assert suggestions._Proposal([group], {}).system == "a"


def test_suggested_rule_ids_avoid_saved_rules(db):
    events(db, 6)
    save_rules(db, rule("suggested_github_pull_request_event", source="other"))

    refresh(db)

    (row,) = open_rows(db)
    assert row.rule["id"] == "suggested_github_pull_request_event_2"


def test_controls_sharing_a_reference_are_never_suggested(db):
    db.add(
        ControlItem(
            framework_slug=FW,
            type="custom",
            ref="CM-1",
            title="Change management",
        )
    )
    db.flush()
    events(db, 6)

    refresh(db)

    assert all("CM-1" not in chosen(row) for row in open_rows(db))


def test_a_framework_of_clauses_only_suggests_clauses(db):
    db.add(Framework(slug="clauses-only", name="Clauses"))
    db.add(
        ControlItem(
            framework_slug="clauses-only",
            type="clause",
            ref="8.1",
            title="Change management",
        )
    )
    db.get(ManagedConfiguration, "organisation-frameworks").document = {
        "enabled": [FW, "clauses-only"],
        "default": "clauses-only",
    }
    db.flush()
    events(db, 6)

    refresh(db)

    (row,) = open_rows(db)
    assert row.rule["map_to"] == [
        {"framework": "clauses-only", "ref": "8.1", "kind": "clause"}
    ]


# --- Keeping suggestions current ----------------------------------------------------


def test_a_later_run_updates_withdraws_and_never_repeats_decisions(db, admin):
    events(db, 6)
    events(db, 6, source="gitlab", action="merge_request")
    refresh(db)
    first = {tuple(map(tuple, row.keys)): row for row in open_rows(db)}
    pulls = first[(("github", "PullRequestEvent"),)]
    merges = first[(("gitlab", "merge_request"),)]

    events(db, 3)
    mapped(db, db.scalars(select(Event).where(Event.source == "gitlab")).all())
    refresh(db)

    (row,) = open_rows(db)
    assert row.id == pulls.id
    assert row.event_count == 9
    assert db.get(RuleSuggestion, merges.id) is None

    suggestions.set_suggestion_status(
        suggestion_id=str(row.id),
        payload=SuggestionStatusInput(status="dismissed"),
        user=admin,
        db=db,
    )
    refresh(db)
    assert open_rows(db) == []
    assert db.get(RuleSuggestion, row.id).status == "dismissed"


def test_decisions_made_while_a_run_works_stand(db, admin, monkeypatch):
    events(db, 6)
    events(db, 6, source="gitlab", action="merge_request")
    refresh(db)
    by_source = {row.keys[0][0]: row for row in open_rows(db)}
    pulls, merges = by_source["github"], by_source["gitlab"]
    status = SuggestionStatusInput(status="dismissed")
    suggestions.set_suggestion_status(
        suggestion_id=str(pulls.id), payload=status, user=admin, db=db
    )
    # The run read the decisions before this one was made.
    monkeypatch.setattr(suggestions, "_decided", lambda db, framework: set())
    # Its events are mapped since, but it was restored after the run began, so
    # its updated time is later than the run's.
    mapped(db, db.scalars(select(Event).where(Event.source == "gitlab")).all())
    merges.updated_at = NOW + timedelta(seconds=1)
    db.commit()

    result = refresh(db)

    assert result["suggested"] == 0
    assert db.get(RuleSuggestion, pulls.id).status == "dismissed"
    assert db.get(RuleSuggestion, merges.id).status == "open"


def test_a_new_default_framework_withdraws_the_others_suggestions(db):
    events(db, 6)
    refresh(db)
    db.get(ManagedConfiguration, "organisation-frameworks").document = {
        "enabled": [FW, OTHER_FW],
        "default": OTHER_FW,
    }
    db.flush()

    refresh(db)

    assert [row.framework_slug for row in open_rows(db)] == [OTHER_FW]


def test_runs_are_kept_up_to_a_limit(db, monkeypatch):
    monkeypatch.setattr(suggestions, "RUNS_KEPT", 2)
    for _ in range(4):
        refresh(db)
    assert db.scalar(select(func.count()).select_from(RuleSuggestionRun)) == 2


def test_a_disabled_evidence_feature_or_missing_framework_stops_a_run(db, monkeypatch):
    events(db, 6)
    monkeypatch.setattr(
        suggestions,
        "feature_state",
        lambda _: SimpleNamespace(enabled=lambda key: key != "evidence"),
    )
    assert refresh(db) == {"status": "disabled"}
    assert suggestions.suggestion_summary(db=db) == {"open_count": 0}
    with pytest.raises(
        DomainError, match=r"^This feature is disabled by an administrator\.$"
    ) as refused:
        suggestions.request_refresh(db=db)
    assert refused.value.status_code == 404
    monkeypatch.undo()
    monkeypatch.setattr(source_meta, "_configured_source_meta", lambda _: {})
    monkeypatch.setattr(
        suggestions, "list_frameworks", lambda db: {"default": "", "items": []}
    )
    assert refresh(db) == {"status": "no_framework"}
    assert suggestions.suggestion_summary(db=db)["open_count"] == 0


def test_a_failed_run_is_recorded_and_changes_nothing(db, monkeypatch):
    events(db, 6)
    refresh(db)
    (row,) = open_rows(db)

    def cancelled(*args):
        raise OperationalError("SELECT 1", {}, errors.QueryCanceled("cancelled"))

    monkeypatch.setattr(suggestions, "_refresh", cancelled)
    assert refresh(db) == {"status": "failed"}
    monkeypatch.setattr(
        suggestions, "_refresh", lambda *args: (_ for _ in ()).throw(RuntimeError())
    )
    assert refresh(db) == {"status": "failed"}

    errors_seen = list(
        db.scalars(
            select(RuleSuggestionRun.error)
            .where(RuleSuggestionRun.status == "failed")
            .order_by(RuleSuggestionRun.finished_at)
        )
    )
    assert errors_seen == [
        "A query took too long; the next run tries again",
        "Suggestions could not be worked out; check the worker log",
    ]
    assert [r.id for r in open_rows(db)] == [row.id]


def test_runs_take_turns_and_have_a_query_budget(db, request):
    postgres = db.get_bind().dialect.name == "postgresql"
    if postgres:
        # Another run holds the lock: this one returns at once.
        engine = request.getfixturevalue("contract_postgres_engine")
        with engine.connect() as other:
            other.execute(
                text("SELECT pg_advisory_lock(:key)"), {"key": suggestions.LOCK_KEY}
            )
            try:
                assert refresh(db) == {"status": "busy"}
            finally:
                other.execute(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": suggestions.LOCK_KEY},
                )
    events(db, 6)
    assert refresh(db)["status"] == "completed"
    if postgres:
        assert db.scalar(text("SHOW statement_timeout")) == "30s"


# --- Reviewing suggestions ------------------------------------------------------


def test_the_list_shows_live_samples_and_hides_suggestions_mapped_since(db, admin):
    rows = events(db, 6)
    events(db, 6, source="gitlab", action="merge_request")
    refresh(db)

    listed = suggestions.list_suggestions(db=db)
    assert listed["framework"] == FW
    assert listed["framework_name"] == f"Framework {FW}"
    assert listed["enabled"] is True
    assert listed["last_run"]["status"] == "completed"
    assert listed["last_run"]["suggested"] == 2
    item = next(i for i in listed["items"] if i["action"] == "PullRequestEvent")
    assert item["sources"] == [{"source": "github", "label": "GitHub"}]
    assert item["event_count"] == 6
    assert item["samples"][0] == {
        "id": str(rows[0].id),
        "timestamp": rows[0].timestamp.isoformat(),
        "summary": "alice opened pull request #0",
    }
    assert suggestions.suggestion_summary(db=db) == {
        "open_count": 2,
        "framework": FW,
        "framework_name": f"Framework {FW}",
    }

    # Mapped since the run, by another rule or by hand: no longer suggested.
    mapped(db, rows)
    assert [i["action"] for i in suggestions.list_suggestions(db=db)["items"]] == [
        "merge_request"
    ]
    assert suggestions.suggestion_summary(db=db)["open_count"] == 1
    # A purged sample leaves the suggestion listed without it.
    merge = next(row for row in open_rows(db) if row.keys[0][0] == "gitlab")
    merge.samples = [{"id": str(uuid.uuid4()), "timestamp": NOW.isoformat()}]
    db.flush()
    (left,) = suggestions.list_suggestions(db=db)["items"]
    assert left["samples"] == []
    merge.samples = [{"id": "not-an-identifier"}, {}]
    db.flush()
    (left,) = suggestions.list_suggestions(db=db)["items"]
    assert left["samples"] == []

    with pytest.raises(
        DomainError, match="^Status must be open, dismissed or applied$"
    ) as refused:
        suggestions.list_suggestions(status="maybe", db=db)
    assert refused.value.status_code == 400


def test_dismissing_and_restoring_record_who_decided(db, admin):
    events(db, 6)
    refresh(db)
    (row,) = open_rows(db)

    def status(value, **extra):
        return suggestions.set_suggestion_status(
            suggestion_id=str(row.id),
            payload=SuggestionStatusInput(status=value, **extra),
            user=admin,
            db=db,
        )

    dismissed = status("dismissed")
    assert dismissed["status"] == "dismissed"
    assert dismissed["decided_by"] == "suggester"
    assert dismissed["decided_at"]
    (listed,) = suggestions.list_suggestions(status="dismissed", db=db)["items"]
    assert listed["id"] == str(row.id)
    assert suggestions.list_suggestions(db=db)["items"] == []

    restored = status("open")
    assert (restored["status"], restored["decided_by"]) == ("open", None)

    with pytest.raises(
        DomainError, match="^Save the rule before marking the suggestion as created$"
    ) as refused:
        status("applied", rule_id="missing")
    assert refused.value.status_code == 409
    save_rules(db, rule("edited", source="github"))
    assert status("applied", rule_id="edited")["rule_id"] == "edited"
    for value in ("open", "dismissed", "applied"):
        with pytest.raises(DomainError) as refused:
            status(value, rule_id="edited")
        assert refused.value.status_code == 409
    for missing in ("nope", str(uuid.uuid4())):
        with pytest.raises(DomainError, match="^Suggestion not found$") as refused:
            suggestions.set_suggestion_status(
                suggestion_id=missing,
                payload=SuggestionStatusInput(status="dismissed"),
                user=admin,
                db=db,
            )
        assert refused.value.status_code == 404


def test_creating_a_suggestion_saves_the_rule_and_maps_past_events(
    db, admin, monkeypatch
):
    queued = []
    monkeypatch.setattr(tasks.backfill_rule_task, "delay", queued.append)
    events(db, 6)
    refresh(db)
    (row,) = open_rows(db)
    # A rule saved meanwhile under the suggested ID keeps it.
    save_rules(db, rule("suggested_github_pull_request_event", source="other"))
    version = db.get(ManagedConfiguration, "rules").version

    with pytest.raises(
        DomainError,
        match=r"^Configuration changed in another session\. Reload before saving\.$",
    ) as stale:
        suggestions.apply_suggestion(
            suggestion_id=str(row.id),
            payload=SuggestionApplyInput(version=version - 1),
            user=admin,
            db=db,
        )
    assert stale.value.status_code == 409
    assert db.get(RuleSuggestion, row.id).status == "open"

    result = suggestions.apply_suggestion(
        suggestion_id=str(row.id),
        payload=SuggestionApplyInput(version=version),
        user=admin,
        db=db,
    )

    assert result["rule"]["id"] == "suggested_github_pull_request_event_2"
    assert result["version"] == version + 1
    assert result["backfill"]["status"] == "queued"
    assert queued == [result["backfill"]["id"]]
    job = db.get(RuleBackfillJob, uuid.UUID(result["backfill"]["id"]))
    assert job.rule_id == "suggested_github_pull_request_event_2"
    saved = db.get(ManagedConfiguration, "rules").document["rules"]
    assert [r["id"] for r in saved] == [
        "suggested_github_pull_request_event",
        "suggested_github_pull_request_event_2",
    ]
    assert saved[1]["when"] == {"source": "github", "action": "PullRequestEvent"}
    assert result["suggestion"]["status"] == "applied"
    assert result["suggestion"]["rule_id"] == "suggested_github_pull_request_event_2"
    assert result["suggestion"]["decided_by"] == "suggester"

    with pytest.raises(
        DomainError, match="^This suggestion has already been decided$"
    ) as decided:
        suggestions.apply_suggestion(
            suggestion_id=str(row.id),
            payload=SuggestionApplyInput(version=version + 1),
            user=admin,
            db=db,
        )
    assert decided.value.status_code == 409
    # Created, it is not suggested again.
    refresh(db)
    assert open_rows(db) == []


def test_creating_a_suggestion_can_choose_among_its_candidates(db, admin, monkeypatch):
    monkeypatch.setattr(tasks.backfill_rule_task, "delay", lambda job: None)
    events(db, 6)
    refresh(db)
    (row,) = open_rows(db)
    row.controls = [
        *row.controls,
        {"ref": "LG-1", "title": "Logging", "kind": "control", "selected": False},
    ]
    db.commit()
    candidates = [c["ref"] for c in row.controls]
    assert candidates == ["CM-1", "LG-1"]

    for too_long, error in (
        (
            ["CM-1"] * (suggestions.MAX_CANDIDATES + 1),
            (
                "too_long",
                ("refs",),
                f"List should have at most {suggestions.MAX_CANDIDATES} items "
                f"after validation, not {suggestions.MAX_CANDIDATES + 1}",
            ),
        ),
        (
            ["x" * 65],
            (
                "string_too_long",
                ("refs", 0),
                "String should have at most 64 characters",
            ),
        ),
    ):
        with pytest.raises(ValidationError) as refused:
            SuggestionApplyInput(version=0, refs=too_long)
        assert [(e["type"], e["loc"], e["msg"]) for e in refused.value.errors()] == [
            error
        ]
    for refs in ([], ["ZZ-9"], [candidates[0], "ZZ-9"]):
        with pytest.raises(
            DomainError, match="^Choose at least one of the suggested controls$"
        ) as refused:
            suggestions.apply_suggestion(
                suggestion_id=str(row.id),
                payload=SuggestionApplyInput(version=0, refs=refs),
                user=admin,
                db=db,
            )
        assert refused.value.status_code == 400
    result = suggestions.apply_suggestion(
        suggestion_id=str(row.id),
        payload=SuggestionApplyInput(version=0, refs=list(reversed(candidates[:2]))),
        user=admin,
        db=db,
    )
    # Targets keep the suggestion's order.
    assert [t["ref"] for t in result["rule"]["map_to"]] == candidates[:2]


def test_a_suggestion_whose_control_was_removed_is_refused_and_stays_open(db, admin):
    events(db, 6)
    refresh(db)
    (row,) = open_rows(db)
    db.execute(
        ControlItem.__table__.delete().where(
            ControlItem.framework_slug == FW, ControlItem.ref == "CM-1"
        )
    )
    db.flush()

    with pytest.raises(
        DomainError, match="^Unknown control: fw-suggestions / CM-1$"
    ) as refused:
        suggestions.apply_suggestion(
            suggestion_id=str(row.id),
            payload=SuggestionApplyInput(version=0),
            user=admin,
            db=db,
        )

    assert refused.value.status_code == 400
    assert db.get(RuleSuggestion, row.id).status == "open"


def test_checking_now_queues_one_run_at_a_time(db, monkeypatch):
    class Store:
        def __init__(self):
            self.keys = set()

        def set(self, key, value, nx, ex):
            if key in self.keys:
                return None
            self.keys.add(key)
            return True

        def delete(self, key):
            self.keys.discard(key)

    store = Store()
    monkeypatch.setattr(suggestions, "get_valkey", lambda: store)
    queued = []
    monkeypatch.setattr(
        tasks.rule_suggestions_task, "apply_async", lambda **kw: queued.append(kw)
    )
    assert suggestions.request_refresh(db=db) == {"status": "queued"}
    assert suggestions.request_refresh(db=db) == {"status": "queued"}
    assert queued == [{"expires": 300}]

    refresh(db)
    recent = suggestions.request_refresh(db=db)
    assert recent["status"] == "recent"
    assert recent["finished_at"]

    monkeypatch.setattr(
        suggestions,
        "utc_now_naive",
        lambda: datetime.now() + timedelta(minutes=5),
    )
    store.keys.clear()

    def unreachable(**kw):
        raise ConnectionError("broker down")

    monkeypatch.setattr(tasks.rule_suggestions_task, "apply_async", unreachable)
    with pytest.raises(
        DomainError,
        match="^The background worker could not be reached; try again shortly$",
    ) as refused:
        suggestions.request_refresh(db=db)
    assert refused.value.status_code == 503
    # The request is forgotten, so the next one is queued.
    assert store.keys == set()

    def lost(key):
        raise ConnectionError("cache down")

    store.delete = lost
    with pytest.raises(
        DomainError,
        match="^The background worker could not be reached; try again shortly$",
    ) as refused:
        suggestions.request_refresh(db=db)
    assert refused.value.status_code == 503
    store.keys.clear()

    # Without the cache, a request is still queued.
    def broken():
        raise ConnectionError("cache down")

    monkeypatch.setattr(suggestions, "get_valkey", broken)
    monkeypatch.setattr(
        tasks.rule_suggestions_task, "apply_async", lambda **kw: queued.append(kw)
    )
    assert suggestions.request_refresh(db=db) == {"status": "queued"}


def test_the_worker_runs_a_refresh_on_its_schedule(monkeypatch):
    schedule = celery_app.conf.beat_schedule["rule-suggestions-every-6h"]
    assert schedule["task"] == tasks.rule_suggestions_task.name
    assert (
        celery_app.amqp.router.route({}, tasks.rule_suggestions_task.name)["queue"].name
        == "celery"
    )
    seen = []

    class Session:
        def __enter__(self):
            return "session"

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(tasks, "SessionLocal", Session)
    monkeypatch.setattr(
        tasks, "refresh_suggestions", lambda db: seen.append(db) or {"status": "ok"}
    )
    assert tasks.rule_suggestions_task.run() == {"status": "ok"}
    assert seen == ["session"]


# --- Administrator routes ------------------------------------------------------


def test_suggestion_routes_are_for_administrators(keen, monkeypatch):  # noqa: F811
    db = keen.db
    _catalogue(db)
    db.commit()
    events(db, 6)
    db.commit()
    suggestions.refresh_suggestions(db, now=datetime.now())
    db.commit()
    queued = []
    monkeypatch.setattr(tasks.backfill_rule_task, "delay", queued.append)
    monkeypatch.setattr(
        tasks.rule_suggestions_task, "apply_async", lambda **kw: queued.append(kw)
    )
    admin = keen.client(keen.user(role="admin"))

    summary = admin.get("/v1/admin/rule-suggestions/summary")
    assert summary.status_code == 200, summary.text
    assert summary.json()["open_count"] == 1
    listed = admin.get("/v1/admin/rule-suggestions").json()
    (item,) = listed["items"]
    version = admin.get("/v1/admin/mapping-rules").json()["version"]

    # Unsafe methods need the CSRF header, like every administrator write.
    refused = admin.post(
        f"/v1/admin/rule-suggestions/{item['id']}/status",
        json={"status": "dismissed"},
    )
    assert (refused.status_code, refused.json()) == (
        403,
        {"detail": "CSRF token missing or invalid"},
    )
    response = admin.post(
        f"/v1/admin/rule-suggestions/{item['id']}/status",
        json={"status": "dismissed"},
        headers=unsafe(admin),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "dismissed"
    response = admin.post(
        f"/v1/admin/rule-suggestions/{item['id']}/status",
        json={"status": "open"},
        headers=unsafe(admin),
    )
    assert response.json()["status"] == "open"
    response = admin.post(
        f"/v1/admin/rule-suggestions/{item['id']}/apply",
        json={"version": version},
        headers=unsafe(admin),
    )
    assert response.status_code == 200, response.text
    assert response.json()["rule"]["id"] == "suggested_github_pull_request_event"
    response = admin.post(
        "/v1/admin/rule-suggestions/refresh", json={}, headers=unsafe(admin)
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "recent"
    home = admin.get("/v1/home", params={"framework": FW})
    assert home.status_code == 200, home.text
    assert home.json()["suggested_rules"] == 0

    reader = keen.client(keen.user("events.read", "isms.manage"))
    for method, path in (
        ("get", "/v1/admin/rule-suggestions"),
        ("get", "/v1/admin/rule-suggestions/summary"),
        ("post", "/v1/admin/rule-suggestions/refresh"),
        ("post", f"/v1/admin/rule-suggestions/{item['id']}/apply"),
        ("post", f"/v1/admin/rule-suggestions/{item['id']}/status"),
    ):
        kwargs = {"json": {}, "headers": unsafe(reader)} if method == "post" else {}
        refused = getattr(reader, method)(path, **kwargs)
        assert (refused.status_code, refused.json()) == (
            403,
            {"detail": "Admin role required"},
        )
    refused = keen.client().get("/v1/admin/rule-suggestions")
    assert (refused.status_code, refused.json()) == (
        401,
        {"detail": "Not authenticated"},
    )


def test_the_home_page_points_administrators_at_suggestions(keen):  # noqa: F811
    db = keen.db
    _catalogue(db)
    db.commit()
    events(db, 6)
    db.commit()
    suggestions.refresh_suggestions(db, now=datetime.now())
    db.commit()
    admin = keen.client(keen.user(role="admin"))

    body = admin.get("/v1/home", params={"framework": FW}).json()

    assert body["suggested_rules"] == 1
    assert body["attention"][0] == {
        "kind": "rule_suggestion",
        "label": "1 suggested mapping rule",
        "detail": f"Proposed from unmapped events for Framework {FW} · review before mapping",
        "href": "/admin.html#rule-suggestions",
    }
    reader = keen.client(keen.user("events.read"))
    assert (
        "suggested_rules" not in reader.get("/v1/home", params={"framework": FW}).json()
    )
