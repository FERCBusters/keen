"""Keep each account's login key: a digest of the username sign-in compares.

Sign-in limits, recognised browsers and account creation treat usernames that
differ only in case, width, spacing or invisible characters as one name. The
key lets the accounts a name compares equal to be found by an indexed read,
so working out a sign-in's limits costs the same whatever the name and however
many accounts there are. Every existing account's key is computed from its
username.
"""

import sqlalchemy as sa
from alembic import op
from myf.security.names import login_key

revision = "0057_user_login_keys"
down_revision = "0056_osa_snapshot_2026_11"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("login_key", sa.String(64), nullable=True))
    bind = op.get_bind()
    users = sa.table(
        "users", sa.column("id"), sa.column("username"), sa.column("login_key")
    )
    for account_id, username in bind.execute(sa.select(users.c.id, users.c.username)):
        bind.execute(
            users.update()
            .where(users.c.id == account_id)
            .values(login_key=login_key(username))
        )
    op.alter_column("users", "login_key", nullable=False)
    op.create_index("ix_users_login_key", "users", ["login_key"])


def downgrade():
    op.drop_index("ix_users_login_key", table_name="users")
    op.drop_column("users", "login_key")
