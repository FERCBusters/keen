"""Enrollment profiles and agent credential lifecycle."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision = '0005_agent_enrollment'
down_revision = '0004_source_connections'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('agent_enrollment_profiles',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('key_hash', sa.String(64), nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('token_days', sa.Integer, nullable=False),
        sa.Column('expires_at', sa.DateTime),
        sa.Column('max_enrollments', sa.Integer),
        sa.Column('enrollment_count', sa.Integer, nullable=False),
        sa.Column('allowed_cidrs', JSONB, nullable=False),
        sa.Column('labels', JSONB, nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.Column('revoked_at', sa.DateTime))
    for name, size in [('enrollment_profile_id',36),('enrollment_nonce_hash',64),('enrollment_ip',64),('previous_token_hash',64),('renewal_nonce_hash',64)]:
        op.add_column('keen_agents', sa.Column(name, sa.String(size)))
    op.add_column('keen_agents', sa.Column('renewal_retry_until', sa.DateTime))
    op.add_column('keen_agents', sa.Column('enrollment_labels', JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.create_foreign_key('fk_agent_enrollment_profile','keen_agents','agent_enrollment_profiles',['enrollment_profile_id'],['id'])
    op.create_index('ix_keen_agents_enrollment_profile_id','keen_agents',['enrollment_profile_id'])
    op.create_unique_constraint('uq_agent_enrollment_attempt','keen_agents',['enrollment_profile_id','enrollment_nonce_hash'])


def downgrade():
    raise RuntimeError('Restore a backup to undo enrollment identities and revocations.')
