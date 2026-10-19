"""Account security views and the admin-page gate through the assembled app.

The MFA summary and SSO linking addresses show a signed-in user their own
factors and addresses, never secret material or other users' records. nginx
asks /v1/auth/check-admin before serving the administration pages.
"""

from datetime import datetime

from app.core.config import settings
from app.db.models import MfaCredential, MfaRecoveryCode, UserSsoEmail

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import keen  # noqa: F401


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
    assert keen.client().get("/v1/me/mfa").status_code == 401


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
    assert keen.client().get("/v1/me/sso-emails").status_code == 401


def test_the_admin_page_gate_admits_administrators_and_auditors(keen):  # noqa: F811
    path = "/v1/auth/check-admin"
    assert keen.client().get(path).status_code == 401

    member = keen.client(keen.user("events.read", "isms.manage"))
    refused = member.get(path)
    assert (refused.status_code, refused.json()["detail"]) == (
        403,
        "Admin or audit access required",
    )

    auditor_user = keen.user("audittrail.read")
    auditor = keen.client(auditor_user).get(path)
    assert auditor.status_code == 204 and not auditor.content
    assert (auditor.headers["x-keen-user"], auditor.headers["x-keen-role"]) == (
        auditor_user.username,
        "normal",
    )

    admin_user = keen.user(role="admin")
    admin = keen.client(admin_user).get(path)
    assert admin.status_code == 204
    assert admin.headers["x-keen-role"] == "admin"


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
