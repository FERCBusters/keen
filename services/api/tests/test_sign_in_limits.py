"""Password sign-in limits and equal-cost failures through the assembled application.

Sign-in attempts are limited per client address and per username before any
password is verified, and the limit fails closed when its store is
unavailable. Unknown, deactivated and directory-backed accounts cost the same
password verification as a real local account.
"""

import pytest
from app.api.routes import auth as auth_routes
from app.core.config import settings
from app.security import auth

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (  # noqa: F401
    BASE,
    PASSWORD,
    keen,
)

LIMITED = "Too many login attempts. Please try again later."


@pytest.fixture
def signin(keen, monkeypatch):  # noqa: F811 - fixture imported above
    monkeypatch.setattr(settings, "login_rate_limit_window_seconds", 300)
    monkeypatch.setattr(settings, "login_rate_limit_per_ip", 5)
    monkeypatch.setattr(settings, "login_rate_limit_per_username", 3)
    monkeypatch.setattr(settings, "ldap_enabled", False)
    verified = []
    real_verify = auth.verify_password

    def verify(password, password_hash):
        verified.append(password_hash)
        return real_verify(password, password_hash)

    monkeypatch.setattr(auth, "verify_password", verify)

    def attempt(username, password=PASSWORD, *, client=None, **payload):
        client = client or keen.client()
        return client.post(
            "/v1/auth/login",
            json={"username": username, "password": password, **payload},
            headers={"Origin": BASE},
        )

    def session_set(response):
        cookies = response.headers.get_list("set-cookie")
        return any(c.startswith(settings.session_cookie_name + "=") for c in cookies)

    keen.verified = verified
    keen.attempt = attempt
    keen.session_set = session_set
    return keen


def test_repeated_failures_lock_a_username_even_for_the_right_password(signin):
    user = signin.user()
    # Case and surrounding spaces do not give an attacker fresh attempts.
    for variant in (user.username.upper(), f" {user.username}", f"{user.username} "):
        assert signin.attempt(variant, "wrong-password").status_code == 401

    locked = signin.attempt(user.username)

    assert locked.status_code == 429
    assert locked.json()["detail"] == LIMITED
    assert 0 < int(locked.headers["retry-after"]) <= 300
    assert not signin.session_set(locked)
    # Another account from the same address is unaffected.
    other = signin.user()
    assert signin.attempt(other.username).status_code == 200


def test_one_address_cannot_spray_many_usernames(signin):
    user = signin.user()
    for n in range(5):
        assert signin.attempt(f"guess-{n}", "wrong-password").status_code == 401

    refused = signin.attempt(user.username)

    assert refused.status_code == 429
    assert refused.json()["detail"] == LIMITED
    assert not signin.session_set(refused)


def test_limited_attempts_never_reach_password_verification(signin):
    user = signin.user()
    for _ in range(3):
        signin.attempt(user.username, "wrong-password")
    verified = len(signin.verified)

    for password in ("wrong-password", PASSWORD):
        assert signin.attempt(user.username, password).status_code == 429
    assert len(signin.verified) == verified


@pytest.mark.parametrize("failure", ["store", "limiter"])
def test_sign_in_fails_closed_when_the_limit_cannot_be_checked(
    signin, monkeypatch, failure
):
    user = signin.user()

    def offline(*args, **kwargs):
        raise ConnectionError("synthetic Valkey outage")

    monkeypatch.setattr(
        auth_routes,
        "get_valkey" if failure == "store" else "fixed_window_allow",
        offline,
    )

    refused = signin.attempt(user.username)

    assert refused.status_code == 503
    assert refused.json()["detail"] == (
        "Service temporarily unavailable. Please try again later."
    )
    assert not signin.session_set(refused) and signin.verified == []


def test_unusable_accounts_cost_one_verification_and_reveal_nothing(signin):
    inactive = signin.user()
    inactive.is_active = False
    directory = signin.user()
    directory.auth_backend = "ldap"
    signin.db.commit()
    dummy = auth._DUMMY_PASSWORD_HASH

    responses = [
        signin.attempt("nobody-by-this-name"),
        signin.attempt(inactive.username),
        signin.attempt(directory.username),
    ]

    # Each attempt verified one password, against the dummy hash, so a missing
    # or unusable account takes as long as a wrong password.
    assert signin.verified == [dummy, dummy, dummy]
    assert {(r.status_code, r.json()["detail"]) for r in responses} == {
        (401, "Invalid username or password")
    }
    assert not any(signin.session_set(r) for r in responses)


def test_signing_in_replaces_a_session_planted_in_the_browser(signin):
    # Session fixation: an attacker plants their own session ID in the
    # victim's browser before the victim signs in.
    attacker = signin.client(signin.user())
    planted = attacker.cookies.get(settings.session_cookie_name)
    victim = signin.user()
    browser = signin.client()

    response = browser.post(
        "/v1/auth/login",
        json={"username": victim.username, "password": PASSWORD},
        headers={"Origin": BASE, "Cookie": f"{settings.session_cookie_name}={planted}"},
    )

    assert response.status_code == 200, response.text
    issued = response.cookies.get(settings.session_cookie_name)
    assert issued and issued != planted
    assert browser.get("/v1/auth/check").headers["x-keen-user"] == victim.username
    # The planted session is revoked rather than upgraded.
    assert attacker.get("/v1/auth/check").status_code == 401


@pytest.mark.parametrize(
    "method,setting", [("local", "local_auth_enabled"), ("ldap", "ldap_enabled")]
)
def test_disabled_sign_in_methods_refuse_before_any_check(
    signin, monkeypatch, method, setting
):
    user = signin.user()
    monkeypatch.setattr(settings, setting, False)

    refused = signin.attempt(user.username, method=method)

    assert refused.status_code == 404
    assert refused.json()["detail"] == "Login method is disabled"
    assert signin.verified == []


def test_sign_in_from_another_site_is_refused(signin):
    user = signin.user()
    client = signin.client()

    refused = client.post(
        "/v1/auth/login",
        json={"username": user.username, "password": PASSWORD},
        headers={"Origin": "https://attacker.example"},
    )

    assert refused.status_code == 403
    assert refused.json()["detail"] == "Authentication request origin is not allowed"
    assert not signin.session_set(refused) and signin.verified == []
