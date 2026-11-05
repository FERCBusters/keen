"""Evidence retention batches at their edges, on PostgreSQL.

A purge cancelled after retention was checked deletes nothing; an event that
gains an audit hold after a batch selected it is kept; signed UEU batch
manifests go once no retained evidence names them; and storage clean-up
reports more work after a full batch, then drains the rest.
"""

import uuid
from datetime import timedelta

from app.core.datetime_utils import utc_now_naive
from app.db.models import Audit, Event, UeuBatch, UeuEvidence
from app.services import evidence_retention as retention
from sqlalchemy import event, text

# Imported fixture: an isolated PostgreSQL schema with retention's tables,
# and retention's sessions pointed at it.
from tests.test_purge_coordination_postgres import database as database


def _events(factory, count, **columns):
    with factory() as db:
        rows = [
            Event(
                source="retention-edge",
                external_id=uuid.uuid4().hex,
                timestamp=utc_now_naive() - timedelta(days=90),
                summary="Old evidence",
                **columns,
            )
            for _ in range(count)
        ]
        db.add_all(rows)
        db.commit()
        return [row.id for row in rows]


def _purge_all(factory):
    with factory() as db:
        retention.enqueue(db, "all", None, False, "test")
        db.commit()


def _left(factory):
    with factory() as db:
        return set(db.execute(text("SELECT id FROM events")).scalars())


def _job(factory):
    with factory() as db:
        return dict(
            db.execute(text("SELECT status, deleted FROM evidence_purge_jobs"))
            .mappings()
            .one()
        )


def test_a_purge_cancelled_before_its_batch_runs_deletes_nothing(database, monkeypatch):
    _, factory = database
    ids = _events(factory, 2)
    _purge_all(factory)
    opened = []

    def sessions():
        opened.append(True)
        if len(opened) == 2:
            # An administrator cancels between retention's check and its batch.
            with factory() as other:
                other.execute(text("UPDATE evidence_purge_jobs SET status='cancelled'"))
                other.commit()
        return factory()

    monkeypatch.setattr(retention, "SessionLocal", sessions)
    assert retention.database_batch() is False
    assert len(opened) == 2
    assert _left(factory) == set(ids)
    assert _job(factory) == {"status": "cancelled", "deleted": 0}


def test_an_event_held_after_a_batch_selected_it_is_kept(database):
    engine, factory = database
    held, removed = _events(factory, 2)
    with factory() as db:
        audit = Audit(framework_slug="TEST", title="Sampling audit")
        db.add(audit)
        db.commit()
        audit_id = audit.id
    _purge_all(factory)

    def sampled_meanwhile(conn, cursor, statement, parameters, context, many):
        # The audit's sample commits after the batch selected and locked its
        # candidates, before the batch checks this event again.
        if statement.startswith("SELECT 1 FROM events e WHERE e.id=") and str(
            parameters["id"]
        ) == str(held):
            cursor.execute(
                "INSERT INTO audit_event_retention_holds (audit_id, event_id) "
                "VALUES (%(audit)s, %(event)s)",
                {"audit": str(audit_id), "event": str(held)},
            )

    event.listen(engine, "before_cursor_execute", sampled_meanwhile)
    try:
        assert retention.database_batch() is True
    finally:
        event.remove(engine, "before_cursor_execute", sampled_meanwhile)
    assert _left(factory) == {held}
    assert _job(factory) == {"status": "running", "deleted": 1}
    assert removed not in _left(factory)


def test_batch_manifests_go_once_no_retained_evidence_names_them(database):
    _, factory = database
    shared_kept, shared_removed, alone = _events(factory, 3)
    with factory() as db:
        audit = Audit(framework_slug="TEST", title="Holding audit")
        shared = UeuBatch(verification="verified", record_count=2)
        single = UeuBatch(verification="verified", record_count=1)
        db.add_all([audit, shared, single])
        db.flush()
        for event_id, batch in (
            (shared_kept, shared),
            (shared_removed, shared),
            (alone, single),
        ):
            db.add(
                UeuEvidence(
                    event_id=event_id,
                    evidence_id=str(uuid.uuid4()),
                    batch_id=batch.id,
                    event_type="endpoint.firewall.observed",
                    evidence_hash="sha256:" + "0" * 64,
                    verification="verified",
                    chain_status="linked",
                    schema_validation="valid",
                    catalogue="keen@2026.10",
                    custody={},
                )
            )
        db.execute(
            text(
                "INSERT INTO audit_event_retention_holds (audit_id, event_id) "
                "VALUES (:audit, :event)"
            ),
            {"audit": audit.id, "event": shared_kept},
        )
        db.commit()
        shared_id, single_id = shared.id, single.id
    _purge_all(factory)

    assert retention.database_batch() is True
    assert _left(factory) == {shared_kept}
    with factory() as db:
        assert set(db.execute(text("SELECT id FROM ueu_batches")).scalars()) == {
            shared_id
        }
        assert db.get(UeuBatch, single_id) is None
        evidence = db.execute(text("SELECT event_id, batch_id FROM ueu_evidence"))
        assert [tuple(row) for row in evidence] == [(shared_kept, shared_id)]


def test_storage_clean_up_reports_more_work_after_a_full_batch(database, monkeypatch):
    _, factory = database
    count = retention.BATCH_SIZE + 1
    with factory() as db:
        db.execute(
            text(
                "INSERT INTO evidence_object_cleanup (storage_uri, next_attempt_at) "
                "SELECT 'local://local/retention-edge/' || n, :due "
                "FROM generate_series(1, :count) AS n"
            ),
            {"due": utc_now_naive() - timedelta(minutes=1), "count": count},
        )
        db.commit()
    deleted = []
    monkeypatch.setattr(retention, "delete_stored_object", deleted.append)

    assert retention.storage_batch() is True
    assert len(deleted) == retention.BATCH_SIZE == len(set(deleted))
    with factory() as db:
        assert (
            db.execute(text("SELECT count(*) FROM evidence_object_cleanup")).scalar()
            == 1
        )

    assert retention.storage_batch() is False
    assert len(deleted) == count
    assert set(deleted) == {
        f"local://local/retention-edge/{n}" for n in range(1, count + 1)
    }
    with factory() as db:
        assert (
            db.execute(text("SELECT count(*) FROM evidence_object_cleanup")).scalar()
            == 0
        )
