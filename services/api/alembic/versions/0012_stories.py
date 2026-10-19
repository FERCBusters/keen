"""Stories: curated sets of events, records and notes with a timeline and flow chart.

Three permissions arrive with the tables: stories.read views stories and adds
comments, stories.manage creates stories and edits the ones its holder created,
and stories.admin edits or deletes any story.
"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "0012_stories"
down_revision = "0011_retire_unused_counters"
branch_labels = None
depends_on = None

PERMISSIONS = (
    ("stories.read", "View stories and add comments to them."),
    (
        "stories.manage",
        "Create stories and edit or delete the stories you created. "
        "Adding an item requires read access to it.",
    ),
    ("stories.admin", "Edit or delete any story."),
)


def upgrade():
    op.execute("""CREATE TABLE stories (
        id uuid PRIMARY KEY, title varchar(256) NOT NULL,
        summary text NOT NULL DEFAULT '', status varchar(16) NOT NULL DEFAULT 'open',
        version integer NOT NULL DEFAULT 1,
        created_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
        created_at timestamp NOT NULL, updated_at timestamp NOT NULL,
        CONSTRAINT ck_stories_status CHECK (status IN ('open','closed'))
    )""")
    op.create_index("ix_stories_status", "stories", ["status"])
    op.create_index("ix_stories_updated_at", "stories", ["updated_at"])
    op.create_index("ix_stories_created_by_user_id", "stories", ["created_by_user_id"])
    op.execute("""CREATE TABLE story_items (
        id uuid PRIMARY KEY,
        story_id uuid NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
        kind varchar(24) NOT NULL, entity_id uuid,
        label varchar(256) NOT NULL DEFAULT '', note text NOT NULL DEFAULT '',
        occurred_at timestamp, x integer NOT NULL DEFAULT 0, y integer NOT NULL DEFAULT 0,
        position integer NOT NULL DEFAULT 0,
        CONSTRAINT ck_story_item_kind CHECK (kind IN ('event','risk','document','asset','objective','note')),
        CONSTRAINT ck_story_item_entity CHECK ((kind = 'note') = (entity_id IS NULL)),
        CONSTRAINT uq_story_item_entity UNIQUE (story_id, kind, entity_id)
    )""")
    op.create_index("ix_story_items_story_id", "story_items", ["story_id"])
    op.create_index("ix_story_items_entity", "story_items", ["kind", "entity_id"])
    op.execute("""CREATE TABLE story_links (
        id uuid PRIMARY KEY,
        story_id uuid NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
        from_item_id uuid NOT NULL REFERENCES story_items(id) ON DELETE CASCADE,
        to_item_id uuid NOT NULL REFERENCES story_items(id) ON DELETE CASCADE,
        label varchar(128) NOT NULL DEFAULT '',
        CONSTRAINT ck_story_link_ends CHECK (from_item_id <> to_item_id),
        CONSTRAINT uq_story_link UNIQUE (story_id, from_item_id, to_item_id)
    )""")
    op.create_index("ix_story_links_from_item_id", "story_links", ["from_item_id"])
    op.create_index("ix_story_links_to_item_id", "story_links", ["to_item_id"])
    op.execute("""CREATE TABLE story_comments (
        id uuid PRIMARY KEY,
        story_id uuid NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
        body text NOT NULL,
        created_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
        created_at timestamp NOT NULL
    )""")
    op.create_index(
        "ix_story_comments_story_created", "story_comments", ["story_id", "created_at"]
    )
    for code, description in PERMISSIONS:
        op.execute(
            sa.text("""
            INSERT INTO permissions (id, code, description, created_at)
            SELECT :id, :code, :description, CURRENT_TIMESTAMP
            WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :code)
        """).bindparams(id=str(uuid.uuid4()), code=code, description=description)
        )


def downgrade():
    for code, _ in PERMISSIONS:
        op.execute(
            sa.text("DELETE FROM permissions WHERE code = :code").bindparams(code=code)
        )
    op.drop_table("story_comments")
    op.drop_table("story_links")
    op.drop_table("story_items")
    op.drop_table("stories")
