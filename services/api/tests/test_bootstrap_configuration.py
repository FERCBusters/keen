"""Bootstrap secrets are optional settings and never reset an existing account."""

import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import Mock

import pytest
from app import main
from app.core.config import Settings, settings
from app.db.models import Base, User
from app.security.passwords import hash_password, verify_password
from app.security.placeholders import is_placeholder
from sqlalchemy import DefaultClause, MetaData, create_engine, text
from sqlalchemy.orm import sessionmaker

from tests.db_helpers import required_database_url, schema_engine


@pytest.mark.parametrize(
    "values",
    [{}, {"KEEN_BOOTSTRAP_ADMIN_USERNAME": ""}, {"KEEN_BOOTSTRAP_ADMIN_PASSWORD": ""}],
)
def test_settings_import_with_bootstrap_secrets_absent(monkeypatch, values):
    monkeypatch.delenv("KEEN_BOOTSTRAP_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("KEEN_BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    config = Settings()
    assert config.bootstrap_admin_username == ""
    assert config.bootstrap_admin_password == ""


def test_application_and_migration_imports_without_bootstrap_secrets():
    env = dict(os.environ)
    env.pop("KEEN_BOOTSTRAP_ADMIN_USERNAME", None)
    env.pop("KEEN_BOOTSTRAP_ADMIN_PASSWORD", None)
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.db.models import User; from app.main import app; from alembic.config import Config",
        ],
        env=env,
        check=True,
        timeout=20,
    )


@pytest.fixture
def bootstrap_db(contract_db, monkeypatch):
    monkeypatch.setattr(settings, "hosted_mode", False)
    factory = sessionmaker(
        bind=contract_db.get_bind(),
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    monkeypatch.setattr(main, "SessionLocal", factory)
    return factory


@pytest.mark.parametrize(
    "username,password",
    [
        ("", ""),
        ("former-admin", ""),
        ("", "old-bootstrap-secret"),
        ("former-admin", "old-bootstrap-secret"),
    ],
)
def test_existing_account_password_is_preserved(
    bootstrap_db, monkeypatch, username, password
):
    with bootstrap_db() as db:
        user = User(
            username="existing-admin",
            password_hash=hash_password("changed-account-password"),
            role="admin",
            is_active=True,
        )
        db.add(user)
        db.commit()
        original_hash = user.password_hash
        user_id = user.id
    monkeypatch.setattr(settings, "bootstrap_admin_username", username)
    monkeypatch.setattr(settings, "bootstrap_admin_password", password)
    main.bootstrap_initial_admin()
    with bootstrap_db() as db:
        assert db.query(User).count() == 1
        assert db.get(User, user_id).password_hash == original_hash
        assert verify_password("changed-account-password", original_hash) is True


@pytest.mark.parametrize(
    "username,password",
    [
        ("initial-admin", ""),
        ("", "synthetic-password"),
        ("initial-admin", "short"),
        # The placeholder .env.example once shipped, and any other.
        ("admin", "<GENERATE_STRONG_PASSWORD>"),
        ("admin", " <replace-with-a-long-passphrase> "),
        # The rules every new password meets apply to the first one too.
        ("initial-admin", "fourteen-chars"),
        ("initial-admin", "passwordpassword"),
        ("initial-admin", "initial-admin-passphrase"),
    ],
)
def test_invalid_credentials_on_empty_database_fail_without_creating_account(
    bootstrap_db, monkeypatch, username, password, capsys
):
    monkeypatch.setattr(settings, "bootstrap_admin_username", username)
    monkeypatch.setattr(settings, "bootstrap_admin_password", password)
    with pytest.raises(RuntimeError) as exc:
        main.bootstrap_initial_admin()
    assert "synthetic-password" not in str(exc.value)
    assert "short" not in str(exc.value)
    assert not password or password.strip() not in str(exc.value)
    assert "at least 15 characters" in str(exc.value)
    assert capsys.readouterr().out == ""
    with bootstrap_db() as db:
        assert db.query(User).count() == 0


def test_fresh_bootstrap_creates_admin_once(bootstrap_db, monkeypatch):
    monkeypatch.setattr(settings, "bootstrap_admin_username", "initial-admin")
    monkeypatch.setattr(
        settings, "bootstrap_admin_password", "synthetic-bootstrap-password"
    )
    main.bootstrap_initial_admin()
    main.bootstrap_initial_admin()
    with bootstrap_db() as db:
        user = db.query(User).one()
        assert user.role == "admin" and user.is_active
        assert (
            verify_password("synthetic-bootstrap-password", user.password_hash) is True
        )


def test_no_credentials_or_hosted_mode_never_creates_an_account(monkeypatch):
    factory = Mock(side_effect=AssertionError("Unexpected bootstrap database access"))
    monkeypatch.setattr(main, "SessionLocal", factory)
    monkeypatch.setattr(settings, "hosted_mode", False)
    monkeypatch.setattr(settings, "bootstrap_admin_username", "")
    monkeypatch.setattr(settings, "bootstrap_admin_password", "")
    main.bootstrap_initial_admin()
    monkeypatch.setattr(settings, "hosted_mode", True)
    monkeypatch.setattr(settings, "bootstrap_admin_username", "initial-admin")
    monkeypatch.setattr(
        settings, "bootstrap_admin_password", "synthetic-bootstrap-password"
    )
    main.bootstrap_initial_admin()
    factory.assert_not_called()


def test_api_workers_starting_together_create_one_account(monkeypatch):
    """Each API worker process bootstraps at start-up; one account results."""
    url = required_database_url("KEEN_RETENTION_TEST_DATABASE_URL")
    schema = "bootstrap_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    engine = schema_engine(url, schema)
    try:
        metadata = MetaData()
        for table in Base.metadata.sorted_tables:
            table.to_metadata(metadata)
        for table in metadata.tables.values():
            for column in table.columns:
                default = column.server_default
                if default is not None and "now()" in str(default.arg):
                    column.server_default = DefaultClause(text(f"({default.arg})"))
        metadata.create_all(engine)
        monkeypatch.setattr(settings, "hosted_mode", False)
        monkeypatch.setattr(settings, "bootstrap_admin_username", "initial-admin")
        monkeypatch.setattr(
            settings, "bootstrap_admin_password", "synthetic-bootstrap-password"
        )
        monkeypatch.setattr(main, "SessionLocal", sessionmaker(bind=engine))
        create_user = main.create_user

        def slow_create_user(db, **values):
            # Hold the first creation open long enough for the other to start.
            time.sleep(0.5)
            return create_user(db, **values)

        monkeypatch.setattr(main, "create_user", slow_create_user)
        start = threading.Barrier(2)
        failures = []

        def worker():
            start.wait()
            try:
                main.bootstrap_initial_admin()
            except Exception as error:  # noqa: BLE001 - reported below
                failures.append(error)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        assert failures == []
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM users")).scalar() == 1
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()


# -- example files ----------------------------------------------------------------


def _example_values(path):
    for line in path.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and name.isupper() and not name.startswith("#"):
            yield name, value


@pytest.mark.parametrize("name", [".env.example", "tools/staging/staging.env.example"])
def test_no_example_file_ships_a_value_that_works_as_a_credential(name):
    """An operator copies the example file and fills in what start-up asks
    for. A placeholder left in a credential is then a secret anyone can read,
    so the examples ship those settings empty and nothing accepts one."""
    path = Path(__file__).resolve().parents[3] / name
    values = dict(_example_values(path))
    assert len(values) > 50
    assert [n for n, value in values.items() if is_placeholder(value)] == []
    # The original defect: these two were placeholders that Keen accepted.
    assert is_placeholder("<GENERATE_STRONG_PASSWORD>")
    assert is_placeholder(" <GENERATE_RANDOM_SECRET> ")
    assert (
        not is_placeholder("")
        and not is_placeholder("<")
        and not is_placeholder("a<b>")
    )
    if name == ".env.example":
        assert values["KEEN_BOOTSTRAP_ADMIN_PASSWORD"] == ""
        assert values["ENROLL_WEBHOOK_SECRET"] == ""
        assert "KEEN_WEBHOOKS_REQUIRE_SECRET" not in values
