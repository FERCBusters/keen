"""What KEEN's records hold that myf's redaction (``myf.security.redaction``)
needs to know about."""

from myf.security.regex import scan_pattern

# Tokens, codes and consent keys KEEN issues to MCP clients, wherever they
# appear (app.security.mcp_tokens): a referrer or a pasted URL can carry one.
ISSUED_SECRET_RE = scan_pattern(r"(?:kat|krt|kac|kcr)_[A-Za-z0-9_-]{43}")

# Measured workstation-posture facts whose names contain a sensitive-key
# substring but which hold a boolean, a count or a timestamp.
MEASURED_FIELDS: dict[str, str] = {
    "posture.password_required": "boolean",
    "posture.password_required_configured": "boolean",
    "posture.password_delay_seconds": "count",
    "posture.signatures_age_days": "count",
    "posture.signatures_updated": "timestamp",
}
