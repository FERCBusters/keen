"""Legal register: categories, items, assessments and links to other records.

Revision ID: 0059_isms_legal_register
Revises: 0058_isms_document_diagrams
"""

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0059_isms_legal_register"
down_revision = "0058_isms_document_diagrams"
branch_labels = None
depends_on = None

# Categories a register starts with; people rename, add and remove them.
CATEGORIES = (
    (
        "Data protection and privacy",
        "Laws on collecting, holding, using and disclosing personal data.",
    ),
    (
        "Information security and computer misuse",
        "Laws on unauthorised access to, and interference with, computer systems.",
    ),
    (
        "Intellectual property",
        "Copyright, database rights, designs, patents and trade marks.",
    ),
    (
        "Investigatory powers and law enforcement",
        "Retention and disclosure of information for investigations.",
    ),
    (
        "Corporate governance",
        "Company law, directors' duties, reporting and anti-bribery law.",
    ),
    (
        "Financial regulation",
        "Authorisation, conduct and reporting rules for financial services.",
    ),
    (
        "Consumer rights",
        "Quality, fitness for purpose, fair terms and remedies for consumers.",
    ),
    ("Product safety", "Safety, market surveillance and recall of products."),
    ("Labour law", "Employment, working conditions and modern slavery law."),
    ("Fire safety", "Building and fire safety duties."),
    (
        "Aviation and aerospace",
        "Quality and safety requirements for aviation, space and defence.",
    ),
    (
        "Industry-specific regulation",
        "Rules of the sector the organisation works in or supplies.",
    ),
    (
        "Professional and licensing requirements",
        "Licences, registrations and authorisations the organisation must hold.",
    ),
    (
        "Contractual requirements",
        "Security and compliance obligations in customer and supplier contracts.",
    ),
)


def upgrade():
    categories = op.create_table(
        "isms_legal_categories",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_isms_legal_categories_name",
        "isms_legal_categories",
        [sa.text("lower(name)")],
        unique=True,
    )
    op.create_table(
        "isms_legal_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=True),
        sa.Column("category_id", sa.UUID(), nullable=True),
        sa.Column("jurisdiction", sa.String(length=128), nullable=False),
        sa.Column("authority", sa.String(length=256), nullable=False),
        sa.Column("requirement", sa.Text(), nullable=False),
        sa.Column("applicability", sa.Text(), nullable=False),
        sa.Column("in_scope", sa.Boolean(), nullable=False),
        sa.Column("owner_user_id", sa.UUID(), nullable=True),
        sa.Column("compliance_status", sa.String(length=24), nullable=False),
        sa.Column("last_assessed_date", sa.Date(), nullable=True),
        sa.Column("next_assessment_date", sa.Date(), nullable=True),
        sa.Column("reminded_at", sa.DateTime(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "compliance_status in ('not_assessed','compliant',"
            "'partially_compliant','non_compliant')",
            name="ck_legal_item_status",
        ),
        sa.ForeignKeyConstraint(
            ["category_id"], ["isms_legal_categories.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "category_id",
        "in_scope",
        "owner_user_id",
        "compliance_status",
        "next_assessment_date",
    ):
        op.create_index(
            op.f("ix_isms_legal_items_" + column), "isms_legal_items", [column]
        )
    op.create_table(
        "isms_legal_assessments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("assessed_on", sa.Date(), nullable=False),
        sa.Column("assessed_by_user_id", sa.UUID(), nullable=True),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "outcome in ('compliant','partially_compliant','non_compliant')",
            name="ck_legal_assessment_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["isms_legal_items.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["assessed_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_isms_legal_assessments_item_id"), "isms_legal_assessments", ["item_id"]
    )
    op.create_table(
        "isms_legal_item_links",
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("target_type", sa.String(length=24), nullable=False),
        sa.Column("target_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "target_type in ('document','asset','risk')",
            name="ck_legal_item_link_type",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["isms_legal_items.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("item_id", "target_type", "target_id"),
    )
    op.create_index(
        "ix_legal_item_link_target",
        "isms_legal_item_links",
        ["target_type", "target_id"],
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    op.bulk_insert(
        categories,
        [
            {
                "id": uuid.uuid4(),
                "name": name,
                "description": description,
                "created_at": now,
                "updated_at": now,
            }
            for name, description in CATEGORIES
        ],
    )


def downgrade():
    op.drop_table("isms_legal_item_links")
    op.drop_table("isms_legal_assessments")
    op.drop_table("isms_legal_items")
    op.drop_table("isms_legal_categories")
