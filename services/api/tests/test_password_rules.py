"""The rules a new password meets, and the work a stored one costs to check.

A new password needs the length NIST SP 800-63B-4 asks of a single factor and
is compared with passwords that are commonly used or expected here. Stored
hashes use OWASP's PBKDF2 work factor; one stored with fewer iterations costs
the same to refuse, and is replaced when its account next signs in.
"""

from unittest.mock import Mock

import pytest
from app.core.config import Settings, settings
from app.db.models import User
from app.security import auth, passwords
from pydantic import ValidationError

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (  # noqa: F401
    BASE,
    PASSWORD,
    keen,
)

LEGACY_ITERATIONS = 260_000


def legacy_hash(password, iterations=LEGACY_ITERATIONS):
    """A hash as Keen stored it before the work factor rose."""
    salt = b"sixteen-byte-salt"
    digest = passwords._pbkdf2_sha256(password, salt, iterations)
    return "$".join(
        [
            "pbkdf2_sha256",
            str(iterations),
            passwords._b64u_encode(salt),
            passwords._b64u_encode(digest),
        ]
    )


# -- the rules ------------------------------------------------------------------


@pytest.mark.parametrize(
    "password,problem",
    [
        ("fourteen-chars", "Password must be at least 15 characters"),
        ("x" * 1025, "Password must be at most 1024 characters"),
        (" " * 15, "Password repeats too few characters; choose a longer phrase"),
        ("a" * 16, "Password repeats too few characters; choose a longer phrase"),
        ("ab12" * 4, "Password repeats too few characters; choose a longer phrase"),
        ("π" * 15, "Password repeats too few characters; choose a longer phrase"),
        (
            "123456789012345",
            "Password is a keyboard or counting sequence; choose a phrase",
        ),
        (
            "987654321098765",
            "Password is a keyboard or counting sequence; choose a phrase",
        ),
        (
            "ABCDEFGHIJKLMNOPQ",
            "Password is a keyboard or counting sequence; choose a phrase",
        ),
        (
            "qwertyuiopasdfghj",
            "Password is a keyboard or counting sequence; choose a phrase",
        ),
        (
            "passwordpassword",
            "Password is too common; choose a phrase of several unrelated words",
        ),
        (
            "Password123456!",
            "Password is too common; choose a phrase of several unrelated words",
        ),
        (
            "P@ssw0rd-2026-11",
            "Password is too common; choose a phrase of several unrelated words",
        ),
        (
            "admin admin admin",
            "Password is too common; choose a phrase of several unrelated words",
        ),
        (
            "Correct Horse Battery Staple",
            "Password is too common; choose a phrase of several unrelated words",
        ),
        (
            "<<<changeme>>>!!",
            "Password is too common; choose a phrase of several unrelated words",
        ),
    ],
)
def test_a_guessable_new_password_is_refused_with_its_reason(password, problem):
    assert passwords.new_password_problem(password) == problem
    with pytest.raises(ValueError) as refused:
        passwords.hash_password(password)
    assert str(refused.value) == problem


@pytest.mark.parametrize(
    "password",
    [
        "unique π password",
        "ünïcödé-påsswörd",  # sixteen code points, more as bytes
        "synthetic-password-for-tests",
        "tide-lantern-oak-91",
        "x7Qp-2mLw-9zRk-4T",
    ],
)
def test_a_long_unremarkable_password_is_accepted_whatever_its_characters(password):
    assert passwords.new_password_problem(password) is None
    assert passwords.verify_password(password, passwords.hash_password(password))


def test_a_password_may_not_contain_its_accounts_username():
    problem = "Password must not contain the username"
    assert (
        passwords.new_password_problem("riley-walks-at-dawn", username=" Riley ")
        == problem
    )
    with pytest.raises(ValueError, match="^" + problem + "$"):
        passwords.hash_password("RILEY-walks-at-dawn", username="riley")
    # A name too short to be told from ordinary text is not looked for.
    assert passwords.new_password_problem("new-walks-at-dawn-91", username="new") is (
        None
    )
    assert passwords.new_password_problem("riley-walks-at-dawn") is None


def test_the_minimum_length_follows_the_setting(monkeypatch):
    monkeypatch.setattr(settings, "password_min_length", 8)
    assert passwords.new_password_problem("tide-oak") is None
    assert passwords.new_password_problem("tide-oa") == (
        "Password must be at least 8 characters"
    )


@pytest.mark.parametrize(
    "values,accepted",
    [
        ({}, True),
        ({"password_min_length": 15, "mfa_policy": "optional"}, True),
        ({"password_min_length": 8, "mfa_policy": "all"}, True),
        ({"password_min_length": 14, "mfa_policy": "optional"}, False),
        ({"password_min_length": 8, "mfa_policy": "admins"}, False),
        ({"password_min_length": 7, "mfa_policy": "all"}, False),
    ],
)
def test_a_shorter_minimum_needs_a_second_factor_on_every_account(values, accepted):
    if accepted:
        assert Settings(**values).password_min_length == values.get(
            "password_min_length", 15
        )
        return
    with pytest.raises(ValidationError):
        Settings(**values)


def test_setting_a_password_applies_the_rules_through_the_api(keen):  # noqa: F811
    admin = keen.client(keen.user(role="admin"))
    headers = {"Origin": BASE, "X-CSRF-Token": admin.cookies.get("__Host-keen_csrf")}
    common = "Password is too common; choose a phrase of several unrelated words"
    named = "Password must not contain the username"

    created = admin.post(
        "/v1/users",
        headers=headers,
        json={"username": "morgan", "password": "morgan-walks-at-dawn"},
    )
    assert (created.status_code, created.json()) == (400, {"detail": named})
    assert keen.db.query(User).filter_by(username="morgan").count() == 0

    target = keen.user()
    reset = admin.post(
        f"/v1/users/{target.id}/password",
        headers=headers,
        json={"new_password": "passwordpassword"},
    )
    assert (reset.status_code, reset.json()) == (400, {"detail": common})
    reset = admin.post(
        f"/v1/users/{target.id}/password",
        headers=headers,
        json={"new_password": target.username + "-walks-at-dawn"},
    )
    assert (reset.status_code, reset.json()) == (400, {"detail": named})

    owner = keen.client(target)
    own = {"Origin": BASE, "X-CSRF-Token": owner.cookies.get("__Host-keen_csrf")}
    for new_password, detail in [
        ("Password123456!", common),
        (target.username.upper() + "-walks-at-dawn", named),
        ("x" * 1025, "Password must be at most 1024 characters"),
    ]:
        changed = owner.post(
            "/v1/me/password",
            headers=own,
            json={"current_password": PASSWORD, "new_password": new_password},
        )
        assert (changed.status_code, changed.json()) == (400, {"detail": detail})
    # Every refusal left the password as it was.
    keen.db.expire_all()
    assert passwords.verify_password(
        PASSWORD, keen.db.get(User, target.id).password_hash
    )


# -- the work factor --------------------------------------------------------------


def test_new_hashes_use_the_current_work_factor():
    assert passwords._DEFAULT_ITERS == 600_000
    stored = passwords.derive_password_hash("x")
    assert stored.split("$")[:2] == ["pbkdf2_sha256", "600000"]
    assert passwords.needs_rehash(stored) is False
    assert passwords.needs_rehash(legacy_hash("x")) is True
    assert passwords.needs_rehash("!LDAP") is False
    with pytest.raises(ValueError, match="^Password is required$"):
        passwords.derive_password_hash("")


@pytest.mark.parametrize("password", ["a-wrong-password", "the-right-password"])
def test_an_older_hash_costs_the_current_work_to_check(monkeypatch, password):
    stored = legacy_hash("the-right-password")
    derive = Mock(wraps=passwords._pbkdf2_sha256)
    monkeypatch.setattr(passwords, "_pbkdf2_sha256", derive)

    matched = passwords.verify_password(password, stored)

    assert matched is (password == "the-right-password")
    spent = [call.args[2] for call in derive.call_args_list]
    assert spent == [LEGACY_ITERATIONS, passwords._DEFAULT_ITERS - LEGACY_ITERATIONS]
    # The original defect this guards: without the second derivation, a refused
    # attempt on an older account would cost less than one on an unknown name.
    assert sum(spent) == passwords._DEFAULT_ITERS


def test_a_correct_password_replaces_an_older_hash_once(keen):  # noqa: F811
    user = keen.user()
    row = keen.db.get(User, user.id)
    row.password_hash = old = legacy_hash(PASSWORD)
    keen.db.commit()

    refused = keen.client().post(
        "/v1/auth/login",
        json={"username": user.username, "password": "not-the-password"},
        headers={"Origin": BASE},
    )
    keen.db.expire_all()
    assert refused.status_code == 401
    assert keen.db.get(User, user.id).password_hash == old

    keen.client(user)
    keen.db.expire_all()
    replaced = keen.db.get(User, user.id).password_hash
    assert replaced != old and passwords.needs_rehash(replaced) is False
    assert passwords.verify_password(PASSWORD, replaced) is True

    keen.client(user)
    keen.db.expire_all()
    assert keen.db.get(User, user.id).password_hash == replaced


def test_a_rehash_never_undoes_a_password_changed_meanwhile(keen):  # noqa: F811
    user = keen.user()
    row = keen.db.get(User, user.id)
    row.password_hash = legacy_hash(PASSWORD)
    keen.db.commit()
    verified = keen.db.get(User, user.id)
    assert verified.password_hash == legacy_hash(PASSWORD)
    # Another request changes the password after this one verified the old one.
    changed = passwords.hash_password("a-newer-synthetic-passphrase")
    keen.db.query(User).filter(User.id == user.id).update(
        {User.password_hash: changed}, synchronize_session=False
    )

    auth._raise_work_factor(keen.db, verified, PASSWORD)

    keen.db.expire_all()
    assert keen.db.get(User, user.id).password_hash == changed
