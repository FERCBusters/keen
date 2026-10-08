"""KEEN 1.0 final schema, verified against all 90 pre-release migrations."""
from pathlib import Path
from alembic import op

revision = '0001_release_schema'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    # Use the current schema so disposable CI schemas remain supported.
    schema = bind.exec_driver_sql('SELECT current_schema()').scalar_one()
    quoted = bind.dialect.identifier_preparer.quote_schema(schema)
    sql = (Path(__file__).resolve().parents[1] / 'release_schema.sql').read_text()
    sql = sql.replace('public.', quoted + '.')
    # Extensions belong to public, including when tests use a private schema.
    sql = sql.replace(quoted + '.gin_trgm_ops', 'public.gin_trgm_ops')
    bind.exec_driver_sql(sql)


def downgrade():
    raise RuntimeError('Restore a database backup to undo the release baseline.')
