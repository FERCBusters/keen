"""The hosted demo portal's contract with KEEN.

The demo portal starts one KEEN installation per visitor. It configures KEEN
through environment variables, runs the migrations, `python -m
app.bootstrap_hosted` and the demo seeding, then polls `/api/v1/auth/methods`
and `/api/v1/auth/check` until the installation reports an unexpired demo that
offers OIDC sign-in alone. The visitor then follows `/api/v1/auth/oidc/start`,
and the portal's Auth0 tenant returns them to `/api/v1/auth/oidc/callback`, the
redirect URI it registers for the installation.

These tests run the assembled application with the portal's tenant settings
(infra/tenant/identity.tf and deploy/tenant/install.py in the portal) against a
fake Auth0 that answers at the HTTP transport. Synthetic keys and accounts only.
"""

import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from app import bootstrap_hosted
from app.core.config import Settings, settings
from app.security import csrf, oidc
from joserfc import jwt
from joserfc.jwk import RSAKey
from sqlalchemy.orm import sessionmaker

# Imported fixtures: the assembled application with an in-memory session
# store, and the production middleware with a notification socket client.
from tests.test_assembled_authority_regressions import BASE, keen  # noqa: F401
from tests.test_security_design_contracts import (  # noqa: F401
    ORIGIN,
    _notification_client,
    production_client,
)

ISSUER = "https://tenant.auth0.example/"
CLIENT_ID = "tenant-client"
CLIENT_SECRET = "synthetic-tenant-client-secret"
OWNER = "email|workspace-owner"
CALLBACK = BASE + "/api/v1/auth/oidc/callback"


def portal_environment(expires_at):
    """The KEEN variables a demo tenant receives, with synthetic values."""
    return {
        "KEEN_DATABASE_URL": "postgresql+psycopg2://keen:synthetic@postgres:5432/keen",
        "KEEN_REDIS_URL": "redis://:synthetic@valkey:6379/0",
        "KEEN_ENV": "production",
        "KEEN_HOSTED_MODE": "true",
        "KEEN_DEMO_MODE": "true",
        "KEEN_RSS_ENABLED": "true",
        "KEEN_DEMO_EXPIRES_AT": expires_at.isoformat(),
        "KEEN_DEMO_CONSULTATION_URL": "https://consult.example/",
        "KEEN_HOSTED_OWNER_SUBJECT": OWNER,
        "KEEN_BOOTSTRAP_ADMIN_USERNAME": "disabled-hosted-bootstrap",
        "KEEN_BOOTSTRAP_ADMIN_PASSWORD": "synthetic-unused-bootstrap-password",
        "KEEN_LOCAL_AUTH_ENABLED": "false",
        "KEEN_TRUST_REMOTE_USER": "false",
        "KEEN_GOOGLE_SSO_ENABLED": "false",
        "KEEN_GITHUB_SSO_ENABLED": "false",
        "KEEN_OIDC_ENABLED": "true",
        "KEEN_OIDC_AUTO_LINK_EXISTING": "false",
        "KEEN_OIDC_AUTO_PROVISION": "false",
        "KEEN_OIDC_ISSUER": ISSUER,
        "KEEN_OIDC_AUTHORIZATION_ENDPOINT": ISSUER + "authorize",
        "KEEN_OIDC_TOKEN_ENDPOINT": ISSUER + "oauth/token",
        "KEEN_OIDC_JWKS_URI": ISSUER + ".well-known/jwks.json",
        "KEEN_OIDC_CLIENT_ID": CLIENT_ID,
        "KEEN_OIDC_CLIENT_SECRET": CLIENT_SECRET,
        "KEEN_OIDC_TOKEN_ENDPOINT_AUTH_METHOD": "client_secret_post",
        "KEEN_OIDC_REDIRECT_URI": CALLBACK,
        "KEEN_OIDC_POST_LOGOUT_REDIRECT_URI": BASE + "/login.html",
        "KEEN_COOKIE_SECURE": "true",
        "KEEN_COOKIE_SAMESITE": "lax",
        "KEEN_SESSION_COOKIE_NAME": "__Host-keen_session",
        "KEEN_COOKIE_DOMAIN": "",
        "KEEN_PUBLIC_BASE_URL": BASE,
        "KEEN_ARTIFACT_STORAGE_BACKEND": "s3",
        "KEEN_S3_USE_INSTANCE_ROLE": "true",
        "KEEN_S3_BUCKET": "tenant-artifacts",
        "KEEN_S3_REGION": "eu-west-2",
        "KEEN_S3_COMPATIBILITY_MODE": "aws",
        # The installer adds the address of the tenant's nginx container.
        "KEEN_SECURITY_TRUSTED_PROXY_CIDRS": "172.30.64.10/32",
    }


# Variables the tenant also receives that KEEN reads outside its settings: the
# broker password configures the Valkey container, and the owner bootstrap
# reads the owner's address.
OWNER_EMAIL = {"KEEN_HOSTED_OWNER_EMAIL": "owner@example.com"}


def _tenant_settings(monkeypatch, expires_at):
    for name in list(os.environ):
        if name.startswith("KEEN_"):
            monkeypatch.delenv(name)
    environment = portal_environment(expires_at)
    for name, value in {**environment, **OWNER_EMAIL}.items():
        monkeypatch.setenv(name, value)
    return environment, Settings()


def _expiry():
    return (datetime.now(timezone.utc) + timedelta(hours=2)).replace(microsecond=0)


def test_every_variable_the_portal_sets_configures_keen(monkeypatch):
    expires_at = _expiry()

    environment, tenant = _tenant_settings(monkeypatch, expires_at)

    fields = {"KEEN_" + name.upper() for name in Settings.model_fields}
    assert sorted(set(environment) - fields) == []
    assert (tenant.hosted_mode, tenant.demo_mode, tenant.rss_enabled) == (
        True,
        True,
        True,
    )
    assert tenant.demo_expires_at == expires_at
    assert (tenant.local_auth_enabled, tenant.oidc_enabled) == (False, True)
    assert (tenant.oidc_redirect_uri, tenant.public_base_url) == (CALLBACK, BASE)
    assert tenant.oidc_token_endpoint_auth_method == "client_secret_post"
    assert tenant.session_cookie_name == "__Host-keen_session"


class FakeAuth0:
    """The tenant's Auth0 application: token, key set and one signed-in visitor."""

    def __init__(self, key):
        self.key = key
        self.subject = OWNER
        self.nonce = None
        self.token_requests = []

    def id_token(self):
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": self.subject,
            "email": "owner@example.com",
            "email_verified": True,
            "iat": now,
            "exp": now + 300,
            "nonce": self.nonce,
        }
        header = {"alg": "RS256", "kid": self.key.kid}
        return jwt.encode(header, claims, self.key, algorithms=["RS256"])

    def __call__(self, request):
        url = str(request.url).split("?")[0]
        if url == ISSUER + ".well-known/jwks.json":
            return httpx2.Response(
                200, json={"keys": [self.key.as_dict(private=False)]}
            )
        if url == ISSUER + "oauth/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_requests.append((form, request.headers.get("authorization")))
            return httpx2.Response(
                200,
                json={
                    "access_token": "tenant-access",
                    "token_type": "Bearer",
                    "id_token": self.id_token(),
                },
            )
        return httpx2.Response(404)


@pytest.fixture
def demo(keen, contract_db, monkeypatch):  # noqa: F811 - fixture imported above
    if contract_db.get_bind().dialect.name != "postgresql":
        pytest.skip("The owner bootstrap holds a PostgreSQL advisory lock")
    expires_at = _expiry()
    environment, tenant = _tenant_settings(monkeypatch, expires_at)
    # The application runs with the tenant's settings; the fixture keeps its
    # own database and session store.
    for name in Settings.model_fields:
        if "KEEN_" + name.upper() in environment and name not in (
            "database_url",
            "redis_url",
        ):
            monkeypatch.setattr(settings, name, getattr(tenant, name))
    auth0 = FakeAuth0(RSAKey.generate_key(2048, {"kid": "tenant-key"}))
    real_options = oidc.client_options
    monkeypatch.setattr(
        oidc,
        "client_options",
        lambda: {**real_options(), "transport": httpx2.MockTransport(auth0)},
    )
    monkeypatch.setattr(oidc, "_JWKS_CACHE", {})
    # The portal's bootstrap step, as the tenant installer runs it.
    monkeypatch.setattr(
        bootstrap_hosted,
        "SessionLocal",
        sessionmaker(
            bind=contract_db.get_bind(), join_transaction_mode="create_savepoint"
        ),
    )
    bootstrap_hosted.main()
    keen.auth0 = auth0
    keen.expires_at = expires_at
    return keen


def _through_proxy(url):
    """The API path nginx serves for an address under the tenant's /api/."""
    assert url.startswith(BASE + "/api/"), url
    return url.removeprefix(BASE + "/api")


def _ready(browser, expires_at):
    """The portal's readiness check before it offers the demo."""
    config = browser.get(_through_proxy(BASE + "/api/v1/auth/methods")).json()
    denied = browser.get(_through_proxy(BASE + "/api/v1/auth/check"))
    demo = config.get("demo", {})
    return (
        config.get("local_enabled") is False
        and config.get("oidc_enabled") is True
        and demo.get("enabled") is True
        and demo.get("expired") is False
        and datetime.fromisoformat(demo.get("expires_at", "").replace("Z", "+00:00"))
        == expires_at
        and denied.status_code == 401
    )


def test_a_provisioned_demo_reports_ready_only_while_its_sign_in_works(
    demo, monkeypatch
):
    browser = demo.client()

    assert _ready(browser, demo.expires_at)

    # The portal keeps waiting while the OIDC client is incomplete.
    monkeypatch.setattr(settings, "oidc_client_secret", "")
    assert browser.get("/v1/auth/methods").json()["oidc_enabled"] is False
    assert not _ready(browser, demo.expires_at)


def _sign_in(demo, browser):
    # The portal's "Open demo" link.
    started = browser.get(
        _through_proxy(BASE + "/api/v1/auth/oidc/start"), follow_redirects=False
    )
    assert started.status_code == 302, started.text
    location = urlsplit(started.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == (
        ISSUER + "authorize"
    )
    query = {k: v[0] for k, v in parse_qs(location.query).items()}
    assert (query["client_id"], query["redirect_uri"]) == (CLIENT_ID, CALLBACK)
    demo.auth0.nonce = query["nonce"]
    # Auth0 returns the visitor to the registered callback.
    return browser.get(
        _through_proxy(query["redirect_uri"]),
        params={"code": "authorization-code", "state": query["state"]},
        follow_redirects=False,
    )


def test_the_portal_link_signs_the_owner_in_through_the_registered_callback(demo):
    browser = demo.client()

    landed = _sign_in(demo, browser)

    assert (landed.status_code, landed.headers["location"]) == (302, "/")
    check = browser.get("/v1/auth/check")
    assert (check.status_code, check.headers["x-keen-user"]) == (204, "workspace-owner")
    # The tenant's Auth0 application authenticates with client_secret_post.
    ((form, authorization),) = demo.auth0.token_requests
    assert (form["client_id"], form["client_secret"], authorization) == (
        CLIENT_ID,
        CLIENT_SECRET,
        None,
    )
    assert form["redirect_uri"] == CALLBACK

    # Signing out returns the visitor to the address Auth0 allows after logout.
    signed_out = browser.post(
        "/v1/auth/oidc/logout",
        headers={
            "Origin": BASE,
            csrf.csrf_header_name(): browser.cookies.get(csrf.csrf_cookie_name()),
        },
    )
    assert signed_out.json() == {"logout_url": BASE + "/login.html"}
    assert browser.get("/v1/auth/check").status_code == 401


def test_another_auth0_account_cannot_enter_the_workspace(demo):
    demo.auth0.subject = "email|someone-else"
    browser = demo.client()

    refused = _sign_in(demo, browser)

    assert (refused.status_code, refused.json()["detail"]) == (
        403,
        "This identity cannot access this workspace",
    )
    assert browser.get("/v1/auth/check").status_code == 401


# --- The demo ends at its expiry -------------------------------------------------

ENDED = {
    "detail": "This KEEN demo has ended.",
    "consultation_url": "https://consult.example/",
}


def _demo_ends_at(monkeypatch, moment):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "demo_expires_at", moment)
    monkeypatch.setattr(settings, "demo_consultation_url", "https://consult.example/")


def test_an_ended_demo_answers_only_its_status(keen, monkeypatch):  # noqa: F811 - fixture imported above
    _demo_ends_at(monkeypatch, datetime.now(timezone.utc) + timedelta(hours=1))
    browser = keen.client(keen.user(role="admin"))
    assert browser.get("/v1/me").status_code == 200

    expired = datetime.now(timezone.utc) - timedelta(seconds=1)
    monkeypatch.setattr(settings, "demo_expires_at", expired)

    # The portal and the sign-in page still read the demo's state.
    methods = browser.get("/v1/auth/methods")
    assert methods.status_code == 200
    assert methods.json()["demo"] == {
        "enabled": True,
        "expires_at": expired.isoformat(),
        "consultation_url": "https://consult.example/",
        "expired": True,
    }
    assert browser.get("/health").status_code == 200
    # Pages behind nginx's sign-in check are refused; everything else has gone.
    check = browser.get("/v1/auth/check")
    assert (check.status_code, check.json()) == (401, ENDED)
    for method, path in [
        ("GET", "/v1/me"),
        ("GET", "/v1/auth/oidc/start"),
        ("GET", "/v1/auth/sso/oidc/start"),
        ("POST", "/v1/auth/login"),
        ("GET", "/v1/events"),
    ]:
        response = browser.request(method, path)
        assert (response.status_code, response.json()) == (410, ENDED), path


def test_a_live_notification_socket_closes_when_the_demo_ends(
    production_client,  # noqa: F811 - fixture imported above
    monkeypatch,
):
    from app.api.routes import questions
    from starlette.websockets import WebSocketDisconnect

    client, _ = _notification_client(production_client, monkeypatch, recheck=60)
    _demo_ends_at(monkeypatch, datetime.now(timezone.utc) + timedelta(seconds=1))

    started = time.monotonic()
    with client.websocket_connect("/ws/notifications", headers=ORIGIN) as socket:
        assert socket.receive_json()["type"] == "questions.unread_replies_count"
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
    assert closed.value.code == 1008
    assert 0.5 < time.monotonic() - started < 10
    assert not questions.notification_hub._user_sockets

    # After the end, a new socket is closed before it reaches the application.
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect("/ws/notifications", headers=ORIGIN):
            pass
    assert refused.value.code == 1008
    assert not questions.notification_hub._user_sockets
