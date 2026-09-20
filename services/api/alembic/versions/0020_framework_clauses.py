"""framework clauses and control applicability links

Revision ID: 0020_framework_clauses
Revises: 0019_audittrail_perms
Create Date: 2026-05-19

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0020_framework_clauses"
down_revision = "0019_audittrail_perms"
branch_labels = None
depends_on = None

ISO_FRAMEWORK = "ISO27001:2022"

ISO_CLAUSES = [
    ("4", "Context of the Organisation", None),
    ("4.1", "Understanding the organisation and its context", "4"),
    ("4.2", "Understanding the needs and expectations of interested parties", "4"),
    ("4.3", "Determining the Scope of the ISMS", "4"),
    ("4.4", "Information Security Management System", "4"),
    ("5", "Leadership", None),
    ("5.1", "Leadership and Commitment", "5"),
    ("5.2", "Policy", "5"),
    ("5.3", "Organisational roles, responsibilities and authorities", "5"),
    ("6", "Planning", None),
    ("6.1.1", "General", "6"),
    ("6.1.2", "Information Security Risk Assessment", "6"),
    ("6.1.3", "Information Security Risk Treatment", "6"),
    ("6.2", "IS Objectives and plans to achieve them", "6"),
    ("6.3", "Planning of changes", "6"),
    ("7", "Support", None),
    ("7.1", "Resources", "7"),
    ("7.2", "Competence", "7"),
    ("7.3", "Awareness", "7"),
    ("7.4", "Communication", "7"),
    ("7.5", "Documented Information", "7"),
    ("7.5.1", "General", "7.5"),
    ("7.5.2", "Creating and updating", "7.5"),
    ("7.5.3", "Control of documented information", "7.5"),
    ("8", "Operation", None),
    ("8.1", "Operational Planning and Control", "8"),
    ("8.2", "Information Security Risk Assessment", "8"),
    ("8.3", "Information Security Risk Treatment", "8"),
    ("9", "Performance Evaluation", None),
    ("9.1", "Monitoring, measurement, analysis and evaluation", "9"),
    ("9.2", "Internal Audit", "9"),
    ("9.2.1", "General", "9.2"),
    ("9.2.2", "Internal Audit Programme", "9.2"),
    ("9.3", "Management Review", "9"),
    ("9.3.1", "General", "9.3"),
    ("9.3.2", "Management Review Inputs", "9.3"),
    ("9.3.3", "Management Review Results", "9.3"),
    ("10", "Improvement", None),
    ("10.1", "Continual Improvement", "10"),
    ("10.2", "Nonconformity and corrective action", "10"),
]


def _seed_iso_clauses() -> None:
    for idx, (ref, title, parent_ref) in enumerate(ISO_CLAUSES, start=1):
        if parent_ref:
            parent_expr = f"""
                (SELECT id FROM framework_clauses
                 WHERE framework_slug = '{ISO_FRAMEWORK}' AND ref = '{parent_ref}')
            """
        else:
            parent_expr = "NULL"
        op.execute(f"""
            INSERT INTO framework_clauses
                (id, framework_slug, ref, title, parent_clause_id, sort_order, metadata, created_at, updated_at)
            SELECT gen_random_uuid(), '{ISO_FRAMEWORK}', '{ref}', '{title.replace("'", "''")}',
                   {parent_expr}, {idx}, '{{}}'::jsonb, NOW(), NOW()
            WHERE NOT EXISTS (
                SELECT 1 FROM framework_clauses
                WHERE framework_slug = '{ISO_FRAMEWORK}' AND ref = '{ref}'
            );
        """)


def _migrate_existing_controlitem_clauses() -> None:
    """Preserve any pre-existing ControlItem rows of type='clause'.

    Early Keen installs represented clauses as control_items. The new schema has
    first-class framework_clauses, so copy those rows across if they do not
    already exist. They remain in control_items for compatibility with any
    existing mappings, but the new clause UI/API reads from framework_clauses.
    """
    op.execute("""
        INSERT INTO framework_clauses
            (id, framework_slug, ref, title, parent_clause_id, sort_order, metadata, created_at, updated_at)
        SELECT gen_random_uuid(), ci.framework_slug, ci.ref,
               COALESCE(NULLIF(ci.title, ''), ci.ref), NULL,
               100000, COALESCE(ci.metadata, '{}'::jsonb), NOW(), NOW()
        FROM control_items ci
        WHERE ci.type = 'clause'
          AND NOT EXISTS (
              SELECT 1 FROM framework_clauses fc
              WHERE fc.framework_slug = ci.framework_slug AND fc.ref = ci.ref
          );
    """)


def upgrade():
    op.create_table(
        "framework_clauses",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("framework_slug", sa.String(length=64), nullable=False),
        sa.Column("ref", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False, server_default=""),
        sa.Column(
            "parent_clause_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("framework_clauses.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.UniqueConstraint("framework_slug", "ref", name="uq_framework_clause_ref"),
    )
    op.create_index(
        "ix_framework_clauses_framework_slug", "framework_clauses", ["framework_slug"]
    )
    op.create_index("ix_framework_clauses_ref", "framework_clauses", ["ref"])
    op.create_index(
        "ix_framework_clauses_parent_clause_id",
        "framework_clauses",
        ["parent_clause_id"],
    )

    op.create_table(
        "control_clause_links",
        sa.Column(
            "control_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control_items.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "clause_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("framework_clauses.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("applicability", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.CheckConstraint(
            "applicability in ('applicable','partially_applicable')",
            name="ck_control_clause_links_applicability",
        ),
    )
    op.create_index(
        "ix_control_clause_links_control_item_id",
        "control_clause_links",
        ["control_item_id"],
    )
    op.create_index(
        "ix_control_clause_links_clause_id", "control_clause_links", ["clause_id"]
    )

    _seed_iso_clauses()
    _migrate_existing_controlitem_clauses()


def downgrade():
    op.drop_index(
        "ix_control_clause_links_clause_id", table_name="control_clause_links"
    )
    op.drop_index(
        "ix_control_clause_links_control_item_id", table_name="control_clause_links"
    )
    op.drop_table("control_clause_links")

    op.drop_index(
        "ix_framework_clauses_parent_clause_id", table_name="framework_clauses"
    )
    op.drop_index("ix_framework_clauses_ref", table_name="framework_clauses")
    op.drop_index("ix_framework_clauses_framework_slug", table_name="framework_clauses")
    op.drop_table("framework_clauses")
