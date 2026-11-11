"""Test-only database setup; production metadata is never modified."""

import os

import pytest
from sqlalchemy import create_engine, event


def schema_engine(url, schema, **options):
    """Explicitly select the private schema on every connection, and verify it."""
    engine = create_engine(url, **options)

    @event.listens_for(engine, "connect")
    def select_schema(connection, _record):
        # Schema names are generated internally, never supplied by a request.
        if not schema.replace("_", "").isalnum():
            raise ValueError("Invalid test schema")
        with connection.cursor() as cursor:
            cursor.execute(f'SET search_path TO "{schema}"')
            cursor.execute("SET TIME ZONE 'UTC'")
            cursor.execute("SELECT current_schema()")
            if cursor.fetchone()[0] != schema:
                raise RuntimeError("Test database did not select its private schema")
        connection.commit()

    return engine


def required_database_url(variable):
    """The disposable PostgreSQL database a suite runs on."""
    url = os.environ.get(variable)
    if not url:
        pytest.fail(
            f"Set {variable} to a disposable PostgreSQL database; tests.sh "
            "sets it from KEEN_TEST_DATABASE_URL. See TESTING.md.",
            pytrace=False,
        )
    return url
