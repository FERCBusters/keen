"""Database-backed direct/inherited permissions and stale session snapshots."""

from unittest.mock import Mock

import pytest
from app.db.models import (
    Group,
    Permission,
    User,
    group_permissions,
    user_groups,
    user_permissions,
)
from app.security import passwords, permissions
from app.security.permissions import (
    ensure_session_authorization_current,
    get_effective_permission_codes,
    has_permission,
)
from app.security.roles import compute_effective_role, count_effective_admins


@pytest.fixture
def people(contract_db):
    db = contract_db
    users = {
        name: User(username=name, password_hash="unused", role=role, is_active=active)
        for name, role, active in [
            ("admin", "admin", True),
            ("reader", "normal", True),
            ("inherited", "inherit", True),
            ("inactive", "admin", False),
        ]
    }
    group = Group(name="admins", role="admin")
    read = Permission(code="events.read", description="Read events")
    write = Permission(code="events.write", description="Write events")
    db.add_all([*users.values(), group, read, write])
    db.flush()
    db.execute(
        user_groups.insert(),
        [
            {"user_id": users["reader"].id, "group_id": group.id},
            {"user_id": users["inherited"].id, "group_id": group.id},
        ],
    )
    db.execute(
        user_permissions.insert(),
        {"user_id": users["reader"].id, "permission_id": read.id},
    )
    db.execute(
        group_permissions.insert(), {"group_id": group.id, "permission_id": write.id}
    )
    db.commit()
    return db, users


@pytest.mark.parametrize(
    "name,role",
    [
        ("admin", "admin"),
        ("reader", "normal"),
        ("inherited", "admin"),
        ("inactive", "normal"),
    ],
)
def test_explicit_role_and_active_state_take_precedence(people, name, role):
    db, users = people
    assert compute_effective_role(db, users[name]) == role


def test_admin_lockout_count_excludes_inactive_and_explicit_normal(people):
    db, users = people
    assert count_effective_admins(db) == 2
    assert count_effective_admins(db, excluding_user_id=users["admin"].id) == 1


def test_direct_and_group_permission_union_and_user_isolation(people):
    db, users = people
    assert get_effective_permission_codes(db, users["reader"]) == {
        "events.read",
        "events.write",
    }
    assert get_effective_permission_codes(db, users["admin"]) == {"*"}
    assert get_effective_permission_codes(db, users["inactive"]) == set()
    assert not has_permission(db, users["inactive"], "events.read")
    assert not has_permission(db, users["reader"], "admin.secret")
    assert not has_permission(db, users["admin"], "")


def test_stale_snapshot_recomputed_from_database(people, monkeypatch):
    db, users = people
    user = users["reader"]
    user.session_id = "s"
    user.effective_role = "admin"
    user.effective_permission_codes = {"*"}
    user.session_authz_version = 1
    user.authz_version = 2
    update = Mock()
    monkeypatch.setattr(permissions, "update_session_authz", update)
    monkeypatch.setattr(permissions, "get_valkey", lambda: object())
    ensure_session_authorization_current(db, user)
    assert user.effective_role == "normal"
    assert user.effective_permission_codes == {"events.read", "events.write"}
    assert not has_permission(db, user, "admin.secret")
    assert update.call_args.kwargs["authz_version"] == 2


def test_current_snapshot_avoids_reloading_permissions(people, monkeypatch):
    db, users = people
    user = users["reader"]
    user.session_id = "s"
    user.effective_role = "normal"
    user.effective_permission_codes = {"events.read"}
    user.session_authz_version = 2
    user.authz_version = 2
    compute = Mock(side_effect=AssertionError("unexpected query"))
    monkeypatch.setattr(permissions, "compute_effective_role", compute)
    ensure_session_authorization_current(db, user)
    compute.assert_not_called()


def test_stale_snapshot_remains_correct_when_session_cache_is_down(people, monkeypatch):
    db, users = people
    user = users["reader"]
    user.session_id = "s"
    user.effective_role = "admin"
    user.effective_permission_codes = {"*"}
    user.session_authz_version = 0
    user.authz_version = 1
    monkeypatch.setattr(
        permissions, "get_valkey", Mock(side_effect=ConnectionError("offline"))
    )
    ensure_session_authorization_current(db, user)
    assert user.effective_role == "normal" and not has_permission(
        db, user, "admin.secret"
    )


def test_password_hashes_are_salted_and_verify_unicode():
    password = "unique π password"
    first = passwords.hash_password(password)
    second = passwords.hash_password(password)
    assert first != second and password not in first
    assert passwords.verify_password(password, first)
    assert not passwords.verify_password(password + "wrong", first)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "bad",
        "sha1$1$c2FsdA$YWJj",
        "pbkdf2_sha256$0$c2FsdA$YWJj",
        "pbkdf2_sha256$bad$c2FsdA$YWJj",
        "pbkdf2_sha256$1$$YWJj",
    ],
)
def test_malformed_password_hash_fails_closed(raw):
    assert not passwords.verify_password("password", raw)


@pytest.mark.parametrize("password", ["", "short", "1234567"])
def test_short_password_rejected(password):
    with pytest.raises(ValueError):
        passwords.hash_password(password)
