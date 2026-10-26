"""Retire the purple theme; land accounts on the sand default.

Keen's default UI palette moved from the purple family to an interim ink
default and then to the warm sand family. Accounts stored under the retired
purple id, or still carried by the interim ink default at this revision,
re-point to sand so nobody is left on a palette the UI no longer offers.
The ink palettes remain selectable from the theme dropdown.
"""

from alembic import op

revision = "0020_theme_ink"
down_revision = "0019_quizzes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE users SET pref_theme = 'sand' WHERE pref_theme = 'purple'")
    op.execute("UPDATE users SET pref_theme = 'sand' WHERE pref_theme = 'ink'")
    op.execute("ALTER TABLE users ALTER COLUMN pref_theme SET DEFAULT 'sand'")


def downgrade() -> None:
    op.execute("UPDATE users SET pref_theme = 'ink' WHERE pref_theme = 'sand'")
    op.execute("ALTER TABLE users ALTER COLUMN pref_theme SET DEFAULT 'ink'")
