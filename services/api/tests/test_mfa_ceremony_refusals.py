"""Second-factor ceremony refusals through the assembled application.

A pending ceremony is reached only through real password sign-in, its cookie,
the MFA rate limits and the row locks the ceremony takes. Each test meets a
refusal as an attacker, a stale browser or a broken deployment would, and
checks the status, the message, that no session was issued and that no
factor, challenge or recovery code changed.
"""

import base64
import json
from contextlib import contextmanager

import app.services.mfa_management as mfa_operations
import pyotp
import pytest
from app.core.config import settings
from app.db.models import MfaChallenge, MfaCredential, MfaRecoveryCode, User
from app.security import mfa
from app.security.auth import cookie_name as session_cookie_name
from app.security.passwords import hash_password
from cryptography.fernet import Fernet
from sqlalchemy import event, update

from tests.test_assembled_authority_regressions import (  # noqa: F401 - fixtures
    BASE,
    PASSWORD,
    keen,
)
from tests.test_local_mfa import ceremony as ceremony  # noqa: F401 - fixture

ORIGIN = {"Origin": BASE}


@contextmanager
def statements(db):
    seen = []
    bind = db.get_bind()

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(bind, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(bind, "before_cursor_execute", record)


def _sets_session(response):
    cookies = response.headers.get_list("set-cookie")
    return any(c.startswith(session_cookie_name() + "=") for c in cookies)


def _fresh(db, user):
    db.expire_all()
    return db.get(User, user.id)


def test_ceremonies_stop_while_password_sign_in_is_disabled(
    ceremony,  # noqa: F811
    monkeypatch,
):
    client = ceremony.sign_in()
    monkeypatch.setattr(settings, "local_auth_enabled", False)
    monkeypatch.setattr(settings, "ldap_enabled", False)

    for refused in (
        client.post("/v1/auth/mfa/state", headers=ORIGIN),
        ceremony.code(client, ceremony.good),
    ):
        assert (refused.status_code, refused.json()) == (
            404,
            {"detail": "Password authentication is disabled"},
        )
    assert client.get("/v1/auth/check").status_code == 401
    db = ceremony.keen.db
    assert db.query(MfaChallenge).filter_by(user_id=ceremony.user.id).count() == 1
    assert _fresh(db, ceremony.user).mfa_totp_last_step == -1


def test_a_totp_secret_under_another_key_is_reported_and_not_counted(
    ceremony,  # noqa: F811
    monkeypatch,
):
    client = ceremony.sign_in()
    restored = settings.mfa_encryption_key
    monkeypatch.setattr(settings, "mfa_encryption_key", Fernet.generate_key().decode())

    refused = ceremony.code(client, ceremony.good)

    assert (refused.status_code, refused.json()) == (
        503,
        {"detail": "TOTP secret cannot be decrypted; restore the MFA encryption key"},
    )
    assert ceremony.secret not in refused.text
    assert _fresh(ceremony.keen.db, ceremony.user).mfa_code_failures == 0
    # With the key restored, the same ceremony completes.
    monkeypatch.setattr(settings, "mfa_encryption_key", restored)
    assert ceremony.code(client, ceremony.good).status_code == 200
    assert client.post("/v1/auth/mfa/finish", headers=ORIGIN).status_code == 200


def test_a_spent_address_allowance_refuses_codes_before_the_accounts_own(
    ceremony,  # noqa: F811
):
    client = ceremony.sign_in()
    store = ceremony.keen.store
    # Test clients have no parseable address, so they share "unknown".
    store.set("keen:rl:mfa:ip:unknown", 40, ex=300)

    refused = ceremony.code(client, ceremony.good)

    assert (refused.status_code, refused.json()) == (
        429,
        {"detail": "Too many verification attempts; try again later"},
    )
    assert store.get(f"keen:rl:mfa:user:{ceremony.user.id}") is None
    user = _fresh(ceremony.keen.db, ceremony.user)
    assert (user.mfa_code_failures, user.mfa_totp_last_step) == (0, -1)
    assert client.get("/v1/auth/check").status_code == 401


@pytest.mark.parametrize(
    "change",
    [
        {"password_hash": hash_password("another-synthetic-password")},
        {"is_active": False},
    ],
    ids=["password-changed", "deactivated"],
)
def test_an_account_changed_during_sign_in_gets_no_second_factor_challenge(
    ceremony,  # noqa: F811
    monkeypatch,
    change,
):
    real_start = mfa.start

    def changed_meanwhile(db, user, request, purpose="login"):
        # Another request changes the account between the password check
        # and the ceremony taking the account's row lock.
        db.execute(
            update(User)
            .where(User.id == user.id)
            .values(**change)
            .execution_options(synchronize_session=False)
        )
        return real_start(db, user, request, purpose)

    monkeypatch.setattr(mfa, "start", changed_meanwhile)
    client = ceremony.keen.client()

    refused = client.post(
        "/v1/auth/login",
        json={"username": ceremony.user.username, "password": PASSWORD},
        headers=ORIGIN,
    )

    assert (refused.status_code, refused.json()) == (
        401,
        {"detail": "Account changed; sign in again"},
    )
    assert not _sets_session(refused)
    assert mfa.cookie_name() not in refused.headers.get("set-cookie", "")
    db = ceremony.keen.db
    db.expire_all()
    assert db.query(MfaChallenge).filter_by(user_id=ceremony.user.id).count() == 0


@pytest.mark.parametrize("value", ["ABC", "AAAA-BBBB", "F" * 33], ids=repr)
def test_a_recovery_code_of_the_wrong_length_never_reaches_the_stored_codes(
    ceremony,  # noqa: F811
    value,
):
    client = ceremony.sign_in()
    db = ceremony.keen.db

    with statements(db) as seen:
        refused = ceremony.code(client, value, "recovery")

    assert (refused.status_code, refused.json()) == (
        400,
        {"detail": "Invalid or already-used code"},
    )
    assert not [s for s in seen if "mfa_recovery_codes" in s]
    assert db.query(MfaRecoveryCode).filter_by(user_id=ceremony.user.id).count() == 2
    # It is still a wrong code, counted against the account.
    assert _fresh(db, ceremony.user).mfa_code_failures == 1


# -- enrolment during a required sign-in ------------------------------------


@pytest.fixture
def enrolment(keen, monkeypatch):  # noqa: F811
    """An account without a factor signing in where every account needs one."""
    monkeypatch.setattr(settings, "mfa_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "mfa_policy", "all")
    user = keen.user()
    client = keen.client()
    started = client.post(
        "/v1/auth/login",
        json={"username": user.username, "password": PASSWORD},
        headers=ORIGIN,
    )
    assert started.json() == {"mfa_required": True, "next": "/mfa.html"}
    state = client.post("/v1/auth/mfa/state", headers=ORIGIN).json()
    assert (state["stage"], state["purpose"]) == ("enrol", "login")
    return keen, user, client


def test_enrolling_a_factor_during_a_required_sign_in_completes_it(enrolment):
    app, user, client = enrolment
    secret = client.post("/v1/auth/mfa/totp/start", headers=ORIGIN).json()["secret"]
    # Enrolment alone does not sign the browser in.
    early = client.post("/v1/auth/mfa/finish", headers=ORIGIN)
    assert early.status_code == 403 and not _sets_session(early)

    enrolled = client.post(
        "/v1/auth/mfa/totp/confirm",
        json={"code": pyotp.TOTP(secret).now()},
        headers=ORIGIN,
    )

    assert enrolled.status_code == 200, enrolled.text
    assert len(enrolled.json()["recovery_codes"]) == 10
    state = client.post("/v1/auth/mfa/state", headers=ORIGIN).json()
    assert state["stage"] == "ready"
    finished = client.post("/v1/auth/mfa/finish", headers=ORIGIN)
    assert finished.status_code == 200 and _sets_session(finished)
    assert client.get("/v1/auth/check").status_code == 204
    assert _fresh(app.db, user).mfa_enabled is True


def test_an_oversized_security_key_response_is_refused_before_verification(
    enrolment, monkeypatch
):
    app, user, client = enrolment
    verified = []

    def verify_registration_response(**kwargs):
        verified.append(kwargs)
        raise ValueError("synthetic attestation failure")

    monkeypatch.setattr(
        mfa_operations, "verify_registration_response", verify_registration_response
    )

    def register(credential):
        options = client.post("/v1/auth/mfa/webauthn/register-options", headers=ORIGIN)
        assert options.status_code == 200, options.text
        client_data = {
            "type": "webauthn.create",
            "challenge": options.json()["challenge"],
            "origin": BASE,
        }
        credential["response"]["clientDataJSON"] = mfa.b64(
            json.dumps(client_data).encode()
        )
        return client.post(
            "/v1/auth/mfa/webauthn/register",
            json={"credential": credential},
            headers=ORIGIN,
        )

    def credential(**extra):
        return {
            "id": "c2VjdXJpdHkta2V5",
            "rawId": "c2VjdXJpdHkta2V5",
            "type": "public-key",
            "response": {
                "attestationObject": base64.urlsafe_b64encode(b"o").decode(),
            },
            **extra,
        }

    refused = register(credential(padding="x" * 70000))

    assert (refused.status_code, refused.json()) == (
        400,
        {"detail": "Registration failed; request another challenge"},
    )
    assert verified == []
    # The same response at an ordinary size reaches attestation checking.
    assert register(credential()).status_code == 400
    assert len(verified) == 1
    db = app.db
    db.expire_all()
    assert db.query(MfaCredential).filter_by(user_id=user.id).count() == 0
    assert _fresh(db, user).mfa_enabled is False
    (row,) = db.query(MfaChallenge).filter_by(user_id=user.id).all()
    assert "register" not in row.data
