"""Seed the shipped framework catalogues without replacing administrator edits.

Revision ID: 0074_seed_frameworks
Revises: 0073_native_documents

The bundled JSON is a build-time snapshot: migrations never fetch live URLs.
The auditor-donated controls are tagged with their original source. This seed
does not claim that those controls are ISO or IASME's licensed normative text.
"""
import json
import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0074_seed_frameworks"
down_revision = "0073_native_documents"
branch_labels = None
depends_on = None

DATA = Path(__file__).resolve().parents[1] / "seed_frameworks.json"


def upgrade():
    seed = json.loads(DATA.read_text(encoding="utf-8"))
    bind = op.get_bind()
    framework_insert = sa.text("""
        INSERT INTO frameworks (id, slug, name, version, description, upstream_url, created_at)
        VALUES (CAST(:id AS UUID), :slug, :name, :version, :description, :url, NOW())
        ON CONFLICT (slug) DO UPDATE SET
            name=COALESCE(NULLIF(frameworks.name, ''), EXCLUDED.name),
            version=COALESCE(NULLIF(frameworks.version, ''), EXCLUDED.version),
            description=COALESCE(NULLIF(frameworks.description, ''), EXCLUDED.description),
            upstream_url=COALESCE(NULLIF(frameworks.upstream_url, ''), EXCLUDED.upstream_url)
    """)
    control_insert = sa.text("""
        INSERT INTO control_items (id, framework_slug, type, ref, title, in_scope, tags, metadata, created_at)
        VALUES (CAST(:id AS UUID), :slug, :kind, :ref, :title, TRUE, CAST(:tags AS JSONB), CAST(:meta AS JSONB), NOW())
        ON CONFLICT (framework_slug, type, ref) DO UPDATE SET
            title=COALESCE(NULLIF(control_items.title, ''), EXCLUDED.title),
            metadata=COALESCE(control_items.metadata, '{}'::jsonb) ||
                CASE WHEN COALESCE(control_items.metadata->>'upstream_url', '') = ''
                     THEN jsonb_build_object('upstream_url', EXCLUDED.metadata->>'upstream_url')
                     ELSE '{}'::jsonb END
    """)
    clause_insert = sa.text("""
        INSERT INTO framework_clauses
            (id, framework_slug, ref, title, parent_clause_id, sort_order, metadata, created_at, updated_at)
        VALUES (CAST(:id AS UUID), :slug, :ref, :title,
            (SELECT id FROM framework_clauses WHERE framework_slug=:slug AND ref=:parent_ref),
            :order, CAST(:meta AS JSONB), NOW(), NOW())
        ON CONFLICT (framework_slug, ref) DO UPDATE SET
            title=COALESCE(NULLIF(framework_clauses.title, ''), EXCLUDED.title),
            metadata=COALESCE(framework_clauses.metadata, '{}'::jsonb) ||
                CASE WHEN COALESCE(framework_clauses.metadata->>'upstream_url', '') = ''
                     THEN jsonb_build_object('upstream_url', EXCLUDED.metadata->>'upstream_url')
                     ELSE '{}'::jsonb END
    """)
    for slug, framework in seed.items():
        bind.execute(framework_insert, dict(
            id=str(uuid.uuid4()), slug=slug, name=framework['name'],
            version=framework.get('version'), description=framework.get('description'),
            url=framework.get('upstream_url'),
        ))
        for order, item in enumerate(framework.get('clauses', []), start=1):
            meta=item.get('metadata') or {}
            bind.execute(clause_insert, dict(
                id=str(uuid.uuid4()), slug=slug, ref=item['ref'], title=item['title'],
                parent_ref=meta.get('parent_ref'), order=order,
                meta=json.dumps(meta, ensure_ascii=False),
            ))
            # The framework editor also represents clauses as control items.
            bind.execute(control_insert, dict(
                id=str(uuid.uuid4()), slug=slug, kind='clause', ref=item['ref'],
                title=item['title'], tags='{}', meta=json.dumps(meta, ensure_ascii=False),
            ))
        for item in framework['controls']:
            bind.execute(control_insert, dict(
                id=str(uuid.uuid4()), slug=slug, kind=item['type'], ref=item['ref'],
                title=item.get('title') or item['ref'],
                tags=json.dumps(item.get('tags') or {}, ensure_ascii=False),
                meta=json.dumps(item.get('metadata') or {}, ensure_ascii=False),
            ))
    # Correct the earlier ISO seed's missing 6.1 parent. Only default 6-parent
    # links are moved; existing custom hierarchy stays as the admin set it.
    bind.execute(sa.text("""
        UPDATE framework_clauses AS child SET parent_clause_id = parent.id
        FROM framework_clauses AS parent
        WHERE child.framework_slug = 'ISO27001:2022'
          AND parent.framework_slug = child.framework_slug
          AND parent.ref = '6.1'
          AND child.ref IN ('6.1.1', '6.1.2', '6.1.3')
          AND child.parent_clause_id = (
              SELECT old.id FROM framework_clauses AS old
              WHERE old.framework_slug = child.framework_slug AND old.ref = '6'
          )
    """))
    bind.execute(sa.text("""
        UPDATE control_items SET metadata = jsonb_set(metadata, '{parent_ref}', '"6.1"')
        WHERE framework_slug = 'ISO27001:2022' AND type = 'clause'
          AND ref IN ('6.1.1', '6.1.2', '6.1.3')
          AND metadata->>'parent_ref' = '6'
    """))


def downgrade():
    # Seeded rows may have since acquired evidence mappings or manual edits.
    # Removing them during a schema downgrade would silently destroy that work.
    pass
