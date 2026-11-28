"""Stories take controls and clauses and legal items; legal items take SWOT.

A story's author assigns it controls and clauses of a framework, as other
records are linked; these are separate from the controls its events map to.
A story item may also refer to a legal register item. Downgrading removes the
items of that kind, and their flow chart links with them, because the earlier
schema cannot hold them.

A legal register item may also relate to a SWOT item, beside documents, assets
and risks. Downgrading removes those relationships for the same reason.

Revision ID: 0060_story_controls_legal
Revises: 0059_isms_legal_register
"""

import sqlalchemy as sa
from alembic import op

revision = "0060_story_controls_legal"
down_revision = "0059_isms_legal_register"
branch_labels = None
depends_on = None

EARLIER_KINDS = (
    "'event','risk','document','asset','objective','swot','person','vendor',"
    "'effectiveness_measure','meeting','note'"
)
KINDS = (
    "'event','risk','document','asset','objective','swot','person','vendor',"
    "'effectiveness_measure','meeting','legal_item','note'"
)

EARLIER_LINK_TYPES = "'document','asset','risk'"
LINK_TYPES = "'document','asset','risk','swot'"


def _replace_kind_check(kinds: str) -> None:
    op.drop_constraint("ck_story_item_kind", "story_items", type_="check")
    op.create_check_constraint(
        "ck_story_item_kind", "story_items", f"kind IN ({kinds})"
    )


def _replace_link_check(types: str) -> None:
    op.drop_constraint(
        "ck_legal_item_link_type", "isms_legal_item_links", type_="check"
    )
    op.create_check_constraint(
        "ck_legal_item_link_type", "isms_legal_item_links", f"target_type in ({types})"
    )


def upgrade():
    _replace_kind_check(KINDS)
    _replace_link_check(LINK_TYPES)
    op.create_table(
        "story_control_links",
        sa.Column("story_id", sa.UUID(), nullable=False),
        sa.Column("control_item_id", sa.UUID(), nullable=False),
        sa.Column("framework_slug", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["control_item_id"], ["control_items.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("story_id", "control_item_id"),
    )
    op.create_index(
        op.f("ix_story_control_links_control_item_id"),
        "story_control_links",
        ["control_item_id"],
    )
    op.create_table(
        "story_clause_links",
        sa.Column("story_id", sa.UUID(), nullable=False),
        sa.Column("clause_id", sa.UUID(), nullable=False),
        sa.Column("framework_slug", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["clause_id"], ["framework_clauses.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("story_id", "clause_id"),
    )
    op.create_index(
        op.f("ix_story_clause_links_clause_id"), "story_clause_links", ["clause_id"]
    )


def downgrade():
    op.drop_table("story_clause_links")
    op.drop_table("story_control_links")
    # Links to these items go with them through ON DELETE CASCADE.
    op.execute(f"DELETE FROM story_items WHERE kind NOT IN ({EARLIER_KINDS})")
    _replace_kind_check(EARLIER_KINDS)
    op.execute(
        f"DELETE FROM isms_legal_item_links WHERE target_type NOT IN ({EARLIER_LINK_TYPES})"
    )
    _replace_link_check(EARLIER_LINK_TYPES)
