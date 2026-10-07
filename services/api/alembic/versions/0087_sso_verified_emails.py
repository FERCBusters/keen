"""Verified addresses for safe SSO account linking."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
revision = '0087_sso_verified_emails'
down_revision = '0086_source_ingestion_pause'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('user_sso_emails',
        sa.Column('email', sa.String(256), primary_key=True),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('verified_at', sa.DateTime(), nullable=False))
    op.create_index('ix_user_sso_emails_user_id', 'user_sso_emails', ['user_id'])
    op.create_table('sso_email_challenges',
        sa.Column('token_hash', sa.String(64), primary_key=True),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('email', sa.String(256), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('mfa_version', sa.Integer(), nullable=False),
        sa.Column('password_digest', sa.String(64), nullable=False))
    op.create_index('ix_sso_email_challenges_user_id', 'sso_email_challenges', ['user_id'])


def downgrade():
    op.drop_table('sso_email_challenges')
    op.drop_table('user_sso_emails')
