"""Effectiveness measures: drop the free-text method/metric columns and record
the expected source.

A measure's Summary names what is measured and its Description explains how; the
separate effectiveness_measure and metric text columns duplicated them. The new
source_type column names the KEEN evidence source expected to supply values (or
"other") so recorded metric entries can default to it.
"""

import sqlalchemy as sa
from alembic import op

revision = "0014_measure_source_type"
down_revision = "0013_events_source_timestamp"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "isms_effectiveness_measures",
        sa.Column(
            "source_type",
            sa.String(length=64),
            nullable=False,
            server_default="other",
        ),
    )
    # Existing method text is still useful context: fold it into the
    # description so nothing an operator wrote disappears with the column.
    op.execute(
        """
        UPDATE isms_effectiveness_measures
        SET description = trim(both E'\\n' from concat_ws(E'\\n\\n',
            nullif(description, ''),
            nullif(effectiveness_measure, ''),
            nullif(metric, '')))
        WHERE effectiveness_measure <> '' OR metric <> ''
        """
    )
    op.execute(
        """
        UPDATE isms_effectiveness_measures
        SET summary = coalesce(nullif(metric, ''), nullif(effectiveness_measure, ''), 'Effectiveness measure')
        WHERE summary = ''
        """
    )
    op.drop_column("isms_effectiveness_measures", "effectiveness_measure")
    op.drop_column("isms_effectiveness_measures", "metric")


def downgrade():
    op.add_column(
        "isms_effectiveness_measures",
        sa.Column("metric", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "isms_effectiveness_measures",
        sa.Column(
            "effectiveness_measure", sa.Text(), nullable=False, server_default=""
        ),
    )
    op.drop_column("isms_effectiveness_measures", "source_type")
