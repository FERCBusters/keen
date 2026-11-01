"""Explicit browser-auth exceptions; every other HTTP route is authenticated.

An exception applies only to the listed method and complete path shape. New
routes under /auth, /webhooks or /docs do not inherit an exemption. Machine
endpoints authenticate their own credentials; MFA endpoints validate a pending
ceremony and Origin. Keep the contract tests aligned with this small inventory.
"""

from starlette.routing import compile_path

PUBLIC_HTTP_ROUTES = {
    # API documentation and the OpenAPI schema require a signed-in session.
    "GET": (
        "/health",
        "/v1/auth/methods",
        "/v1/auth/logout",
        "/v1/auth/oidc/start",
        "/v1/auth/oidc/callback",
        "/v1/auth/oidc/logout",
        "/v1/auth/sso/{provider_key}/start",
        "/v1/auth/sso/{provider_key}/callback",
        "/v1/agents/server-key",
    ),
    "POST": (
        "/v1/auth/oidc/logout",
        "/v1/auth/login",
        "/v1/auth/logout",
        "/v1/otlp/logs",
        "/v1/agents/heartbeat",
        "/v1/agents/enroll",
        "/v1/agents/renew",
        "/v1/webhooks/{provider}/{event_type}",
        "/v1/webhooks/connections/{connection_id}/{provider}/{event_type}",
        "/v1/webhooks/isms/effectiveness-metrics/{metric_key}",
        "/v1/auth/mfa/manage/start",
        "/v1/auth/mfa/state",
        "/v1/auth/mfa/verify-code",
        "/v1/auth/mfa/totp/start",
        "/v1/auth/mfa/totp/confirm",
        "/v1/auth/mfa/webauthn/options",
        "/v1/auth/mfa/webauthn/verify",
        "/v1/auth/mfa/webauthn/register-options",
        "/v1/auth/mfa/webauthn/register",
        "/v1/auth/mfa/remove",
        "/v1/auth/mfa/recovery/regenerate",
        "/v1/auth/mfa/finish",
        "/v1/auth/mfa/cancel",
    ),
}
_COMPILED = {
    method: tuple(compile_path(path)[0] for path in paths)
    for method, paths in PUBLIC_HTTP_ROUTES.items()
}


def policy_path(request):
    """Return the decoded path the router dispatches on.

    ``request.url.path`` is re-parsed from a URL string, so an encoded ``?`` or
    ``#`` inside a path parameter truncates it. Policy must use the routed path.
    """
    return request.scope.get("path") or ""


def is_public_request(request):
    path = policy_path(request)
    return any(
        pattern.fullmatch(path) for pattern in _COMPILED.get(request.method.upper(), ())
    )


_PERMISSION_WRITES = {
    "POST": (
        "/v1/events/{event_id}/questions",
        "/v1/events/{event_id}/questions/mark-seen",
        "/v1/questions/{thread_id}/posts",
        "/v1/questions/entities/{entity_type}/{entity_id}",
        "/v1/questions/entities/{entity_type}/{entity_id}/mark-seen",
        "/v1/pestle/items",
        "/v1/pestle/business-processes",
        "/v1/stories",
        "/v1/stories/{story_id}/items",
        "/v1/stories/{story_id}/comments",
        # A person sits only their own attempts; the operation checks the
        # account's linked person and the round's participants.
        "/v1/me/quizzes/rounds/{round_id}/attempt",
        "/v1/me/quizzes/attempts/{attempt_id}/submit",
    ),
    "PATCH": (
        "/v1/pestle/items/{item_id}",
        "/v1/pestle/business-processes/{process_id}",
        "/v1/pestle/items/{item_id}/business-processes",
        "/v1/pestle/items/{item_id}/clauses",
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
        "/v1/pestle/business-processes/{process_id}",
        "/v1/stories/{story_id}",
        "/v1/stories/{story_id}/items/{item_id}",
        "/v1/stories/{story_id}/comments/{comment_id}",
    ),
}
_PERMISSION_WRITE_PATTERNS = {
    method: tuple(compile_path(path)[0] for path in paths)
    for method, paths in _PERMISSION_WRITES.items()
}


def is_permission_checked_write(request):
    path = policy_path(request)
    return any(
        pattern.fullmatch(path)
        for pattern in _PERMISSION_WRITE_PATTERNS.get(request.method.upper(), ())
    )


# Writes the middleware gates on one permission before the adapter runs.
_GATED_WRITES = {
    ("POST", "/v1/events/{event_id}/incident"): "incident.create",
    ("DELETE", "/v1/events/{event_id}/incidents/{incident_id}"): "incident.delete",
}
_GATED_WRITE_PATTERNS = tuple(
    (method, compile_path(path)[0], code)
    for (method, path), code in _GATED_WRITES.items()
)


def gated_write_permission(request):
    """Return the permission the middleware requires for this write, if any."""
    method, path = request.method.upper(), policy_path(request)
    for allowed, pattern, code in _GATED_WRITE_PATTERNS:
        if method == allowed and pattern.fullmatch(path):
            return code
    return None
