"""Who a request is: the trusted-proxy header, session cookies and hosted owners.

Keen accepts a proxy's identity header only from a configured proxy address,
only when native single sign-on is off, and only in one well-formed copy. A
session cookie identifies its user while that user is active and, in a hosted
installation, only while the session belongs to the workspace owner's OIDC
sign-in. Requests run through the production middleware.
"""

import pytest
from app.core.config import Settings, settings
from app.db.models import UserIdentity
from app.security import auth, sessions
from app.security.auth import cookie_name as session_cookie_name
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.datastructures import Headers

# Imported fixture: the production middleware with an in-memory session store.
from tests.test_security_design_contracts import production_client  # noqa: F401


@pytest.fixture
def proxy(monkeypatch):
    monkeypatch.setattr(settings, "trust_remote_user", True)
    monkeypatch.setattr(settings, "oidc_enabled", False)
    monkeypatch.setattr(settings, "remote_user_trusted_cidrs", "127.0.0.1/32")
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


@pytest.mark.parametrize(
    "peer,values,expected",
    [
        ("127.0.0.1", ["alice"], "alice"),
        ("192.0.2.1", ["alice"], None),
        (None, ["alice"], None),
        ("127.0.0.1", ["attacker", "alice"], None),
    ],
    ids=["trusted-peer", "untrusted-peer", "no-peer", "two-copies"],
)
def test_the_header_counts_only_from_a_trusted_direct_peer_in_one_copy(
    proxy, peer, values, expected
):
    # A forged second copy, or one sent past the proxy, names no one.
    headers = Headers(raw=[(b"remote_user", v.encode()) for v in values])
    assert auth._remote_user_from_headers(headers, peer=peer) == expected


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
    monkeypatch.setattr(settings, "remote_user_trusted_cidrs", cidrs)
    assert _identity({"REMOTE_USER": "alice"}) is None


def test_a_proxy_trusted_for_client_addresses_names_no_account(proxy, monkeypatch):
    """Forwarding trust and identity trust are separate lists: the Compose
    default trusts the network gateway to forward addresses, and every process
    on the Docker host connects through it."""
    monkeypatch.setattr(settings, "security_trusted_proxy_cidrs", "172.30.65.1/32")
    monkeypatch.setattr(settings, "remote_user_trusted_cidrs", "172.30.65.10/32")
    assert _identity({"REMOTE_USER": "alice"}, peer="172.30.65.1") is None
    assert _identity({"REMOTE_USER": "alice"}, peer="172.30.65.10") == "alice"
    # The original defect: the forwarding list alone named accounts.
    monkeypatch.setattr(settings, "remote_user_trusted_cidrs", "")
    assert _identity({"REMOTE_USER": "alice"}, peer="172.30.65.1") is None


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
        refused = client.get("/v1/auth/check", headers={"REMOTE_USER": "nobody"})
        assert (refused.status_code, refused.json()) == (
            401,
            {"detail": "Not authenticated"},
        )
        # A proxy identity Keen cannot use is not replaced by a session cookie.
        _, redis, admin, _ = production_client
        sid = sessions.create_session(redis, user_id=str(admin.id), ttl_seconds=300)
        client.cookies.set(session_cookie_name(), sid)
        assert client.get("/v1/auth/check").status_code == 204
        refused = client.get("/v1/auth/check", headers={"REMOTE_USER": "nobody"})
        assert (refused.status_code, refused.json()) == (
            401,
            {"detail": "Not authenticated"},
        )
        reader.is_active = False
        contract_db.commit()
        refused = client.get("/v1/auth/check", headers={"REMOTE_USER": reader.username})
        assert (refused.status_code, refused.json()) == (
            401,
            {"detail": "Not authenticated"},
        )
    finally:
        client.close()


def test_a_session_naming_no_valid_user_id_is_refused(production_client):  # noqa: F811 - fixture imported above
    client, redis, *_ = production_client
    sid = sessions.create_session(redis, user_id="not-a-uuid", ttl_seconds=300)
    client.cookies.set(session_cookie_name(), sid)

    refused = client.get("/v1/auth/check")
    assert (refused.status_code, refused.json()) == (
        401,
        {"detail": "Not authenticated"},
    )


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
        client.cookies.set(session_cookie_name(), sid)
        return client.get("/v1/auth/check").status_code, sid

    status, _ = check(admin, "oidc")
    assert status == 204
    # Another account, or the owner's account signed in another way, is
    # refused, and the session is withdrawn.
    for user, method in [(reader, "oidc"), (admin, "local")]:
        status, sid = check(user, method)
        assert status == 401, (user.username, method)
        assert sessions.get_session(redis, sid) is None


@pytest.mark.parametrize(
    "values,problem",
    [
        ({"trust_remote_user": True}, "KEEN_REMOTE_USER_TRUSTED_CIDRS"),
        (
            {"trust_remote_user": True, "remote_user_trusted_cidrs": " , "},
            "KEEN_REMOTE_USER_TRUSTED_CIDRS",
        ),
        (
            # The forwarding list is no substitute.
            {
                "trust_remote_user": True,
                "security_trusted_proxy_cidrs": "172.30.65.10/32",
            },
            "KEEN_REMOTE_USER_TRUSTED_CIDRS",
        ),
        ({"remote_user_trusted_cidrs": "not-an-address"}, "not-an-address"),
        ({"remote_user_trusted_cidrs": "10.0.0.1/33"}, "10.0.0.1/33"),
    ],
)
def test_remote_user_mode_does_not_start_without_its_own_proxy_list(values, problem):
    with pytest.raises(ValidationError) as refused:
        Settings(**values)
    assert problem in str(refused.value)


def test_remote_user_mode_starts_with_its_proxies_named():
    assert Settings().remote_user_trusted_cidrs == ""
    configured = Settings(
        trust_remote_user=True, remote_user_trusted_cidrs="10.0.0.5/32, 10.1.0.0/16"
    )
    assert configured.remote_user_trusted_cidrs == "10.0.0.5/32, 10.1.0.0/16"
