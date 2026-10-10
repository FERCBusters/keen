"""Reapplying a rule reconciles its own mappings without losing other evidence."""

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
from app.worker import rule_backfill


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
    from app.api.routes.managed_configurations import _queue_rule_backfill
    from app.worker.tasks import backfill_rule_task

    db, controls, event, mapping, job, clear = setup
    mapping("removed")
    db.commit()
    enqueue = Mock()
    monkeypatch.setattr(backfill_rule_task, "delay", enqueue)
    result = _queue_rule_backfill(db, rule(source="new-source"), 1)
    assert result["status"] == "queued"
    enqueue.assert_called_once_with(result["id"])
