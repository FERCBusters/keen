"""Remove business processes.

PESTLE(E) factors were rated against business processes, a register Keen
shipped empty and nothing else used. The two tables go, with the change
history of the processes themselves and the business process entries inside
each factor's history, and the "Business processes" feature setting leaves the
stored feature configuration, which accepts only settings Keen defines.
"""

import sqlalchemy as sa
from alembic import op

revision = "0052_remove_business_processes"
down_revision = "0051_isms_business_processes"
branch_labels = None
depends_on = None

PROCESSES = "pestle_business_processes"
RELEVANCE = "pestle_business_process_relevance"
FEATURE = "business_processes"


def upgrade():
    op.drop_table(RELEVANCE)
    op.drop_table(PROCESSES)
    op.execute(
        "DELETE FROM entity_changelogs WHERE entity_type = 'pestle_business_process'"
    )
    # A factor's history keeps its other changes; a change that recorded only
    # business processes has nothing left to show.
    # A state that was never recorded is stored as a JSON null, which has no
    # keys to remove.
    op.execute(
        sa.text(
            "UPDATE entity_changelogs SET"
            " before_state = CASE WHEN jsonb_typeof(before_state) = 'object'"
            " THEN before_state - :key ELSE before_state END,"
            " after_state = CASE WHEN jsonb_typeof(after_state) = 'object'"
            " THEN after_state - :key ELSE after_state END,"
            " changes = COALESCE((SELECT jsonb_agg(change) FROM"
            " jsonb_array_elements(changes) AS change"
            " WHERE change->>'field' <> :key), '[]'::jsonb)"
            " WHERE entity_type = 'pestle_item'"
            " AND jsonb_typeof(changes) = 'array'"
        ).bindparams(key=FEATURE)
    )
    op.execute(
        "DELETE FROM entity_changelogs WHERE entity_type = 'pestle_item'"
        " AND action = 'updated' AND changes = '[]'::jsonb"
        " AND request_path LIKE '%/business-processes'"
    )
    op.execute(
        sa.text(
            "UPDATE managed_configurations SET document = document - :key,"
            " version = version + 1 WHERE name = 'features' AND document ? :key"
        ).bindparams(key=FEATURE)
    )


def downgrade():
    """Recreate both tables empty; the rows and history they held are not restored."""
    now = sa.text("now()")
    op.create_table(
        PROCESSES,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=now, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pestle_business_processes_pkey"),
        sa.UniqueConstraint("name", name="uq_pestle_business_processes_name"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="pestle_business_processes_created_by_user_id_fkey",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_pestle_business_processes_created_at", PROCESSES, ["created_at"]
    )
    op.create_index("ix_pestle_business_processes_name", PROCESSES, ["name"])
    op.create_table(
        RELEVANCE,
        sa.Column("pestle_item_id", sa.UUID(), nullable=False),
        sa.Column("business_process_id", sa.UUID(), nullable=False),
        sa.Column("relevance_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=now, nullable=False),
        sa.PrimaryKeyConstraint(
            "pestle_item_id",
            "business_process_id",
            name="pestle_business_process_relevance_pkey",
        ),
        sa.ForeignKeyConstraint(
            ["pestle_item_id"],
            ["pestle_items.id"],
            name="pestle_business_process_relevance_pestle_item_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["business_process_id"],
            [PROCESSES + ".id"],
            name="pestle_business_process_relevance_business_process_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["relevance_id"],
            ["pestle_relevance_levels.id"],
            name="pestle_business_process_relevance_relevance_id_fkey",
        ),
    )
    op.create_index(
        "ix_pestle_business_process_relevance_relevance_id", RELEVANCE, ["relevance_id"]
    )
