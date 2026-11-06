"""Credentials Keen issues to MCP clients, and what they may grant.

Access tokens, refresh tokens, authorisation codes and pending consent requests
are random values with a fixed prefix. Keen keeps only their SHA-256 digests, so
a copy of the database authenticates nothing.

A grant holds scopes, never a role. Every scope is a read permission, offered
only to an account that holds it or its domain's manage permission, and each
request narrows the account's current permissions to the grant's scopes again.
A token therefore carries no permission code that admits less than one its
account holds, and loses whatever its account loses.

This module imports nothing from the application: the settings validate the
configured redirect URIs with it.
"""

from __future__ import annotations

import hashlib
import secrets
import string
from urllib.parse import urlsplit

ACCESS_TOKEN_SECONDS = 3600
# A refresh token rotates on every use and lapses after 30 days unused.
REFRESH_TOKEN_SECONDS = 30 * 24 * 3600
# However often it is refreshed, a grant ends 90 days after consent.
GRANT_SECONDS = 90 * 24 * 3600
AUTHORIZATION_CODE_SECONDS = 60
# How long a person has to approve or refuse a client's request.
CONSENT_REQUEST_SECONDS = 600

# The ASGI scope key carrying an MCP tool's access token to the in-process
# request it makes (app.api.mcp.tools). No client can set an ASGI scope key.
INNER_TOKEN = "keen.mcp_access_token"

ACCESS_PREFIX = "kat_"
REFRESH_PREFIX = "krt_"
CODE_PREFIX = "kac_"
CONSENT_PREFIX = "kcr_"
CLIENT_PREFIX = "kmc_"

_SECRET_BYTES = 32
_SECRET_LENGTH = 43  # token_urlsafe(32)
_URLSAFE = frozenset(string.ascii_letters + string.digits + "-_")

# Each scope, with the permissions of its own domain that let an account grant
# it. Every rule admitting a scope also admits those permissions
# (tools/security_e2e/access_policy.py; a test compares them), so a request
# carrying the scope passes no check its account would fail. Risk readers may
# also read the ISMS registers in Keen, but grant only risk.read: the codes a
# connection carries never stand in for another domain's. Audit management
# implies events.read and isms.read; with_implied_permissions adds those first.
SCOPES: dict[str, tuple[str, frozenset[str]]] = {
    "isms.read": (
        "ISMS records: objectives, SWOT, policies and processes, and assets",
        frozenset({"isms.read", "isms.manage"}),
    ),
    "risk.read": (
        "Risks and the risk register",
        frozenset({"risk.read", "risk.manage"}),
    ),
    "pestle.read": (
        "PESTLE(E) items",
        frozenset({"pestle.read", "pestle.manage"}),
    ),
    "interested_parties.read": (
        "Interested parties",
        frozenset({"interested_parties.read", "interested_parties.manage"}),
    ),
    "events.read": (
        "Evidence events and the coverage counts built from them",
        frozenset({"events.read"}),
    ),
}

# The routes MCP tools read (app.api.mcp.tools). An in-process request from a
# tool may reach these and no others; a test checks every path the tools ask.
INNER_ROUTES = (
    "/v1/frameworks",
    "/v1/statement-of-applicability",
    "/v1/controls",
    "/v1/controls/{control_id:uuid}",
    "/v1/controls/{control_id:uuid}/isms",
    "/v1/controls/{control_id:uuid}/evidence/summary",
    "/v1/risks",
    "/v1/risks/{risk_id:uuid}",
    "/v1/risks/{risk_id:uuid}/controls",
    "/v1/isms/swot",
    "/v1/isms/objectives",
    "/v1/isms/documents",
    "/v1/isms/documents/{document_id:uuid}",
    "/v1/isms/assets",
    "/v1/interested-parties",
    "/v1/pestle/items",
    "/v1/events",
)

# Schemes a configured redirect URI may not use: the web and script schemes a
# browser would open as a page, run, or read from the device.
_REFUSED_SCHEMES = frozenset(
    {"http", "javascript", "data", "file", "vbscript", "blob", "about", "ws", "wss"}
)
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_URI_LIMIT = 2048


SECRET_PREFIXES = (ACCESS_PREFIX, REFRESH_PREFIX, CODE_PREFIX, CONSENT_PREFIX)


def new_secret(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(_SECRET_BYTES)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def well_formed(value: str, prefix: str) -> bool:
    """Whether ``value`` has the shape of a secret Keen issued with ``prefix``."""
    body = value[len(prefix) :]
    return (
        value.startswith(prefix)
        and len(body) == _SECRET_LENGTH
        and _URLSAFE.issuperset(body)
    )


def is_secret(value: str) -> bool:
    """Whether ``value`` is a token, code or consent key Keen issues."""
    return any(well_formed(value, prefix) for prefix in SECRET_PREFIXES)


def readable_scopes(held: set[str]) -> list[str]:
    """The scopes an account holding ``held`` may grant, in display order.

    ``held`` is the account's permission codes with their implications, or
    ``{"*"}`` for an administrator.
    """
    if "*" in held:
        return list(SCOPES)
    return [code for code, (_, granted_by) in SCOPES.items() if held & granted_by]


def granted_scopes(requested: list[str], held: set[str]) -> list[str]:
    """``requested`` narrowed to known scopes the account may grant, in order."""
    allowed = set(readable_scopes(held)) & set(requested)
    return [code for code in SCOPES if code in allowed]


def _uri_shape_problem(uri: str) -> str | None:
    if len(uri) > _URI_LIMIT:
        return "a redirect URI is too long"
    if any(ord(c) < 33 or ord(c) == 127 or c == "\\" for c in uri):
        return "a redirect URI contains whitespace or control characters"
    try:
        parts = urlsplit(uri)
        parts.port  # Rejects a malformed port.
    except ValueError:
        return "a redirect URI is not a valid URI"
    if not parts.scheme:
        return "a redirect URI must be absolute"
    if parts.fragment or "#" in uri:
        return "a redirect URI must not have a fragment"
    if parts.username is not None or parts.password is not None:
        return "a redirect URI must not carry credentials"
    return None


def configured_redirect_problem(uri: str) -> str | None:
    """Why an administrator-configured redirect URI is unsafe, or None."""
    if problem := _uri_shape_problem(uri):
        return problem
    parts = urlsplit(uri)
    if parts.scheme.lower() in _REFUSED_SCHEMES:
        return f"{uri!r} must use https or an application's own scheme"
    if parts.scheme.lower() == "https" and not parts.hostname:
        return f"{uri!r} must name a host"
    return None


def is_loopback_redirect(uri: str) -> bool:
    """An http callback on this device (RFC 8252 section 7.3), on any port."""
    if _uri_shape_problem(uri):
        return False
    parts = urlsplit(uri)
    return parts.scheme == "http" and parts.hostname in _LOOPBACK_HOSTS


def redirect_allowed(uri: str, configured: str) -> bool:
    """Whether a client may register ``uri``, given KEEN_MCP_REDIRECT_URIS."""
    # The settings validate each configured entry when Keen starts.
    return is_loopback_redirect(uri) or uri in filter(None, configured.split(","))
