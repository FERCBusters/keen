"""Interested parties are organisation-wide records.

An interested party, such as customers or a regulator, is one record for the
organisation; the controls that address it belong to a framework, and those
links already carry their framework. Parties that were recorded once per
framework are merged into one record, keeping the earliest, with their control
links, communications, risk links, audit samples and change history.
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_parties_org_wide"
down_revision = "0015_risk_wizard"
branch_labels = None
depends_on = None


def _merge_duplicates(bind) -> None:
    groups = bind.execute(
        sa.text(
            """SELECT name_id, nature_id
               FROM interested_parties
               GROUP BY name_id, nature_id HAVING COUNT(*) > 1"""
        )
    ).all()
    for name_id, nature_id in groups:
        rows = bind.execute(
            sa.text(
                """SELECT id, note FROM interested_parties
                   WHERE name_id = :name_id AND nature_id = :nature_id
                   ORDER BY created_at, id"""
            ),
            {"name_id": name_id, "nature_id": nature_id},
        ).all()
        survivor, note = rows[0]
        notes = [note.strip()] if (note or "").strip() else []
        for duplicate, duplicate_note in rows[1:]:
            text = (duplicate_note or "").strip()
            if text and text not in notes:
                notes.append(text)
            params = {"survivor": survivor, "duplicate": duplicate}
            bind.execute(
                sa.text(
                    """INSERT INTO interested_party_control_links
                       (interested_party_id, framework_slug, control_item_id, created_at)
                       SELECT :survivor, l.framework_slug, l.control_item_id, l.created_at
                       FROM interested_party_control_links l
                       WHERE l.interested_party_id = :duplicate
                         AND NOT EXISTS (
                           SELECT 1 FROM interested_party_control_links s
                           WHERE s.interested_party_id = :survivor
                             AND s.framework_slug = l.framework_slug
                             AND s.control_item_id = l.control_item_id)"""
                ),
                params,
            )
            bind.execute(
                sa.text(
                    """DELETE FROM interested_party_control_links
                       WHERE interested_party_id = :duplicate"""
                ),
                params,
            )
            bind.execute(
                sa.text(
                    """UPDATE interested_party_communications
                       SET interested_party_id = :survivor
                       WHERE interested_party_id = :duplicate"""
                ),
                params,
            )
            bind.execute(
                sa.text(
                    """DELETE FROM risk_interested_parties
                       WHERE interested_party_id = :duplicate
                         AND risk_id IN (
                           SELECT risk_id FROM risk_interested_parties
                           WHERE interested_party_id = :survivor)"""
                ),
                params,
            )
            bind.execute(
                sa.text(
                    """UPDATE risk_interested_parties
                       SET interested_party_id = :survivor
                       WHERE interested_party_id = :duplicate"""
                ),
                params,
            )
            bind.execute(
                sa.text(
                    """DELETE FROM audit_evidence
                       WHERE entity_type = 'interested_party' AND entity_id = :duplicate
                         AND audit_id IN (
                           SELECT audit_id FROM audit_evidence
                           WHERE entity_type = 'interested_party'
                             AND entity_id = :survivor)"""
                ),
                params,
            )
            bind.execute(
                sa.text(
                    """UPDATE audit_evidence SET entity_id = :survivor
                       WHERE entity_type = 'interested_party' AND entity_id = :duplicate"""
                ),
                params,
            )
            bind.execute(
                sa.text(
                    """UPDATE entity_changelogs SET entity_id = :survivor
                       WHERE entity_type = 'interested_party' AND entity_id = :duplicate"""
                ),
                params,
            )
            bind.execute(
                sa.text("DELETE FROM interested_parties WHERE id = :duplicate"), params
            )
        bind.execute(
            sa.text("UPDATE interested_parties SET note = :note WHERE id = :survivor"),
            {"note": "\n\n".join(notes), "survivor": survivor},
        )


def upgrade():
    bind = op.get_bind()
    _merge_duplicates(bind)
    op.drop_constraint(
        "uq_interested_parties_framework_name_nature", "interested_parties"
    )
    op.drop_index("ix_interested_parties_framework_slug", "interested_parties")
    op.drop_column("interested_parties", "framework_slug")
    op.create_unique_constraint(
        "uq_interested_parties_name_nature",
        "interested_parties",
        ["name_id", "nature_id"],
    )


def downgrade():
    # A party returns to the framework of its first control link, or to no
    # framework; the merge of duplicate records is not undone.
    op.drop_constraint("uq_interested_parties_name_nature", "interested_parties")
    op.add_column(
        "interested_parties",
        sa.Column(
            "framework_slug", sa.String(length=64), nullable=False, server_default=""
        ),
    )
    op.execute(
        """UPDATE interested_parties p SET framework_slug = COALESCE(
             (SELECT l.framework_slug FROM interested_party_control_links l
              WHERE l.interested_party_id = p.id
              ORDER BY l.created_at LIMIT 1), '')"""
    )
    op.create_index(
        "ix_interested_parties_framework_slug", "interested_parties", ["framework_slug"]
    )
    op.create_unique_constraint(
        "uq_interested_parties_framework_name_nature",
        "interested_parties",
        ["framework_slug", "name_id", "nature_id"],
    )
