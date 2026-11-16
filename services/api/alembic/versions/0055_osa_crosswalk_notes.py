"""A control's description is what the control requires, not a crosswalk note.

Catalogues imported from Open Security Architecture were seeded with a
description made of the control's title, OSA's note on how NIST SP 800-53
maps to it, the gaps OSA sees in that mapping and OSA's coverage estimate.
None of that describes the control. Each part is already held in its own
metadata field (``rationale``, ``gaps``, ``coverage_pct`` and
``osa_nist_controls``), which the control page now shows as a crosswalk to
NIST SP 800-53, so the composed description is removed.

Only a description that still reads exactly as it was seeded is removed: one
an administrator has written or changed stays. Downgrading composes the
description again for an OSA control that has none.
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0055_osa_crosswalk_notes"
down_revision = "0054_drop_unused_columns"
branch_labels = None
depends_on = None

OSA = "https://www.opensecurityarchitecture.org/"
TABLES = ("control_items", "framework_clauses")


def seeded_description(meta: dict) -> str:
    """The description the OSA import composed from a control's own metadata."""
    return "\n\n".join(
        part
        for part in (
            meta.get("title"),
            meta.get("rationale"),
            "Coverage gaps: " + meta["gaps"] if meta.get("gaps") else "",
            "OSA estimated coverage: " + str(meta["coverage_pct"]) + "%"
            if "coverage_pct" in meta
            else "",
        )
        if part
    )


def _rows(bind, table: str):
    return bind.execute(
        sa.text(
            f"SELECT id, metadata FROM {table}"  # noqa: S608 - fixed table names
            " WHERE metadata->>'source' = :osa"
        ),
        {"osa": OSA},
    ).all()


def _store(bind, table: str, changed: list[dict]) -> None:
    if changed:
        bind.execute(
            sa.text(
                f"UPDATE {table} SET metadata = CAST(:metadata AS jsonb)"  # noqa: S608
                " WHERE id = :id"
            ),
            changed,
        )


def upgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        changed = []
        for ident, meta in _rows(bind, table):
            if "description" in meta and meta["description"] == seeded_description(
                meta
            ):
                del meta["description"]
                changed.append({"id": ident, "metadata": json.dumps(meta)})
        _store(bind, table, changed)


def downgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        changed = []
        for ident, meta in _rows(bind, table):
            if not meta.get("description"):
                meta["description"] = seeded_description(meta)
                changed.append({"id": ident, "metadata": json.dumps(meta)})
        _store(bind, table, changed)
