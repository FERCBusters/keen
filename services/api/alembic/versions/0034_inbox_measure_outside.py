"""Inbox items for measures whose latest result falls outside the threshold.

The item names the result that crossed the threshold, so each crossing is its
own notification.
"""

from alembic import op

revision = "0034_inbox_measure_outside"
down_revision = "0033_inbox_items"
branch_labels = None
depends_on = None

KINDS = "'audit_started','quiz_round','meeting_minutes'"


def upgrade():
    op.drop_constraint("ck_inbox_items_kind", "inbox_items", type_="check")
    op.create_check_constraint(
        "ck_inbox_items_kind", "inbox_items", f"kind IN ({KINDS},'measure_outside')"
    )


def downgrade():
    op.execute("DELETE FROM inbox_items WHERE kind = 'measure_outside'")
    op.drop_constraint("ck_inbox_items_kind", "inbox_items", type_="check")
    op.create_check_constraint(
        "ck_inbox_items_kind", "inbox_items", f"kind IN ({KINDS})"
    )
