"""Password sign-in limits and equal-cost failures through the assembled application.

The sign-in throttle (app/security/sign_in_throttle.py, shared with OSPMAE)
hears a browser that has proved an account's password whatever an attacker
does elsewhere; every other browser draws on a per-address allowance and the
account's growing waits, decided before any password is verified, and the
throttle fails closed when its store is unavailable. Unknown, deactivated and
directory-backed accounts cost the same password verification as a real
local account.
"""

import json
from urllib.parse import parse_qs

import pytest
import sqlalchemy
from app.api.routes import auth as auth_routes
from app.core.config import settings
from app.db.models import AuditLog, User
from app.security import auth
from app.security import sign_in_throttle as throttle
from app.security.auth import cookie_name as session_cookie_name
from app.security.rate_limit import login_name

# Imported fixtures: the assembled application with an in-memory session store,
# and a fake directory server.
from tests.test_assembled_authority_regressions import (  # noqa: F401
    BASE,
    PASSWORD,
    keen,
    unsafe,
)
from tests.test_ldap_roles_export import directory  # noqa: F401

LIMITED = "Too many login attempts. Please try again later."


@pytest.fixture
def signin(keen, monkeypatch):  # noqa: F811 - fixture imported above
    monkeypatch.setattr(settings, "login_rate_limit_window_seconds", 300)
    monkeypatch.setattr(settings, "login_rate_limit_per_ip", 100)
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


def _attack(signin, username, failures=throttle.FREE_FAILURES + 1):
    """Wrong passwords from fresh, unrecognised browsers."""
    for n in range(failures):
        refused = signin.attempt(username, f"wrong-password-{n}")
        assert (refused.status_code, refused.json()) == (
            401,
            {"detail": "Invalid username or password"},
        )


def test_unrecognised_browsers_wait_even_with_the_right_password(signin):
    user = signin.user()
    # Case and surrounding spaces do not give an attacker fresh attempts.
    for variant in (user.username.upper(), f" {user.username}", f"{user.username} "):
        refused = signin.attempt(variant, "wrong-password")
        assert refused.status_code == 401
    _attack(signin, user.username, throttle.FREE_FAILURES + 1 - 3)

    held = signin.attempt(user.username)

    assert held.status_code == 429
    assert held.json()["detail"] == LIMITED
    assert 0 < int(held.headers["retry-after"]) <= throttle.FIRST_WAIT_SECONDS
    assert not signin.session_set(held)
    # Another account from the same address is unaffected.
    other = signin.user()
    assert signin.attempt(other.username).status_code == 200


def test_a_recognised_browser_signs_in_while_the_account_is_under_attack(signin):
    user = signin.user()
    owner = signin.client()
    assert signin.attempt(user.username, client=owner).status_code == 200
    _attack(signin, user.username)
    assert signin.attempt(user.username).status_code == 429

    verified = len(signin.verified)
    signed_in = signin.attempt(user.username, client=owner)

    assert signed_in.status_code == 200, signed_in.text
    assert signin.session_set(signed_in)
    assert len(signin.verified) == verified + 1
    # Its success ends the account's count for every browser.
    assert (
        signin.store.get(f"keen:login:failures:{throttle.name_key(user.username)}")
        is None
    )
    assert signin.attempt(user.username).status_code == 200


def test_device_cookie_shape_rotation_and_scope(signin):
    user = signin.user()
    other = signin.user()
    owner = signin.client()
    first = signin.attempt(user.username, client=owner)
    cookie = next(
        c
        for c in first.headers.get_list("set-cookie")
        if c.startswith(throttle.device_cookie_name() + "=")
    )
    assert throttle.device_cookie_name() == "__Host-keen_device"
    for part in ("httponly", "secure", "samesite=strict", "path=/"):
        assert part in cookie.lower()
    assert "domain=" not in cookie.lower()
    old = owner.cookies.get(throttle.device_cookie_name())
    assert signin.attempt(user.username, client=owner).status_code == 200
    new = owner.cookies.get(throttle.device_cookie_name())
    assert new != old
    # Only digests are stored, and the retired token no longer counts.
    devices = signin.store.keys("keen:login:device:*")
    assert devices == [f"keen:login:device:{throttle._token_hash(new)}"]
    assert old not in " ".join(devices) and new not in " ".join(devices)

    # Recognition speaks only for the name it signed in with.
    _attack(signin, other.username)
    assert signin.attempt(other.username, client=owner).status_code == 429


def test_five_wrong_passwords_end_a_browsers_recognition(signin):
    user = signin.user()
    owner = signin.client()
    assert signin.attempt(user.username, client=owner).status_code == 200
    for _ in range(throttle.DEVICE_FAILURES):
        assert signin.attempt(user.username, "wrong", client=owner).status_code == 401
    assert signin.store.keys("keen:login:device:*") == []
    # The account's own count was untouched while the browser was recognised.
    key = f"keen:login:failures:{throttle.name_key(user.username)}"
    assert signin.store.get(key) is None
    assert signin.attempt(user.username, "wrong", client=owner).status_code == 401
    assert signin.store.get(key) == "1"


def _burst(signin, monkeypatch, username, cookie, size):
    """Send ``size`` wrong passwords with one device cookie, each arriving
    while every earlier one is still being verified, as attempts sent together
    are while a password hash runs. Returns how many were heard as a
    recognised and as an unrecognised browser's, and the responses in order
    of arrival."""
    heard = {True: 0, False: 0}
    responses = []
    real = auth_routes.login_service.authenticate_password

    def guess():
        browser = signin.client()
        browser.cookies.set(throttle.device_cookie_name(), cookie)
        position = len(responses)
        responses.append(None)
        responses[position] = signin.attempt(username, "wrong", client=browser)

    def in_flight(db, context, *, recognised, **credentials):
        heard[recognised] += 1
        if len(responses) < size:
            guess()
        return real(db, context, recognised=recognised, **credentials)

    monkeypatch.setattr(auth_routes.login_service, "authenticate_password", in_flight)
    guess()
    return heard, [response.status_code for response in responses]


def test_attempts_sent_together_share_a_recognised_browsers_allowance(
    signin, monkeypatch
):
    user = signin.user()
    owner = signin.client()
    assert signin.attempt(user.username, client=owner).status_code == 200
    cookie = owner.cookies.get(throttle.device_cookie_name())

    heard, statuses = _burst(signin, monkeypatch, user.username, cookie, 40)

    # Each attempt was counted before its password was checked, so five were
    # heard as the recognised browser and the rest as any other browser: the
    # account's free failures, the attempt that opens its wait, then refusal.
    assert heard == {
        True: throttle.DEVICE_FAILURES,
        False: throttle.FREE_FAILURES + 1,
    }
    assert statuses == [401] * (len(statuses) - 1) + [429]
    assert len(statuses) == sum(heard.values()) + 1
    assert signin.store.keys("keen:login:device:*") == []


def test_counting_only_after_judging_would_hear_every_attempt_sent_together(
    signin, monkeypatch
):
    """The defect the test above guards against, reproduced by counting a
    recognised attempt only once its password has been judged."""
    user = signin.user()
    owner = signin.client()
    assert signin.attempt(user.username, client=owner).status_code == 200
    cookie = owner.cookies.get(throttle.device_cookie_name())

    def count_afterwards(r, device):
        if int(r.hincrby(f"keen:login:device:{device}", "failures", 1)) >= (
            throttle.DEVICE_FAILURES
        ):
            throttle.revoke_device(r, device)

    monkeypatch.setattr(throttle, "reserve_device_attempt", lambda r, device: True)
    monkeypatch.setattr(throttle, "device_failed", count_afterwards)

    heard, statuses = _burst(signin, monkeypatch, user.username, cookie, 40)

    assert heard == {True: 40, False: 0}
    assert statuses == [401] * 40


def test_a_recognised_attempt_is_counted_before_its_password_is_judged(signin):
    r = signin.store
    token = throttle.issue_device(r, user_id="account", key="name")
    device = throttle.recognised_device(r, token, "name")
    key = f"keen:login:device:{device}"

    for attempt in range(1, throttle.DEVICE_FAILURES + 1):
        assert throttle.reserve_device_attempt(r, device) is True
        assert r.hget(key, "failures") == str(attempt)
    # No password has been judged, and the allowance is already spent.
    assert throttle.reserve_device_attempt(r, device) is False
    assert r.exists(key) == 0 and r.zcard("keen:login:devices:account") == 0
    # Counting a revoked device leaves nothing behind for it.
    assert throttle.reserve_device_attempt(r, device) is False
    assert r.exists(key) == 0
    throttle.release_device_attempt(r, device)
    assert r.exists(key) == 0


def test_a_directory_outage_spends_none_of_a_browsers_recognition(
    signin,
    directory,  # noqa: F811 - fixture imported above
    monkeypatch,
):
    monkeypatch.setattr(settings, "ldap_enabled", True)
    state, _, _ = directory
    owner = signin.client()
    assert (
        signin.attempt("alice", "password", method="ldap", client=owner).status_code
        == 200
    )
    device = signin.store.keys("keen:login:device:*")[0]
    state["service_bind"] = 52

    for _ in range(throttle.DEVICE_FAILURES + 2):
        outage = signin.attempt("alice", "password", method="ldap", client=owner)
        assert outage.status_code == 503
        assert signin.store.hget(device, "failures") == "0"
    # A browser the account does not recognise is answered the same way.
    assert signin.attempt("alice", "password", method="ldap").status_code == 503

    del state["service_bind"]
    assert (
        signin.attempt("alice", "password", method="ldap", client=owner).status_code
        == 200
    )


def test_recognised_browsers_skip_the_address_allowance(signin, monkeypatch):
    user = signin.user()
    owner = signin.client()
    assert signin.attempt(user.username, client=owner).status_code == 200
    monkeypatch.setattr(settings, "login_rate_limit_per_ip", 2)
    for n in range(2):
        signin.attempt(f"guess-{n}", "wrong")
    assert signin.attempt("guess-x", "wrong").status_code == 429
    assert signin.attempt(user.username, client=owner).status_code == 200


def test_the_wait_schedule_and_ceiling(signin):
    r = signin.store
    key = throttle.name_key("schedule")
    for _ in range(throttle.FREE_FAILURES):
        assert throttle.reserve_unrecognised_attempt(r, key) == (True, 0)
    waits = []
    for _ in range(9):
        assert throttle.reserve_unrecognised_attempt(r, key) == (True, 0)
        allowed, retry = throttle.reserve_unrecognised_attempt(r, key)
        assert allowed is False
        waits.append(retry)
        r.delete(f"keen:login:gate:{key}")  # the wait elapses
    assert waits == [30, 60, 120, 240, 480, 960, 1920, 3600, 3600]
    # Refused attempts are not counted as failures.
    assert r.get(f"keen:login:failures:{key}") == str(throttle.FREE_FAILURES + 9)
    ttl = r.ttl(f"keen:login:failures:{key}")
    assert throttle.FORGET_FAILURES_AFTER_SECONDS - 5 <= ttl
    r.set(f"keen:login:failures:{key}", throttle.FAILURE_CEILING)
    r.delete(f"keen:login:gate:{key}")
    assert throttle.reserve_unrecognised_attempt(r, key) == (
        False,
        throttle.MAX_WAIT_SECONDS,
    )
    assert r.get(f"keen:login:failures:{key}") == str(throttle.FAILURE_CEILING)
    assert [throttle.wait_seconds(n) for n in (0, 4, 5, 11, 12, 99)] == [
        0,
        0,
        30,
        1920,
        3600,
        3600,
    ]


def test_an_account_keeps_its_newest_recognised_browsers(signin):
    r = signin.store
    tokens = [
        throttle.issue_device(r, user_id="u1", key="k")
        for _ in range(throttle.DEVICES_PER_ACCOUNT + 2)
    ]
    assert len(r.keys("keen:login:device:*")) == throttle.DEVICES_PER_ACCOUNT
    assert throttle.recognised_device(r, tokens[0], "k") is None
    assert throttle.recognised_device(r, tokens[-1], "k")
    assert throttle.recognised_device(r, tokens[-1], "other") is None
    assert throttle.recognised_device(r, "x" * 129, "k") is None
    throttle.revoke_account_devices(r, "u1", keep=throttle._token_hash(tokens[-1]))
    assert len(r.keys("keen:login:device:*")) == 1


def test_password_changes_and_resets_end_recognition(signin, monkeypatch):
    from app.services.me import end_password_recognition

    user = signin.user()
    owner = signin.client()
    assert signin.attempt(user.username, client=owner).status_code == 200
    _attack(signin, user.username)
    end_password_recognition(str(user.id), key=throttle.name_key(user.username))
    assert signin.store.keys("keen:login:device:*") == []
    # A reset also clears the name's throttle.
    assert signin.attempt(user.username).status_code == 200


def test_one_address_cannot_spray_many_usernames(signin, monkeypatch):
    monkeypatch.setattr(settings, "login_rate_limit_per_ip", 5)
    user = signin.user()
    for n in range(5):
        refused_2 = signin.attempt(f"guess-{n}", "wrong-password")
        assert (refused_2.status_code, refused_2.json()) == (
            401,
            {"detail": "Invalid username or password"},
        )

    refused = signin.attempt(user.username)

    assert refused.status_code == 429
    assert refused.json()["detail"] == LIMITED
    assert not signin.session_set(refused)


def test_limited_attempts_never_reach_password_verification(signin):
    user = signin.user()
    _attack(signin, user.username)
    verified = len(signin.verified)

    for password in ("wrong-password", PASSWORD):
        refused = signin.attempt(user.username, password)
        assert (refused.status_code, refused.json()) == (
            429,
            {"detail": "Too many login attempts. Please try again later."},
        )
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
        "get_valkey" if failure == "store" else "address_window_allow",
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
    refused = attacker.get("/v1/auth/check")
    assert (refused.status_code, refused.json()) == (
        401,
        {"detail": "Not authenticated"},
    )


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
    assert signin.store.keys("keen:login:*") == []
    assert signin.db.query(AuditLog).filter_by(path="/action/sign-in").count() == 0


def test_oversized_credentials_are_refused_before_any_work(signin):
    user = signin.user()

    for payload, field, most in (
        ({"username": "u" * 257, "password": PASSWORD}, "username", 256),
        ({"username": user.username, "password": "p" * 1025}, "password", 1024),
    ):
        refused = signin.keen_client.post(
            "/v1/auth/login", json=payload, headers={"Origin": BASE}
        )
        assert (refused.status_code, refused.json()) == (
            422,
            {
                "detail": [
                    {
                        "type": "string_too_long",
                        "loc": ["body", field],
                        "msg": f"String should have at most {most} characters",
                    }
                ]
            },
        )
    assert signin.verified == [] and signin.store.keys("keen:rl:login:*") == []
    assert signin.store.keys("keen:login:*") == []


def test_an_account_named_unknown_has_its_own_limit(signin):
    # "unknown" once marked a blank username and was exempt from the limit.
    user = signin.user()
    user.username = "Unknown"
    signin.db.commit()
    _attack(signin, "Unknown")

    locked = signin.attempt("Unknown")

    assert (locked.status_code, locked.json()) == (
        429,
        {"detail": "Too many login attempts. Please try again later."},
    )
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
    # The throttle is keyed by a digest, so no typed name is stored in Valkey.
    (key,) = {throttle.name_key(v) for v in variants}
    assert len(key) == 64 and "ada" not in key
    for variant in variants * 2:
        refused = signin.attempt(variant, "wrong-password")
        assert (refused.status_code, refused.json()) == (
            401,
            {"detail": "Invalid username or password"},
        )

    locked = signin.attempt("Ada Lovelace")

    assert (locked.status_code, locked.json()) == (
        429,
        {"detail": "Too many login attempts. Please try again later."},
    )
    assert not signin.session_set(locked)


def test_spellings_that_reach_one_directory_entry_share_one_allowance(
    signin,
    directory,  # noqa: F811 - fixture imported above
    monkeypatch,
):
    # The directory resolves each of these names to the same entry; the
    # username limit alone would give each its own allowance.
    monkeypatch.setattr(settings, "ldap_enabled", True)
    state, events, _ = directory
    state["bind"] = False
    entry = ("bind", "uid=alice,ou=People,dc=example")
    # Two failures per spelling keep each name's own throttle free, while the
    # entry's shared throttle reaches its first wait.
    for name in ("alice", "alice.", "alice@") * 2:
        refused = signin.attempt(name, "wrong", method="ldap")
        assert (refused.status_code, refused.json()) == (
            401,
            {"detail": "Invalid username or password"},
        )
    assert events.count(entry) == 6

    state["bind"] = True
    locked = signin.attempt("ALICE_", "password", method="ldap")

    # A held entry is answered as a wrong password, so the answer cannot show
    # that this spelling reached an entry, and the password is never tried
    # against the directory.
    wrong = signin.attempt("nobody-here", "password", method="ldap")
    assert locked.status_code == wrong.status_code == 401
    assert locked.json() == wrong.json()
    assert events.count(entry) == 6 and not signin.session_set(locked)


@pytest.mark.parametrize("password", ["\u00ad", "\t", "\u0000", "\ufeff\u200b"])
def test_a_password_the_directory_library_cannot_prepare_shows_nothing_about_a_name(
    signin,
    directory,  # noqa: F811 - fixture imported above
    monkeypatch,
    password,
):
    """ldap3 refuses to prepare such a password as text, once a search has
    matched an entry; that refusal was answered 503 while unknown names and
    names outside the required group got 401."""
    monkeypatch.setattr(settings, "ldap_enabled", True)
    state, events, _ = directory
    state["bind"] = False
    state["sent"] = []
    held = signin.attempt("alice", password, method="ldap")
    state["entries"], kept = [], state["entries"]
    unknown = signin.attempt("nobody-here", password, method="ldap")
    state["entries"] = kept
    monkeypatch.setattr(settings, "ldap_group_filter", "(objectClass=groupOfNames)")
    state["groups"] = []
    outside = signin.attempt("alice", password, method="ldap")

    for response in (held, unknown, outside):
        assert (response.status_code, response.json()) == (
            401,
            {"detail": "Invalid username or password"},
        )
        assert not signin.session_set(response)


def test_a_recognised_browser_reaches_a_held_directory_entry(
    signin,
    directory,  # noqa: F811 - fixture imported above
    monkeypatch,
):
    monkeypatch.setattr(settings, "ldap_enabled", True)
    state, events, _ = directory
    entry = ("bind", "uid=alice,ou=People,dc=example")
    owner = signin.client()
    state["bind"] = True
    assert (
        signin.attempt("alice", "password", method="ldap", client=owner).status_code
        == 200
    )
    state["bind"] = False
    for name in ("alice.", "alice@") * 3:
        assert signin.attempt(name, "wrong", method="ldap").status_code == 401
    state["bind"] = True
    held = signin.attempt("alice@", "password", method="ldap")
    assert held.status_code == 401 and not signin.session_set(held)
    binds = events.count(entry)

    # The owner's browser is recognised for its own spelling, so the held
    # entry does not keep the account's owner out.
    signed_in = signin.attempt("alice", "password", method="ldap", client=owner)

    assert signed_in.status_code == 200, signed_in.text
    assert signin.session_set(signed_in)
    assert events.count(entry) == binds + 1


def test_the_device_cookie_is_host_bound_only_when_it_can_be(monkeypatch):
    monkeypatch.setattr(settings, "cookie_secure", True)
    monkeypatch.setattr(settings, "cookie_domain", "")
    assert throttle.device_cookie_name() == "__Host-keen_device"
    monkeypatch.setattr(settings, "cookie_domain", "example.org")
    assert throttle.device_cookie_name() == "keen_device"
    monkeypatch.setattr(settings, "cookie_domain", "")
    monkeypatch.setattr(settings, "cookie_secure", False)
    assert throttle.device_cookie_name() == "keen_device"


def test_revoking_an_unknown_browser_changes_nothing(signin):
    r = signin.store
    r.zadd("keen:login:devices:someone", {"kept": 1.0})
    throttle.revoke_device(r, "unknown-device")
    assert r.zrange("keen:login:devices:someone", 0, -1) == ["kept"]


def test_a_store_outage_does_not_stop_a_password_change(monkeypatch):
    from app.services import me

    def unavailable():
        raise ConnectionError("valkey down")

    monkeypatch.setattr(me, "get_valkey", unavailable)
    # Recognition then expires on its own, which grants no access.
    assert me.end_password_recognition("00000000-0000-0000-0000-000000000000") is None


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

    refused = signin.attempt(user.username, "wrong-password")
    assert (refused.status_code, refused.json()) == (
        401,
        {"detail": "Invalid username or password"},
    )
    refused = signin.attempt(inactive.username)
    assert (refused.status_code, refused.json()) == (
        401,
        {"detail": "Invalid username or password"},
    )
    refused = signin.attempt(typed, "anything")
    assert (refused.status_code, refused.json()) == (
        401,
        {"detail": "Invalid username or password"},
    )
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


# --- A deactivation during sign-in issues no session ----------------------------


def _deactivate_after_the_password_is_checked(monkeypatch, change):
    """Run ``change(db, account)`` once the password is proven and before the
    session is issued, as an administrator's request arriving then would."""
    from app.api.routes import auth as auth_routes

    check = auth_routes.login_service.authenticate_password

    def changed_meanwhile(db, context, **attempt):
        account = check(db, context, **attempt)
        change(db, account)
        return account

    monkeypatch.setattr(
        auth_routes.login_service, "authenticate_password", changed_meanwhile
    )


def _deactivate(db, account):
    db.query(User).filter(User.id == account.id).update(
        {User.is_active: False, User.session_version: User.session_version + 1},
        synchronize_session=False,
    )
    db.commit()


def _refused_without_a_session(signin, response):
    assert (response.status_code, response.json()) == (
        401,
        {"detail": "Invalid username or password"},
    )
    assert not signin.session_set(response)


def test_a_deactivation_during_directory_sign_in_issues_no_session(
    signin,
    directory,  # noqa: F811 - fixture imported above
    monkeypatch,
):
    # A directory sign-in commits the identity's details after checking that
    # the account is active, so the account is read again before its session
    # is issued. A deactivation committed in between must refuse the sign-in:
    # a session carrying the version the deactivation set would work again
    # once the account was reactivated.
    monkeypatch.setattr(settings, "ldap_enabled", True)
    owner = signin.client()
    assert (
        signin.attempt("alice", "password", method="ldap", client=owner).status_code
        == 200
    )
    owner.cookies.delete(session_cookie_name())
    _deactivate_after_the_password_is_checked(monkeypatch, _deactivate)

    refused = signin.attempt("alice", "password", method="ldap", client=owner)

    _refused_without_a_session(signin, refused)
    signin.db.expire_all()
    account = signin.db.query(User).filter_by(username="ldap:alice").one()
    assert account.is_active is False
    # Reactivating the account brings no session back with it.
    account.is_active = True
    signin.db.commit()
    assert owner.get("/v1/me").status_code == 401
    # The refusal is audited against the account, by its sign-in method.
    last = (
        signin.db.query(AuditLog)
        .filter_by(path="/action/sign-in")
        .order_by(AuditLog.ts.desc())
        .first()
    )
    assert (last.username, parse_qs(last.query_string)) == (
        "ldap:alice",
        {"method": ["ldap"], "outcome": ["failed"]},
    )


@pytest.mark.parametrize("stored", ["current", "older"], ids=["hash", "rehash"])
def test_a_deactivation_during_local_sign_in_issues_no_session(
    signin, monkeypatch, stored
):
    # The older hash is replaced, and committed, at the account's correct
    # password; either way the account is read under its row lock afterwards.
    from tests.test_password_rules import legacy_hash

    user = signin.user()
    if stored == "older":
        signin.db.get(User, user.id).password_hash = legacy_hash(PASSWORD)
        signin.db.commit()
    browser = signin.client()
    _deactivate_after_the_password_is_checked(monkeypatch, _deactivate)

    refused = signin.attempt(user.username, client=browser)

    _refused_without_a_session(signin, refused)
    signin.db.expire_all()
    account = signin.db.get(User, user.id)
    assert account.is_active is False and account.last_login_at is None
    account.is_active = True
    signin.db.commit()
    assert browser.get("/v1/me").status_code == 401


def test_an_account_removed_during_sign_in_gets_no_session(signin, monkeypatch):
    user = signin.user()

    def remove(db, account):
        # The row is gone for this request without the loaded account expiring.
        db.execute(sqlalchemy.delete(User.__table__).where(User.id == account.id))

    _deactivate_after_the_password_is_checked(monkeypatch, remove)

    refused = signin.attempt(user.username)

    _refused_without_a_session(signin, refused)


def test_a_sign_in_issues_the_version_read_under_the_accounts_lock(signin):
    # The session carries the account's current version, the last sign-in time
    # is stored with it, and the lock is taken on the account's own row.
    user = signin.user()
    signin.db.get(User, user.id).session_version = 7
    signin.db.commit()
    statements = []
    bind = signin.db.get_bind()

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    sqlalchemy.event.listen(bind, "before_cursor_execute", record)
    try:
        browser = signin.client()
        assert signin.attempt(user.username, client=browser).status_code == 200
    finally:
        sqlalchemy.event.remove(bind, "before_cursor_execute", record)

    assert any(
        "FROM users" in statement and statement.rstrip().endswith("FOR UPDATE")
        for statement in statements
    )
    signin.db.expire_all()
    assert signin.db.get(User, user.id).last_login_at is not None
    (session,) = [
        json.loads(signin.store.get(key)) for key in signin.store.keys("keen:session:*")
    ]
    assert session["session_version"] == 7
    assert browser.get("/v1/me").status_code == 200


# --- Recognition and limits belong to one account -------------------------------


def _failures(signin, name, method="local"):
    key = throttle.name_key(name, method)
    return int(signin.store.get(f"keen:login:failures:{key}") or 0)


def test_a_browser_recognised_for_a_directory_account_is_not_for_its_local_namesake(
    signin,
    directory,  # noqa: F811 - fixture imported above
    monkeypatch,
):
    # A local account and a directory account can have one compared name and
    # belong to different people. A browser that proved the directory
    # account's password is recognised for that account only: its guesses at
    # the local account's password draw on the local account's limits like any
    # unrecognised browser's, and its own sign-in leaves that count in place.
    monkeypatch.setattr(settings, "ldap_enabled", True)
    monkeypatch.setattr(settings, "local_auth_enabled", True)
    local = User(
        username="alice",
        password_hash=auth.hash_password(PASSWORD),
        role="admin",
        is_active=True,
    )
    signin.db.add(local)
    signin.db.commit()
    namesake = signin.client()
    assert (
        signin.attempt("alice", "password", method="ldap", client=namesake).status_code
        == 200
    )
    assert throttle.name_key("alice", "ldap") != throttle.name_key("alice", "local")
    assert throttle.name_key("alice") == throttle.name_key(" ALICE ", "local")

    for n in range(3):
        refused = signin.attempt("alice", f"wrong-{n}", client=namesake)
        assert refused.status_code == 401

    assert _failures(signin, "alice") == 3
    # Its own correct directory password ends the directory account's count,
    # and no one else's.
    assert (
        signin.attempt("alice", "password", method="ldap", client=namesake).status_code
        == 200
    )
    assert _failures(signin, "alice") == 3
    # The reverse holds: the local account's browser is unrecognised for the
    # directory account, whose count its wrong passwords advance.
    owner = signin.client()
    assert signin.attempt("alice", client=owner).status_code == 200
    assert _failures(signin, "alice") == 0
    state, _, _ = directory
    state["bind"] = False  # The directory refuses the password.
    assert (
        signin.attempt("alice", "wrong", method="ldap", client=owner).status_code == 401
    )
    assert _failures(signin, "alice", "ldap") == 1


@pytest.mark.parametrize(
    "spelling",
    ["Carol", "CAROL", "car\u200bol", "\uff43arol"],
    ids=["case", "upper", "zero-width", "fullwidth"],
)
def test_a_username_that_compares_equal_to_an_existing_one_is_refused(signin, spelling):
    # Limits and recognition are kept by compared name. Two accounts whose
    # names compare equal would share them, so the second is not created.
    admin = signin.client(signin.user(role="admin"))

    def create(username):
        return admin.post(
            "/v1/users",
            json={"username": username, "password": PASSWORD, "role": "normal"},
            headers=unsafe(admin),
        )

    assert create("carol").status_code == 200
    refused = create(spelling)
    assert (refused.status_code, refused.json()) == (
        400,
        {
            "detail": "a username that differs only in case, width, spacing or"
            " invisible characters already exists"
        },
    )
    again = create("carol")
    assert (again.status_code, again.json()) == (
        400,
        {"detail": "username already exists"},
    )
    assert signin.db.query(User).filter(User.username.ilike("%arol%")).count() == 1
    # A name that only resembles it is a different name.
    assert create("carol2").status_code == 200


def test_accounts_that_already_share_a_compared_name_keep_separate_limits(signin):
    # Account creation refuses a username whose compared name an account
    # already has, but an installation may hold such a pair from before it
    # did. Each is still one account: the holder of one is not recognised for
    # the other, wrong passwords count against the account they name, and a
    # correct sign-in to one leaves the other's count alone.
    from app.services.login import sign_in_key

    dana, other = signin.user(), signin.user()
    dana.username, other.username = "legacy-Dana", "legacy-dana"
    signin.db.commit()
    assert login_name(dana.username) == login_name(other.username)
    keys = {sign_in_key(signin.db, name) for name in (dana.username, other.username)}
    shared = throttle.name_key(dana.username)
    assert len(keys) == 2 and shared not in keys
    # Other spellings name neither account and keep the compared name's key.
    assert sign_in_key(signin.db, "LEGACY-DANA") == shared

    def failures(name):
        key = sign_in_key(signin.db, name)
        return int(signin.store.get(f"keen:login:failures:{key}") or 0)

    browser = signin.client()
    assert signin.attempt(other.username, client=browser).status_code == 200
    # The recognised browser guesses at the namesake: heard as any other
    # browser, so each guess counts and the account's waits begin as they
    # would for anyone.
    counted = throttle.FREE_FAILURES + 1
    for n in range(counted):
        refused = signin.attempt(dana.username, f"wrong-{n}", client=browser)
        assert refused.status_code == 401
    assert failures(dana.username) == counted
    assert signin.attempt(dana.username, "wrong", client=browser).status_code == 429
    # Its own sign-in is still heard, and clears only its own count.
    assert signin.attempt(other.username, client=browser).status_code == 200
    assert failures(dana.username) == counted
    assert failures(other.username) == 0
    # An account without a namesake keeps the compared name's key.
    single = signin.user()
    assert sign_in_key(signin.db, single.username) == throttle.name_key(single.username)
    assert sign_in_key(signin.db, "alice", "ldap") == throttle.name_key("alice", "ldap")
