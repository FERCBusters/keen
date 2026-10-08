"""Restore collection provenance for existing Forgejo API events."""
from alembic import op
revision = "0082_forgejo_feed_identity"
down_revision = "0081_keen_agent"
branch_labels = None
depends_on = None

def upgrade():
    # Use the actual recorded feed URL; do not guess from host/repository names.
    op.execute("""
        UPDATE events SET raw_pointer = jsonb_set(
            raw_pointer, '{forgejo,feed}', normalized_payload->'feed_url', true)
        WHERE source = 'forgejo'
          AND jsonb_typeof(raw_pointer->'forgejo') = 'object'
          AND raw_pointer->'forgejo'->>'feed' IS NULL
          AND jsonb_typeof(normalized_payload->'feed_url') = 'string'
          AND normalized_payload->>'feed_url' <> ''
    """)

def downgrade():
    # Provenance is useful evidence metadata and is intentionally retained.
    pass
