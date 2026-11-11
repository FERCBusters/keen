"""Reapplying a rule reconciles its own mappings without losing other evidence."""

import uuid
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from unittest.mock import Mock

import pytest
from app.db.models import (
    ControlItem,
    Event,
    ManagedConfiguration,
    Mapping,
    RuleBackfillJob,
)
from app.mapping.collector import collector_id
from app.worker import rule_backfill
from sqlalchemy import event as sql_event
from sqlalchemy import insert
from sqlalchemy.orm import Session


def rule(refs=("keep",), **when):
    return {
        "id": "edited",
        "when": {"source": "test", **when},
        "map_to": [{"framework": "TEST", "ref": ref} for ref in refs],
        "confidence": 0.8,
    }


@pytest.fixture
def setup(contract_db, monkeypatch):
    db = contract_db

    @contextmanager
    def session():
        yield db

    monkeypatch.setattr(rule_backfill, "SessionLocal", session)
    clear = Mock()
    monkeypatch.setattr(rule_backfill, "clear_stats_caches", clear)
    controls = {
        ref: ControlItem(framework_slug="TEST", type="custom", ref=ref, title=ref)
        for ref in ["keep", "removed", "manual", "imported", "other", "unknown"]
    }
    event = Event(
        source="test",
        external_id="one",
        timestamp=datetime(2026, 1, 1),
        created_at=datetime(2026, 1, 1),
        summary="original",
        action="old",
    )
    db.add_all([event, *controls.values()])
    db.flush()

    def mapping(ref, method="rule", owner="edited", mapped_by="system"):
        row = Mapping(
            event_id=event.id,
            control_item_id=controls[ref].id,
            method=method,
            mapped_by=mapped_by,
            confidence=0.5,
            rationale=f"auto by rule {owner} (TEST)",
        )
        db.add(row)
        return row

    def job(document, others=()):
        db.add(
            ManagedConfiguration(
                name="rules", version=1, document={"rules": [document, *others]}
            )
        )
        row = RuleBackfillJob(
            rule_id="edited",
            source=document["when"]["source"],
            rule_document=deepcopy(document),
            rules_version=1,
            cutoff=datetime(2026, 2, 1),
        )
        db.add(row)
        db.commit()
        return row

    return db, controls, event, mapping, job, clear


def test_removed_target_is_deleted_and_manual_import_other_provenance_survive(setup):
    db, controls, event, mapping, job, clear = setup
    mapping("removed")
    mapping("manual", method="manual")
    mapping("imported", method="import")
    mapping("other", owner="unrelated")
    mapping("unknown", mapped_by=None)
    task = job(rule())
    assert rule_backfill.process_batch(task.id) is False
    rows = {m.control_item_id: m for m in db.query(Mapping).all()}
    assert set(rows) == {
        controls[r].id for r in ["keep", "manual", "imported", "other", "unknown"]
    }
    assert task.created_mappings == 1 and task.examined == 1
    clear.assert_called_once()
    assert rule_backfill.process_batch(task.id) is False
    assert db.query(Mapping).count() == 5


def test_other_active_rule_retains_shared_target_and_takes_provenance(setup):
    db, controls, event, mapping, job, clear = setup
    old = mapping("removed")
    other = rule(("removed",))
    other["id"] = "supporting"
    other["confidence"] = 0.9
    task = job(rule(), [other])
    rule_backfill.process_batch(task.id)
    db.refresh(old)
    assert old.rationale == "auto by rule supporting (TEST)" and old.confidence == 0.9
    assert db.query(Mapping).count() == 2


@pytest.mark.parametrize("when", [{"action": "new"}, {"source": "new-source"}])
def test_previous_match_is_removed_when_conditions_or_source_change(setup, when):
    db, controls, event, mapping, job, clear = setup
    mapping("removed")
    task = job(rule(**when))
    rule_backfill.process_batch(task.id)
    assert db.query(Mapping).count() == 0
    assert task.examined == 1 and task.matched == 0 and task.created_mappings == 0
    clear.assert_called_once()  # Deletion-only batches invalidate caches too.


def test_disabled_supporting_rule_does_not_preserve_removed_target(setup):
    db, controls, event, mapping, job, clear = setup
    mapping("removed")
    other = rule(("removed",))
    other.update(id="disabled", enabled=False)
    task = job(rule(), [other])
    rule_backfill.process_batch(task.id)
    assert {m.control_item_id for m in db.query(Mapping)} == {controls["keep"].id}


def test_superseded_job_does_not_modify_mappings(setup):
    db, controls, event, mapping, job, clear = setup
    old = mapping("removed")
    task = job(rule())
    config = db.get(ManagedConfiguration, "rules")
    config.version = 2
    config.document = {"rules": [rule(("other",))]}
    db.commit()
    assert rule_backfill.process_batch(task.id) is False
    assert task.status == "superseded" and db.get(Mapping, old.id) is not None
    clear.assert_not_called()


def test_batched_cleanup_resumes_without_skipping_previously_owned_events(
    setup, monkeypatch
):
    db, controls, event, mapping, job, clear = setup
    mapping("removed")
    second = Event(
        source="test",
        external_id="two",
        timestamp=datetime(2026, 1, 2),
        created_at=datetime(2026, 1, 2),
        summary="second",
    )
    db.add(second)
    db.flush()
    db.add(
        Mapping(
            event_id=second.id,
            control_item_id=controls["removed"].id,
            method="rule",
            mapped_by="system",
            rationale="auto by rule edited (TEST)",
            confidence=0.5,
        )
    )
    task = job(rule(source="new-source"))
    monkeypatch.setattr(rule_backfill, "BATCH_SIZE", 1)
    assert rule_backfill.process_batch(task.id) is True
    assert rule_backfill.process_batch(task.id) is True
    assert rule_backfill.process_batch(task.id) is False
    assert task.examined == 2 and db.query(Mapping).count() == 0


def test_queue_accepts_cleanup_when_new_source_has_no_events(setup, monkeypatch):
    from app.services.managed_configurations import _queue_rule_backfill
    from app.worker.tasks import backfill_rule_task

    db, controls, event, mapping, job, clear = setup
    mapping("removed")
    db.commit()
    enqueue = Mock()
    monkeypatch.setattr(backfill_rule_task, "delay", enqueue)
    result = _queue_rule_backfill(db, rule(source="new-source"), 1)
    assert result["status"] == "queued"
    enqueue.assert_called_once_with(result["id"])


def test_unchanged_rule_adopts_the_new_rules_version(setup):
    # Editing another rule bumps the configuration version; a job for a rule
    # whose document is unchanged continues under the new version.
    db, controls, event, mapping, job, clear = setup
    task = job(rule())
    config = db.get(ManagedConfiguration, "rules")
    other = rule(("other",))
    other["id"] = "unrelated"
    config.version = 2
    config.document = {"rules": [rule(), other]}
    db.commit()
    assert rule_backfill.process_batch(task.id) is False
    assert task.status == "completed" and task.rules_version == 2
    assert {m.control_item_id for m in db.query(Mapping)} == {controls["keep"].id}


def test_connection_scope_limits_the_examined_events(setup):
    # A rule bound to one connection ignores evidence from other connections.
    db, controls, event, mapping, job, clear = setup
    event.connection_id = "primary"
    other = Event(
        source="test",
        external_id="elsewhere",
        connection_id="secondary",
        timestamp=datetime(2026, 1, 3),
        created_at=datetime(2026, 1, 3),
        summary="elsewhere",
    )
    db.add(other)
    task = job(rule(connection_id="primary"))
    assert rule_backfill.process_batch(task.id) is False
    assert task.examined == 1 and task.total_estimate == 1
    assert [m.event_id for m in db.query(Mapping)] == [event.id]


def test_a_job_whose_rule_cannot_be_parsed_fails_loudly(setup):
    # A saved document that parses to no rule is an error, never a silent
    # completion that would leave the job looking finished.
    db, controls, event, mapping, job, clear = setup
    disabled = rule()
    disabled["enabled"] = False
    task = job(disabled)
    with pytest.raises(ValueError, match="cannot be parsed"):
        rule_backfill.process_batch(task.id)
    assert db.query(Mapping).count() == 0


def test_existing_mapping_to_the_same_control_is_left_alone(setup):
    # A manual mapping already covering the rule's target is kept as-is and
    # the batch creates nothing for that pair.
    db, controls, event, mapping, job, clear = setup
    manual = mapping("keep", method="manual", mapped_by="alice")
    task = job(rule())
    assert rule_backfill.process_batch(task.id) is False
    rows = db.query(Mapping).all()
    assert [m.id for m in rows] == [manual.id]
    assert rows[0].method == "manual"
    assert task.matched == 1 and task.created_mappings == 0
    clear.assert_not_called()


def test_cache_invalidation_failure_is_logged_without_failing_the_batch(
    setup, monkeypatch, caplog
):
    # The committed mappings stand even when the stats cache cannot be cleared.
    import logging

    db, controls, event, mapping, job, clear = setup
    clear.side_effect = ConnectionError("valkey down")
    task = job(rule())
    with caplog.at_level(logging.WARNING, logger="app.worker.rule_backfill"):
        assert rule_backfill.process_batch(task.id) is False
    assert task.status == "completed" and task.created_mappings == 1
    assert db.query(Mapping).count() == 1
    assert any("stats cache" in record.message for record in caplog.records)


def test_recover_jobs_returns_queued_and_stale_running_jobs_only(setup):
    # Recovery requeues queued jobs and running jobs that stopped updating; a
    # job still making progress and finished jobs are left alone.
    from datetime import timedelta

    from app.core.datetime_utils import utc_now_naive

    db, controls, event, mapping, job, clear = setup
    now = utc_now_naive()

    def add(status, updated_at, created_at):
        row = RuleBackfillJob(
            rule_id="edited",
            source="test",
            rule_document=rule(),
            rules_version=1,
            cutoff=datetime(2026, 2, 1),
            status=status,
            created_at=created_at,
            updated_at=updated_at,
        )
        db.add(row)
        return row

    queued = add("queued", now, now - timedelta(minutes=1))
    stale = add("running", now - timedelta(minutes=10), now - timedelta(minutes=9))
    add("running", now, now - timedelta(minutes=8))
    add("completed", now - timedelta(hours=1), now - timedelta(hours=2))
    add("failed", now - timedelta(hours=1), now - timedelta(hours=2))
    db.commit()
    assert rule_backfill.recover_jobs() == [stale.id, queued.id]


@pytest.mark.parametrize("order", [("strong", "weak"), ("weak", "strong")])
def test_the_most_confident_supporting_rule_keeps_a_shared_target(setup, order):
    # A control target and a plain reference name the same control; whichever
    # rule is listed first, the more confident one keeps the mapping.
    db, controls, event, mapping, job, clear = setup
    old = mapping("removed")
    strong = rule(("removed",))
    strong.update(id="strong", confidence=0.9)
    strong["map_to"][0]["kind"] = "control"
    weak = rule(("removed",))
    weak.update(id="weak", confidence=0.6)
    others = {"strong": strong, "weak": weak}
    task = job(rule(), [others[name] for name in order])

    rule_backfill.process_batch(task.id)

    db.refresh(old)
    assert (old.confidence, old.rationale) == (0.9, "auto by rule strong (TEST)")
    assert db.query(Mapping).count() == 2


def test_an_owned_mapping_its_rule_still_supports_as_it_is_is_left_alone(setup):
    db, controls, event, mapping, job, clear = setup
    kept = mapping("keep")
    kept.confidence = 0.8  # the rule's own confidence
    task = job(rule())

    assert rule_backfill.process_batch(task.id) is False

    db.refresh(kept)
    assert (kept.confidence, kept.rationale) == (0.8, "auto by rule edited (TEST)")
    assert [m.id for m in db.query(Mapping)] == [kept.id]
    assert task.matched == 1 and task.created_mappings == 0
    clear.assert_not_called()


def test_a_mapping_another_writer_adds_during_the_batch_is_kept_uncounted(setup):
    db, controls, event, mapping, job, clear = setup
    task = job(rule())
    raced = []

    def concurrent_writer(state):
        # Another worker maps the event after the batch read its mappings.
        if raced or not state.is_insert or state.statement.table.name != "mappings":
            return
        raced.append(True)
        state.session.connection().execute(
            insert(Mapping.__table__).values(
                id=uuid.uuid4(),
                event_id=event.id,
                control_item_id=controls["keep"].id,
                method="manual",
                mapped_by="auditor",
                confidence=1.0,
                rationale="added meanwhile",
            )
        )

    sql_event.listen(db, "do_orm_execute", concurrent_writer)
    try:
        assert rule_backfill.process_batch(task.id) is False
    finally:
        sql_event.remove(db, "do_orm_execute", concurrent_writer)

    assert raced == [True]
    (row,) = db.query(Mapping).all()
    assert (row.control_item_id, row.method) == (controls["keep"].id, "manual")
    assert task.matched == 1 and task.created_mappings == 0
    clear.assert_not_called()


def test_a_rule_for_one_collection_item_examines_only_that_items_events(setup):
    db, controls, event, mapping, job, clear = setup
    for name in ("deploy", "nightly"):
        db.add(
            Event(
                source="jenkins",
                external_id=name,
                timestamp=datetime(2026, 1, 5),
                created_at=datetime(2026, 1, 5),
                summary=f"{name} build",
                raw_pointer={"jenkins": {"job": name}},
            )
        )
    identity = collector_id("jenkins", "jobs", "deploy")
    task = job(rule(source="jenkins", collector=identity))

    assert rule_backfill.process_batch(task.id) is False

    assert task.examined == 1 and task.total_estimate == 1
    assert [db.get(Event, m.event_id).external_id for m in db.query(Mapping)] == [
        "deploy"
    ]


def test_a_batch_takes_the_shared_rule_lock_before_reading_its_job(
    postgres_engine, monkeypatch
):
    engine = postgres_engine
    statements = []
    sql_event.listen(
        engine,
        "before_cursor_execute",
        lambda conn, cursor, statement, *args: statements.append(statement),
    )
    try:
        with Session(engine) as db:
            control = ControlItem(
                framework_slug="TEST", type="custom", ref="keep", title="keep"
            )
            event = Event(
                source="test",
                external_id="one",
                timestamp=datetime(2026, 1, 1),
                created_at=datetime(2026, 1, 1),
                summary="original",
            )
            document = rule()
            db.add_all(
                [
                    control,
                    event,
                    ManagedConfiguration(
                        name="rules", version=1, document={"rules": [document]}
                    ),
                ]
            )
            task = RuleBackfillJob(
                rule_id="edited",
                source="test",
                rule_document=deepcopy(document),
                rules_version=1,
                cutoff=datetime(2026, 2, 1),
            )
            db.add(task)
            db.commit()
            task_id, control_id = task.id, control.id

        monkeypatch.setattr(rule_backfill, "SessionLocal", lambda: Session(engine))
        monkeypatch.setattr(rule_backfill, "clear_stats_caches", Mock())

        assert rule_backfill.process_batch(task_id) is False

        with Session(engine) as db:
            assert db.get(RuleBackfillJob, task_id).status == "completed"
            assert [m.control_item_id for m in db.query(Mapping)] == [control_id]
        # Connection removal takes the same lock exclusively, so a batch waits
        # for it before reading the job it would otherwise map from.
        lock = "SELECT pg_advisory_xact_lock_shared(1262830926)"
        batch = statements[statements.index(lock) :]
        assert batch.count(lock) == 1
        assert "FROM rule_backfill_jobs" in batch[1]
    finally:
        engine.dispose()
