"""Seed HIPAA regulations and the attributed OSA SOC 2 catalogue.

Revision ID: 0089_hipaa_soc2
Revises: 0088_ldap_risk_roles

Offline snapshots; insert missing records only, preserving administrator edits.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa

revision = "0089_hipaa_soc2"
down_revision = "0088_ldap_risk_roles"
branch_labels = None
depends_on = None

DATA = Path(__file__).resolve().parents[1] / "catalogues"


def seed_catalogues(bind):
    # Local table declarations freeze this migration independently of app models.
    metadata = sa.MetaData()
    frameworks = sa.Table(
        "frameworks",
        metadata,
        sa.Column("id", sa.Uuid),
        sa.Column("slug", sa.String),
        sa.Column("name", sa.String),
        sa.Column("version", sa.String),
        sa.Column("description", sa.Text),
        sa.Column("upstream_url", sa.Text),
        sa.Column("created_at", sa.DateTime),
    )
    controls = sa.Table(
        "control_items",
        metadata,
        sa.Column("id", sa.Uuid),
        sa.Column("framework_slug", sa.String),
        sa.Column("type", sa.String),
        sa.Column("ref", sa.String),
        sa.Column("title", sa.String),
        sa.Column("in_scope", sa.Boolean),
        sa.Column("tags", sa.JSON),
        sa.Column("metadata", sa.JSON),
        sa.Column("created_at", sa.DateTime),
    )
    clauses = sa.Table(
        "framework_clauses",
        metadata,
        sa.Column("id", sa.Uuid),
        sa.Column("framework_slug", sa.String),
        sa.Column("ref", sa.String),
        sa.Column("title", sa.String),
        sa.Column("parent_clause_id", sa.Uuid),
        sa.Column("sort_order", sa.Integer),
        sa.Column("metadata", sa.JSON),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for filename in ("soc2-tsc-2017.json", "hipaa-2026.json"):
        for slug, framework in json.loads((DATA / filename).read_text()).items():
            if (
                bind.execute(
                    sa.select(frameworks.c.id).where(frameworks.c.slug == slug)
                ).first()
                is None
            ):
                bind.execute(
                    frameworks.insert().values(
                        id=uuid.uuid4(),
                        slug=slug,
                        **{
                            k: framework[k]
                            for k in ("name", "version", "description", "upstream_url")
                        },
                        created_at=now,
                    )
                )
            existing = set(
                bind.execute(
                    sa.select(controls.c.type, controls.c.ref).where(
                        controls.c.framework_slug == slug
                    )
                )
            )
            parents = dict(
                bind.execute(
                    sa.select(clauses.c.ref, clauses.c.id).where(
                        clauses.c.framework_slug == slug
                    )
                ).all()
            )
            additions = []
            for item in framework["clauses"] + framework["controls"]:
                meta = item["metadata"]
                if item["type"] == "clause" and item["ref"] not in parents:
                    parent_ref = meta.get("parent_ref")
                    if parent_ref and parent_ref not in parents:
                        raise ValueError(f"Missing seeded parent: {slug} {parent_ref}")
                    ident = uuid.uuid4()
                    bind.execute(
                        clauses.insert().values(
                            id=ident,
                            framework_slug=slug,
                            ref=item["ref"],
                            title=item["title"],
                            metadata=meta,
                            parent_clause_id=parents.get(parent_ref),
                            sort_order=meta["sort_order"],
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    parents[item["ref"]] = ident
                if (item["type"], item["ref"]) not in existing:
                    additions.append(
                        dict(
                            id=uuid.uuid4(),
                            framework_slug=slug,
                            type=item["type"],
                            ref=item["ref"],
                            title=item["title"],
                            in_scope=item["in_scope"],
                            tags=item.get("tags", {}),
                            metadata=meta,
                            created_at=now,
                        )
                    )
            if additions:
                bind.execute(controls.insert(), additions)


def upgrade():
    pass  # Superseded by 0090; keep the revision for already provisioned instances.


def downgrade():
    # Seeded records can have evidence and audit links. Keep those records intact.
    pass
