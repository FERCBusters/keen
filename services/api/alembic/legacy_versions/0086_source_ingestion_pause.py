"""Persistent operator pause switches for source ingestion."""
from alembic import op
import sqlalchemy as sa
revision = '0086_source_ingestion_pause'
down_revision = '0085_security_notifications'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('source_ingestion_states',
        sa.Column('source', sa.String(64), primary_key=True),
        sa.Column('paused', sa.Boolean(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('updated_by', sa.String(256), nullable=True))


def downgrade():
    op.drop_table('source_ingestion_states')
