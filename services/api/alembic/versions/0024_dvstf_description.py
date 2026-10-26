"""Fill the missing UK DVSTF description without replacing local edits."""

import sqlalchemy as sa
from alembic import op

revision = "0024_dvstf_description"
down_revision = "0023_workstation_people"
branch_labels = None
depends_on = None

DESCRIPTION = (
    "The UK digital verification services trust framework is the set of rules and "
    "standards that show what a good digital identity looks like.\n\n"
    "Digital verification services can be independently certified against at least "
    "one of the current versions of the trust framework.\n\n"
    "The most recent 1.0 publication is titled the UK digital verification services "
    "trust framework. Prior versions were titled the UK digital identity and "
    "attributes trust framework."
)


def upgrade():
    op.get_bind().execute(
        sa.text(
            "UPDATE frameworks SET description = :description "
            "WHERE slug = :slug AND (description IS NULL OR description = '')"
        ),
        {"slug": "UK-DVSTF:1.0", "description": DESCRIPTION},
    )


def downgrade():
    op.get_bind().execute(
        sa.text(
            "UPDATE frameworks SET description = NULL "
            "WHERE slug = :slug AND description = :description"
        ),
        {"slug": "UK-DVSTF:1.0", "description": DESCRIPTION},
    )
