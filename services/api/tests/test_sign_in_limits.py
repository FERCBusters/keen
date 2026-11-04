"""Password sign-in limits and equal-cost failures through the assembled application.

Sign-in attempts are limited per client address and per username before any
password is verified, and the limit fails closed when its store is
unavailable. Unknown, deactivated and directory-backed accounts cost the same
password verification as a real local account.
"""

from urllib.parse import parse_qs

import pytest
from app.api.routes import auth as auth_routes
from app.core.config import settings
from app.db.models import AuditLog
from app.security import auth
from app.security.auth import cookie_name as session_cookie_name
from app.security.rate_limit import login_limit_key, login_name

# Imported fixtures: the assembled application with an in-memory session store,
# and a fake directory server.
from tests.test_assembled_authority_regressions import (  # noqa: F401
    BASE,
    PASSWORD,
    keen,
)
from tests.test_ldap_roles_export import directory  # noqa: F401

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
        return any(c.startswith(session_cookie_name() + "=") for c in cookies)

    keen.verified = verified
    keen.attempt = attempt
    keen.session_set = session_set
    keen.keen_client = keen.client()
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
    external = signin.user()
    external.auth_backend = "ldap"
    signin.db.commit()
    dummy = auth._DUMMY_PASSWORD_HASH

    responses = [
        signin.attempt("nobody-by-this-name"),
        signin.attempt(inactive.username),
        signin.attempt(external.username),
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
    planted = attacker.cookies.get(session_cookie_name())
    victim = signin.user()
    browser = signin.client()

    response = browser.post(
        "/v1/auth/login",
        json={"username": victim.username, "password": PASSWORD},
        headers={"Origin": BASE, "Cookie": f"{session_cookie_name()}={planted}"},
    )

    assert response.status_code == 200, response.text
    issued = response.cookies.get(session_cookie_name())
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


# --- Usernames and their allowances ---------------------------------------------


@pytest.mark.parametrize("username", ["", "   ", "\t\n", "​⁠", "　­"])
def test_a_blank_username_is_refused_before_any_work(signin, username):
    refused = signin.attempt(username)

    assert refused.status_code == 422
    assert [item["loc"] for item in refused.json()["detail"]] == [["body", "username"]]
    # No allowance is drawn on, no password is derived and nothing is audited.
    assert signin.verified == []
    assert signin.store.keys("keen:rl:login:*") == []
    assert signin.db.query(AuditLog).filter_by(path="/action/sign-in").count() == 0


def test_oversized_credentials_are_refused_before_any_work(signin):
    user = signin.user()

    for payload in (
        {"username": "u" * 257, "password": PASSWORD},
        {"username": user.username, "password": "p" * 1025},
    ):
        refused = signin.keen_client.post(
            "/v1/auth/login", json=payload, headers={"Origin": BASE}
        )
        assert refused.status_code == 422
    assert signin.verified == [] and signin.store.keys("keen:rl:login:*") == []


def test_an_account_named_unknown_has_its_own_limit(signin):
    # "unknown" once marked a blank username and was exempt from the limit.
    user = signin.user()
    user.username = "Unknown"
    signin.db.commit()
    for _ in range(3):
        assert signin.attempt("Unknown", "wrong-password").status_code == 401

    locked = signin.attempt("Unknown")

    assert locked.status_code == 429
    assert not signin.session_set(locked)


def test_spellings_of_a_username_share_one_allowance(signin):
    user = signin.user()
    user.username = "Ada Lovelace"
    signin.db.commit()
    # Case, fullwidth forms, ideographic and repeated spaces, and invisible
    # characters all name the same directory entry.
    variants = [
        "ADA LOVELACE",
        "ａｄａ　ｌｏｖｅｌａｃｅ",
        "Ada​  Love­lace",
    ]
    assert {login_name(v) for v in variants} == {"ada lovelace"}
    # The allowance is keyed by a digest, so no typed name is stored in Valkey.
    (key,) = {login_limit_key(v) for v in variants}
    assert len(key) == len("keen:rl:login:user:") + 64 and "ada" not in key
    for variant in variants:
        assert signin.attempt(variant, "wrong-password").status_code == 401

    locked = signin.attempt("Ada Lovelace")

    assert locked.status_code == 429
    assert not signin.session_set(locked)


def test_spellings_that_reach_one_directory_entry_share_one_allowance(
    signin,
    directory,  # noqa: F811 - fixture imported above
    monkeypatch,
):
    # The directory resolves each of these names to the same entry; the
    # username limit alone would give each its own allowance.
    monkeypatch.setattr(settings, "ldap_enabled", True)
    monkeypatch.setattr(settings, "login_rate_limit_per_username", 3)
    state, events, _ = directory
    state["bind"] = False
    entry = ("bind", "uid=alice,ou=People,dc=example")
    for name in ("alice", "alice.", "alice@"):
        assert signin.attempt(name, "wrong", method="ldap").status_code == 401
    assert events.count(entry) == 3

    state["bind"] = True
    locked = signin.attempt("ALICE_", "password", method="ldap")

    # A spent entry is answered as a wrong password, so the answer cannot show
    # that this spelling reached an entry, and the password is never tried
    # against the directory.
    wrong = signin.attempt("nobody-here", "password", method="ldap")
    assert locked.status_code == wrong.status_code == 401
    assert locked.json() == wrong.json()
    assert events.count(entry) == 3 and not signin.session_set(locked)


# --- Sign-in audit rows ---------------------------------------------------------


def _sign_in_rows(keen):  # noqa: F811 - fixture imported above
    keen.db.expire_all()
    return [
        (row.username, parse_qs(row.query_string or ""))
        for row in keen.db.query(AuditLog)
        .filter_by(path="/action/sign-in")
        .order_by(AuditLog.ts)
        .all()
    ]


def test_sign_in_attempts_are_audited_without_unmatched_usernames(signin):
    user = signin.user()
    inactive = signin.user()
    inactive.is_active = False
    signin.db.commit()
    typed = "my-secret-password-in-the-wrong-box"

    assert signin.attempt(user.username, "wrong-password").status_code == 401
    assert signin.attempt(inactive.username).status_code == 401
    assert signin.attempt(typed, "anything").status_code == 401
    assert signin.attempt(user.username).status_code == 200

    def outcome(query):
        return query["method"][0], query["outcome"][0]

    assert [(name, outcome(query)) for name, query in _sign_in_rows(signin)] == [
        (user.username, ("local", "failed")),
        (inactive.username, ("local", "failed")),
        (None, ("local", "failed")),
        (user.username, ("local", "signed-in")),
    ]
    # A username matching no account is stored nowhere in the audit trail.
    for row in signin.db.query(AuditLog).all():
        assert typed not in " ".join(
            str(value or "") for value in (row.username, row.path, row.query_string)
        )
