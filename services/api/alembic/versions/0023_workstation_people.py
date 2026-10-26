"""Associate workstation identities with people and index current posture evidence."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0023_workstation_people"
down_revision = "0022_insert_vacuum_upkeep"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "keen_agents", sa.Column("posture_assigned_at", sa.DateTime(), nullable=True)
    )
    op.add_column(
        "keen_agents",
        sa.Column("agent_type", sa.String(16), nullable=False, server_default="server"),
    )
    op.add_column(
        "keen_agents",
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_person",
        "keen_agents",
        "isms_people",
        ["person_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_keen_agents_person_id", "keen_agents", ["person_id"])
    op.create_check_constraint(
        "ck_agent_type", "keen_agents", "agent_type IN ('server', 'workstation')"
    )
    op.create_check_constraint(
        "ck_agent_person",
        "keen_agents",
        "agent_type = 'workstation' OR person_id IS NULL",
    )
    op.create_table(
        "agent_posture_observations",
        sa.Column(
            "agent_id",
            sa.String(36),
            sa.ForeignKey("keen_agents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("stream_key", sa.String(64), primary_key=True),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_agent_posture_observations_event_id",
        "agent_posture_observations",
        ["event_id"],
    )
    # Match only the authoritative account FK, never names or unverified email.
    op.execute("""INSERT INTO isms_people (id, name, email, position, notes, user_id, created_at, updated_at)
        SELECT gen_random_uuid(), u.username, COALESCE(u.email, ''), '', '', u.id,
               now() AT TIME ZONE 'UTC', now() AT TIME ZONE 'UTC'
        FROM users u WHERE NOT EXISTS (SELECT 1 FROM isms_people p WHERE p.user_id = u.id)
        ON CONFLICT (user_id) DO NOTHING""")


def downgrade():
    # People are durable user records and are retained on downgrade.
    op.drop_table("agent_posture_observations")
    op.drop_constraint("ck_agent_person", "keen_agents", type_="check")
    op.drop_constraint("ck_agent_type", "keen_agents", type_="check")
    op.drop_index("ix_keen_agents_person_id", "keen_agents")
    op.drop_constraint("fk_agent_person", "keen_agents", type_="foreignkey")
    op.drop_column("keen_agents", "person_id")
    op.drop_column("keen_agents", "agent_type")
    op.drop_column("keen_agents", "posture_assigned_at")
