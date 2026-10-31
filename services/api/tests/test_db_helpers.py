"""SQLite test schemas preserve the values used by PostgreSQL model types."""

import uuid

import pytest
from app.db.models import Framework
from sqlalchemy import create_engine, select, text

from tests.db_helpers import create_sqlite_schema


@pytest.mark.parametrize(
    "value",
    ["00000000-0000-4000-8000-000000000001", "12345678-1234-4234-8123-123456789e12"],
)
def test_sqlite_uuid_columns_preserve_numeric_looking_identifiers(value):
    engine = create_engine("sqlite://")
    try:
        create_sqlite_schema(engine)
        ident = uuid.UUID(value)
        with engine.begin() as connection:
            connection.execute(
                Framework.__table__.insert().values(
                    id=ident, slug="TEST:1", name="Test"
                )
            )
            assert (
                connection.execute(
                    text("SELECT typeof(id) FROM frameworks")
                ).scalar_one()
                == "text"
            )
            assert connection.execute(select(Framework.id)).scalar_one() == ident
    finally:
        engine.dispose()
