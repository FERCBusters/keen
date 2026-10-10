"""Exercise real JWT verification through the callback, with no external IdP."""

import asyncio
import base64
import hashlib
import json
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from app.security import oidc
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import ECKey, RSAKey
from starlette.requests import Request


@pytest.fixture(scope="module")
def signing_key():
    return RSAKey.generate_key(2048, {"kid": "issuer-key"})


@pytest.fixture
def callback(monkeypatch, signing_key):
    provider = replace(
        oidc._oidc_provider(),
        issuer="https://issuer.example",
        client_id="keen-client",
        redirect_uri="https://keen.example/api/v1/oidc/callback",
        token_endpoint="https://issuer.example/token",
        jwks_uri="https://issuer.example/jwks",
        id_token_leeway_seconds=0,
    )
    row = SimpleNamespace(
        nonce="browser-nonce", code_verifier="pkce-secret", next_url="/controls.html"
    )
    request = Request(
        {
            "type": "http",
            "scheme": "https",
            "server": ("keen.example", 443),
            "path": "/api/v1/oidc/callback",
            "query_string": b"code=code&state=state",
            "headers": [],
        }
    )
    now = int(time.time())
    claims = {
        "iss": provider.issuer,
        "aud": provider.client_id,
        "sub": "subject",
        "iat": now,
        "exp": now + 300,
        "nonce": row.nonce,
        "at_hash": base64.urlsafe_b64encode(
            hashlib.sha256(b"access-token").digest()[:16]
        )
        .rstrip(b"=")
        .decode(),
    }
    user = object()
    provision = Mock(return_value=user)
    monkeypatch.setattr(oidc, "_get_or_create_user_for_identity", provision)
    jwks = AsyncMock(return_value={"keys": [signing_key.as_dict(private=False)]})
    monkeypatch.setattr(oidc, "_get_jwks", jwks)
    fetch = AsyncMock()
    monkeypatch.setattr(
        oidc,
        "_new_client",
        lambda *a, **kw: SimpleNamespace(fetch_token=fetch, aclose=AsyncMock()),
    )

    def run(
        overrides=None,
        *,
        drop=(),
        raw=None,
        key=signing_key,
        alg="RS256",
        selected_provider=None,
    ):
        digest = hashlib.new("sha" + alg[-3:], b"access-token").digest()
        at_hash = (
            base64.urlsafe_b64encode(digest[: len(digest) // 2]).rstrip(b"=").decode()
        )
        payload = {**claims, "at_hash": at_hash, **(overrides or {})}
        for claim in drop:
            payload.pop(claim, None)
        encoded = (
            raw
            if raw is not None
            else jwt.encode(
                {"alg": alg, "kid": key.kid}, payload, key, algorithms=[alg]
            )
        )
        fetch.return_value = {"id_token": encoded, "access_token": "access-token"}
        result = asyncio.run(
            oidc._handle_oidc_callback(
                request, None, selected_provider or provider, row
            )
        )
        assert result == (user, "/controls.html", encoded)
        assert fetch.call_args.kwargs["code_verifier"] == "pkce-secret"
        return result

    return SimpleNamespace(
        run=run, provider=provider, claims=claims, provision=provision, jwks=jwks
    )


@pytest.mark.parametrize("alg", ["RS256", "RS384", "RS512", "PS256"])
def test_valid_signatures_and_claims(callback, alg):
    callback.run(
        alg=alg, selected_provider=replace(callback.provider, allowed_algs=(alg,))
    )
    callback.provision.assert_called_once()
    assert callback.provision.call_args.kwargs["subject"] == "subject"


def test_ec_signature(callback):
    key = ECKey.generate_key("P-256", {"kid": "ec-key"})
    callback.jwks.return_value = {"keys": [key.as_dict(private=False)]}
    callback.run(
        key=key,
        alg="ES256",
        selected_provider=replace(callback.provider, allowed_algs=("ES256",)),
    )


@pytest.mark.parametrize(
    "change",
    [
        {"iss": "https://wrong.example"},
        {"aud": "other-client"},
        {"nonce": "other-browser"},
        {"exp": 1},
        {"iat": 4102444800},
        {"nbf": 4102444800},
        {"azp": "other-client"},
        {"aud": ["keen-client", "other-client"]},
        {"at_hash": "incorrect"},
    ],
)
def test_invalid_claims_cannot_provision(callback, change):
    with pytest.raises(JoseError):
        callback.run(change)
    callback.provision.assert_not_called()


@pytest.mark.parametrize("claim", ["iss", "aud", "sub", "exp", "iat", "nonce"])
def test_required_claims_cannot_be_omitted(callback, claim):
    with pytest.raises(JoseError):
        callback.run(drop=[claim])
    callback.provision.assert_not_called()


def test_single_trusted_audience_and_optional_at_hash(callback):
    callback.run({"aud": ["keen-client"], "azp": "keen-client"}, drop=["at_hash"])


def test_authorized_party_does_not_trust_additional_audiences(callback):
    with pytest.raises(Exception) as error:
        callback.run({"aud": ["keen-client", "other-client"], "azp": "keen-client"})
    assert error.value.__class__.__name__ == "InvalidClaimError"
    callback.provision.assert_not_called()


def test_configured_issuer_alias_and_leeway(callback):
    provider = replace(
        callback.provider,
        issuer="https://issuer.example,https://alias.example",
        id_token_leeway_seconds=60,
    )
    callback.run(
        {"iss": "https://alias.example", "exp": int(time.time()) - 20},
        selected_provider=provider,
    )
    assert (
        callback.provision.call_args.kwargs["claims"]["iss"] == "https://issuer.example"
    )


def test_wrong_signature_cannot_provision(callback):
    key = RSAKey.generate_key(2048, {"kid": "issuer-key"})
    with pytest.raises(JoseError):
        callback.run(key=key)
    callback.provision.assert_not_called()


def test_unsigned_token_cannot_provision(callback):
    encode = lambda value: (
        base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()
    )
    token = encode({"alg": "none"}) + "." + encode(callback.claims) + "."
    with pytest.raises(JoseError):
        callback.run(raw=token)
    callback.provision.assert_not_called()


def test_oauth_httpx2_transport(monkeypatch):
    import httpx2

    provider = replace(
        oidc._oidc_provider(),
        client_id="client",
        client_secret="secret",
        token_endpoint_auth_method="client_secret_post",
    )
    from urllib.parse import parse_qs

    requests = []

    def token_endpoint(request):
        requests.append(request)
        return httpx2.Response(
            200, json={"access_token": "access", "token_type": "Bearer"}
        )

    client_class = oidc.AsyncOAuth2Client
    monkeypatch.setattr(
        oidc,
        "AsyncOAuth2Client",
        lambda **kwargs: client_class(
            **{k: v for k, v in kwargs.items() if k != "transport"},
            transport=httpx2.MockTransport(token_endpoint),
        ),
    )

    async def exercise():
        client = oidc._new_client(
            provider, redirect_uri="https://keen.example/callback"
        )
        assert isinstance(client, httpx2.AsyncClient)
        assert client.timeout.read == 10
        try:
            url, state = client.create_authorization_url(
                "https://issuer.example/authorize",
                state="state",
                code_verifier="x" * 64,
                nonce="nonce",
            )
            assert "code_challenge_method=S256" in url
            assert state == "state"
            token = await client.fetch_token(
                "https://issuer.example/token",
                code="authorization-code",
                code_verifier="x" * 64,
            )
            assert token["access_token"] == "access"
            form = parse_qs(requests[0].content.decode())
            assert form["code"] == ["authorization-code"]
            assert form["code_verifier"] == ["x" * 64]
            assert form["client_id"] == ["client"]
            assert form["client_secret"] == ["secret"]
        finally:
            await client.aclose()

    asyncio.run(exercise())


def test_unknown_signing_key_cannot_provision(callback):
    key = RSAKey.generate_key(2048, {"kid": "unknown-key"})
    with pytest.raises(JoseError):
        callback.run(key=key)
    callback.provision.assert_not_called()


@pytest.mark.parametrize("alg", ["RS384", "RS512", "PS256"])
def test_unconfigured_algorithm_rejected_even_with_valid_signature(callback, alg):
    with pytest.raises(JoseError):
        callback.run(alg=alg)
    callback.provision.assert_not_called()


@pytest.mark.parametrize("algs", [(), ("none",), ("HS256",), ("RS256", "unknown")])
def test_invalid_algorithm_configuration_fails_closed(callback, algs):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        callback.run(selected_provider=replace(callback.provider, allowed_algs=algs))
    assert exc.value.status_code == 503
    callback.provision.assert_not_called()


def test_generic_oidc_reads_operator_algorithm_policy(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "oidc_allowed_algs", "RS256, ES256")
    assert oidc._oidc_provider().allowed_algs == ("RS256", "ES256")
    assert oidc._google_provider().allowed_algs == ("RS256",)


@pytest.mark.parametrize(
    "change",
    [
        {"sub": 123},
        {"sub": ["subject"]},
        {"sub": True},
        {"exp": True},
        {"iat": False},
        {"azp": ""},
        {"azp": False},
    ],
)
def test_claim_types_are_not_coerced_into_identities(callback, change):
    with pytest.raises(JoseError):
        callback.run(change)
    callback.provision.assert_not_called()
