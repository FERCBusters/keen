"""KEEN's own keys for end-to-end encrypted agent traffic.

The identity key (Ed25519) is pinned by agents through the fingerprint at the
end of every enrolment code and bootstrap key, and signs the encryption keys
(HPKE, MLKEM768-X25519) that agents encrypt every request to. Private halves
are encrypted with KEEN_INTEGRATION_SECRET_KEY; an encryption key's private
half is erased once agents may no longer use it.

Codes and bootstrap keys issued before this revision carry no fingerprint,
and agents before 0.3 send plaintext; both must be reissued or upgraded.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0030_agent_server_keys"
down_revision = "0029_stats_delete_increment"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_server_keys",
        sa.Column("key_id", sa.String(64), primary_key=True),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("private_key", sa.Text(), nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("statement", postgresql.JSONB(), nullable=True),
        sa.Column("signature", sa.String(128), nullable=True),
        sa.Column("not_after", sa.DateTime(), nullable=True),
        sa.Column("retired_at", sa.DateTime(), nullable=True),
        sa.Column("destroyed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "purpose IN ('identity','encryption')", name="ck_agent_server_key_purpose"
        ),
        sa.CheckConstraint(
            "state IN ('active','retired','destroyed')",
            name="ck_agent_server_key_state",
        ),
        sa.CheckConstraint(
            "(state = 'destroyed') = (private_key IS NULL)",
            name="ck_agent_server_key_private",
        ),
    )
    op.create_index(
        "uq_agent_server_keys_one_active",
        "agent_server_keys",
        ["purpose"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
    )


def downgrade():
    op.drop_index("uq_agent_server_keys_one_active", table_name="agent_server_keys")
    op.drop_table("agent_server_keys")
