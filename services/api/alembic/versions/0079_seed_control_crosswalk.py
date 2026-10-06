"""Seed reviewed, directed control evidence links once; preserve administrator edits."""
import json
import uuid
from pathlib import Path
import sqlalchemy as sa
from alembic import op

revision = "0079_control_crosswalk"
down_revision = "0078_library_control_links"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    seed = json.loads((Path(__file__).resolve().parents[1] / 'seed_control_links.json').read_text())
    for link in seed['links']:
        # Ref+type+title/description guards avoid silently linking repurposed catalogue entries.
        ids = []
        for side in ('source', 'target'):
            row = bind.execute(sa.text('''SELECT id FROM control_items
                WHERE framework_slug=:framework AND type=:type AND ref=:ref
                  AND title=:title AND COALESCE(metadata->>'description', '')=:description'''),
                {key: link[f'{side}_{key}'] for key in ('framework','ref','type','title','description')}).scalar_one_or_none()
            ids.append(row)
        if None in ids:
            continue
        bind.execute(sa.text('''INSERT INTO cross_framework_control_links
            (id, source_control_id, target_control_id, rationale, created_at)
            VALUES (:id, :source, :target, :rationale, NOW())
            ON CONFLICT (source_control_id, target_control_id) DO NOTHING'''),
            dict(id=uuid.uuid4().hex, source=ids[0], target=ids[1],
                 rationale='KEEN starter crosswalk v1 ['+link['scope']+']: '+link['rationale']))
    # Evidence is inherited at query time; no historical Mapping rows are duplicated.


def downgrade():
    # Leave reviewed links intact; users can remove them in the existing control-link UI.
    pass
