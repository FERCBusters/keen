"""Request policy refusals: CSRF, response headers, body limits and the audit trail.

The CSRF, response-policy and audit-trail paths run through the assembled
application; the body-limit middleware is driven over ASGI with a client that
disconnects or never finishes its body. The shared sanitiser, pattern compiler
and collection diagnostics are called with the hostile input they exist for.
Each test checks what the client receives and what was stored or dispatched.
"""

import asyncio
import ssl

import anyio
import httpx
import pytest
from app import main
from app.core.config import settings
from app.db.models import AuditLog
from app.security import request_limits
from app.security.errors import collection_error
from app.security.regex import compile_pattern
from app.security.request_limits import RequestBodyLimitMiddleware
from app.security.rich_text import sanitize_rich_text_html
from sqlalchemy import event

from tests.test_assembled_authority_regressions import (  # noqa: F401 - fixtures
    BASE,
    keen,
    unsafe,
)


def _csrf_cookies(response):
    return [c for c in response.headers.get_list("set-cookie") if "keen_csrf=" in c]


# -- CSRF -------------------------------------------------------------------


def test_a_csrf_token_with_non_ascii_characters_is_refused(keen):  # noqa: F811
    client = keen.client(keen.user())
    headers = {**unsafe(client), "X-CSRF-Token": "é".encode("latin-1")}

    refused = client.put("/v1/me/preferences", json={}, headers=headers)

    assert (refused.status_code, refused.json()) == (
        403,
        {"detail": "CSRF token missing or invalid"},
    )


def test_a_browser_keeps_working_when_its_replacement_csrf_token_cannot_be_made(
    keen,  # noqa: F811
    monkeypatch,
):
    user = keen.user()
    client = keen.client(user)
    client.cookies.delete("keen_csrf")
    replaced = client.get("/v1/me")
    assert replaced.status_code == 200 and len(_csrf_cookies(replaced)) == 1
    client.cookies.delete("keen_csrf")

    def unavailable():
        raise OSError("synthetic randomness failure")

    monkeypatch.setattr(main, "generate_csrf_token", unavailable)

    response = client.get("/v1/me")

    assert response.status_code == 200
    assert response.json()["user"] == user.username
    assert _csrf_cookies(response) == []


# -- response policy --------------------------------------------------------


@pytest.mark.parametrize("secure", [True, False])
def test_transport_security_is_announced_only_for_secure_cookies(
    keen,  # noqa: F811
    monkeypatch,
    secure,
):
    monkeypatch.setattr(settings, "cookie_secure", secure)

    response = keen.client().get("/health")

    assert response.status_code == 200
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers.get("strict-transport-security") == (
        "max-age=31536000" if secure else None
    )


# -- the audit trail --------------------------------------------------------


# Requests refused by the middleware leave no route session open on the test
# connection, whose rollback would otherwise take the audit record with it.


def test_long_request_details_are_clipped_in_the_audit_trail(keen):  # noqa: F811
    user = keen.user()
    client = keen.client(user)
    referer = BASE + "/" + "r" * 600

    response = client.get(
        "/v1/users",
        params={"q": "x" * 5000},
        headers={"User-Agent": "u" * 300, "Referer": referer},
    )

    assert response.status_code == 403
    keen.db.expire_all()
    (row,) = keen.db.query(AuditLog).filter_by(path="/v1/users").all()
    assert (row.username, row.status_code) == (user.username, 403)
    assert row.query_string == ("q=" + "x" * 5000)[:4096]
    assert row.user_agent == "u" * 256
    assert row.referer == referer[:512]


def test_an_audit_trail_that_cannot_be_written_leaves_the_response_intact(
    keen,  # noqa: F811
):
    client = keen.client(keen.user())
    recorded = client.get("/v1/users")
    bind = keen.db.get_bind()

    def unavailable(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO audit_logs"):
            raise OSError("synthetic database failure")

    event.listen(bind, "before_cursor_execute", unavailable)
    try:
        unrecorded = client.get("/v1/users")
    finally:
        event.remove(bind, "before_cursor_execute", unavailable)

    assert (
        (unrecorded.status_code, unrecorded.json())
        == (
            recorded.status_code,
            recorded.json(),
        )
        == (403, {"detail": "Admin role required"})
    )
    keen.db.expire_all()
    assert keen.db.query(AuditLog).filter_by(path="/v1/users").count() == 1


# -- request bodies ---------------------------------------------------------


def _drive(receive, path="/v1/me/preferences"):
    """Send one request through the body-limit middleware to a recording app."""
    dispatched, sent = [], []

    async def app(scope, receive, send):
        dispatched.append(scope["path"])

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "PUT",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
    }
    asyncio.run(RequestBodyLimitMiddleware(app)(scope, receive, send))
    return dispatched, sent


def test_a_client_that_disconnects_mid_body_reaches_no_handler():
    messages = iter(
        [
            {"type": "http.request", "body": b'{"theme": ', "more_body": True},
            {"type": "http.disconnect"},
        ]
    )

    async def receive():
        return next(messages)

    dispatched, sent = _drive(receive)

    assert dispatched == [] and sent == []


def test_a_body_that_never_finishes_is_refused_at_its_deadline(monkeypatch):
    deadlines = []
    real = anyio.fail_after

    def at_once(delay, *args, **kwargs):
        deadlines.append(delay)
        return real(0)

    monkeypatch.setattr(request_limits.anyio, "fail_after", at_once)

    async def receive():
        await anyio.Event().wait()

    dispatched, sent = _drive(receive)

    assert deadlines == [30]
    assert dispatched == []
    start, body = sent
    assert start["status"] == 408
    assert (b"cache-control", b"no-store") in start["headers"]
    assert body["body"] == b'{"detail":"Request body timeout"}'


# -- shared helpers given hostile input -------------------------------------


@pytest.mark.parametrize(
    "html,expected",
    [
        ('<a href="  " onclick="steal()">text</a>', "<a>text</a>"),
        ("<a>text</a>", "<a>text</a>"),
        ('<a href="https://[evil.example">text</a>', "<a>text</a>"),
        ("<p/>one<br/>two", "one<br>two"),
        ('<img src="x" onerror="alert(1)"/>text<h1/>', "text"),
    ],
    ids=[
        "blank-href",
        "no-href",
        "unparseable-href",
        "self-closed-container",
        "self-closed-disallowed",
    ],
)
def test_rich_text_drops_links_and_self_closed_tags_it_cannot_trust(html, expected):
    assert sanitize_rich_text_html(html) == expected


@pytest.mark.parametrize(
    "pattern,message",
    [
        ("a.*b.*c", "Use one unbounded repetition at most"),
        ("(?:x+)y*", "Use one unbounded repetition at most"),
        # Python reads this as a set; the matching engine reads a POSIX class.
        ("[a[:digit:]", "Invalid match pattern"),
    ],
)
def test_administrator_patterns_the_matcher_cannot_bound_are_refused(pattern, message):
    with pytest.raises(ValueError, match="^" + message.replace("(", r"\(") + "$"):
        compile_pattern(pattern)
    assert compile_pattern("a.*b").search("a--b")


@pytest.mark.parametrize(
    "error,message",
    [
        (
            TimeoutError("read from https://user:SECRET_VALUE@example.test"),
            "Collection timed out; narrow the input or check upstream availability",
        ),
        (
            httpx.ReadTimeout("SECRET_VALUE"),
            "Collection timed out; narrow the input or check upstream availability",
        ),
        (
            ssl.SSLCertVerificationError("certificate for SECRET_VALUE.example"),
            "TLS verification failed; check the upstream certificate and trusted CA",
        ),
    ],
    ids=["timeout", "client-timeout", "certificate"],
)
def test_collection_diagnostics_name_the_failure_without_its_text(error, message):
    assert collection_error(error) == message
