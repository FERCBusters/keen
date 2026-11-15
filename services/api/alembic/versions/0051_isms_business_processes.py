"""Remove ISMS business processes and the application configuration matrix.

Keen has no screen and no endpoint for either. Business processes are kept
under PESTLE(E), in their own table, which stays as it is. The six ISMS
business processes an installation starts with go with their table, and an
audit sample of an application configuration row goes with the row it named.
Change history already recorded for either kind of record is kept.
"""

import sqlalchemy as sa
from alembic import op

revision = "0051_isms_business_processes"
down_revision = "0050_external_auditor_questions"
branch_labels = None
depends_on = None

ENTRIES = "isms_application_configuration_entries"
PROCESSES = "isms_business_processes"
SAMPLE = "isms_application_configuration"


def upgrade():
    op.execute(
        sa.text("DELETE FROM audit_evidence WHERE entity_type = :kind").bindparams(
            kind=SAMPLE
        )
    )
    # Each table's indexes and constraints go with it.
    op.drop_table(ENTRIES)
    op.drop_table(PROCESSES)


def downgrade():
    """Recreate both tables empty; the rows they held are not restored."""
    now = sa.text("now()")
    op.create_table(
        PROCESSES,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "is_default", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=now, nullable=False),
        sa.PrimaryKeyConstraint("id", name="isms_business_processes_pkey"),
        sa.UniqueConstraint("name", name="uq_isms_business_processes_name"),
    )
    op.create_index("ix_isms_business_processes_name", PROCESSES, ["name"])

    def reference(column, target, ondelete, name=None, nullable=True):
        return sa.Column(
            column,
            sa.UUID(),
            sa.ForeignKey(
                target,
                ondelete=ondelete,
                name=name or f"{ENTRIES}_{column}_fkey",
            ),
            nullable=nullable,
        )

    op.create_table(
        ENTRIES,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        reference("document_id", "isms_documents.id", "CASCADE"),
        reference("user_id", "users.id", "CASCADE"),
        reference(
            "asset_id",
            "risk_assets.id",
            "CASCADE",
            name="fk_isms_app_config_asset_id_risk_assets",
        ),
        reference("org_node_id", "isms_org_nodes.id", "CASCADE"),
        reference(
            "business_process_id",
            "isms_business_processes.id",
            "CASCADE",
            nullable=False,
        ),
        sa.Column("value", sa.String(16), server_default="Low", nullable=False),
        sa.Column("notes", sa.Text(), server_default="", nullable=False),
        reference("created_by_user_id", "users.id", "SET NULL"),
        sa.Column("created_at", sa.DateTime(), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=now, nullable=False),
        sa.PrimaryKeyConstraint(
            "id", name="isms_application_configuration_entries_pkey"
        ),
        sa.CheckConstraint(
            "source_type in ('document','person','asset','org_node')",
            name="ck_isms_app_config_source_type",
        ),
        sa.CheckConstraint(
            "value in ('Low','Medium','High')", name="ck_isms_app_config_value"
        ),
    )
    for column in (
        "asset_id",
        "business_process_id",
        "created_at",
        "document_id",
        "org_node_id",
        "source_type",
        "user_id",
        "value",
    ):
        op.create_index(f"ix_{ENTRIES}_{column}", ENTRIES, [column])
