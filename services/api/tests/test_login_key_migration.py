"""Migration 0057: every account keeps the login key of its username."""

import uuid

from alembic import command
from mosp.security.names import login_key
from sqlalchemy import text

from tests.test_link_migrations import release

BEFORE = "0056_osa_snapshot_2026_11"
AFTER = "0057_user_login_keys"


def test_each_account_gets_its_usernames_login_key_and_downgrade_removes_it():
    with release(BEFORE) as (conn, config):
        names = {
            "Alice": uuid.uuid4(),
            "ｂｏｂ": uuid.uuid4(),
            "ldap:carol": uuid.uuid4(),
        }
        for name, ident in names.items():
            conn.execute(
                text(
                    "INSERT INTO users (id, username, password_hash, created_at,"
                    " updated_at, pref_auto_apply_filters, pref_source_colors,"
                    " pref_landing_page, pref_date_format, authz_version)"
                    " VALUES (:id, :name, 'unused', now(), now(), false, '{}',"
                    " '/', 'dmy', 0)"
                ),
                {"id": ident, "name": name},
            )
        conn.commit()

        command.upgrade(config, AFTER)
        keys = dict(conn.execute(text("SELECT username, login_key FROM users")).all())
        assert keys == {name: login_key(name) for name in names}
        # Names that compare equal share a key: "ｂｏｂ" is "bob".
        assert keys["ｂｏｂ"] == login_key("BOB")
        nullable = conn.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns"
                " WHERE table_schema = current_schema()"
                " AND table_name = 'users' AND column_name = 'login_key'"
            )
        ).scalar_one()
        assert nullable == "NO"

        command.downgrade(config, BEFORE)
        columns = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = current_schema() AND table_name = 'users'"
            )
        ).scalars()
        assert "login_key" not in set(columns)
