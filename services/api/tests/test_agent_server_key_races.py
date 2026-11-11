"""Two requests making KEEN's agent keys at once, on separate PostgreSQL
connections.

A request that loses the race to add the identity key or a new encryption key
returns the key the other request committed, and only that key is stored.
"""

import pytest
from app.agents import server_keys
from app.core.datetime_utils import utc_now_naive
from app.db.models import AgentServerKey
from cryptography.fernet import Fernet
from sqlalchemy import event, text
from sqlalchemy.orm import Session


@pytest.fixture
def engine(contract_postgres_engine, monkeypatch):
    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", Fernet.generate_key().decode())
    yield contract_postgres_engine
    with Session(contract_postgres_engine) as db:
        db.query(AgentServerKey).delete()
        db.commit()


def committed_before_write(first: Session, second: Session) -> None:
    """Commit first just before second writes its key, after second has read
    that no active key exists."""

    @event.listens_for(second, "before_flush", once=True)
    def commit_first(*_):
        first.commit()

    # A missed commit would leave second waiting on first's key.
    second.execute(text("SET LOCAL lock_timeout = '5s'"))


def stored(engine, purpose):
    with Session(engine) as db:
        return sorted(
            (row.key_id, row.state)
            for row in db.query(AgentServerKey).filter_by(purpose=purpose)
        )


def test_a_request_that_loses_the_identity_race_uses_the_winners_key(engine):
    with Session(engine) as first, Session(engine) as second:
        made = server_keys.identity(first)
        committed_before_write(first, second)
        found = server_keys.identity(second)
        second.commit()
    assert (found.fingerprint, found.public_key) == (made.fingerprint, made.public_key)
    assert stored(engine, "identity") == [(made.fingerprint, "active")]


def test_a_request_that_loses_the_encryption_key_race_uses_the_winners_key(engine):
    with Session(engine) as db:
        ident = server_keys.identity(db)
        db.commit()
    now = utc_now_naive()
    with Session(engine) as first, Session(engine) as second:
        made = server_keys.rotate_if_due(first, ident, now).key_id
        committed_before_write(first, second)
        found = server_keys.rotate_if_due(second, ident, now)
        assert (found.key_id, found.state) == (made, "active")
        second.commit()
    assert stored(engine, "encryption") == [(made, "active")]
