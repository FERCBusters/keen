"""Signed UEU evidence: agent keys, enrolment codes, batches and provenance."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0025_ueu_evidence"
down_revision = "0024_dvstf_description"
branch_labels = None
depends_on = None

MODES = "('events','events_with_lines','all_lines')"
SUBJECTS = "('pairwise','public')"


def upgrade():
    # Existing identities hold bearer tokens; new keen-agent identities use keys.
    for column in (
        sa.Column(
            "credential_type", sa.String(8), nullable=False, server_default="token"
        ),
        sa.Column("enrollment_code_hash", sa.String(64), nullable=True),
        sa.Column("enrollment_code_expires_at", sa.DateTime(), nullable=True),
        sa.Column("validity_days", sa.Integer(), nullable=False, server_default="90"),
        sa.Column(
            "subject_type", sa.String(16), nullable=False, server_default="pairwise"
        ),
        sa.Column(
            "evidence_mode_ceiling",
            sa.String(32),
            nullable=False,
            server_default="events",
        ),
        sa.Column("ueu_last_sequence", sa.BigInteger(), nullable=True),
        sa.Column("ueu_last_hash", sa.String(80), nullable=True),
    ):
        op.add_column("keen_agents", column)
    op.create_check_constraint(
        "ck_agent_credential_type", "keen_agents", "credential_type IN ('key','token')"
    )
    op.create_check_constraint(
        "ck_agent_subject_type", "keen_agents", "subject_type IN " + SUBJECTS
    )
    op.create_check_constraint(
        "ck_agent_evidence_mode", "keen_agents", "evidence_mode_ceiling IN " + MODES
    )
    for column in (
        sa.Column(
            "subject_type", sa.String(16), nullable=False, server_default="pairwise"
        ),
        sa.Column(
            "evidence_mode_ceiling",
            sa.String(32),
            nullable=False,
            server_default="events",
        ),
    ):
        op.add_column("agent_enrollment_profiles", column)
    op.create_check_constraint(
        "ck_profile_subject_type",
        "agent_enrollment_profiles",
        "subject_type IN " + SUBJECTS,
    )
    op.create_check_constraint(
        "ck_profile_evidence_mode",
        "agent_enrollment_profiles",
        "evidence_mode_ceiling IN " + MODES,
    )
    op.create_table(
        "agent_keys",
        sa.Column("key_id", sa.String(64), primary_key=True),
        sa.Column(
            "agent_id",
            sa.String(36),
            sa.ForeignKey("keen_agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("public_key", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("retired_at", sa.DateTime(), nullable=True),
        sa.Column("previous_key_id", sa.String(64), nullable=True),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("credential_hash", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "state IN ('active','retired','revoked')", name="ck_agent_key_state"
        ),
        sa.CheckConstraint(
            "origin IN ('code','profile','renewal')", name="ck_agent_key_origin"
        ),
        sa.UniqueConstraint(
            "agent_id", "credential_hash", name="uq_agent_key_credential"
        ),
    )
    op.create_index("ix_agent_keys_agent_id", "agent_keys", ["agent_id"])
    op.create_index(
        "uq_agent_keys_one_active",
        "agent_keys",
        ["agent_id"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
    )
    op.create_table(
        "ueu_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agent_id",
            sa.String(36),
            sa.ForeignKey("keen_agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("key_id", sa.String(64), nullable=True),
        sa.Column("manifest", sa.Text(), nullable=True),
        sa.Column("signature", sa.String(128), nullable=True),
        sa.Column("verification", sa.String(16), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_ueu_batches_agent_id", "ueu_batches", ["agent_id"])
    op.create_index("ix_ueu_batches_received_at", "ueu_batches", ["received_at"])
    op.create_table(
        "ueu_evidence",
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "agent_id",
            sa.String(36),
            sa.ForeignKey("keen_agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("evidence_id", sa.String(36), nullable=False),
        sa.Column(
            "batch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ueu_batches.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("key_id", sa.String(64), nullable=True),
        sa.Column("evidence_hash", sa.String(80), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=True),
        sa.Column("verification", sa.String(16), nullable=False),
        sa.Column("chain_status", sa.String(16), nullable=False),
        sa.Column("schema_validation", sa.String(32), nullable=False),
        sa.Column("catalogue", sa.String(65), nullable=False),
        sa.Column(
            "custody",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    for name, column in (
        ("ix_ueu_evidence_agent_id", "agent_id"),
        ("ix_ueu_evidence_evidence_id", "evidence_id"),
        ("ix_ueu_evidence_batch_id", "batch_id"),
        ("ix_ueu_evidence_event_type", "event_type"),
    ):
        op.create_index(name, "ueu_evidence", [column])
    op.create_table(
        "ueu_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_subject_key", sa.Text(), nullable=True),
        sa.CheckConstraint("id = 1", name="ck_ueu_settings_singleton"),
    )


def downgrade():
    op.drop_table("ueu_settings")
    op.drop_table("ueu_evidence")
    op.drop_table("ueu_batches")
    op.drop_table("agent_keys")
    for name in ("ck_profile_evidence_mode", "ck_profile_subject_type"):
        op.drop_constraint(name, "agent_enrollment_profiles", type_="check")
    for column in ("evidence_mode_ceiling", "subject_type"):
        op.drop_column("agent_enrollment_profiles", column)
    for name in (
        "ck_agent_evidence_mode",
        "ck_agent_subject_type",
        "ck_agent_credential_type",
    ):
        op.drop_constraint(name, "keen_agents", type_="check")
    for column in (
        "ueu_last_hash",
        "ueu_last_sequence",
        "evidence_mode_ceiling",
        "subject_type",
        "validity_days",
        "enrollment_code_expires_at",
        "enrollment_code_hash",
        "credential_type",
    ):
        op.drop_column("keen_agents", column)
