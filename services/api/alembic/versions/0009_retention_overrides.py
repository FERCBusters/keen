"""Source-type and recorded-rule evidence retention overrides."""

from alembic import op

revision = "0009_retention_overrides"
down_revision = "0008_integration_auth_kind"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE evidence_retention_overrides (
        scope text NOT NULL CHECK (scope IN ('source','rule')),
        key text NOT NULL CHECK (length(key) BETWEEN 1 AND 128),
        mode text NOT NULL,
        value bigint,
        PRIMARY KEY (scope,key),
        CHECK ((mode='disabled' AND value IS NULL) OR
               (mode='age' AND value IS NOT NULL AND value BETWEEN 1 AND 36500) OR
               (mode='count' AND value IS NOT NULL AND value BETWEEN 1 AND 1000000000))
    )""")
    op.execute("ALTER TABLE evidence_purge_jobs ADD COLUMN policy_overrides jsonb")
    op.execute(
        "ALTER TABLE evidence_purge_jobs DROP CONSTRAINT evidence_purge_jobs_mode_check"
    )
    op.execute(
        "ALTER TABLE evidence_purge_jobs ADD CONSTRAINT evidence_purge_jobs_mode_check CHECK (mode IN ('disabled','age','count','all'))"
    )


def downgrade():
    op.execute("DELETE FROM evidence_purge_jobs WHERE mode='disabled'")
    op.execute(
        "ALTER TABLE evidence_purge_jobs DROP CONSTRAINT evidence_purge_jobs_mode_check"
    )
    op.execute(
        "ALTER TABLE evidence_purge_jobs ADD CONSTRAINT evidence_purge_jobs_mode_check CHECK (mode IN ('age','count','all'))"
    )
    op.execute("ALTER TABLE evidence_purge_jobs DROP COLUMN policy_overrides")
    op.execute("DROP TABLE evidence_retention_overrides")
