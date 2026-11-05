from __future__ import annotations

import hashlib
import math
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import Any
from urllib.parse import urlsplit

import httpx2 as httpx
from anyio import to_thread
from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.httpx_client import AsyncOAuth2Client
from authlib.oauth2.rfc6749.errors import OAuth2Error
from authlib.oidc.core import CodeIDToken
from fastapi import HTTPException, Request
from joserfc import jwt
from joserfc.errors import InvalidClaimError, JoseError
from joserfc.jwk import KeySet, KeySetSerialization
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.core.email_address import single_mailbox
from app.db.models import OidcLoginState, User, UserIdentity, UserSsoEmail
from app.security.sso_http import client_options

TOKEN_ENDPOINT_AUTH_METHODS = ("client_secret_basic", "client_secret_post")


@dataclass(frozen=True)
class SsoProvider:
    key: str
    kind: str
    label: str
    enabled: bool
    client_id: str
    client_secret: str
    authorization_endpoint: str
    token_endpoint: str
    scopes: str
    redirect_uri: str = ""
    issuer: str = ""
    jwks_uri: str = ""
    userinfo_endpoint: str = ""
    emails_endpoint: str = ""
    end_session_endpoint: str = ""
    post_logout_redirect_uri: str = ""
    username_claims: str = "preferred_username,email"
    allowed_email_domains: str = ""
    auto_provision: bool = False
    auto_provision_role: str = "pending"
    auto_link_existing: bool = True
    token_endpoint_auth_method: str = "client_secret_basic"
    supports_id_token: bool = True
    id_token_leeway_seconds: int = 60
    allowed_algs: tuple[str, ...] = ("RS256",)

    def public_dict(self) -> dict[str, str]:
        return {
            "key": self.key,
            "label": self.label,
            "kind": self.kind,
            "start_url": provider_start_url(self),
        }


def _now() -> datetime:
    return utc_now_naive()


def _split_csv(value: str) -> list[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def _setting(name: str, default: Any = "") -> Any:
    return getattr(settings, name, default)


def _first_nonempty(*values: Any) -> str:
    for v in values:
        s = str(v or "").strip()
        if s:
            return s
    return ""


def _oidc_provider() -> SsoProvider:
    return SsoProvider(
        key="oidc",
        allowed_algs=tuple(
            str(_setting("oidc_allowed_algs", "RS256")).replace(",", " ").split()
        ),
        kind="oidc",
        label=_first_nonempty(_setting("oidc_provider_label", ""), "OpenID Connect"),
        enabled=bool(_setting("oidc_enabled", False)),
        client_id=_first_nonempty(_setting("oidc_client_id", "")),
        client_secret=_first_nonempty(_setting("oidc_client_secret", "")),
        authorization_endpoint=_first_nonempty(
            _setting("oidc_authorization_endpoint", "")
        ),
        token_endpoint=_first_nonempty(_setting("oidc_token_endpoint", "")),
        jwks_uri=_first_nonempty(_setting("oidc_jwks_uri", "")),
        issuer=_first_nonempty(_setting("oidc_issuer", "")),
        userinfo_endpoint=_first_nonempty(_setting("oidc_userinfo_endpoint", "")),
        scopes=_first_nonempty(_setting("oidc_scopes", ""), "openid email profile"),
        redirect_uri=_first_nonempty(_setting("oidc_redirect_uri", "")),
        end_session_endpoint=_first_nonempty(_setting("oidc_end_session_endpoint", "")),
        post_logout_redirect_uri=_first_nonempty(
            _setting("oidc_post_logout_redirect_uri", "")
        ),
        username_claims=_first_nonempty(
            _setting("oidc_username_claims", ""),
            "preferred_username,email",
        ),
        allowed_email_domains=_first_nonempty(
            _setting("oidc_allowed_email_domains", "")
        ),
        auto_provision=bool(_setting("oidc_auto_provision", False)),
        auto_provision_role="",
        auto_link_existing=bool(_setting("oidc_auto_link_existing", True)),
        token_endpoint_auth_method=_first_nonempty(
            _setting("oidc_token_endpoint_auth_method", ""), "client_secret_basic"
        ).lower(),
        supports_id_token=True,
        id_token_leeway_seconds=int(_setting("oidc_id_token_leeway_seconds", 60) or 0),
    )


def _google_provider() -> SsoProvider:
    return SsoProvider(
        key="google",
        kind="oidc",
        label=_first_nonempty(_setting("google_provider_label", ""), "Google"),
        enabled=bool(_setting("google_sso_enabled", False)),
        client_id=_first_nonempty(_setting("google_client_id", "")),
        client_secret=_first_nonempty(_setting("google_client_secret", "")),
        authorization_endpoint=_first_nonempty(
            _setting("google_authorization_endpoint", ""),
            "https://accounts.google.com/o/oauth2/v2/auth",
        ),
        token_endpoint=_first_nonempty(
            _setting("google_token_endpoint", ""),
            "https://oauth2.googleapis.com/token",
        ),
        jwks_uri=_first_nonempty(
            _setting("google_jwks_uri", ""),
            "https://www.googleapis.com/oauth2/v3/certs",
        ),
        issuer=_first_nonempty(
            _setting("google_issuer", ""),
            "https://accounts.google.com,accounts.google.com",
        ),
        userinfo_endpoint=_first_nonempty(
            _setting("google_userinfo_endpoint", ""),
            "https://openidconnect.googleapis.com/v1/userinfo",
        ),
        scopes=_first_nonempty(_setting("google_scopes", ""), "openid email profile"),
        redirect_uri=_first_nonempty(_setting("google_redirect_uri", "")),
        end_session_endpoint=_first_nonempty(
            _setting("google_end_session_endpoint", "")
        ),
        post_logout_redirect_uri=_first_nonempty(
            _setting("google_post_logout_redirect_uri", "")
        ),
        username_claims=_first_nonempty(
            _setting("google_username_claims", ""), "email"
        ),
        allowed_email_domains=_first_nonempty(
            _setting("google_allowed_email_domains", "")
        ),
        auto_provision=bool(_setting("google_auto_provision", False)),
        auto_provision_role=_first_nonempty(
            _setting("google_auto_provision_role", ""), "pending"
        ),
        auto_link_existing=bool(_setting("google_auto_link_existing", True)),
        token_endpoint_auth_method="client_secret_post",
        supports_id_token=True,
    )


def _github_provider() -> SsoProvider:
    return SsoProvider(
        key="github",
        kind="github",
        label=_first_nonempty(_setting("github_provider_label", ""), "GitHub"),
        enabled=bool(_setting("github_sso_enabled", False)),
        client_id=_first_nonempty(_setting("github_client_id", "")),
        client_secret=_first_nonempty(_setting("github_client_secret", "")),
        authorization_endpoint=_first_nonempty(
            _setting("github_authorization_endpoint", ""),
            "https://github.com/login/oauth/authorize",
        ),
        token_endpoint=_first_nonempty(
            _setting("github_token_endpoint", ""),
            "https://github.com/login/oauth/access_token",
        ),
        userinfo_endpoint=_first_nonempty(
            _setting("github_user_endpoint", ""), "https://api.github.com/user"
        ),
        emails_endpoint=_first_nonempty(
            _setting("github_emails_endpoint", ""),
            "https://api.github.com/user/emails",
        ),
        issuer=_first_nonempty(
            _setting("github_issuer", ""), "https://github.com/login/oauth"
        ),
        scopes=_first_nonempty(_setting("github_scopes", ""), "read:user user:email"),
        redirect_uri=_first_nonempty(_setting("github_redirect_uri", "")),
        allowed_email_domains=_first_nonempty(
            _setting("github_allowed_email_domains", "")
        ),
        auto_provision=bool(_setting("github_auto_provision", False)),
        auto_provision_role=_first_nonempty(
            _setting("github_auto_provision_role", ""), "pending"
        ),
        auto_link_existing=bool(_setting("github_auto_link_existing", True)),
        token_endpoint_auth_method="client_secret_post",
        supports_id_token=False,
    )


def all_sso_providers() -> list[SsoProvider]:
    return [_oidc_provider(), _google_provider(), _github_provider()]


def get_sso_provider(provider_key: str | None) -> SsoProvider | None:
    key = (provider_key or "").strip().lower() or "oidc"
    for provider in all_sso_providers():
        if provider.key == key:
            return provider
    return None


def _canonical_issuer(provider: SsoProvider) -> str:
    values = _split_csv(provider.issuer)
    return values[0] if values else (provider.issuer or "")


def provider_start_url(provider: SsoProvider) -> str:
    return f"/api/v1/auth/sso/{provider.key}/start"


def configured_sso_providers() -> list[SsoProvider]:
    out: list[SsoProvider] = []
    for provider in all_sso_providers():
        if not provider.enabled:
            continue
        try:
            _require_provider_config(provider.key)
        except HTTPException:
            continue
        out.append(provider)
    return out


def _required_missing(provider: SsoProvider) -> list[str]:
    prefix = {
        "oidc": "KEEN_OIDC",
        "google": "KEEN_GOOGLE",
        "github": "KEEN_GITHUB",
    }.get(provider.key, f"KEEN_{provider.key.upper()}")

    missing: list[str] = []
    if not provider.client_id:
        missing.append(f"{prefix}_CLIENT_ID")
    if not provider.client_secret:
        missing.append(f"{prefix}_CLIENT_SECRET")
    if not provider.authorization_endpoint:
        missing.append(f"{prefix}_AUTHORIZATION_ENDPOINT")
    if not provider.token_endpoint:
        missing.append(f"{prefix}_TOKEN_ENDPOINT")
    if not provider.redirect_uri:
        missing.append(f"{prefix}_REDIRECT_URI")
    if provider.kind == "oidc":
        if not provider.jwks_uri:
            missing.append(f"{prefix}_JWKS_URI")
        if not _split_csv(provider.issuer):
            missing.append(f"{prefix}_ISSUER")
    return missing


def _require_provider_config(provider_key: str | None) -> SsoProvider:
    provider = get_sso_provider(provider_key)
    if not provider or not provider.enabled:
        raise HTTPException(status_code=404, detail="SSO provider disabled")

    if provider.auto_provision:
        raise HTTPException(
            status_code=500,
            detail="SSO auto-provisioning is not supported; Keen only allows existing active users",
        )

    missing = _required_missing(provider)
    if missing:
        raise HTTPException(
            status_code=500,
            detail=(
                f"{provider.label} SSO is enabled but required settings are missing: "
                f"{', '.join(missing)}"
            ),
        )
    # Both supported methods authenticate Keen with its client secret.
    if provider.token_endpoint_auth_method not in TOKEN_ENDPOINT_AUTH_METHODS:
        raise HTTPException(
            status_code=500,
            detail=(
                f"{provider.label} token endpoint authentication must be "
                + " or ".join(TOKEN_ENDPOINT_AUTH_METHODS)
            ),
        )
    for setting_name, value in (
        ("authorization endpoint", provider.authorization_endpoint),
        ("token endpoint", provider.token_endpoint),
        ("JWKS URI", provider.jwks_uri if provider.kind == "oidc" else ""),
        ("user endpoint", provider.userinfo_endpoint),
        ("emails endpoint", provider.emails_endpoint),
    ):
        if not value:
            continue
        scheme = urlsplit(value).scheme.lower()
        if scheme != "https" and not (
            scheme == "http" and bool(_setting("oidc_allow_insecure_http", False))
        ):
            raise HTTPException(
                status_code=500,
                detail=f"{provider.label} {setting_name} must use HTTPS",
            )
    # The callback must reach this installation, whose host-only cookie binds
    # the sign-in to the browser that started it.
    if not provider.redirect_uri.startswith(settings.public_base_url + "/"):
        raise HTTPException(
            status_code=500,
            detail=f"{provider.label} redirect URI must be on KEEN_PUBLIC_BASE_URL",
        )
    return provider


_JWKS_CACHE: dict[str, tuple[KeySetSerialization, datetime]] = {}


def _new_client(provider: SsoProvider, *, redirect_uri: str) -> AsyncOAuth2Client:
    scopes = (provider.scopes or "openid email profile").strip() or "openid"
    return AsyncOAuth2Client(
        client_id=provider.client_id,
        client_secret=provider.client_secret,
        scope=scopes,
        redirect_uri=redirect_uri,
        token_endpoint_auth_method=provider.token_endpoint_auth_method,
        # Enable PKCE (S256) for providers that support it. GitHub ignores this
        # safely for OAuth apps that do not require PKCE.
        code_challenge_method="S256",
        **client_options(),
    )


async def _get_jwks(jwks_uri: str) -> KeySetSerialization:
    now = _now()
    cached = _JWKS_CACHE.get(jwks_uri)
    if cached:
        data, expires_at = cached
        if now < expires_at:
            return data

    async with httpx.AsyncClient(**client_options()) as client:
        resp = await client.get(jwks_uri)
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, dict) or "keys" not in data:
        raise HTTPException(status_code=500, detail="OIDC JWKS response invalid")

    _JWKS_CACHE[jwks_uri] = (data, now + timedelta(hours=1))
    return data


def create_login_state(
    db: Session, *, next_url: str | None, state: str | None = None
) -> OidcLoginState:
    db.query(OidcLoginState).filter(OidcLoginState.expires_at <= _now()).delete(
        synchronize_session=False
    )
    state = state or secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    code_verifier = secrets.token_urlsafe(48)

    row = OidcLoginState(
        state=state,
        nonce=nonce,
        code_verifier=code_verifier,
        next_url=(next_url or "").strip() or None,
        expires_at=_now() + timedelta(minutes=10),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def pop_login_state(db: Session, *, state: str) -> OidcLoginState | None:
    key = (state or "").strip()
    if not key:
        return None

    row = db.query(OidcLoginState).filter(OidcLoginState.state == key).one_or_none()
    if not row:
        return None

    if row.expires_at <= _now():
        try:
            db.delete(row)
            db.commit()
        except Exception:
            db.rollback()
        return None

    db.expunge(row)  # Retain loaded nonce/verifier after the database deletion.
    # Atomic consumption: concurrent callbacks cannot both redeem one transaction.
    consumed = (
        db.query(OidcLoginState)
        .filter(OidcLoginState.state == key, OidcLoginState.expires_at > _now())
        .delete(synchronize_session=False)
    )
    db.commit()
    return row if consumed == 1 else None


def sso_binding_cookie() -> str:
    return "__Host-keen_sso" if settings.cookie_secure else "keen_sso"


def _browser_state(provider_key: str, secret: str) -> str:
    return hashlib.sha256((provider_key + "\0" + secret).encode()).hexdigest()


def set_sso_binding(response, request):
    response.set_cookie(
        sso_binding_cookie(),
        request.state.sso_browser_secret,
        max_age=600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _normalize_next(next_url: str | None) -> str | None:
    raw = (next_url or "").strip()
    if not raw:
        return None
    path = raw.split("?", 1)[0].split("#", 1)[0]
    # Browsers resolve dot segments, including percent-encoded ones, so a path
    # such as /.//host would become the network-path reference //host.
    if (
        raw.startswith("/")
        and not raw.startswith("//")
        and "://" not in raw
        and "\\" not in raw
        and not any(ord(c) < 32 for c in raw)
        and not any(code in path.lower() for code in ("%2e", "%2f", "%5c"))
        and not any(segment in {".", ".."} for segment in path.split("/"))
    ):
        return raw
    return None


def _allowed_by_domain(provider: SsoProvider, claims: dict[str, Any]) -> bool:
    """Whether the provider's current verified email is in an allowed domain."""
    domains = _split_csv(provider.allowed_email_domains or "")
    if not domains:
        return True
    email = _claim_email(claims)
    if not email or not _email_is_verified(provider, claims):
        return False
    dom = email.split("@", 1)[1]
    return any(dom == d.lower() for d in domains)


def _claim_email(claims: dict[str, Any]) -> str | None:
    email = single_mailbox(claims.get("email"))
    return email.lower() if email else None


def _email_is_verified(provider: SsoProvider, claims: dict[str, Any]) -> bool:
    # OIDC uses a JSON boolean; GitHub synthesizes one from /user/emails.
    return claims.get("email_verified") is True


def _update_identity_metadata(identity: UserIdentity, claims: dict[str, Any]) -> None:
    identity.email = _claim_email(claims) or identity.email
    identity.preferred_username = (
        str(
            claims.get("preferred_username") or identity.preferred_username or ""
        ).strip()
        or None
    )
    identity.display_name = (
        str(claims.get("name") or identity.display_name or "").strip() or None
    )
    identity.claims = claims or {}


class LinkedAccountRefused(HTTPException):
    """A refused sign-in whose provider identity is linked to an account."""

    def __init__(self, account: User, detail: str) -> None:
        super().__init__(status_code=403, detail=detail)
        self.account_id = account.id


def linked_account_id(error: Exception):
    """The account a refused SSO sign-in reached, if its identity is linked."""
    return error.account_id if isinstance(error, LinkedAccountRefused) else None


def sso_attempt(request: Request) -> str | None:
    """The provider of a sign-in this request's callback consumed, if any."""
    return getattr(request.state, "sso_provider", None)


def _get_or_create_user_for_identity(
    db: Session, *, provider: SsoProvider, subject: str, claims: dict[str, Any]
) -> User:
    issuer = _canonical_issuer(provider)
    from app.security.hosted import enforce_hosted_identity

    enforce_hosted_identity(provider.key, issuer, subject)
    identity = (
        db.query(UserIdentity)
        .filter(UserIdentity.issuer == issuer, UserIdentity.subject == subject)
        .one_or_none()
    )
    # The allowlist governs every sign-in, so an identity linked earlier stops
    # signing in once its email leaves the allowed domains.
    allowed = _allowed_by_domain(provider, claims)
    if identity:
        user = db.query(User).filter(User.id == identity.user_id).one_or_none()
        if not user:
            raise HTTPException(status_code=403, detail="User is inactive")
        if not allowed:
            raise LinkedAccountRefused(user, "Email domain is not allowed")
        if not user.is_active:
            raise LinkedAccountRefused(user, "User is inactive")
        _update_identity_metadata(identity, claims)
        identity.provider = provider.key
        user.last_login_at = _now()
        db.add(identity)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    email = _claim_email(claims)
    if not allowed:
        raise HTTPException(status_code=403, detail="Email domain is not allowed")
    if not provider.auto_link_existing:
        raise HTTPException(
            status_code=403, detail="No local account linked to this identity"
        )

    candidates: dict[str, User] = {}
    # Both Keen mailbox verification and provider verification are required.
    if email and _email_is_verified(provider, claims):
        for user in (
            db.query(User)
            .join(UserSsoEmail, UserSsoEmail.user_id == User.id)
            .filter(UserSsoEmail.email == email)
            .all()
        ):
            candidates[str(user.id)] = user

    active = [user for user in candidates.values() if user.is_active]
    if len(active) != 1:
        detail = (
            "Multiple active Keen users match this SSO identity"
            if len(active) > 1
            else "No active Keen account matches this SSO identity"
        )
        raise HTTPException(status_code=403, detail=detail)

    user = active[0]
    identity = UserIdentity(
        user_id=user.id,
        provider=provider.key,
        issuer=issuer,
        subject=subject,
        email=email,
        preferred_username=str(claims.get("preferred_username") or "").strip() or None,
        display_name=str(claims.get("name") or "").strip() or None,
        claims=claims or {},
    )
    try:
        user.last_login_at = _now()
        db.add(user)
        db.add(identity)
        db.commit()
        db.refresh(user)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=403, detail="SSO identity link conflict"
        ) from exc
    return user


def _sso_start_allowed(request: Request) -> tuple[bool, int]:
    from app.core.valkey import get_valkey
    from app.security.rate_limit import address_window_allow, client_ip

    return address_window_allow(
        get_valkey(), "sso-start", client_ip(request), 30, 300, fail_closed=True
    )


async def build_authorize_redirect(
    request: Request, db: Session, next_url: str | None, provider_key: str | None = None
) -> str:
    provider = _require_provider_config(provider_key)
    # Valkey and database work runs in the thread pool, off the event loop.
    allowed, retry = await to_thread.run_sync(_sso_start_allowed, request)
    if not allowed:
        raise HTTPException(
            429, "Too many sign-in attempts", headers={"Retry-After": str(retry)}
        )
    redirect_uri = provider.redirect_uri
    secret = secrets.token_urlsafe(32)
    request.state.sso_browser_secret = secret
    state_row = await to_thread.run_sync(
        partial(
            create_login_state,
            db,
            next_url=_normalize_next(next_url),
            state=_browser_state(provider.key, secret),
        )
    )

    client = _new_client(provider, redirect_uri=redirect_uri)
    try:
        url, _ = client.create_authorization_url(
            provider.authorization_endpoint,
            state=state_row.state,
            nonce=state_row.nonce if provider.supports_id_token else None,
            code_verifier=state_row.code_verifier,
            redirect_uri=redirect_uri,
        )
    finally:
        await client.aclose()
    return url


async def _fetch_github_claims(
    provider: SsoProvider, token: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    access_token = str((token or {}).get("access_token") or "").strip()
    if not access_token:
        raise HTTPException(
            status_code=400, detail="GitHub token response missing access_token"
        )

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {access_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(**client_options()) as client:
        user_resp = await client.get(provider.userinfo_endpoint, headers=headers)
        user_resp.raise_for_status()
        user_info = user_resp.json()

        emails: list[dict[str, Any]] = []
        email_resp = await client.get(provider.emails_endpoint, headers=headers)
        if email_resp.status_code < 400:
            raw = email_resp.json()
            if isinstance(raw, list):
                emails = [e for e in raw if isinstance(e, dict)]

    if not isinstance(user_info, dict):
        raise HTTPException(status_code=400, detail="GitHub user response invalid")

    subject = str(user_info.get("id") or "").strip()
    login = str(user_info.get("login") or "").strip()
    if not subject or not login:
        raise HTTPException(
            status_code=400, detail="GitHub user response missing id/login"
        )

    email = str(user_info.get("email") or "").strip().lower() or None
    email_verified = False
    primary_verified = next(
        (
            e
            for e in emails
            if bool(e.get("primary"))
            and bool(e.get("verified"))
            and str(e.get("email") or "").strip()
        ),
        None,
    )
    # A public GitHub profile email carries no verification flag. It is kept for
    # display and never links accounts across providers.
    if primary_verified:
        email = str(primary_verified.get("email") or "").strip().lower() or email
        email_verified = True

    claims = {
        "iss": _canonical_issuer(provider),
        "sub": subject,
        "preferred_username": login,
        "email": email,
        "email_verified": email_verified,
        "name": str(user_info.get("name") or "").strip() or None,
        "github_login": login,
        "github_id": subject,
        "avatar_url": str(user_info.get("avatar_url") or "").strip() or None,
        "html_url": str(user_info.get("html_url") or "").strip() or None,
    }
    return subject, claims


async def _handle_github_callback(
    code: str, db: Session, provider: SsoProvider, row: OidcLoginState
) -> tuple[User, str | None, str | None]:
    redirect_uri = provider.redirect_uri
    client = _new_client(provider, redirect_uri=redirect_uri)
    try:
        token = await client.fetch_token(
            provider.token_endpoint,
            grant_type="authorization_code",
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=row.code_verifier,
        )
    finally:
        await client.aclose()
    subject, claims = await _fetch_github_claims(provider, token)
    user = await to_thread.run_sync(
        partial(
            _get_or_create_user_for_identity,
            db,
            provider=provider,
            subject=subject,
            claims=claims,
        )
    )
    next_url = _normalize_next(row.next_url) or None
    return user, next_url, None


async def _handle_oidc_callback(
    code: str, db: Session, provider: SsoProvider, row: OidcLoginState
) -> tuple[User, str | None, str | None]:
    issuer_values = _split_csv(provider.issuer)
    if not issuer_values:
        # _require_provider_config refuses such a provider first; an empty list
        # here would switch off the token's issuer check.
        raise HTTPException(status_code=500, detail="SSO issuer is not configured")
    redirect_uri = provider.redirect_uri
    client = _new_client(provider, redirect_uri=redirect_uri)

    try:
        token = await client.fetch_token(
            provider.token_endpoint,
            grant_type="authorization_code",
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=row.code_verifier,
        )
    finally:
        await client.aclose()

    id_token = (token or {}).get("id_token")
    if not id_token:
        raise HTTPException(
            status_code=400, detail="OIDC token response missing id_token"
        )

    jwks = await _get_jwks(provider.jwks_uri)

    claims_options = {
        "iss": {"essential": True, "values": issuer_values},
        "aud": {"essential": True, "values": [provider.client_id]},
    }
    claims_params = {
        "nonce": row.nonce,
        "client_id": provider.client_id,
        "access_token": (token or {}).get("access_token"),
    }

    # The deployment's algorithm policy is authoritative; the token header
    # cannot widen it. Public JWKS are suitable only for asymmetric verification.
    supported = {
        "RS256",
        "RS384",
        "RS512",
        "ES256",
        "ES384",
        "ES512",
        "ES256K",
        "PS256",
        "PS384",
        "PS512",
        "EdDSA",
    }
    if not provider.allowed_algs or not set(provider.allowed_algs) <= supported:
        raise HTTPException(
            503, "Configure supported asymmetric OIDC signing algorithms"
        )
    decoded = jwt.decode(
        id_token,
        KeySet.import_key_set(jwks),
        algorithms=list(provider.allowed_algs),
    )
    # Keen configures one trusted audience: its own client ID. A matching azp
    # does not establish trust in additional audiences or make non-string IDs safe.
    raw_claims = decoded.claims
    aud = raw_claims.get("aud")
    if aud != provider.client_id and aud != [provider.client_id]:
        raise InvalidClaimError("aud")
    for name in ("iss", "sub", "nonce"):
        if not isinstance(raw_claims.get(name), str) or not raw_claims[name]:
            raise InvalidClaimError(name)
    for name in ("exp", "iat", "nbf", "auth_time"):
        if name in raw_claims:
            value = raw_claims[name]
            if type(value) not in (int, float) or not math.isfinite(value):
                raise InvalidClaimError(name)
    if "azp" in raw_claims and raw_claims["azp"] != provider.client_id:
        raise InvalidClaimError("azp")
    claims = CodeIDToken(
        decoded.claims, decoded.header, options=claims_options, params=claims_params
    )
    claims.validate(leeway=int(provider.id_token_leeway_seconds or 0))

    claims_dict: dict[str, Any] = dict(claims)
    subject = str(claims_dict.get("sub") or "").strip()
    if not subject:
        raise HTTPException(status_code=400, detail="OIDC claims missing sub")

    # Store the configured provider issuer for identity uniqueness even when a
    # provider accepts more than one issuer string.
    claims_dict["iss"] = _canonical_issuer(provider)

    user = await to_thread.run_sync(
        partial(
            _get_or_create_user_for_identity,
            db,
            provider=provider,
            subject=subject,
            claims=claims_dict,
        )
    )
    next_url = _normalize_next(row.next_url) or None
    return user, next_url, str(id_token)


def _authorization_code(request: Request) -> str:
    """The callback's single authorisation code.

    Only the code reaches the token exchange: given the whole request URL,
    authlib takes tokens from a fragment without calling the token endpoint,
    and assumes a client-credentials grant when the code is missing.
    """
    codes = request.query_params.getlist("code")
    code = codes[0].strip() if len(codes) == 1 else ""
    if not code or len(code) > 2048 or not code.isascii() or not code.isprintable():
        raise HTTPException(
            status_code=400,
            detail="SSO verification failed; start sign-in again or contact an administrator",
        )
    return code


async def handle_callback(
    request: Request, db: Session, provider_key: str | None = None
) -> tuple[User, str | None, str | None, SsoProvider]:
    provider = _require_provider_config(provider_key)
    state = (request.query_params.get("state") or "").strip()
    secret = request.cookies.get(sso_binding_cookie(), "")
    if (
        len(state) != 64
        or not state.isascii()
        or not secret
        or len(secret) > 128
        or not secrets.compare_digest(state, _browser_state(provider.key, secret))
    ):
        raise HTTPException(
            status_code=400,
            detail="SSO browser verification failed; start sign-in again",
        )
    row = await to_thread.run_sync(partial(pop_login_state, db, state=state))
    if not row:
        raise HTTPException(status_code=400, detail="SSO state missing or expired")
    # From here the callback completes or refuses a sign-in this browser began.
    request.state.sso_provider = provider.key
    code = _authorization_code(request)

    try:
        if provider.kind == "github":
            user, next_url, id_token = await _handle_github_callback(
                code, db, provider, row
            )
        else:
            user, next_url, id_token = await _handle_oidc_callback(
                code, db, provider, row
            )
    except (
        JoseError,
        # authlib's HTTP client reports a provider's token error responses as
        # OAuthError; OAuth2Error covers its protocol-level errors.
        OAuthError,
        OAuth2Error,
        httpx.HTTPError,
        TimeoutError,
        ValueError,
        TypeError,
    ):
        # Provider diagnostics can include tokens and echoed client credentials.
        # Do not expose them in an HTTP response or an unhandled traceback.
        raise HTTPException(
            400,
            "SSO verification failed; start sign-in again or contact an administrator",
        ) from None
    return user, next_url, id_token, provider
