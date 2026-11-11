"""Purge gates and transaction lock order; run SQL cases against PostgreSQL too."""

from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
from app.db.session import _coordinate_evidence_purge
from app.services.errors import DomainError
from app.services.ingestion_pause import (
    is_paused,
    purge_pause_status,
    require_purge_receiving,
)


@pytest.mark.parametrize(
    "active,offset,paused",
    [(True, None, True), (False, 299, True), (False, -1, False), (False, None, False)],
)
def test_active_job_and_cooldown_gate(active, offset, paused):
    now = datetime(2026, 10, 8)
    db = Mock()
    db.execute.return_value.mappings.return_value.one.return_value = {
        "active": active,
        "resume_at": now + timedelta(seconds=offset) if offset is not None else None,
        "current_time": now,
    }
    assert purge_pause_status(db)["paused"] is paused
    if paused:
        assert is_paused(db, "rss")
        db.scalar.assert_not_called()
        with pytest.raises(
            DomainError,
            match=r"^Ingestion is paused for evidence purge and its five-minute cooldown; retry later\.$",
        ) as exc:
            require_purge_receiving(db)
        assert exc.value.status_code == 503 and exc.value.headers["Retry-After"] == "60"
    else:
        require_purge_receiving(db)


def test_reader_shared_and_purge_exclusive_lock_before_any_application_sql():
    connection = Mock()
    session = Mock()
    session.info = {}
    transaction = Mock()
    transaction.nested = False
    _coordinate_evidence_purge(session, transaction, connection)
    assert "pg_advisory_xact_lock_shared" in str(connection.execute.call_args.args[0])
    connection.reset_mock()
    session.info = {"evidence_purge_batch": True}
    _coordinate_evidence_purge(session, transaction, connection)
    calls = connection.execute.call_args_list
    assert "lock_timeout" in str(calls[0].args[0])
    assert "pg_advisory_xact_lock(" in str(calls[1].args[0])
    connection.reset_mock()
    transaction.nested = True
    _coordinate_evidence_purge(session, transaction, connection)
    connection.execute.assert_not_called()


def test_only_a_purge_batch_stores_evidence_without_the_purge_check(
    postgres_engine, monkeypatch
):
    from app.db.models import Event
    from app.services import ingestion_pause
    from sqlalchemy.orm import Session

    checked = []
    monkeypatch.setattr(ingestion_pause, "require_purge_receiving", checked.append)

    def store(db, name):
        db.add(
            Event(
                source="test",
                external_id=name,
                timestamp=datetime(2026, 1, 1),
                summary=name,
            )
        )
        db.flush()

    # The purge deletes evidence under the lock that pauses every other
    # writer, so its own session is the one that is never asked to wait.
    with Session(postgres_engine, info={"evidence_purge_batch": True}) as purge:
        store(purge, "during-purge")
        assert checked == []
        purge.commit()
    with Session(postgres_engine) as writer:
        store(writer, "ordinary")
        assert checked == [writer]
