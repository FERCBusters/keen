"""Isolated contracts on a migrated PostgreSQL schema."""

import os
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool, StaticPool

from tests.db_helpers import required_database_url, schema_engine


def pytest_configure(config):
    # Keen requires its public origin at start-up. Tests use the test client's
    # origin unless the environment chooses another; this runs before any test
    # module imports the settings.
    os.environ.setdefault("KEEN_PUBLIC_BASE_URL", "http://testserver")


@contextmanager
def migrated_schema(prefix):
    """An engine on a private schema migrated to head, dropped afterwards."""
    url = required_database_url("KEEN_CONTRACT_TEST_DATABASE_URL")
    schema = prefix + uuid.uuid4().hex
    admin = create_engine(url, poolclass=NullPool)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    admin.dispose()
    engine = schema_engine(url, schema)
    try:
        from alembic import command
        from alembic.config import Config

        root = Path(__file__).resolve().parents[1]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "alembic"))
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0001_release_schema")
            # Contract fixtures supply their own reference data. Skip only the
            # expensive catalogue seed; discover every later migration via Alembic.
            # The release-baseline tests exercise the full seeded upgrade separately.
            command.stamp(config, "0002_release_seed")
            command.upgrade(config, "head")
        yield engine
    finally:
        try:
            with engine.begin() as conn:
                conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        finally:
            engine.dispose()


@pytest.fixture(scope="module")
def contract_postgres_engine():
    # A schema per module and an outer transaction per test keep committed
    # route writes isolated while avoiding dozens of full schema migrations.
    with migrated_schema("contracts_") as engine:
        yield engine


@pytest.fixture(scope="session")
def committing_schema():
    """One migrated schema for the tests that commit; see postgres_engine."""
    with migrated_schema("committing_") as engine:
        with engine.begin() as connection:
            schema = connection.execute(text("SELECT current_schema()")).scalar()
            tables = [
                name
                for name in inspect(connection).get_table_names()
                if name != "alembic_version"
            ]
            seeded = {}
            for name in tables:
                rows = connection.execute(text(f'SELECT * FROM "{name}"'))
                found = [dict(row) for row in rows.mappings()]
                if found:
                    seeded[name] = found
        yield engine, schema, tables, seeded


def _restore(engine, tables, seeded):
    """Leave the schema as its migrations did: their rows and nothing else."""
    with engine.connect() as connection:
        connection.rollback()
        held = " UNION ALL ".join(
            f"SELECT '{name}' WHERE EXISTS (SELECT 1 FROM \"{name}\")"
            for name in tables
        )
        used = [row[0] for row in connection.execute(text(held))]
        changed = [
            name
            for name in used
            if name not in seeded
            or seeded[name]
            != [
                dict(row)
                for row in connection.execute(
                    text(f'SELECT * FROM "{name}"')
                ).mappings()
            ]
        ]
        if changed:
            names = ", ".join(f'"{name}"' for name in changed)
            connection.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        # A cascade may have emptied a table the migrations filled.
        for name, rows in seeded.items():
            if not connection.execute(text(f'SELECT count(*) FROM "{name}"')).scalar():
                columns = ", ".join(f'"{column}"' for column in rows[0])
                values = ", ".join(f":{column}" for column in rows[0])
                connection.execute(
                    text(f'INSERT INTO "{name}" ({columns}) VALUES ({values})'), rows
                )
        # A row stored and deleted again still advanced its sequence.
        for (name,) in connection.execute(
            text(
                "SELECT sequencename FROM pg_sequences "
                "WHERE schemaname = current_schema() AND last_value IS NOT NULL"
            )
        ).all():
            connection.execute(text(f'ALTER SEQUENCE "{name}" RESTART'))
        connection.commit()


@pytest.fixture
def postgres_engine(committing_schema):
    """An engine on the migrated schema for code that commits its own work.

    Every session shares its one connection and transaction, so code under
    test that opens its own session sees what the test has yet to commit. What
    a test stores, committed or not, is removed before the next test, and the
    engine is the test's own, with any listeners it attaches.
    """
    owner, schema, tables, seeded = committing_schema
    engine = schema_engine(
        owner.url.render_as_string(hide_password=False), schema, poolclass=StaticPool
    )
    try:
        yield engine
    finally:
        engine.dispose()
        _restore(owner, tables, seeded)


@pytest.fixture
def contract_db(contract_postgres_engine):
    with contract_postgres_engine.connect() as connection:
        transaction = connection.begin()
        try:
            with Session(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            ) as db:
                yield db
        finally:
            transaction.rollback()


@pytest.fixture
def without_implied_permissions(monkeypatch):
    """Audit management implies events.read and isms.read. A check that stands
    on its own behind that implication runs with it switched off, so the account
    under test holds exactly the permissions it was granted."""
    from app.security import permissions

    monkeypatch.setattr(permissions, "PERMISSION_IMPLICATIONS", {})


@pytest.fixture
def empty_schema_engine():
    """An engine on a schema with no tables, for a migration's own fixtures."""
    url = required_database_url("KEEN_CONTRACT_TEST_DATABASE_URL")
    schema = "empty_" + uuid.uuid4().hex
    admin = create_engine(url, poolclass=NullPool)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = schema_engine(url, schema, poolclass=StaticPool)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def no_evidence_purge(monkeypatch):
    """No purge has ingestion paused, for collectors given a stand-in database.

    A stand-in cannot answer the purge query. The per-source pause still reads
    whatever the stand-in returns.
    """
    from app.services import ingestion_pause

    monkeypatch.setattr(
        ingestion_pause,
        "purge_pause_status",
        lambda db: {"paused": False, "resume_at": None},
    )


@pytest.fixture
def no_competing_run(monkeypatch):
    """No other run holds a connection, for collectors given a stand-in database.

    The run lock is a session advisory lock taken on a connection of its own,
    which a stand-in cannot open.
    """
    from app.ingest import connections

    @contextmanager
    def free(db, key):
        yield True

    monkeypatch.setattr(connections, "run_lock", free)
