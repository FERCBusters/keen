"""Each account chooses where notifications go: the inbox, email or both."""

import sqlalchemy as sa
from alembic import op

revision = "0035_notification_delivery"
down_revision = "0034_inbox_measure_outside"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column(
            "pref_notification_delivery",
            sa.String(8),
            nullable=False,
            server_default="app",
        ),
    )


def downgrade():
    op.drop_column("users", "pref_notification_delivery")
