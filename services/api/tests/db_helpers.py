"""Test-only database setup; production metadata is never modified."""
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def sqlite_jsonb(_type, _compiler, **_kw):
    return "JSON"


from sqlalchemy import DefaultClause, MetaData, create_engine, event, text


def create_sqlite_schema(engine):
    from app.db.session import Base
    # Lightweight adapter tests use SQLite. Retention SQL, triggers and MFA
    # migrations are tested separately on PostgreSQL, not emulated here.
    metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(metadata)
    for table in metadata.tables.values():
        for column in table.columns:
            default = column.server_default
            if default is not None and str(default.arg) == "now() AT TIME ZONE 'UTC'":
                column.server_default = DefaultClause(text('CURRENT_TIMESTAMP'))
    metadata.create_all(engine)


def schema_engine(url, schema):
    """Explicitly select the private schema on every connection, and verify it."""
    engine = create_engine(url)
    @event.listens_for(engine, 'connect')
    def select_schema(connection, _record):
        # Schema names are generated internally, never supplied by a request.
        if not schema.replace('_', '').isalnum():
            raise ValueError('Invalid test schema')
        with connection.cursor() as cursor:
            cursor.execute(f'SET search_path TO "{schema}"')
            cursor.execute("SET TIME ZONE 'UTC'")
            cursor.execute('SELECT current_schema()')
            if cursor.fetchone()[0] != schema:
                raise RuntimeError('Test database did not select its private schema')
        connection.commit()
    return engine
