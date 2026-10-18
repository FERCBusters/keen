"""Single sign-on through the assembled application against a fake provider.

The provider is replaced only at the HTTP transport, so KEEN's real code builds
the authorization request (state, nonce and PKCE), exchanges the code with
authlib, fetches and caches the JWKS, verifies the signed ID token, consumes
the browser-bound login state, links the identity to an existing account and
creates the session. GitHub sign-in runs the same way against fake GitHub
endpoints. Synthetic keys and accounts only.
"""

import base64
import hashlib
import time
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.db.models import OidcLoginState, User, UserIdentity, UserSsoEmail
from app.security import oidc
from joserfc import jwt
from joserfc.jwk import RSAKey

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import keen  # noqa: F401

ISSUER = "https://idp.example"
CLIENT_ID = "keen-client"
CLIENT_SECRET = "synthetic-client-secret"
REDIRECT = "https://keen.example/api/v1/auth/sso/oidc/callback"
LABEL = "Example IdP"
GITHUB_ISSUER = "https://github.com/login/oauth"


@pytest.fixture(scope="module")
def signing_key():
    return RSAKey.generate_key(2048, {"kid": "idp-key"})


class FakeProvider:
    """OIDC and GitHub endpoints answering from per-test settings."""

    def __init__(self, key):
        self.key = key
        self.requests = []
        self.token_requests = []
        self.subject = "idp-subject"
        self.claims = {}
        self.nonce = None
        self.token_response = None
        self.jwks_response = None
        self.github_user = {"id": 4242, "login": "octo", "name": "Octo Cat"}
        self.github_emails = httpx2.Response(200, json=[])

    def id_token(self):
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": self.subject,
            "iat": now,
            "exp": now + 300,
            "nonce": self.nonce,
            **self.claims,
        }
        header = {"alg": "RS256", "kid": self.key.kid}
        return jwt.encode(header, claims, self.key, algorithms=["RS256"])

    def __call__(self, request):
        url = str(request.url).split("?")[0]
        self.requests.append((request.method, url))
        if url == f"{ISSUER}/jwks":
            return self.jwks_response or httpx2.Response(
                200, json={"keys": [self.key.as_dict(private=False)]}
            )
        if url in (f"{ISSUER}/token", f"{GITHUB_ISSUER}/access_token"):
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_requests.append((form, request.headers.get("authorization")))
            if self.token_response is not None:
                return self.token_response
            if url.startswith(GITHUB_ISSUER):
                return httpx2.Response(
                    200, json={"access_token": "gh-access", "token_type": "bearer"}
                )
            return httpx2.Response(
                200,
                json={
                    "access_token": "idp-access",
                    "token_type": "Bearer",
                    "id_token": self.id_token(),
                },
            )
        if url == "https://api.github.com/user":
            assert request.headers["authorization"] == "Bearer gh-access"
            return httpx2.Response(200, json=self.github_user)
        if url == "https://api.github.com/user/emails":
            assert request.headers["authorization"] == "Bearer gh-access"
            return self.github_emails
        return httpx2.Response(404)


@pytest.fixture
def sso(keen, monkeypatch, signing_key):  # noqa: F811 - fixture imported above
    for name, value in {
        "oidc_enabled": True,
        "oidc_provider_label": LABEL,
        "oidc_client_id": CLIENT_ID,
        "oidc_client_secret": CLIENT_SECRET,
        "oidc_authorization_endpoint": f"{ISSUER}/authorize",
        "oidc_token_endpoint": f"{ISSUER}/token",
        "oidc_jwks_uri": f"{ISSUER}/jwks",
        "oidc_issuer": ISSUER,
        "oidc_redirect_uri": REDIRECT,
        "oidc_allowed_algs": "RS256",
        "oidc_token_endpoint_auth_method": "client_secret_basic",
        "oidc_allowed_email_domains": "",
        "oidc_auto_link_existing": True,
        "oidc_auto_provision": False,
        "oidc_allow_insecure_http": False,
        "github_sso_enabled": False,
        "github_client_id": "gh-client",
        "github_client_secret": "gh-secret",
        "github_redirect_uri": "https://keen.example/api/v1/auth/sso/github/callback",
        "github_allowed_email_domains": "",
    }.items():
        monkeypatch.setattr(settings, name, value)
    provider = FakeProvider(signing_key)
    real_options = oidc.client_options
    monkeypatch.setattr(
        oidc,
        "client_options",
        lambda: {**real_options(), "transport": httpx2.MockTransport(provider)},
    )
    monkeypatch.setattr(oidc, "_JWKS_CACHE", {})
    db = keen.db

    def account(*, active=True, subject=None, email=None):
        user = keen.user()
        user.is_active = active
        if subject:
            db.add(
                UserIdentity(
                    user_id=user.id,
                    provider="oidc",
                    issuer=ISSUER,
                    subject=subject,
                    email="old@company.example",
                )
            )
        if email:
            db.add(UserSsoEmail(email=email, user_id=user.id))
        db.commit()
        return user

    def start(browser, next_url=None, provider_key="oidc"):
        params = {} if next_url is None else {"next": next_url}
        response = browser.get(
            f"/v1/auth/sso/{provider_key}/start", params=params, follow_redirects=False
        )
        return response

    def authorize(browser, next_url=None, provider_key="oidc"):
        response = start(browser, next_url, provider_key)
        assert response.status_code == 302, response.text
        query = {
            k: v[0]
            for k, v in parse_qs(urlsplit(response.headers["location"]).query).items()
        }
        provider.nonce = query.get("nonce")
        return response, query

    def finish(browser, state, provider_key="oidc", code="authorization-code"):
        return browser.get(
            f"/v1/auth/sso/{provider_key}/callback",
            params={"code": code, "state": state},
            follow_redirects=False,
        )

    def sign_in(browser, next_url=None, provider_key="oidc"):
        _, query = authorize(browser, next_url, provider_key)
        return finish(browser, query["state"], provider_key)

    def states():
        db.expire_all()
        return db.query(OidcLoginState).count()

    return SimpleNamespace(
        keen=keen,
        db=db,
        provider=provider,
        account=account,
        start=start,
        authorize=authorize,
        finish=finish,
        sign_in=sign_in,
        states=states,
    )


def _signed_in_as(browser):
    check = browser.get("/v1/auth/check")
    return check.headers.get("x-keen-user") if check.status_code == 204 else None


def _sets_session(response):
    cookies = response.headers.get_list("set-cookie")
    return any(c.startswith(settings.session_cookie_name + "=") for c in cookies)


# --- The authorization request -------------------------------------------------


def test_sign_in_binds_the_browser_uses_pkce_and_returns_to_the_requested_page(sso):
    user = sso.account(subject="idp-subject")
    browser = sso.keen.client()

    started, query = sso.authorize(browser, "/controls.html?framework=iso")

    location = urlsplit(started.headers["location"])
    assert (location.scheme, location.netloc, location.path) == (
        "https",
        "idp.example",
        "/authorize",
    )
    assert (query["response_type"], query["client_id"], query["redirect_uri"]) == (
        "code",
        CLIENT_ID,
        REDIRECT,
    )
    assert "openid" in query["scope"].split()
    assert query["code_challenge_method"] == "S256"
    row = sso.db.query(OidcLoginState).filter_by(state=query["state"]).one()
    assert row.nonce == query["nonce"]
    assert row.next_url == "/controls.html?framework=iso"
    remaining = row.expires_at - utc_now_naive()
    assert timedelta(minutes=9) < remaining <= timedelta(minutes=10)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(row.code_verifier.encode()).digest()
    ).rstrip(b"=")
    assert query["code_challenge"] == challenge.decode()
    # The state is derived from a secret held only in this browser's cookie.
    binding = browser.cookies.get(oidc.sso_binding_cookie())
    assert query["state"] == oidc._browser_state("oidc", binding)
    cookie = next(
        c
        for c in started.headers.get_list("set-cookie")
        if c.startswith(oidc.sso_binding_cookie() + "=")
    )
    assert all(part in cookie for part in ("HttpOnly", "Max-Age=600", "SameSite=lax"))
    assert started.headers["cache-control"] == "no-store"
    assert sso.provider.requests == []

    landed = sso.finish(browser, query["state"])

    assert landed.status_code == 302, landed.text
    assert landed.headers["location"] == "/controls.html?framework=iso"
    assert landed.headers["cache-control"] == "no-store"
    ((form, authorization),) = sso.provider.token_requests
    assert form == {
        "grant_type": "authorization_code",
        "code": "authorization-code",
        "redirect_uri": REDIRECT,
        "code_verifier": row.code_verifier,
    }
    credentials = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    assert authorization == f"Basic {credentials}"
    assert _signed_in_as(browser) == user.username
    # The state is single use and the binding cookie is withdrawn.
    assert sso.states() == 0
    assert oidc.sso_binding_cookie() not in browser.cookies


@pytest.mark.parametrize("binding", [None, "own"], ids=["no-binding", "own-binding"])
def test_another_browser_cannot_complete_a_sign_in(sso, binding):
    # Login CSRF: a victim's browser is handed an attacker's callback URL.
    user = sso.account(subject="idp-subject")
    initiator = sso.keen.client()
    _, query = sso.authorize(initiator)
    other = sso.keen.client()
    if binding == "own":
        sso.authorize(other)

    refused = sso.finish(other, query["state"])

    assert refused.status_code == 400
    assert refused.json()["detail"] == (
        "SSO browser verification failed; start sign-in again"
    )
    assert not _sets_session(refused) and _signed_in_as(other) is None
    assert sso.provider.token_requests == []
    # The initiating browser can still finish its own sign-in. (The fake
    # provider issues one nonce at a time, so it returns to the initiator's.)
    sso.provider.nonce = query["nonce"]
    assert sso.finish(initiator, query["state"]).status_code == 302
    assert _signed_in_as(initiator) == user.username


def test_the_configured_token_endpoint_authentication_is_used(sso, monkeypatch):
    # Operators choose how the provider expects the client secret.
    monkeypatch.setattr(
        settings, "oidc_token_endpoint_auth_method", " Client_Secret_Post "
    )
    user = sso.account(subject="idp-subject")
    browser = sso.keen.client()

    assert sso.sign_in(browser).status_code == 302
    assert _signed_in_as(browser) == user.username
    ((form, authorization),) = sso.provider.token_requests
    assert (form["client_id"], form["client_secret"]) == (CLIENT_ID, CLIENT_SECRET)
    assert authorization is None


def test_a_replayed_callback_is_refused_even_with_the_original_binding(sso):
    sso.account(subject="idp-subject")
    browser = sso.keen.client()
    _, query = sso.authorize(browser)
    binding = browser.cookies.get(oidc.sso_binding_cookie())
    assert sso.finish(browser, query["state"]).status_code == 302

    replayer = sso.keen.client()
    replayer.cookies.set(oidc.sso_binding_cookie(), binding, domain="keen.example")
    replay = sso.finish(replayer, query["state"])

    assert replay.status_code == 400
    assert replay.json()["detail"] == "SSO state missing or expired"
    assert not _sets_session(replay)
    assert len(sso.provider.token_requests) == 1


@pytest.mark.parametrize(
    "target",
    [
        "//attacker.example/landing",
        "https://attacker.example/",
        "/\\attacker.example",
        "\\\\attacker.example",
        "/safe\r\nSet-Cookie: injected=1",
        "javascript:alert(1)",
        "controls.html",
        "/redirect?to=https://attacker.example",
    ],
)
def test_sign_in_never_lands_off_site(sso, target):
    sso.account(subject="idp-subject")
    browser = sso.keen.client()
    _, query = sso.authorize(browser, target)
    row = sso.db.query(OidcLoginState).filter_by(state=query["state"]).one()

    landed = sso.finish(browser, query["state"])

    # The destination is discarded when sign-in starts, and the user lands on
    # the home page.
    assert row.next_url is None
    assert landed.status_code == 302, landed.text
    assert landed.headers["location"] == "/"
    assert "injected" not in " ".join(landed.headers.get_list("set-cookie"))


def test_a_tampered_stored_destination_is_checked_again_at_the_callback(sso):
    sso.account(subject="idp-subject")
    browser = sso.keen.client()
    _, query = sso.authorize(browser, "/controls.html")
    row = sso.db.query(OidcLoginState).filter_by(state=query["state"]).one()
    row.next_url = "//attacker.example/landing"
    sso.db.commit()

    landed = sso.finish(browser, query["state"])

    assert landed.status_code == 302 and landed.headers["location"] == "/"


# --- Linking the identity to an account ---------------------------------------


def test_an_existing_identity_signs_in_and_refreshes_its_profile(sso):
    user = sso.account(subject="idp-subject")
    sso.provider.claims = {
        "email": "Ada@Company.example",
        "preferred_username": "ada",
        "name": "Ada Lovelace",
    }
    browser = sso.keen.client()

    assert sso.sign_in(browser).status_code == 302
    assert _signed_in_as(browser) == user.username
    sso.db.expire_all()
    identity = sso.db.query(UserIdentity).filter_by(subject="idp-subject").one()
    assert (identity.email, identity.preferred_username, identity.display_name) == (
        "ada@company.example",
        "ada",
        "Ada Lovelace",
    )
    assert sso.db.get(User, user.id).last_login_at is not None


def test_a_deactivated_account_cannot_sign_in_with_its_linked_identity(sso):
    sso.account(subject="idp-subject", active=False)
    browser = sso.keen.client()

    refused = sso.sign_in(browser)

    assert refused.status_code == 403
    assert refused.json()["detail"] == "User is inactive"
    assert not _sets_session(refused) and _signed_in_as(browser) is None
    assert sso.states() == 0


def test_a_first_sign_in_links_only_a_mailbox_both_sides_verified(sso):
    user = sso.account(email="ada@company.example")
    sso.provider.subject = "new-subject"
    browser = sso.keen.client()

    # The provider does not vouch for the address: no link.
    sso.provider.claims = {"email": "ada@company.example", "email_verified": False}
    refused = sso.sign_in(browser)
    assert refused.status_code == 403
    assert (
        refused.json()["detail"] == "No active KEEN account matches this SSO identity"
    )
    assert sso.db.query(UserIdentity).count() == 0

    sso.provider.claims = {"email": "ada@company.example", "email_verified": True}
    assert sso.sign_in(browser).status_code == 302
    assert _signed_in_as(browser) == user.username
    identity = sso.db.query(UserIdentity).one()
    assert (identity.user_id, identity.issuer, identity.subject) == (
        user.id,
        ISSUER,
        "new-subject",
    )


def test_a_deactivated_account_is_never_linked_by_email(sso):
    sso.account(email="ada@company.example", active=False)
    sso.provider.subject = "new-subject"
    sso.provider.claims = {"email": "ada@company.example", "email_verified": True}

    refused = sso.sign_in(sso.keen.client())

    assert refused.status_code == 403
    assert (
        refused.json()["detail"] == "No active KEEN account matches this SSO identity"
    )
    assert sso.db.query(UserIdentity).count() == 0


def test_the_email_domain_allowlist_applies_before_linking(sso, monkeypatch):
    monkeypatch.setattr(settings, "oidc_allowed_email_domains", "Company.example")
    allowed = sso.account(email="ada@company.example")
    sso.account(email="eve@partner.example")
    sso.provider.subject = "eve-subject"
    sso.provider.claims = {"email": "eve@partner.example", "email_verified": True}

    refused = sso.sign_in(sso.keen.client())
    assert refused.status_code == 403
    assert refused.json()["detail"] == "Email domain is not allowed"
    assert sso.db.query(UserIdentity).count() == 0

    sso.provider.subject = "ada-subject"
    sso.provider.claims = {"email": "ada@company.example", "email_verified": True}
    browser = sso.keen.client()
    assert sso.sign_in(browser).status_code == 302
    assert _signed_in_as(browser) == allowed.username


# --- Provider failures ---------------------------------------------------------


def test_provider_error_details_never_reach_the_browser(sso):
    sso.account(subject="idp-subject")
    sso.provider.token_response = httpx2.Response(
        400,
        json={
            "error": "invalid_grant",
            "error_description": f"code rejected for client_secret={CLIENT_SECRET}",
        },
    )
    browser = sso.keen.client()

    refused = sso.sign_in(browser)

    assert refused.status_code == 400
    assert refused.json()["detail"] == (
        "SSO verification failed; start sign-in again or contact an administrator"
    )
    assert CLIENT_SECRET not in refused.text and "invalid_grant" not in refused.text
    assert not _sets_session(refused)


def test_an_id_token_from_another_key_cannot_sign_in(sso):
    sso.account(subject="idp-subject")
    forger = RSAKey.generate_key(2048, {"kid": "idp-key"})
    sso.provider.key, genuine = forger, sso.provider.key
    sso.provider.jwks_response = httpx2.Response(
        200, json={"keys": [genuine.as_dict(private=False)]}
    )

    refused = sso.sign_in(sso.keen.client())

    assert refused.status_code == 400
    assert not _sets_session(refused)


@pytest.mark.parametrize(
    "response,status",
    [
        (httpx2.Response(503, json={"error": "unavailable"}), 400),
        (httpx2.Response(200, json={"not": "a key set"}), 500),
    ],
    ids=["provider-error", "malformed-key-set"],
)
def test_an_unusable_key_set_fails_closed_and_is_not_cached(sso, response, status):
    sso.account(subject="idp-subject")
    sso.provider.jwks_response = response

    refused = sso.sign_in(sso.keen.client())

    assert refused.status_code == status
    assert not _sets_session(refused)
    assert oidc._JWKS_CACHE == {}
    # Once the provider recovers, sign-in works without waiting for a cache.
    sso.provider.jwks_response = None
    assert sso.sign_in(sso.keen.client()).status_code == 302


def test_the_key_set_is_fetched_once_an_hour(sso, monkeypatch):
    sso.account(subject="idp-subject")

    def jwks_fetches():
        return sum(1 for _, url in sso.provider.requests if url.endswith("/jwks"))

    assert sso.sign_in(sso.keen.client()).status_code == 302
    assert sso.sign_in(sso.keen.client()).status_code == 302
    assert jwks_fetches() == 1

    later = oidc._now() + timedelta(hours=1, seconds=1)
    monkeypatch.setattr(oidc, "_now", lambda: later)
    assert sso.sign_in(sso.keen.client()).status_code == 302
    assert jwks_fetches() == 2


# --- Provider configuration ----------------------------------------------------


@pytest.mark.parametrize(
    "changes,status,detail",
    [
        ({"oidc_enabled": False}, 404, "SSO provider disabled"),
        (
            {"oidc_auto_provision": True},
            500,
            "SSO auto-provisioning is not supported; KEEN only allows existing "
            "active users",
        ),
        (
            {"oidc_client_secret": "", "oidc_jwks_uri": "", "oidc_issuer": ""},
            500,
            f"{LABEL} SSO is enabled but required settings are missing: "
            "KEEN_OIDC_CLIENT_SECRET, KEEN_OIDC_JWKS_URI, KEEN_OIDC_ISSUER",
        ),
        (
            # KEEN never derives a callback address from the request.
            {"oidc_redirect_uri": ""},
            500,
            f"{LABEL} SSO is enabled but required settings are missing: "
            "KEEN_OIDC_REDIRECT_URI",
        ),
        (
            {
                "oidc_redirect_uri": "https://elsewhere.example/api/v1/auth/sso/oidc/callback"
            },
            500,
            f"{LABEL} redirect URI must be on KEEN_PUBLIC_BASE_URL",
        ),
        (
            # The public origin followed by more host name is another host.
            {"oidc_redirect_uri": "https://keen.example.attacker.example/callback"},
            500,
            f"{LABEL} redirect URI must be on KEEN_PUBLIC_BASE_URL",
        ),
        (
            {"oidc_redirect_uri": "http://keen.example/api/v1/auth/sso/oidc/callback"},
            500,
            f"{LABEL} redirect URI must be on KEEN_PUBLIC_BASE_URL",
        ),
        (
            {"oidc_token_endpoint": "http://idp.example/token"},
            500,
            f"{LABEL} token endpoint must use HTTPS",
        ),
        (
            {
                "oidc_jwks_uri": "ftp://idp.example/jwks",
                "oidc_allow_insecure_http": True,
            },
            500,
            f"{LABEL} JWKS URI must use HTTPS",
        ),
        (
            # A public-client method would send no client secret at all.
            {"oidc_token_endpoint_auth_method": "none"},
            500,
            f"{LABEL} token endpoint authentication must be client_secret_basic "
            "or client_secret_post",
        ),
    ],
    ids=[
        "disabled",
        "auto-provision",
        "incomplete",
        "no-redirect-uri",
        "redirect-elsewhere",
        "redirect-on-a-longer-host",
        "redirect-over-http",
        "plain-http",
        "other-scheme",
        "no-client-authentication",
    ],
)
def test_misconfigured_sign_in_fails_closed_before_contacting_the_provider(
    sso, monkeypatch, changes, status, detail
):
    for name, value in changes.items():
        monkeypatch.setattr(settings, name, value)
    browser = sso.keen.client()

    response = sso.start(browser)

    assert response.status_code == status
    assert response.json()["detail"] == detail
    assert sso.states() == 0 and sso.provider.requests == []
    methods = browser.get("/v1/auth/methods").json()
    assert (methods["sso_enabled"], methods["sso_providers"]) == (False, [])


def test_only_working_providers_are_offered_on_the_sign_in_page(sso, monkeypatch):
    methods = sso.keen.client().get("/v1/auth/methods").json()
    assert methods["sso_providers"] == [
        {
            "key": "oidc",
            "label": LABEL,
            "kind": "oidc",
            "start_url": "/api/v1/auth/sso/oidc/start",
        }
    ]
    assert methods["sso_enabled"] is True
    # The sign-in page reads only the provider list; there is no second,
    # provider-specific description of the same sign-in.
    assert set(methods) == {
        "demo",
        "local_enabled",
        "ldap_enabled",
        "remote_user_enabled",
        "sso_enabled",
        "sso_providers",
    }

    # Plain HTTP is accepted only with the explicit development setting.
    monkeypatch.setattr(settings, "oidc_token_endpoint", "http://idp.example/token")
    assert sso.keen.client().get("/v1/auth/methods").json()["sso_providers"] == []
    monkeypatch.setattr(settings, "oidc_allow_insecure_http", True)
    offered = sso.keen.client().get("/v1/auth/methods").json()["sso_providers"]
    assert [item["key"] for item in offered] == ["oidc"]


# --- GitHub --------------------------------------------------------------------


@pytest.fixture
def github(sso, monkeypatch):
    monkeypatch.setattr(settings, "github_sso_enabled", True)
    return sso


def _github_sign_in(sso, browser=None):
    browser = browser or sso.keen.client()
    _, query = sso.authorize(browser, provider_key="github")
    return browser, query, sso.finish(browser, query["state"], "github")


def test_github_sign_in_links_a_primary_verified_address(github):
    user = github.account(email="ada@company.example")
    github.provider.github_emails = httpx2.Response(
        200,
        json=[
            {"email": "ada@company.example", "primary": True, "verified": True},
            {"email": "old@elsewhere.example", "primary": False, "verified": True},
        ],
    )

    browser, query, landed = _github_sign_in(github)

    assert landed.status_code == 302, landed.text
    assert _signed_in_as(browser) == user.username
    assert (
        query["redirect_uri"] == "https://keen.example/api/v1/auth/sso/github/callback"
    )
    assert "nonce" not in query
    ((form, _),) = github.provider.token_requests
    # GitHub receives its client credentials in the form, with the PKCE verifier.
    assert (form["client_id"], form["client_secret"]) == ("gh-client", "gh-secret")
    assert form["code_verifier"]
    identity = github.db.query(UserIdentity).one()
    assert (identity.provider, identity.issuer, identity.subject) == (
        "github",
        GITHUB_ISSUER,
        "4242",
    )


@pytest.mark.parametrize(
    "emails",
    [
        # The address is listed but not verified by GitHub.
        httpx2.Response(
            200,
            json=[{"email": "ada@company.example", "primary": True, "verified": False}],
        ),
        # Verified, but not the account's primary address.
        httpx2.Response(
            200,
            json=[
                {"email": "ada@company.example", "primary": False, "verified": True},
                {"email": "eve@attacker.example", "primary": True, "verified": True},
            ],
        ),
        # GitHub withholds the address list.
        httpx2.Response(403, json={"message": "Resource not accessible"}),
    ],
    ids=["unverified", "not-primary", "list-withheld"],
)
def test_a_public_github_profile_address_cannot_claim_an_account(github, emails):
    # Anyone can put another person's address on a public GitHub profile.
    github.account(email="ada@company.example")
    github.provider.github_user = {
        "id": 666,
        "login": "eve",
        "email": "ada@company.example",
    }
    github.provider.github_emails = emails

    browser, _, refused = _github_sign_in(github)

    assert refused.status_code == 403
    assert (
        refused.json()["detail"] == "No active KEEN account matches this SSO identity"
    )
    assert github.db.query(UserIdentity).count() == 0
    assert _signed_in_as(browser) is None


@pytest.mark.parametrize(
    "user_response",
    [
        httpx2.Response(200, json={"login": "octo"}),
        httpx2.Response(200, json={"id": 4242}),
        httpx2.Response(200, json=["not", "an", "object"]),
        httpx2.Response(500, json={"message": "Server error"}),
    ],
    ids=["no-id", "no-login", "not-an-object", "server-error"],
)
def test_incomplete_github_profiles_cannot_sign_in(github, monkeypatch, user_response):
    github.account(email="ada@company.example")
    original = github.provider.__call__

    def provider(request):
        if str(request.url) == "https://api.github.com/user":
            github.provider.requests.append((request.method, str(request.url)))
            return user_response
        return original(request)

    monkeypatch.setattr(
        oidc,
        "client_options",
        lambda: {"timeout": 10.0, "transport": httpx2.MockTransport(provider)},
    )

    browser, _, refused = _github_sign_in(github)

    assert refused.status_code == 400
    assert not _sets_session(refused) and _signed_in_as(browser) is None
    assert github.db.query(UserIdentity).count() == 0
