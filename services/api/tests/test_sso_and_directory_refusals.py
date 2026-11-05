"""Single sign-on and directory sign-in refusals.

OIDC and GitHub sign-ins run through the assembled application against the
fake provider of test_sso_sign_in_flows, replaced only at the HTTP transport;
LDAP sign-ins run against its fake directory connection. Concurrent changes
are made from inside the request's own transaction at the point where another
request would make them. Each test checks the refusal, that no session was
issued and that no identity link or account was stored.
"""

import asyncio
from datetime import timedelta

import httpx2
import pytest
from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.db.models import OidcLoginState, User, UserIdentity
from app.security import ldap, oidc
from app.security.sso_http import MAX_RESPONSE_BYTES, SsoTransport
from app.services import people
from fastapi import HTTPException
from sqlalchemy import insert
from sqlalchemy.exc import OperationalError

from tests.test_assembled_authority_regressions import keen  # noqa: F401 - fixture
from tests.test_ldap_roles_export import directory  # noqa: F401 - fixture
from tests.test_sso_sign_in_flows import (  # noqa: F401 - fixtures
    ISSUER,
    _github_sign_in,
    _sets_session,
    _signed_in_as,
    github,
    signing_key,
    sso,
)

VERIFICATION_FAILED = (
    "SSO verification failed; start sign-in again or contact an administrator"
)


def _login_state(db, state, *, expires_in):
    row = OidcLoginState(
        state=state,
        nonce="synthetic-nonce",
        code_verifier="synthetic-verifier",
        expires_at=utc_now_naive() + expires_in,
    )
    db.add(row)
    db.commit()
    return row


def _route_session(monkeypatch):
    """The callback's database session, once it resolves the identity."""
    captured = {}
    resolve = oidc._get_or_create_user_for_identity

    def capturing(db, **kwargs):
        captured["db"] = db
        return resolve(db, **kwargs)

    monkeypatch.setattr(oidc, "_get_or_create_user_for_identity", capturing)
    return captured


# -- login state ------------------------------------------------------------


def test_a_blank_login_state_never_redeems_a_stored_state(contract_db):
    _login_state(contract_db, "", expires_in=timedelta(minutes=5))

    assert oidc.pop_login_state(contract_db, state="   ") is None
    assert oidc.pop_login_state(contract_db, state="") is None

    assert contract_db.query(OidcLoginState).filter_by(state="").count() == 1


def test_an_expired_login_state_is_refused_when_its_removal_fails(
    contract_db, monkeypatch
):
    _login_state(contract_db, "s" * 64, expires_in=-timedelta(seconds=1))

    def unavailable():
        raise OperationalError("COMMIT", {}, Exception("synthetic database failure"))

    monkeypatch.setattr(contract_db, "commit", unavailable)

    assert oidc.pop_login_state(contract_db, state="s" * 64) is None
    monkeypatch.undo()
    # The failed removal was rolled back; the session remains usable.
    assert contract_db.query(OidcLoginState).filter_by(state="s" * 64).count() == 1


# -- identity resolution races ----------------------------------------------


def test_an_account_deleted_during_sign_in_is_refused(sso, monkeypatch):  # noqa: F811
    user = sso.account(subject="idp-subject")
    captured = _route_session(monkeypatch)
    allowed = oidc._allowed_by_domain

    def deleted_meanwhile(provider, claims):
        # An administrator deletes the account after its identity was read.
        db = captured["db"]
        db.query(User).filter(User.id == user.id).delete(synchronize_session=False)
        db.flush()
        return allowed(provider, claims)

    monkeypatch.setattr(oidc, "_allowed_by_domain", deleted_meanwhile)
    browser = sso.keen.client()

    refused = sso.sign_in(browser)

    assert (refused.status_code, refused.json()) == (
        403,
        {"detail": "User is inactive"},
    )
    assert not _sets_session(refused) and _signed_in_as(browser) is None


def test_an_identity_linked_concurrently_is_refused(sso, monkeypatch):  # noqa: F811
    user = sso.account(email="ada@company.example")
    other = sso.keen.user()
    sso.provider.claims = {"email": "ada@company.example", "email_verified": True}
    captured = _route_session(monkeypatch)
    verified = oidc._email_is_verified

    def linked_meanwhile(provider, claims):
        # Another callback links this provider identity first.
        captured["db"].execute(
            insert(UserIdentity).values(
                user_id=other.id,
                provider="oidc",
                issuer=ISSUER,
                subject="idp-subject",
                claims={},
            )
        )
        return verified(provider, claims)

    monkeypatch.setattr(oidc, "_email_is_verified", linked_meanwhile)
    browser = sso.keen.client()

    refused = sso.sign_in(browser)

    assert (refused.status_code, refused.json()) == (
        403,
        {"detail": "SSO identity link conflict"},
    )
    assert not _sets_session(refused) and _signed_in_as(browser) is None
    sso.db.expire_all()
    assert sso.db.query(UserIdentity).filter_by(user_id=user.id).count() == 0


# -- provider answers -------------------------------------------------------


def test_a_callback_for_a_provider_without_an_issuer_contacts_no_one(
    sso,  # noqa: F811
    monkeypatch,
):
    # Configuration refuses such a provider first (see
    # test_misconfigured_sign_in_fails_closed_before_contacting_the_provider);
    # the callback refuses it too, since an empty issuer list would switch off
    # the token's issuer check.
    monkeypatch.setattr(settings, "oidc_issuer", " , ")
    provider = oidc.get_sso_provider("oidc")
    contacted = []
    monkeypatch.setattr(oidc, "_new_client", lambda *a, **k: contacted.append(a))

    with pytest.raises(HTTPException) as refused:
        asyncio.run(oidc._handle_oidc_callback("code", sso.db, provider, None))

    assert (refused.value.status_code, refused.value.detail) == (
        500,
        "SSO issuer is not configured",
    )
    assert contacted == []
    assert sso.db.query(UserIdentity).count() == 0


@pytest.mark.parametrize(
    "emails",
    [
        b'{"email": "ada@company.example", "primary": true, "verified": true}',
        b"42",
        b"null",
    ],
    ids=["object", "number", "null"],
)
def test_a_github_address_list_of_another_shape_verifies_no_address(
    github,  # noqa: F811
    emails,
):
    github.account(email="ada@company.example")
    github.provider.github_user = {
        "id": 777,
        "login": "octo",
        "email": "ada@company.example",
    }
    github.provider.github_emails = httpx2.Response(
        200, content=emails, headers={"Content-Type": "application/json"}
    )

    browser, _, refused = _github_sign_in(github)

    assert (refused.status_code, refused.json()) == (
        403,
        {"detail": "No active Keen account matches this SSO identity"},
    )
    assert github.db.query(UserIdentity).count() == 0
    assert _signed_in_as(browser) is None


class Body(httpx2.AsyncByteStream):
    """A provider response body that arrives in chunks."""

    def __init__(self, size):
        self.size, self.closed = size, False

    async def __aiter__(self):
        remaining = self.size
        while remaining:
            chunk = min(64 * 1024, remaining)
            remaining -= chunk
            yield b"x" * chunk

    async def aclose(self):
        self.closed = True


def test_identity_provider_responses_are_read_through_the_size_limit():
    sent, bodies = [], []

    def provider(request):
        sent.append(request.headers["accept-encoding"])
        bodies.append(Body(int(request.url.params["size"])))
        return httpx2.Response(200, stream=bodies[-1])

    async def fetch(size):
        transport = SsoTransport()
        await transport.transport.aclose()
        transport.transport = httpx2.MockTransport(provider)
        async with httpx2.AsyncClient(transport=transport) as client:
            response = await client.get(
                "https://idp.example/jwks", params={"size": size}
            )
            return response.content

    assert asyncio.run(fetch(MAX_RESPONSE_BYTES)) == b"x" * MAX_RESPONSE_BYTES
    with pytest.raises(ValueError, match="^Identity provider response exceeds 1 MiB$"):
        asyncio.run(fetch(MAX_RESPONSE_BYTES + 1))
    assert sent == ["identity", "identity"]
    # The body is closed whether it was read whole or refused part-way.
    assert [body.closed for body in bodies] == [True, True]


# -- directory sign-in ------------------------------------------------------


def test_a_directory_address_too_long_to_name_its_accounts_is_unavailable(
    directory,  # noqa: F811
    monkeypatch,
):
    state, events, _ = directory
    monkeypatch.setattr(settings, "ldap_url", "ldaps://" + "d" * 500 + ".example:636")
    store = ldap.get_valkey()

    with pytest.raises(HTTPException) as refused:
        ldap.directory_identity("alice", "synthetic-user-password")

    assert refused.value.status_code == 503
    assert refused.value.detail == (
        "LDAP authentication unavailable; check directory configuration and connectivity"
    )
    # The directory saw the service bind and search alone; the password stayed here.
    assert ("bind", "uid=alice,ou=People,dc=example") not in events
    assert store.keys() == []


def test_a_directory_account_created_concurrently_is_refused(
    contract_db,
    directory,  # noqa: F811
    monkeypatch,
):
    other = User(username="local-other", password_hash="!", role="normal")
    contract_db.add(other)
    contract_db.commit()
    provision = people.ensure_person_for_user

    def provisioned_meanwhile(db, user):
        # Another sign-in by the same directory entry links it first.
        db.execute(
            insert(UserIdentity).values(
                user_id=other.id,
                provider="ldap",
                issuer="ldap:ldap://directory.example:389",
                subject="immutable-id",
                claims={},
            )
        )
        return provision(db, user)

    monkeypatch.setattr(people, "ensure_person_for_user", provisioned_meanwhile)

    with pytest.raises(HTTPException) as refused:
        ldap.authenticate_ldap(contract_db, "alice", "synthetic-user-password")

    assert refused.value.status_code == 409
    assert refused.value.detail == (
        "Account was created concurrently; please sign in again"
    )
    assert contract_db.query(User).filter(User.username.like("ldap:%")).count() == 0
    assert contract_db.query(UserIdentity).count() == 0
