"""Separate audit ownership management from audit administration."""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "0007_audit_permissions"
down_revision = "0006_session_revocation"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        sa.text("""
        INSERT INTO permissions (id, code, description, created_at)
        SELECT :id, 'audits.admin',
               'Edit and delete any audit or scheduled audit template.', CURRENT_TIMESTAMP
        WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'audits.admin')
    """).bindparams(id=str(uuid.uuid4()))
    )
    op.execute(
        sa.text("""
        UPDATE permissions SET description =
            'Create audits and scheduled templates; run and edit those you created. Sampling requires source read access.'
        WHERE code = 'audits.manage'
    """)
    )


def downgrade():
    op.execute(sa.text("DELETE FROM permissions WHERE code = 'audits.admin'"))
    op.execute(
        sa.text("""
        UPDATE permissions SET description =
            'Create and maintain audit engagements, sampled evidence, scope, attendees, findings and final reports.'
        WHERE code = 'audits.manage'
    """)
    )
