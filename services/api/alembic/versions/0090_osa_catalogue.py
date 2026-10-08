"""Replace pre-production framework catalogues with the frozen OSA catalogue.

Offline, intentionally canonical reset. Subsequent startups never re-import it.
OSA data and KEEN's adaptation: CC BY-SA 4.0; see catalogues/OSA-LICENSE.txt.
"""
import json
import uuid
from datetime import datetime
from pathlib import Path
from alembic import op
import sqlalchemy as sa

revision = '0090_osa_catalogue'
down_revision = '0089_hipaa_soc2'
branch_labels = None
depends_on = None
DATA = Path(__file__).resolve().parents[1] / 'catalogues/osa-2026-10-08.json'
KEEP = ('KEEN-AF:1.0', 'UK-DVSTF:1.0', 'CYBER-ESSENTIALS:2026')


def seed_catalogue(bind):
    data = json.loads(DATA.read_text())
    metadata = sa.MetaData()
    # Frozen declarations: migration does not depend on evolving application models.
    def table(name, *columns):
        return sa.Table(name, metadata, *(sa.Column(n, t) for n, t in columns))
    table('frameworks', ('id', sa.Uuid), ('slug', sa.String), ('name', sa.String),
          ('version', sa.String), ('description', sa.Text), ('upstream_url', sa.Text), ('created_at', sa.DateTime))
    table('control_items', ('id', sa.Uuid), ('framework_slug', sa.String), ('type', sa.String),
          ('ref', sa.String), ('title', sa.String), ('in_scope', sa.Boolean), ('tags', sa.JSON),
          ('metadata', sa.JSON), ('created_at', sa.DateTime))
    table('framework_clauses', ('id', sa.Uuid), ('framework_slug', sa.String), ('ref', sa.String),
          ('title', sa.String), ('parent_clause_id', sa.Uuid), ('sort_order', sa.Integer),
          ('metadata', sa.JSON), ('created_at', sa.DateTime), ('updated_at', sa.DateTime))
    table('mappings', ('control_item_id', sa.Uuid))
    table('osa_control_mappings', ('control_id', sa.Uuid), ('nist_ref', sa.String))
    table('cross_framework_control_links', ('id', sa.Uuid), ('source_control_id', sa.Uuid),
          ('target_control_id', sa.Uuid), ('rationale', sa.Text), ('created_at', sa.DateTime))
    table('managed_configurations', ('name', sa.String), ('document', sa.JSON), ('version', sa.Integer))
    frameworks, controls, clauses = (metadata.tables[t] for t in ('frameworks', 'control_items', 'framework_clauses'))
    membership = metadata.tables['osa_control_mappings']
    obsolete = sa.and_(controls.c.framework_slug.not_in(KEEP), ~controls.c.framework_slug.like('RISKLEDGER:%'))
    old_ids = sa.select(controls.c.id).where(obsolete)
    # mappings predates ON DELETE CASCADE. Other references carry CASCADE/SET NULL.
    mappings = metadata.tables['mappings']
    bind.execute(mappings.delete().where(mappings.c.control_item_id.in_(old_ids)))
    bind.execute(controls.delete().where(obsolete))
    bind.execute(clauses.delete().where(clauses.c.framework_slug.not_in(KEEP), ~clauses.c.framework_slug.like('RISKLEDGER:%')))
    bind.execute(frameworks.delete().where(frameworks.c.slug.not_in(KEEP), ~frameworks.c.slug.like('RISKLEDGER:%')))
    now = datetime.utcnow()
    for framework in data['frameworks']:
        slug = framework['id']
        url = 'https://www.opensecurityarchitecture.org/frameworks/' + framework['slug'] + '/'
        bind.execute(frameworks.insert().values(id=uuid.uuid4(), slug=slug, name=framework['name'],
            version=framework['metadata'].get('version'),
            description=framework['description'] + '\n\nOpen Security Architecture, CC BY-SA 4.0. '
                'Coverage is OSA’s assessment; consult the framework’s normative text. Offline snapshot 2026-10-08.',
            upstream_url=url, created_at=now))
        rows, memberships = [], []
        refs = {item['id'] for item in framework['clauses']}
        for order, item in enumerate(framework['clauses']):
            ref = item['id']
            ident = uuid.uuid5(uuid.NAMESPACE_URL, 'https://www.opensecurityarchitecture.org/' + slug + '/' + ref)
            # Only use an ancestor that actually exists upstream. Never invent clauses.
            parents = [r for r in refs if ref.startswith(r + '.') or ref.startswith(r + '-')]
            parent = max(parents, key=len) if parents else None
            description = '\n\n'.join(filter(None, [item['title'], item.get('rationale'),
                'Coverage gaps: ' + item['gaps'] if item.get('gaps') else '',
                'OSA estimated coverage: ' + str(item['coverage_pct']) + '%' if 'coverage_pct' in item else '']))
            meta = {**item, 'description': description, 'parent_ref': parent, 'sort_order': order,
                    'upstream_url': url, 'source': data['source'], 'license': data['license'],
                    'license_url': data['license_url'], 'source_commit': data['source_commit'],
                    'osa_nist_controls': item['controls'], 'osa_framework_metadata': framework['metadata']}
            is_clause = slug == 'iso_27001_2022' and not ref.startswith('A.')
            if is_clause:
                bind.execute(clauses.insert().values(id=ident, framework_slug=slug, ref=ref,
                    title=item['title'][:256], parent_clause_id=None, sort_order=order, metadata=meta,
                    created_at=now, updated_at=now))
            rows.append(dict(id=ident, framework_slug=slug, type='clause' if is_clause else 'annex_control', ref=ref,
                title=item['title'][:256], in_scope=True, tags={}, metadata=meta, created_at=now))
            memberships.extend(dict(control_id=ident, nist_ref=nist) for nist in sorted(set(item['controls'])))
        if rows:
            bind.execute(controls.insert(), rows)
        if memberships:
            bind.execute(membership.insert(), memberships)
    # Retain KEEN's separately reviewed starter bridges to its own catalogues.
    # These are explicitly attributed to KEEN, never to OSA.
    links = metadata.tables['cross_framework_control_links']
    lookup = {(row.framework_slug, row.ref): row for row in bind.execute(sa.select(controls)).mappings()}
    existing = set(bind.execute(sa.select(links.c.source_control_id, links.c.target_control_id)).all())
    starter = json.loads((DATA.parents[1] / 'seed_control_links.json').read_text())
    additions = []
    for link in starter['links']:
        pair = []
        for side in ('source', 'target'):
            slug = link[side + '_framework'].replace('ISO27001:2022', 'iso_27001_2022')
            row = lookup.get((slug, link[side + '_ref']))
            if row is None:
                break
            if slug in KEEP and (row['title'] != link[side + '_title'] or
                    (row['metadata'] or {}).get('description', '') != link[side + '_description']):
                break
            pair.append(row['id'])
        if len(pair) == 2 and tuple(pair) not in existing:
            additions.append(dict(id=uuid.uuid4(), source_control_id=pair[0], target_control_id=pair[1],
                rationale='KEEN starter crosswalk [' + link['scope'] + ']: ' + link['rationale'], created_at=now))
            existing.add(tuple(pair))
    if additions:
        bind.execute(links.insert(), additions)
    # Ensure Risk Ledger remains discoverable before its first successful collection.
    if not bind.execute(sa.select(frameworks.c.id).where(frameworks.c.slug == 'RISKLEDGER:ASSESSMENT')).first():
        bind.execute(frameworks.insert().values(id=uuid.uuid4(), slug='RISKLEDGER:ASSESSMENT',
            name='Risk Ledger', description='Supplier assessment controls imported by the Risk Ledger ingester.', created_at=now))
    # Preserve supported selections; use the retained KEEN framework if none survive.
    config = metadata.tables['managed_configurations']
    row = bind.execute(sa.select(config).where(config.c.name == 'organisation-frameworks')).mappings().first()
    known = {f['id'] for f in data['frameworks']} | set(KEEP) | {'RISKLEDGER:ASSESSMENT'}
    if row:
        doc = dict(row['document'])
        enabled = [('iso_27001_2022' if s == 'ISO27001:2022' else s) for s in doc.get('enabled', [])]
        doc['enabled'] = sorted(set(enabled) & known) or ['KEEN-AF:1.0']
        default = 'iso_27001_2022' if doc.get('default') == 'ISO27001:2022' else doc.get('default')
        doc['default'] = default if default in doc['enabled'] else doc['enabled'][0]
        bind.execute(config.update().where(config.c.name == row['name']).values(document=doc, version=row['version']+1))
    # Update persisted rules' ISO machine name; unsupported frameworks are removed
    # at evaluation time by ensure_controls rather than resurrected as empty catalogues.
    row = bind.execute(sa.select(config).where(config.c.name == 'rules')).mappings().first()
    if row:
        document = json.loads(json.dumps(row['document']).replace('ISO27001:2022', 'iso_27001_2022'))
        bind.execute(config.update().where(config.c.name == 'rules').values(document=document, version=row['version']+1))


def upgrade():
    op.create_table('osa_control_mappings',
        sa.Column('control_id', sa.Uuid(), sa.ForeignKey('control_items.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('nist_ref', sa.String(64), primary_key=True))
    op.create_index('ix_osa_control_mappings_nist_ref', 'osa_control_mappings', ['nist_ref'])
    seed_catalogue(op.get_bind())


def downgrade():
    raise RuntimeError('The canonical catalogue reset cannot reconstruct deleted pre-production frameworks; restore a backup.')
