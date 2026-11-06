"""Session store, identity, password, permission and allowance refusals.

The session store, the trusted-proxy identity header, password verification,
permission snapshots and the fixed-window allowances each fail closed or keep
their state when given what an attacker or a failing store would give them.
Requests go through the assembled application where the condition is reachable
from a request; the store and lock helpers are called directly with the input
their guards exist for.
"""

import json
import logging
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import fakeredis
import pytest
from app.core.config import settings
from app.db.models import Group, Permission, User
from app.security import client_ip, permissions, roles
from app.security.auth import _remote_user_from_headers, create_user
from app.security.passwords import hash_password, verify_password
from app.security.rate_limit import fixed_window_allow, fixed_window_spend
from app.security.sessions import (
    SESSION_PREFIX,
    create_session,
    delete_session,
    get_session,
    revoke_session,
    session_key,
    update_session_authz,
)
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from tests.db_helpers import create_sqlite_schema
from tests.test_assembled_authority_regressions import (  # noqa: F401 - fixtures
    BASE,
    PASSWORD,
    keen,
    unsafe,
)
from tests.test_sign_in_limits import LIMITED
from tests.test_sign_in_limits import signin as signin  # noqa: F401 - fixture


@contextmanager
def statements(db):
    """The SQL statements a block sends through db's connection."""
    seen = []
    bind = db.get_bind()

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(bind, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(bind, "before_cursor_execute", record)


def _store():
    return fakeredis.FakeRedis(decode_responses=True)


def _account(db, *, role="normal", active=True, group_role=None):
    user = User(
        username="user-" + uuid.uuid4().hex[:10],
        password_hash="!",
        role=role,
        is_active=active,
    )
    if group_role:
        user.groups.append(Group(name="g-" + uuid.uuid4().hex[:10], role=group_role))
    db.add(user)
    db.commit()
    return user


# -- the session store ------------------------------------------------------


def test_a_blank_session_id_never_reads_or_changes_the_bare_session_key():
    store = _store()
    planted = json.dumps(
        {
            "user_id": str(uuid.uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    store.set(SESSION_PREFIX, planted)

    assert get_session(store, "") is None
    update_session_authz(
        store,
        "",
        effective_role="admin",
        permission_codes=["*"],
        authz_version=9,
        ttl_seconds=60,
    )
    delete_session(store, "")
    revoke_session(store, "")

    assert store.get(SESSION_PREFIX) == planted
    assert store.ttl(SESSION_PREFIX) == -1


def test_a_session_read_survives_a_failed_expiry_refresh():
    store = _store()
    sid = create_session(store, user_id="u-1", ttl_seconds=600)

    class NoRefresh:
        def get(self, key):
            return store.get(key)

        def expire(self, key, seconds):
            raise ConnectionError("synthetic Valkey failure")

    session = get_session(NoRefresh(), sid, refresh_ttl_seconds=900)

    assert session["user_id"] == "u-1"
    # The session keeps the lifetime it had; nothing extended it.
    assert 0 < store.ttl(session_key(sid)) <= 600


def test_a_permission_refresh_never_recreates_an_ended_session():
    store = _store()
    sid = create_session(store, user_id="u-1", ttl_seconds=600)
    revoke_session(store, sid)

    update_session_authz(
        store,
        sid,
        effective_role="admin",
        permission_codes=["*"],
        authz_version=2,
        ttl_seconds=600,
    )

    assert store.get(session_key(sid)) is None


def test_a_permission_refresh_never_extends_a_session_past_its_lifetime():
    store = _store()
    sid = create_session(store, user_id="u-1", ttl_seconds=600, effective_role="normal")
    key = session_key(sid)
    payload = json.loads(store.get(key))
    payload["created_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=120)
    ).isoformat()
    store.set(key, json.dumps(payload), ex=600)

    update_session_authz(
        store,
        sid,
        effective_role="admin",
        permission_codes=["*"],
        authz_version=2,
        ttl_seconds=600,
        max_age_seconds=60,
    )

    assert json.loads(store.get(key)) == payload


def test_a_stale_session_is_refused_even_when_the_store_cannot_delete_it(
    keen,  # noqa: F811
    monkeypatch,
):
    user = keen.user()
    client = keen.client(user)
    assert client.get("/v1/auth/check").status_code == 204
    user.session_version += 1
    keen.db.commit()

    def unavailable(*keys):
        raise ConnectionError("synthetic Valkey failure")

    monkeypatch.setattr(keen.store, "delete", unavailable)

    refused = client.get("/v1/me")

    assert (refused.status_code, refused.json()) == (
        401,
        {"detail": "Not authenticated"},
    )


def test_an_explicit_sign_out_reports_a_store_that_cannot_delete():
    class Unavailable:
        def delete(self, key):
            raise ConnectionError("synthetic Valkey failure")

    with pytest.raises(ConnectionError, match="^synthetic Valkey failure$"):
        revoke_session(Unavailable(), "session-id")
    assert delete_session(Unavailable(), "session-id") is None


# -- trusted-proxy identity and accounts ------------------------------------


def test_a_trusted_proxy_mapping_without_the_identity_header_names_no_one(
    monkeypatch,
):
    monkeypatch.setattr(settings, "trust_remote_user", True)
    monkeypatch.setattr(settings, "oidc_enabled", False)
    monkeypatch.setattr(settings, "remote_user_header_name", "REMOTE_USER")
    monkeypatch.setattr(settings, "security_trusted_proxy_cidrs", "10.0.0.0/8")

    assert _remote_user_from_headers({}, peer="10.0.0.5") is None
    assert _remote_user_from_headers({"REMOTE_USER": "alice"}, peer="10.0.0.5") == (
        "alice"
    )
    assert _remote_user_from_headers({"REMOTE_USER": "alice"}, peer="192.0.2.1") is None


@pytest.mark.parametrize(
    "username,role,message",
    [
        ("   ", "normal", "username required"),
        ("new-account", "owner", "role must be 'admin', 'normal', or 'inherit'"),
    ],
    ids=["blank-username", "unknown-role"],
)
def test_an_account_needs_a_name_and_a_known_role(contract_db, username, role, message):
    with pytest.raises(ValueError, match="^" + message + "$"):
        create_user(contract_db, username=username, password=PASSWORD, role=role)

    assert contract_db.query(User).count() == 0


# -- passwords --------------------------------------------------------------


def test_a_stored_hash_that_cannot_be_computed_verifies_nothing():
    damaged = "pbkdf2_sha256$" + str(2**31) + "$c2FsdHNhbHQ$ZGlnZXN0ZGlnZXN0"
    assert verify_password(PASSWORD, damaged) is False
    # A caller that skips request validation cannot raise with a lone surrogate.
    assert verify_password("\ud800" + PASSWORD, hash_password(PASSWORD)) is False
    assert verify_password(PASSWORD, hash_password(PASSWORD)) is True


def test_an_account_with_a_damaged_password_hash_cannot_sign_in(keen):  # noqa: F811
    user = keen.user()
    user.password_hash = "pbkdf2_sha256$" + str(2**31) + "$c2FsdHNhbHQ$ZGlnZXN0"
    keen.db.commit()
    client = keen.client()

    refused = client.post(
        "/v1/auth/login",
        json={"username": user.username, "password": PASSWORD},
        headers={"Origin": BASE},
    )

    assert (refused.status_code, refused.json()) == (
        401,
        {"detail": "Invalid username or password"},
    )
    refused_2 = client.get("/v1/auth/check")
    assert (refused_2.status_code, refused_2.json()) == (
        401,
        {"detail": "Not authenticated"},
    )


# -- permission snapshots and roles -----------------------------------------


def test_permission_codes_reuse_the_role_resolved_for_the_request(contract_db):
    user = _account(contract_db, role="inherit", group_role="admin")
    assert roles.attach_effective_role(contract_db, user) == "admin"

    with statements(contract_db) as seen:
        codes = permissions.get_effective_permission_codes(contract_db, user)

    assert codes == {"*"}
    assert seen == []


def test_a_request_without_a_session_keeps_the_role_it_resolved(contract_db):
    user = _account(contract_db, role="inherit", group_role="admin")
    roles.attach_effective_role(contract_db, user)

    with statements(contract_db) as seen:
        permissions.ensure_session_authorization_current(contract_db, user)

    assert user.effective_role == "admin" and seen == []


@pytest.mark.parametrize("account", ["inactive", "anonymous"])
def test_no_permission_snapshot_is_written_for_an_unusable_account(
    contract_db, monkeypatch, account
):
    store = _store()
    monkeypatch.setattr(permissions, "get_valkey", lambda: store)
    user = None
    if account == "inactive":
        user = _account(contract_db, role="admin", active=False)
        user.session_id = create_session(store, user_id=str(user.id), ttl_seconds=600)
    before = {key: store.get(key) for key in store.keys()}

    with statements(contract_db) as seen:
        permissions.ensure_session_authorization_current(contract_db, user)

    assert seen == []
    assert {key: store.get(key) for key in store.keys()} == before
    if user is not None:
        assert user.effective_role is None


def test_a_deactivated_administrator_cannot_read_evidence(contract_db):
    user = _account(contract_db, role="admin")
    user.effective_role = "admin"
    assert permissions.can_read_events(contract_db, user) is True

    user.is_active = False

    assert permissions.can_read_events(contract_db, user) is False


def test_blank_overlong_and_repeated_permission_codes_are_ignored(keen):  # noqa: F811
    admin = keen.client(keen.user(role="admin"))
    target = keen.user()
    if not keen.db.query(Permission).filter_by(code="events.read").one_or_none():
        keen.db.add(Permission(code="events.read", description="events.read"))
        keen.db.commit()

    saved = admin.put(
        f"/v1/users/{target.id}/access",
        json={
            "explicit_permission_codes": [
                "events.read",
                " events.read ",
                "",
                "   ",
                "x" * 129,
            ]
        },
        headers=unsafe(admin),
    )

    assert saved.status_code == 200, saved.text
    assert saved.json()["explicit_permission_codes"] == ["events.read"]
    keen.db.expire_all()
    assert [p.code for p in keen.db.get(User, target.id).permissions] == ["events.read"]


def test_administrator_changes_take_a_database_lock_only_on_postgresql(contract_db):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    try:
        create_sqlite_schema(engine)
        with Session(engine) as sqlite:
            with statements(sqlite) as seen:
                roles.lock_admin_changes(sqlite)
            assert seen == []
    finally:
        engine.dispose()
    if contract_db.get_bind().dialect.name == "postgresql":
        with statements(contract_db) as seen:
            roles.lock_admin_changes(contract_db)
        assert "SELECT pg_advisory_xact_lock(%(key)s)" in seen


# -- client addresses -------------------------------------------------------


def test_an_untrusted_proxy_is_reported_once_when_two_requests_race(
    monkeypatch, caplog
):
    reported = threading.Event()
    monkeypatch.setattr(client_ip, "_untrusted_proxy_reported", reported)

    class RacedLock:
        """Another request reports the proxy while this one waits for the lock."""

        def __enter__(self):
            reported.set()
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(client_ip, "_report_lock", RacedLock())
    with caplog.at_level(logging.WARNING, logger="app.security.client_ip"):
        client_ip._report_untrusted_proxy("10.0.0.9")

    assert reported.is_set()
    assert caplog.records == []


# -- usernames and allowances -----------------------------------------------


def test_control_characters_never_give_a_username_its_own_allowance(signin):
    user = signin.user()
    for variant in (f"{user.username}\x07", f"\x1b{user.username}", user.username):
        refused = signin.attempt(variant, "wrong-password")
        assert (refused.status_code, refused.json()) == (
            401,
            {"detail": "Invalid username or password"},
        )

    locked = signin.attempt(user.username)

    assert (locked.status_code, locked.json()["detail"]) == (429, LIMITED)
    assert not signin.session_set(locked)


def test_a_username_of_control_characters_alone_is_refused_before_any_work(signin):
    refused = signin.attempt("\x01\x02\x1b")

    assert refused.status_code == 422
    assert "Enter a username" in refused.text
    assert signin.verified == []


class Pipeline:
    """A Valkey pipeline that answers INCR/INCRBY and TTL with given replies."""

    def __init__(self, store, replies):
        self.store, self.replies = store, replies

    def incr(self, key):
        return self

    def incrby(self, key, amount):
        return self

    def ttl(self, key):
        return self

    def execute(self):
        if isinstance(self.replies, Exception):
            raise self.replies
        return list(self.replies)


class Store:
    def __init__(self, replies, *, expire_fails=False):
        self.replies, self.expire_fails, self.expired = replies, expire_fails, []

    def pipeline(self):
        return Pipeline(self, self.replies)

    def expire(self, key, seconds):
        if self.expire_fails:
            raise ConnectionError("synthetic Valkey failure")
        self.expired.append((key, seconds))


@pytest.mark.parametrize(
    "replies,expected",
    [
        (("not-a-count", 30), (False, 30)),
        ((6, "not-a-ttl"), (False, 60)),
        ((5, "not-a-ttl"), (True, 0)),
    ],
    ids=["unreadable-count", "unreadable-ttl-over-limit", "unreadable-ttl-within"],
)
def test_an_unreadable_counter_reply_is_counted_as_spent(replies, expected):
    # Even an allowance that fails open when the store is down refuses here.
    assert fixed_window_allow(Store(replies), "k", 5, 60, fail_closed=False) == expected


@pytest.mark.parametrize(
    "count,expected", [(1, (True, 0)), (6, (False, 60))], ids=["within", "over"]
)
def test_an_allowance_still_counts_when_its_expiry_cannot_be_set(count, expected):
    store = Store((count, -1), expire_fails=True)

    assert fixed_window_allow(store, "k", 5, 60, fail_closed=True) == expected


def test_a_spend_without_a_limit_never_touches_the_store():
    class Untouchable:
        def __getattr__(self, name):
            raise AssertionError("the store was used")

    assert fixed_window_spend(Untouchable(), "k", 10, 0, 60, fail_closed=True) == (
        True,
        0,
    )
    assert fixed_window_spend(Untouchable(), "k", 10, 100, 0, fail_closed=True) == (
        True,
        0,
    )


@pytest.mark.parametrize(
    "fail_closed,expected", [(True, (False, 60)), (False, (True, 0))]
)
def test_a_spend_follows_its_failure_policy_when_the_store_is_down(
    fail_closed, expected
):
    store = Store(ConnectionError("synthetic Valkey failure"))

    assert fixed_window_spend(store, "k", 10, 100, 60, fail_closed) == expected
