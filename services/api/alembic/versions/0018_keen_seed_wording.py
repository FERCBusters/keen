"""Seeded starter text names Keen and uses straight apostrophes.

The release seed names the product in capitals in the Keen Assurance Framework
name, framework descriptions, control and clause metadata and starter crosswalk
rationales, and uses curly apostrophes in Keen-authored descriptions. Each
replacement matches a whole seeded phrase, so wording an administrator has
changed stays as it is. Downgrading restores the seeded phrases.
"""

import sqlalchemy as sa
from alembic import op

revision = "0018_keen_seed_wording"
down_revision = "0017_story_records"
branch_labels = None
depends_on = None

FRAMEWORK = "KEEN-AF:1.0"
NAME = ("KEEN Assurance Framework", "Keen Assurance Framework")
# (seeded phrase, current phrase)
DESCRIPTIONS = (
    (
        "KEEN Assurance Framework is based on Vanta’s controls",
        "Keen Assurance Framework is based on Vanta's controls",
    ),
    ("KEEN adaptations include", "Keen adaptations include"),
    ("Vanta Inc. KEEN is also licensed", "Vanta Inc. Keen is also licensed"),
    ("KEEN summaries cover", "Keen summaries cover"),
    (
        "Coverage is OSA’s assessment; consult the framework’s normative text",
        "Coverage is OSA's assessment; consult the framework's normative text",
    ),
)
EDITORIAL = ("KEEN editorial summaries", "Keen editorial summaries")
EDITORIAL_APOSTROPHE = ("organisation’s", "organisation's")
METADATA = (
    (
        "Auditor-donated KEEN Assurance Framework source spreadsheet",
        "Auditor-donated Keen Assurance Framework source spreadsheet",
    ),
    ("Vanta Inc. Adapted by KEEN.", "Vanta Inc. Adapted by Keen."),
    EDITORIAL,
)
RATIONALE = ("KEEN starter crosswalk [", "Keen starter crosswalk [")
METADATA_TABLES = ("control_items", "framework_clauses")


def _apply(forward: bool) -> None:
    bind = op.get_bind()

    def run(sql: str, pair: tuple[str, str], **params) -> None:
        old, new = pair if forward else pair[::-1]
        bind.execute(sa.text(sql), {"old": old, "new": new, **params})

    run(
        "UPDATE frameworks SET name = :new WHERE slug = :slug AND name = :old",
        NAME,
        slug=FRAMEWORK,
    )
    for pair in DESCRIPTIONS:
        run(
            "UPDATE frameworks SET description = replace(description, :old, :new)"
            " WHERE strpos(description, :old) > 0",
            pair,
        )
    # Keen's editorial summaries are the only metadata with curly apostrophes.
    # They are found by their source before that source is renamed or restored.
    source = EDITORIAL[0] if forward else EDITORIAL[1]
    for table in METADATA_TABLES:
        run(
            f"UPDATE {table} SET metadata = replace(metadata::text, :old, :new)::jsonb"
            " WHERE metadata->>'catalogue_source' = :source"
            " AND strpos(metadata::text, :old) > 0",
            EDITORIAL_APOSTROPHE,
            source=source,
        )
        for pair in METADATA:
            run(
                f"UPDATE {table} SET metadata = replace(metadata::text, :old, :new)::jsonb"
                " WHERE strpos(metadata::text, :old) > 0",
                pair,
            )
    run(
        "UPDATE cross_framework_control_links"
        " SET rationale = :new || substr(rationale, length(:old) + 1)"
        " WHERE left(rationale, length(:old)) = :old",
        RATIONALE,
    )


def upgrade():
    _apply(forward=True)


def downgrade():
    _apply(forward=False)
