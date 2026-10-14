"""SWOT analysis and objective planning dates."""

from alembic import op

revision = "0010_swot_planning"
down_revision = "0009_retention_overrides"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE isms_swot (
        id uuid PRIMARY KEY, title varchar(256) NOT NULL, description text NOT NULL DEFAULT '',
        response text NOT NULL DEFAULT '', kind varchar(16) NOT NULL,
        significance integer NOT NULL DEFAULT 3, status varchar(32) NOT NULL DEFAULT 'defined',
        parent_id uuid REFERENCES isms_swot(id) ON DELETE RESTRICT,
        owner_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
        start_date date, target_date date, version integer NOT NULL DEFAULT 1,
        created_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
        created_at timestamp NOT NULL, updated_at timestamp NOT NULL,
        CONSTRAINT ck_swot_kind CHECK (kind IN ('strength','weakness','opportunity','threat')),
        CONSTRAINT ck_swot_significance CHECK (significance BETWEEN 1 AND 5),
        CONSTRAINT ck_swot_status CHECK (status IN ('defined','planned','in_progress','under_review','completed','deferred','superseded')),
        CONSTRAINT ck_swot_parent CHECK (parent_id <> id),
        CONSTRAINT ck_swot_dates CHECK (target_date >= start_date)
    )""")
    for column in ("kind", "significance", "status", "parent_id", "target_date"):
        op.create_index("ix_isms_swot_" + column, "isms_swot", [column])
    op.execute("""CREATE TABLE isms_swot_links (
        swot_id uuid REFERENCES isms_swot(id) ON DELETE CASCADE NOT NULL,
        target_type varchar(24) NOT NULL, target_id uuid NOT NULL,
        PRIMARY KEY (swot_id, target_type, target_id),
        CONSTRAINT ck_swot_link_type CHECK (target_type IN ('objective','document','asset','person','vendor','risk'))
    )""")
    op.create_index(
        "ix_swot_link_target", "isms_swot_links", ["target_type", "target_id"]
    )
    op.execute("""CREATE TABLE audit_swot_scope (
        audit_id uuid REFERENCES audits(id) ON DELETE CASCADE NOT NULL,
        swot_id uuid REFERENCES isms_swot(id) ON DELETE RESTRICT NOT NULL,
        PRIMARY KEY (audit_id, swot_id)
    )""")
    op.create_index("ix_audit_swot_scope_swot_id", "audit_swot_scope", ["swot_id"])
    op.execute("ALTER TABLE isms_objectives ADD COLUMN start_date date")
    op.execute("ALTER TABLE isms_objectives DROP CONSTRAINT ck_isms_objectives_status")
    op.execute(
        "ALTER TABLE isms_objectives ADD CONSTRAINT ck_isms_objectives_status CHECK (status IN ('not_started','defined','planned','in_progress','under_review','completed','deferred','superseded'))"
    )


def downgrade():
    op.execute(
        "UPDATE isms_objectives SET status='not_started' WHERE status IN ('defined','planned','under_review')"
    )
    op.execute("ALTER TABLE isms_objectives DROP CONSTRAINT ck_isms_objectives_status")
    op.execute(
        "ALTER TABLE isms_objectives ADD CONSTRAINT ck_isms_objectives_status CHECK (status IN ('not_started','in_progress','completed','deferred','superseded'))"
    )
    op.drop_column("isms_objectives", "start_date")
    op.drop_table("audit_swot_scope")
    op.drop_table("isms_swot_links")
    op.drop_table("isms_swot")
