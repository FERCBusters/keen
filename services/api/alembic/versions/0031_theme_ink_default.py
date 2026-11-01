"""Make Ink the default theme for new accounts.

Accounts keep the theme they have stored, Sand included; only accounts
created from now on start on Ink.
"""

from alembic import op

revision = "0031_theme_ink_default"
down_revision = "0030_agent_server_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ALTER COLUMN pref_theme SET DEFAULT 'ink'")


def downgrade() -> None:
    op.execute("ALTER TABLE users ALTER COLUMN pref_theme SET DEFAULT 'sand'")
