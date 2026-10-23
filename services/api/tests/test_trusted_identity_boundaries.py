"""Who a request is: the trusted-proxy header, session cookies and hosted owners.

Keen accepts a proxy's identity header only from a configured proxy address,
only when native single sign-on is off, and only in one well-formed copy. A
session cookie identifies its user while that user is active and, in a hosted
installation, only while the session belongs to the workspace owner's OIDC
sign-in. Requests run through the production middleware.
"""

import pytest
from app.core.config import settings
from app.db.models import UserIdentity
from app.security import auth, sessions
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

# Imported fixture: the production middleware with an in-memory session store.
from tests.test_security_design_contracts import production_client  # noqa: F401


@pytest.fixture
def proxy(monkeypatch):
    monkeypatch.setattr(settings, "trust_remote_user", True)
    monkeypatch.setattr(settings, "oidc_enabled", False)
    monkeypatch.setattr(settings, "security_trusted_proxy_cidrs", "127.0.0.1/32")
    monkeypatch.setattr(settings, "remote_user_header_name", "REMOTE_USER")


def _identity(headers, peer="127.0.0.1"):
    return auth._remote_user_from_headers(Headers(headers), peer=peer)


def test_native_single_sign_on_turns_the_proxy_header_off(proxy, monkeypatch):
    assert _identity({"REMOTE_USER": "alice"}) == "alice"

    monkeypatch.setattr(settings, "oidc_enabled", True)

    assert _identity({"REMOTE_USER": "alice"}) is None


@pytest.mark.parametrize(
    "value",
    ["", "   ", "alice\nadmin", "alice\x00", "ali\x7fce", "alice\tadmin"],
    ids=["empty", "blank", "newline", "nul", "delete", "tab"],
)
def test_unusable_header_values_identify_no_one(proxy, value):
    assert _identity({"REMOTE_USER": value}) is None


def test_only_the_configured_header_names_the_user(proxy, monkeypatch):
    monkeypatch.setattr(settings, "remote_user_header_name", "X-Forwarded-User")
    assert _identity({"X-Forwarded-User": " alice "}) == "alice"
    assert _identity({"REMOTE_USER": "alice"}) is None
    assert _identity({}) is None
    # A blank setting falls back to the standard header name.
    monkeypatch.setattr(settings, "remote_user_header_name", "   ")
    assert _identity({"REMOTE_USER": "alice"}) == "alice"


@pytest.mark.parametrize("cidrs", ["127.0.0.1/33", "not-an-address", ""])
def test_an_unusable_proxy_list_trusts_no_one(proxy, monkeypatch, cidrs):
    monkeypatch.setattr(settings, "security_trusted_proxy_cidrs", cidrs)
    assert _identity({"REMOTE_USER": "alice"}) is None


def test_the_proxy_names_only_active_accounts(production_client, proxy, contract_db):  # noqa: F811 - fixture imported above
    from app import main

    _, _, _, reader = production_client
    client = TestClient(
        main.app, base_url="https://keen.example", client=("127.0.0.1", 1)
    )
    try:
        assert (
            client.get(
                "/v1/auth/check", headers={"REMOTE_USER": reader.username}
            ).status_code
            == 204
        )
        assert (
            client.get("/v1/auth/check", headers={"REMOTE_USER": "nobody"}).status_code
            == 401
        )
        # A proxy identity Keen cannot use is not replaced by a session cookie.
        _, redis, admin, _ = production_client
        sid = sessions.create_session(redis, user_id=str(admin.id), ttl_seconds=300)
        client.cookies.set(settings.session_cookie_name, sid)
        assert client.get("/v1/auth/check").status_code == 204
        assert (
            client.get("/v1/auth/check", headers={"REMOTE_USER": "nobody"}).status_code
            == 401
        )
        reader.is_active = False
        contract_db.commit()
        assert (
            client.get(
                "/v1/auth/check", headers={"REMOTE_USER": reader.username}
            ).status_code
            == 401
        )
    finally:
        client.close()


def test_a_session_naming_no_valid_user_id_is_refused(production_client):  # noqa: F811 - fixture imported above
    client, redis, *_ = production_client
    sid = sessions.create_session(redis, user_id="not-a-uuid", ttl_seconds=300)
    client.cookies.set(settings.session_cookie_name, sid)

    assert client.get("/v1/auth/check").status_code == 401


def test_a_hosted_workspace_admits_only_its_owners_sign_in(
    production_client,  # noqa: F811 - fixture imported above
    contract_db,
    monkeypatch,
):
    client, redis, admin, reader = production_client
    issuer, subject = "https://tenant.auth0.example/", "email|owner"
    monkeypatch.setattr(settings, "hosted_mode", True)
    monkeypatch.setattr(settings, "oidc_issuer", issuer)
    monkeypatch.setattr(settings, "hosted_owner_subject", subject)
    contract_db.add(
        UserIdentity(user_id=admin.id, provider="oidc", issuer=issuer, subject=subject)
    )
    contract_db.commit()

    def check(user, auth_method):
        sid = sessions.create_session(
            redis, user_id=str(user.id), ttl_seconds=300, auth_method=auth_method
        )
        client.cookies.set(settings.session_cookie_name, sid)
        return client.get("/v1/auth/check").status_code, sid

    status, _ = check(admin, "oidc")
    assert status == 204
    # Another account, or the owner's account signed in another way, is
    # refused, and the session is withdrawn.
    for user, method in [(reader, "oidc"), (admin, "local")]:
        status, sid = check(user, method)
        assert status == 401, (user.username, method)
        assert sessions.get_session(redis, sid) is None
