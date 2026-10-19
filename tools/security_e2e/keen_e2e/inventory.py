"""What the suite expects of a KEEN installation's public surface.

Keep PUBLIC_API in step with services/api/app/security/route_policy.py. A route
that is public on the server but missing here is reported as a finding.
"""

from __future__ import annotations

import re
from pathlib import Path

PUBLIC_API = {
    "GET": {
        "/health",
        "/v1/auth/methods",
        "/v1/auth/logout",
        "/v1/auth/oidc/start",
        "/v1/auth/oidc/callback",
        "/v1/auth/oidc/logout",
        "/v1/auth/sso/{provider_key}/start",
        "/v1/auth/sso/{provider_key}/callback",
    },
    "POST": {
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
    },
}

# Pages served before sign-in. Everything else must show the login page.
PUBLIC_PAGES = {"login.html", "mfa.html", "sso-email.html"}
ADMIN_PAGES = {"admin.html", "agents.html"}

# Used when the suite runs outside a KEEN checkout.
FALLBACK_PAGES = [
    "index.html",
    "events.html",
    "event.html",
    "controls.html",
    "control.html",
    "clauses.html",
    "clause.html",
    "sources.html",
    "source.html",
    "risks.html",
    "risk.html",
    "risk-register.html",
    "pestle.html",
    "pestle_item.html",
    "interested_parties.html",
    "interested_party.html",
    "isms.html",
    "isms-objective.html",
    "isms-document.html",
    "isms-meeting.html",
    "isms-asset.html",
    "isms-access.html",
    "isms-effectiveness-measure.html",
    "isms-effectiveness-metric.html",
    "audits.html",
    "audit.html",
    "questions.html",
    "my-questions.html",
    "account.html",
    "documents.html",
    "assurance.html",
    "statement-of-applicability.html",
    "mitigator.html",
    "bookstack-sections.html",
    "admin.html",
    "agents.html",
]


def html_pages() -> list[str]:
    public = Path(__file__).resolve().parents[3] / "services" / "ui" / "public"
    if public.is_dir():
        return sorted(p.name for p in public.glob("*.html"))
    return list(FALLBACK_PAGES)


def concrete(path: str, value: str = "00000000-0000-0000-0000-000000000000") -> str:
    """Fill path parameters with a syntactically valid identifier."""
    return re.sub(r"\{[^}]+\}", value, path)


def is_public(method: str, path: str) -> bool:
    return path in PUBLIC_API.get(method.upper(), set())
