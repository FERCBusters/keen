"""KEEN's routes in mosp's route policy (``mosp.security.route_policy``).

mosp declares its own routes (signing in and out, single sign-on, second
factors, SSO addresses); these are KEEN's. Every other HTTP route is
authenticated, and every write without a rule below needs an administrator.
Machine endpoints authenticate their own credentials. Keep the contract tests
aligned with this small inventory.
"""

from mosp.security.route_policy import (
    PrefixRule,
    RouteCodes,
    RoutePolicy,
    RouteSet,
    policy_path,
)

from app.security.mcp_tokens import INNER_ROUTES

PUBLIC_HTTP_ROUTES = {
    # API documentation and the OpenAPI schema require a signed-in session.
    "GET": (
        "/health",
        "/v1/agents/server-key",
        # MCP's OAuth discovery documents and authorisation endpoint: the
        # authorisation endpoint sends a browser to the consent page, which
        # requires a signed-in session (app/api/routes/mcp.py).
        "/.well-known/oauth-protected-resource/api/v1/mcp",
        "/.well-known/oauth-authorization-server/api/v1/oauth",
        "/v1/oauth/authorize",
        # Streamable HTTP's server-to-client event stream. FastMCP either checks
        # the bearer or refuses it with 405; a browser-auth 401 makes MCP clients
        # refresh their tokens on every connection.
        "/v1/mcp",
    ),
    "DELETE": (
        # Streamable HTTP ends a session with DELETE; FastMCP checks the bearer.
        "/v1/mcp",
    ),
    "POST": (
        "/v1/otlp/logs",
        "/v1/agents/heartbeat",
        "/v1/agents/enroll",
        "/v1/agents/renew",
        "/v1/webhooks/{provider}/{event_type}",
        "/v1/webhooks/connections/{connection_id}/{provider}/{event_type}",
        "/v1/webhooks/isms/effectiveness-metrics/{metric_key}",
        # The OAuth endpoints check PKCE, codes and tokens themselves; the MCP
        # endpoint admits only a current access token (FastMCP's bearer check).
        "/v1/oauth/token",
        "/v1/oauth/register",
        "/v1/oauth/revoke",
        "/v1/mcp",
    ),
}

_PERMISSION_WRITES = {
    "POST": (
        "/v1/events/{event_id}/questions",
        "/v1/events/{event_id}/questions/mark-seen",
        "/v1/questions/{thread_id}/posts",
        "/v1/questions/entities/{entity_type}/{entity_id}",
        "/v1/questions/entities/{entity_type}/{entity_id}/mark-seen",
        "/v1/pestle/items",
        "/v1/stories",
        "/v1/stories/{story_id}/items",
        "/v1/stories/{story_id}/comments",
        # A person sits only their own attempts; the operation checks the
        # account's linked person and the round's participants.
        "/v1/me/quizzes/rounds/{round_id}/attempt",
        "/v1/me/quizzes/attempts/{attempt_id}/submit",
        # A person marks only their own notifications as read.
        "/v1/me/inbox/{item_id}/read",
        "/v1/me/inbox/read-all",
        # A person answers consent requests as themselves; the operation grants
        # no more than the account can read.
        "/v1/me/mcp/consent/view",
        "/v1/me/mcp/consent",
    ),
    "PATCH": (
        "/v1/pestle/items/{item_id}",
        "/v1/pestle/items/{item_id}/clauses",
        "/v1/pestle/items/{item_id}/controls",
        "/v1/stories/{story_id}",
        "/v1/stories/{story_id}/items/{item_id}",
    ),
    "PUT": (
        "/v1/stories/{story_id}/layout",
        "/v1/me/quizzes/attempts/{attempt_id}/pages/{page}",
    ),
    "DELETE": (
        "/v1/questions/{thread_id}",
        "/v1/pestle/items/{item_id}",
        "/v1/stories/{story_id}",
        "/v1/stories/{story_id}/items/{item_id}",
        "/v1/stories/{story_id}/comments/{comment_id}",
        # A person revokes only their own MCP connections.
        "/v1/me/mcp/grants/{grant_id}",
    ),
}

# Writes the middleware gates on one permission before the adapter runs.
_GATED_WRITES = {
    ("POST", "/v1/events/{event_id}/incident"): "incident.create",
    ("DELETE", "/v1/events/{event_id}/incidents/{incident_id}"): "incident.delete",
}

KEEN_POLICY = RoutePolicy(
    public=RouteSet(PUBLIC_HTTP_ROUTES),
    object_checked=RouteSet(_PERMISSION_WRITES),
    gated=RouteCodes(_GATED_WRITES),
    # Any write to these is the account's own.
    self_service=RouteSet(
        dict.fromkeys(
            ("POST", "PUT", "PATCH", "DELETE"),
            ("/v1/me/preferences",),
        )
    ),
    self_service_prefixes=("/v1/me/saved-searches",),
    prefix_rules=(
        PrefixRule("/v1/audits", ("audits.manage", "audits.admin")),
        PrefixRule("/v1/risks", ("risk.manage",)),
        PrefixRule("/v1/quizzes", ("quiz.manage",)),
        PrefixRule(
            "/v1/isms",
            ("isms.manage", "risk.manage"),
            "isms.manage permission required",
        ),
    ),
    admin_prefixes=("/v1/users", "/v1/admin"),
    # The administrators' question badge poll and the webhook ingesters.
    unaudited_paths=frozenset({"/v1/admin/questions/summary", "/v1/webhooks"}),
    unaudited_public_reads=frozenset({"/health", "/v1/agents/server-key"}),
)


# The routes MCP tools call in-process, and the scope each needs (None when
# the tool and route decide).
_MCP_TOOL_ROUTES = RouteCodes(INNER_ROUTES)
# An in-process request no MCP tool makes.
NOT_A_TOOL_ROUTE = "not-a-tool-route"


def mcp_tool_route_scope(request):
    """The scope an MCP tool's in-process request to this route needs: a scope,
    None when the tool and route decide, or NOT_A_TOOL_ROUTE."""
    method = request.method.upper()
    if method == "HEAD":
        method = "GET"
    return _MCP_TOOL_ROUTES.lookup(method, policy_path(request), NOT_A_TOOL_ROUTE)
