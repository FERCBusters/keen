"""Explicit browser-auth exceptions; every other HTTP route is authenticated.

An exception applies only to the listed method and complete path shape. New
routes under /auth, /webhooks or /docs do not inherit an exemption. Machine
endpoints authenticate their own credentials; MFA endpoints validate a pending
ceremony and Origin. Keep the contract tests aligned with this small inventory.
"""

from starlette.routing import compile_path

PUBLIC_HTTP_ROUTES = {
    "GET": (
        "/health",
        "/openapi.json",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        "/v1/openapi.json",
        "/v1/docs",
        "/v1/docs/oauth2-redirect",
        "/v1/redoc",
        "/v1/auth/methods",
        "/v1/auth/logout",
        "/v1/auth/oidc/start",
        "/v1/auth/oidc/callback",
        "/v1/auth/oidc/logout",
        "/v1/auth/sso/{provider_key}/start",
        "/v1/auth/sso/{provider_key}/callback",
    ),
    "POST": (
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


def is_public_request(request):
    return any(
        pattern.fullmatch(request.url.path)
        for pattern in _COMPILED.get(request.method.upper(), ())
    )
