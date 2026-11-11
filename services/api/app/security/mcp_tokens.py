"""Credentials Keen issues to MCP clients, and what they may grant.

Access tokens, refresh tokens, authorisation codes and pending consent requests
are random values with a fixed prefix. Keen keeps only their SHA-256 digests, so
a copy of the database authenticates nothing.

A grant holds scopes, never a role. A read scope is offered only to an account
that holds it or its domain's manage permission; a change scope only to one
that holds that manage permission, and only while KEEN_MCP_WRITE_ENABLED is
set; the mapping-rules scope only to an administrator. Each request narrows the
account's current permissions to the grant's scopes again, so a token carries
no permission code that admits less than one its account holds, and loses
whatever its account loses.

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
        "ISMS records: objectives, SWOT, policies and processes, assets, people "
        "and vendors / 3rd parties",
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
    "events.read": (
        "Evidence events and the coverage counts built from them",
        frozenset({"events.read"}),
    ),
    # Change scopes: each adds and updates records of its domain, never
    # deletes them, and is granted only by that domain's manage permission.
    "isms.manage": (
        "Add and update ISMS records: policies and processes, objectives, SWOT, "
        "assets, the access matrix, the organisation chart, measures, meetings, "
        "people and vendors / 3rd parties",
        frozenset({"isms.manage"}),
    ),
    "risk.manage": (
        "Add, update and import risks",
        frozenset({"risk.manage"}),
    ),
    "pestle.manage": (
        "Add and update PESTLE(E) factors and business processes",
        frozenset({"pestle.manage"}),
    ),
    # Mapping rules belong to administrators, whom no permission code names:
    # only an administrator ("*") may grant this scope.
    "rules.write": (
        "Add mapping rules that relate collected evidence to controls (administrators)",
        frozenset(),
    ),
}

# Each change scope, with the read scope a connection holding it also holds:
# the records it adds are records it can read.
WRITE_SCOPES: dict[str, str] = {
    "isms.manage": "isms.read",
    "risk.manage": "risk.read",
    "pestle.manage": "pestle.read",
    "rules.write": "events.read",
}
# Scopes that act with the administrator role, on the routes they open and no
# others (INNER_ROUTES), while the account is an administrator.
ADMIN_SCOPES = frozenset({"rules.write"})

# The routes MCP tools ask in process (app.api.mcp), by method and path, with
# the scope each needs before the route's own permission check runs. None
# leaves the decision to the tool, which checks its scope itself, and to the
# route. An in-process request may reach these and no others; a test checks
# every request the tools make. No delete is listed: nothing is removed over
# MCP. Record identifiers are UUIDs, except a mapping rule's own ID, which the
# rule editor validates.
_ISMS = "isms.manage"
INNER_ROUTES: dict[tuple[str, str], str | None] = {
    ("GET", "/v1/frameworks"): None,
    ("GET", "/v1/statement-of-applicability"): None,
    ("GET", "/v1/controls"): None,
    ("GET", "/v1/controls/{control_id:uuid}"): None,
    ("GET", "/v1/controls/{control_id:uuid}/isms"): None,
    ("GET", "/v1/controls/{control_id:uuid}/evidence/summary"): None,
    ("GET", "/v1/risks"): None,
    ("GET", "/v1/risks/{risk_id:uuid}"): None,
    ("GET", "/v1/risks/{risk_id:uuid}/controls"): None,
    ("GET", "/v1/isms/swot"): None,
    ("GET", "/v1/isms/objectives"): None,
    ("GET", "/v1/isms/documents"): None,
    ("GET", "/v1/isms/documents/{document_id:uuid}"): None,
    ("GET", "/v1/isms/assets"): None,
    ("GET", "/v1/pestle/items"): None,
    ("GET", "/v1/events"): None,
    # Read before a change: the choices a record can name, and the records a
    # whole-record update starts from.
    ("GET", "/v1/isms/meta"): None,
    ("GET", "/v1/isms/document-folders"): None,
    ("GET", "/v1/isms/people"): None,
    ("GET", "/v1/isms/people/{person_id:uuid}"): None,
    ("GET", "/v1/isms/vendors"): None,
    ("GET", "/v1/isms/vendors/{vendor_id:uuid}"): None,
    ("GET", "/v1/pestle/meta"): None,
    ("GET", "/v1/pestle/items/{item_id:uuid}"): None,
    ("GET", "/v1/pestle/business-processes"): None,
    ("GET", "/v1/risks/categories"): None,
    # Changes to ISMS records.
    ("POST", "/v1/isms/documents"): _ISMS,
    ("PATCH", "/v1/isms/documents/{document_id:uuid}"): _ISMS,
    ("POST", "/v1/isms/documents/{document_id:uuid}/file"): _ISMS,
    ("POST", "/v1/isms/document-folders"): _ISMS,
    ("POST", "/v1/isms/objectives"): _ISMS,
    ("PATCH", "/v1/isms/objectives/{objective_id:uuid}"): _ISMS,
    ("POST", "/v1/isms/swot"): _ISMS,
    ("PATCH", "/v1/isms/swot/{item_id:uuid}"): _ISMS,
    ("POST", "/v1/isms/swot/import"): _ISMS,
    ("POST", "/v1/isms/assets"): _ISMS,
    ("PATCH", "/v1/isms/assets/{asset_id:uuid}"): _ISMS,
    ("POST", "/v1/isms/licenses"): _ISMS,
    ("PATCH", "/v1/isms/licenses/{license_id:uuid}"): _ISMS,
    ("POST", "/v1/isms/access-control-matrix"): _ISMS,
    ("PATCH", "/v1/isms/access-control-matrix/{entry_id:uuid}"): _ISMS,
    ("POST", "/v1/isms/org-nodes"): _ISMS,
    ("PATCH", "/v1/isms/org-nodes/{node_id:uuid}"): _ISMS,
    ("POST", "/v1/isms/effectiveness-measures"): _ISMS,
    ("PATCH", "/v1/isms/effectiveness-measures/{measure_id:uuid}"): _ISMS,
    ("POST", "/v1/isms/meetings"): _ISMS,
    ("PATCH", "/v1/isms/meetings/{meeting_id:uuid}"): _ISMS,
    ("POST", "/v1/isms/people"): _ISMS,
    ("PATCH", "/v1/isms/people/{person_id:uuid}"): _ISMS,
    ("POST", "/v1/isms/vendors"): _ISMS,
    ("PATCH", "/v1/isms/vendors/{vendor_id:uuid}"): _ISMS,
    # Changes to risks and PESTLE(E) factors.
    ("POST", "/v1/risks"): "risk.manage",
    ("PATCH", "/v1/risks/{risk_id:uuid}"): "risk.manage",
    ("POST", "/v1/risks/import"): "risk.manage",
    ("POST", "/v1/pestle/items"): "pestle.manage",
    ("PATCH", "/v1/pestle/items/{item_id:uuid}"): "pestle.manage",
    ("POST", "/v1/pestle/business-processes"): "pestle.manage",
    ("PATCH", "/v1/pestle/business-processes/{process_id:uuid}"): "pestle.manage",
    # Mapping rules, as an administrator: what sources and fields exist, the
    # current rules, a preview against collected evidence, and a new rule.
    ("GET", "/v1/admin/evidence-event-samples"): "rules.write",
    ("GET", "/v1/admin/mapping-rules"): "rules.write",
    ("POST", "/v1/admin/mapping-rules/preview"): "rules.write",
    ("PUT", "/v1/admin/mapping-rules/{rule_id}"): "rules.write",
}

# Schemes a configured redirect URI may not use: the web and script schemes a
# browser would open as a page, run, or read from the device.
_REFUSED_SCHEMES = frozenset(
    {"http", "javascript", "data", "file", "vbscript", "blob", "about", "ws", "wss"}
)
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_URI_LIMIT = 2048


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


def grantable_scopes(held: set[str], *, write: bool) -> list[str]:
    """The scopes an account holding ``held`` may grant, in display order.

    ``held`` is the account's permission codes with their implications, or
    ``{"*"}`` for an administrator. Change scopes are offered only with
    ``write``, which is KEEN_MCP_WRITE_ENABLED.
    """
    return [
        code
        for code, (_, granted_by) in SCOPES.items()
        if (write or code not in WRITE_SCOPES) and ("*" in held or held & granted_by)
    ]


def granted_scopes(requested: list[str], held: set[str], *, write: bool) -> list[str]:
    """``requested`` narrowed to known scopes the account may grant, in order."""
    allowed = set(grantable_scopes(held, write=write)) & set(requested)
    return [code for code in SCOPES if code in allowed]


def offered_scopes(*, write: bool) -> list[str]:
    """Every scope a connection may ask for: change scopes only with ``write``."""
    return [code for code in SCOPES if write or code not in WRITE_SCOPES]


def with_read_scopes(chosen: list[str]) -> list[str]:
    """``chosen`` with the read scope each change scope carries, in order."""
    wanted = set(chosen) | {WRITE_SCOPES[c] for c in chosen if c in WRITE_SCOPES}
    return [code for code in SCOPES if code in wanted]


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
