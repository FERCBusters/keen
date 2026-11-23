"""KEEN's request size classes and routes whose path parameters may hold "/";
mosp's request limits (``mosp.security.request_limits``) apply them."""

from mosp.security.regex import scan_pattern

from app.core.config import settings

MAX_BODY = 40 * 1024 * 1024
# A decoded JSON document costs many times its size in memory and processor
# time, so the application parses at most this much JSON from one request.
# Administration routes, which hold configuration and control imports, may
# send a whole request body of JSON.
MAX_JSON_BODY = 2 * 1024 * 1024
# Checking a document against a route's model also costs time for each value,
# and that check runs on the event loop. Outside administration, no form sends
# more than a few thousand values, so a request holds at most this many.
MAX_JSON_VALUES = 20_000
# ISMS document files and audit final reports accept a single file up to this
# size; their operations check the file itself against the same figure.
MAX_FILE_UPLOAD = 100 * 1024 * 1024
# An MCP call while connections may add records (app.api.mcp.changes).
MCP_WRITE_BODY = 6 * 1024 * 1024
# A signed effectiveness-metric submission.
MAX_METRIC_SUBMISSION = 256 * 1024
# Room for the multipart boundary and part headers around that one file.
_MULTIPART_ROOM = 64 * 1024
_SEALED_OVERHEAD = 1120 + 32 + 16
_FILE_UPLOADS = scan_pattern(r"/v1/(?:isms/documents/[^/]+/file|audits/[^/]+/report)")


def body_limit(path):
    """KEEN's limit for a path; mosp's own sign-in, password and second-factor
    routes take mosp's limits (mosp.host.hooks.body_limit) before this is asked."""
    if path in {"/v1/agents/enroll", "/v1/agents/renew"}:
        return 16 * 1024
    # keen-agent requests are encrypted to KEEN, which adds a fixed overhead
    # (app.agents.server_keys.SEALED_OVERHEAD) to the plaintext limits.
    if path == "/v1/agents/heartbeat":
        return 32 * 1024 + _SEALED_OVERHEAD
    if path == "/v1/otlp/logs":
        return 1024 * 1024 + _SEALED_OVERHEAD
    # Signed metric submissions are small; the body is read before its signature
    # can be checked, so an unsigned sender holds no more than this in memory.
    if path.startswith("/v1/webhooks/isms/effectiveness-metrics/"):
        return MAX_METRIC_SUBMISSION
    if path.startswith("/v1/webhooks/"):
        return 10 * 1024 * 1024
    if _FILE_UPLOADS.fullmatch(path):
        return MAX_FILE_UPLOAD + _MULTIPART_ROOM
    # MCP clients' OAuth requests are small forms and JSON documents, which the
    # MCP SDK parses on the event loop; MCP calls carry short tool arguments,
    # unless connections may add records: then a call can carry a document
    # (a file of up to 4 MiB, base64-encoded) or the rows of an import.
    if path.startswith("/v1/oauth/"):
        return 16 * 1024
    if path == "/v1/mcp":
        return MCP_WRITE_BODY if settings.mcp_write_enabled else 256 * 1024
    return MAX_BODY


def json_body_limit(path):
    """The most JSON the application parses from one request to this path.

    The check applies where a body is parsed as JSON, whatever its declared
    content type; webhooks and agents read their bodies under their own limits.
    """
    if path.startswith("/v1/admin/"):
        return MAX_BODY
    return MAX_JSON_BODY


def json_value_limit(path):
    """The most values a parsed JSON request to this path may hold, if bounded."""
    if path.startswith("/v1/admin/"):
        return None
    return MAX_JSON_VALUES


# The routes whose last parameter may itself contain "/" (a ``:path``
# parameter), and so may arrive with an encoded slash. Every such route is
# listed here; tests/test_request_targets.py fails when another appears.
ENCODED_SLASH_ROUTES = scan_pattern(r"/v1/admin/frameworks/[^/]+/nodes/[^/]+/.+")
