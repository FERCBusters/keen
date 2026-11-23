"""KEEN's part in mosp's sign-in, session and routing flows (``mosp.host.hooks``).

Each hook imports its KEEN module when called, so tests that replace those
modules' functions reach the hooks too.
"""

from __future__ import annotations


def session_allowed(db, user, session):
    from app.security.hosted import hosted_session_allowed

    return hosted_session_allowed(db, user, session)


def user_created(db, user):
    from app.services.people import ensure_person_for_user

    ensure_person_for_user(db, user)


def sso_identity_allowed(provider_key, issuer, subject):
    from app.security.hosted import enforce_hosted_identity

    enforce_hosted_identity(provider_key, issuer, subject)


def sign_in_methods():
    from app.security.demo import demo_metadata

    return {"demo": demo_metadata()}


def route_dependencies():
    from app.api.dependencies.features import enforce_features

    return (enforce_features,)


def security_notice(event):
    if event == "mcp-connected":
        return (
            "An AI app was connected to your Keen account",
            "The app can read Keen as you through MCP, within the scopes approved. "
            "Review and revoke connected apps under Account, Security.",
        )
    return None


def origin_required():
    from app.core.config import settings

    return settings.hosted_mode


def permission_implications():
    from app.security.permissions import PERMISSION_IMPLICATIONS

    return PERMISSION_IMPLICATIONS


def body_limit(path):
    from app.security import request_policy

    return request_policy.body_limit(path)


def encoded_slash_routes():
    from app.security.request_policy import ENCODED_SLASH_ROUTES

    return ENCODED_SLASH_ROUTES


def measured_fields():
    from app.security.redaction_policy import MEASURED_FIELDS

    return MEASURED_FIELDS


def issued_secret_pattern():
    from app.security.redaction_policy import ISSUED_SECRET_RE

    return ISSUED_SECRET_RE


def route_policy():
    from app.security.route_policy import KEEN_POLICY

    return KEEN_POLICY


def json_body_limit(path):
    from app.security import request_policy

    return request_policy.json_body_limit(path)


def json_value_limit(path):
    from app.security import request_policy

    return request_policy.json_value_limit(path)


def inner_request_user(db, request):
    from app.security import mcp_requests

    return mcp_requests.inner_request_user(db, request)


def bootstrap_allowed():
    # A hosted installation's owner is created explicitly before the API starts.
    from app.core.config import settings

    return not settings.hosted_mode
