"""Account security views and the admin-page gate through the assembled app.

The MFA summary and SSO linking addresses show a signed-in user their own
factors and addresses, never secret material or other users' records. nginx
asks /v1/auth/check-admin before serving the administration pages.
"""

from datetime import datetime

import pytest
from app.core.config import settings
from app.db.models import (
    MfaCredential,
    MfaRecoveryCode,
    SecurityNotification,
    SsoEmailChallenge,
    UserSsoEmail,
)
from app.services import mailer

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import keen, unsafe  # noqa: F401


def test_the_mfa_summary_lists_factors_without_their_secrets(keen):  # noqa: F811
    user = keen.user()
    client = keen.client(user)
    user.mfa_totp_secret = "synthetic-encrypted-totp-secret"
    keen.db.add_all(
        [
            MfaCredential(
                user_id=user.id,
                credential_id="synthetic-credential-id",
                public_key="synthetic-public-key",
                name="Desk key",
            ),
            MfaRecoveryCode(user_id=user.id, code_hash="a" * 64),
            MfaRecoveryCode(user_id=user.id, code_hash="b" * 64),
        ]
    )
    other = keen.user()
    keen.db.add(MfaRecoveryCode(user_id=other.id, code_hash="c" * 64))
    keen.db.commit()

    response = client.get("/v1/me/mfa")

    assert response.status_code == 200, response.text
    summary = response.json()
    assert (summary["enabled"], summary["totp"]) == (False, True)
    assert [(c["name"], c["last_used_at"]) for c in summary["credentials"]] == [
        ("Desk key", None)
    ]
    assert summary["recovery_codes_remaining"] == 2
    assert (summary["policy"], summary["required"], summary["local_enabled"]) == (
        "optional",
        False,
        True,
    )
    for secret in ("synthetic-encrypted-totp-secret", "synthetic-public-key", "a" * 64):
        assert secret not in response.text
    assert response.headers["cache-control"] == "no-store"
    refused = keen.client().get("/v1/me/mfa")
    assert (refused.status_code, refused.json()) == (
        401,
        {"detail": "Not authenticated"},
    )


def test_sso_linking_addresses_are_the_users_own(keen, monkeypatch):  # noqa: F811
    user = keen.user()
    user.email = "ada@example.test"
    other = keen.user()
    verified = datetime(2026, 5, 1, 9, 30)
    keen.db.add_all(
        [
            UserSsoEmail(
                email="z-ada@example.test", user_id=user.id, verified_at=verified
            ),
            UserSsoEmail(
                email="a-ada@example.test", user_id=user.id, verified_at=verified
            ),
            UserSsoEmail(
                email="bob@example.test", user_id=other.id, verified_at=verified
            ),
        ]
    )
    keen.db.commit()
    monkeypatch.setattr(settings, "smtp_host", "")

    response = keen.client(user).get("/v1/me/sso-emails")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "smtp_configured": False,
        "primary_email": "ada@example.test",
        "addresses": [
            {"email": "a-ada@example.test", "verified_at": "2026-05-01T09:30:00"},
            {"email": "z-ada@example.test", "verified_at": "2026-05-01T09:30:00"},
        ],
    }
    refused = keen.client().get("/v1/me/sso-emails")
    assert (refused.status_code, refused.json()) == (
        401,
        {"detail": "Not authenticated"},
    )


def test_requesting_a_linking_address_reveals_nothing_about_other_accounts(
    keen,  # noqa: F811 - fixture imported above
    monkeypatch,
):
    monkeypatch.setattr(settings, "smtp_host", "smtp.invalid")
    monkeypatch.setattr(settings, "smtp_from_email", "keen@keen.example")
    monkeypatch.setattr(
        mailer,
        "send_email",
        lambda **kw: pytest.fail("the request contacts the mail server"),
    )
    victim = keen.user()
    victim.email = "Victim.Person@corp.example"
    keen.db.add(UserSsoEmail(email="alias.person@corp.example", user_id=keen.user().id))
    keen.db.commit()
    requester = keen.user()
    client = keen.client(requester)  # a fresh sign-in satisfies the five-minute rule

    def request(email):
        return client.post(
            "/v1/me/sso-emails/request", json={"email": email}, headers=unsafe(client)
        )

    def challenge():
        keen.db.expire_all()
        return keen.db.query(SsoEmailChallenge).filter_by(user_id=requester.id).one()

    free = request("nobody@corp.example")
    first = challenge().token_hash
    answers = [free, request("victim.person@corp.example")]
    after_primary = challenge().token_hash
    answers.append(request("alias.person@corp.example"))

    # Another account's email or linking address gets the answer a free
    # address gets, and replaces the outstanding link just the same.
    assert {(r.status_code, r.text) for r in answers} == {(200, '{"ok":true}')}
    assert len({first, after_primary, challenge().token_hash}) == 3
    queued = keen.db.query(SecurityNotification).filter_by(user_id=requester.id)
    assert [n.recipient for n in queued] == ["nobody@corp.example"]
    assert keen.db.query(UserSsoEmail).filter_by(user_id=requester.id).count() == 0


def test_the_admin_page_gate_admits_administrators_only(keen):  # noqa: F811
    path = "/v1/auth/check-admin"
    answer = keen.client().get(path)
    assert (answer.status_code, answer.json()) == (401, {"detail": "Not authenticated"})

    # Audit managers, and a permission named after the retired audit trail
    # permission, are refused like any other account.
    for codes in (("events.read", "isms.manage"), ("audittrail.read", "audits.admin")):
        refused = keen.client(keen.user(*codes)).get(path)
        assert (refused.status_code, refused.json()["detail"]) == (
            403,
            "Admin role required",
        )

    admin_user = keen.user(role="admin")
    admin = keen.client(admin_user).get(path)
    assert admin.status_code == 204 and not admin.content
    assert (admin.headers["x-keen-user"], admin.headers["x-keen-role"]) == (
        admin_user.username,
        "admin",
    )


def test_every_documentation_page_renders_for_a_signed_in_user(keen):  # noqa: F811
    client = keen.client(keen.user())
    for prefix in ("", "/v1"):
        schema = client.get(prefix + "/openapi.json")
        assert schema.status_code == 200 and "/v1/me" in schema.json()["paths"]
        for page, marker in (("/docs", "swagger-ui"), ("/redoc", "redoc")):
            response = client.get(prefix + page)
            assert response.status_code == 200, (prefix, page)
            assert marker in response.text.lower()
            # A relative schema URL keeps working behind the /api proxy prefix.
            assert "./openapi.json" in response.text
        redirect = client.get(prefix + "/docs/oauth2-redirect")
        assert redirect.status_code == 200 and "oauth2" in redirect.text.lower()


def test_health_needs_no_session(keen):  # noqa: F811
    response = keen.client().get("/health")
    assert (response.status_code, response.json()) == (
        200,
        {"ok": True, "service": "keen"},
    )
