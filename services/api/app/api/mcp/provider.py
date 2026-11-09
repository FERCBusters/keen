"""Keen as the OAuth authorisation server for its MCP endpoint.

FastMCP's OAuthProvider, on the MCP SDK's handlers, implements the protocol:
authorisation with PKCE, the token endpoint, client registration, revocation
and the discovery documents. This class keeps its records in Keen's database
(app.services.mcp_oauth) and sends people to Keen's consent page.
"""

from __future__ import annotations

from datetime import timezone

from fastmcp.server.auth import AccessToken, OAuthProvider
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    RegistrationErrorCode,
    TokenError,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.db.session import SessionLocal
from app.security.mcp_tokens import SCOPES
from app.services import mcp_oauth as service
from app.services.errors import DomainError

MCP_PATH = "/v1/mcp"
_GRANT_TYPES = ("authorization_code", "refresh_token")
_NAME_LIMIT = 128
_REDIRECT_LIMIT = 5
# Longest PKCE challenge (RFC 7636 section 4.2) and client state Keen keeps.
_CHALLENGE_LIMIT = 128
_STATE_LIMIT = 1024


def _in_session(operation, **values):
    with SessionLocal() as db:
        return operation(db, **values)


async def _db(operation, **values):
    """Run a service operation with its own session, off the event loop."""
    return await run_in_threadpool(_in_session, operation, **values)


def _timestamp(value) -> int:
    return int(value.replace(tzinfo=timezone.utc).timestamp())


def _display_name(raw: str | None) -> str:
    # Control and invisible characters read as spaces, so a name shows as typed.
    name = " ".join("".join(c if c.isprintable() else " " for c in raw or "").split())
    return name[:_NAME_LIMIT] or "Unnamed client"


def _tokens(issued: service.IssuedTokens | None, problem: str) -> OAuthToken:
    if issued is None:
        raise TokenError("invalid_grant", problem)
    return OAuthToken(
        access_token=issued.access_token,
        expires_in=issued.expires_in,
        scope=" ".join(issued.scopes) or None,
        refresh_token=issued.refresh_token,
    )


class KeenOAuthProvider(OAuthProvider):
    def __init__(self) -> None:
        super().__init__(
            base_url=service.issuer_url(),
            issuer_url=service.issuer_url(),
            resource_base_url=settings.public_base_url + "/api",
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=list(SCOPES), default_scopes=list(SCOPES)
            ),
            revocation_options=RevocationOptions(enabled=True),
        )
        self.set_mcp_path(MCP_PATH)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        info = await _db(service.client_info, client_id=client_id)
        return OAuthClientInformationFull.model_validate(info) if info else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        redirect_uris = [str(uri) for uri in client_info.redirect_uris or ()]
        if len(redirect_uris) > _REDIRECT_LIMIT:
            raise RegistrationError(
                "invalid_redirect_uri", f"Register at most {_REDIRECT_LIMIT} URIs"
            )
        # Every client is public and proves possession with PKCE (RFC 7591 lets
        # a server replace requested values; the response shows the client).
        client_info.token_endpoint_auth_method = "none"
        client_info.client_secret = None
        client_info.client_secret_expires_at = None
        client_info.grant_types = [
            g for g in _GRANT_TYPES if g in client_info.grant_types
        ]
        client_info.response_types = ["code"]
        # The handler checked each requested scope is known; keep each once.
        requested = set((client_info.scope or "").split())
        client_info.scope = " ".join(code for code in SCOPES if code in requested)
        client_info.client_name = _display_name(client_info.client_name)
        info = client_info.model_dump(
            mode="json",
            include={
                "client_id",
                "client_name",
                "client_id_issued_at",
                "redirect_uris",
                "grant_types",
                "response_types",
                "scope",
                "token_endpoint_auth_method",
            },
            exclude_none=True,
        )
        try:
            await _db(
                service.register_client,
                client_id=client_info.client_id,
                name=client_info.client_name,
                info=info,
            )
        except DomainError as error:
            code: RegistrationErrorCode = (
                "invalid_redirect_uri"
                if error.status_code == 400
                else "invalid_client_metadata"
            )
            raise RegistrationError(code, str(error.detail)) from None

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if (
            params.resource is not None
            and params.resource.rstrip("/") != service.mcp_url()
        ):
            raise AuthorizeError(
                "invalid_target", "Keen issues tokens for its MCP endpoint only"
            )
        if (
            len(params.code_challenge) > _CHALLENGE_LIMIT
            or len(params.state or "") > _STATE_LIMIT
        ):
            raise AuthorizeError("invalid_request", "The request's values are too long")
        try:
            secret = await _db(
                service.create_consent_request,
                client_id=client.client_id,
                # A request naming no scopes asks for those the client registered.
                scopes=params.scopes
                if params.scopes is not None
                else (client.scope or "").split(),
                state=params.state,
                code_challenge=params.code_challenge,
                redirect_uri=str(params.redirect_uri),
                redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            )
        except DomainError as error:
            raise AuthorizeError("invalid_request", str(error.detail)) from None
        return f"{settings.public_base_url}/mcp-authorize.html?request={secret}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        record = await _db(
            service.authorization_code,
            client_id=client.client_id,
            code=authorization_code,
        )
        if record is None:
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=record.scopes,
            expires_at=_timestamp(record.expires_at),
            client_id=record.client_id,
            code_challenge=record.code_challenge,
            redirect_uri=AnyUrl(record.redirect_uri),
            redirect_uri_provided_explicitly=record.redirect_uri_provided_explicitly,
            resource=service.mcp_url(),
            subject=record.user_id,
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        issued = await _db(service.exchange_code, code=authorization_code.code)
        return _tokens(issued, "The authorization code was already used")

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        record = await _db(
            service.refresh_token, client_id=client.client_id, token=refresh_token
        )
        if record is None:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=record.client_id,
            scopes=record.scopes,
            expires_at=_timestamp(record.expires_at),
            resource=service.mcp_url(),
            subject=record.user_id,
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        issued = await _db(
            service.exchange_refresh, token=refresh_token.token, scopes=scopes
        )
        return _tokens(issued, "The refresh token was already used")

    async def load_access_token(self, token: str) -> AccessToken | None:
        record = await _db(service.access_token, token=token)
        if record is None:
            return None
        return AccessToken(
            token=token,
            client_id=record.client_id,
            scopes=record.scopes,
            expires_at=_timestamp(record.expires_at),
            resource=service.mcp_url(),
            subject=record.user_id,
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        await _db(service.revoke_token, token=token.token)
