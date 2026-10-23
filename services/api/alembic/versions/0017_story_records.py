"""Stories relate to SWOT items, people, vendors, effectiveness measures and minutes.

A story item may now refer to a SWOT item, a person, a vendor, an effectiveness
measure or a set of meeting minutes, as well as an event, a risk, a document,
an asset or an objective. Downgrading removes the items of the new kinds, and
their flow chart links with them, because the earlier schema cannot hold them.
"""

from alembic import op

revision = "0017_story_records"
down_revision = "0016_parties_org_wide"
branch_labels = None
depends_on = None

EARLIER_KINDS = "'event','risk','document','asset','objective','note'"
KINDS = (
    "'event','risk','document','asset','objective','swot','person','vendor',"
    "'effectiveness_measure','meeting','note'"
)


def _replace_kind_check(kinds: str) -> None:
    op.drop_constraint("ck_story_item_kind", "story_items", type_="check")
    op.create_check_constraint(
        "ck_story_item_kind", "story_items", f"kind IN ({kinds})"
    )


def upgrade():
    _replace_kind_check(KINDS)


def downgrade():
    # Links to these items go with them through ON DELETE CASCADE.
    op.execute(f"DELETE FROM story_items WHERE kind NOT IN ({EARLIER_KINDS})")
    _replace_kind_check(EARLIER_KINDS)
