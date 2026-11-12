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
from app.security import auth as security_auth
from app.security import request_limits
from app.security.errors import collection_error
from app.security.regex import compile_pattern
from app.security.request_limits import RequestBodyLimitMiddleware
from app.security.rich_text import sanitize_rich_text_html
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import event

from tests.test_assembled_authority_regressions import (  # noqa: F401 - fixtures
    BASE,
    PASSWORD,
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


def test_a_csrf_token_the_session_never_issued_proves_nothing(keen):  # noqa: F811
    """Matching cookie and header is no proof by itself: the token must be the
    one issued with the session, whatever wrote the cookie."""
    user = keen.user()
    client = keen.client(user)
    issued = client.cookies.get("__Host-keen_csrf")
    stored = keen.store.get(keen.store.keys("keen:session:*")[0])
    assert issued and f'"csrf": "{issued}"' in stored

    client.cookies.delete("__Host-keen_csrf")
    client.cookies.set("__Host-keen_csrf", "written-by-something-else")
    planted = client.patch(
        "/v1/me/preferences",
        json={"theme": "ocean"},
        headers={"Origin": BASE, "X-CSRF-Token": "written-by-something-else"},
    )
    assert (planted.status_code, planted.json()) == (
        403,
        {"detail": "CSRF token missing or invalid"},
    )
    assert client.get("/v1/me/preferences").json()["theme"] != "ocean"

    # Another signed-in account's token is no better.
    other = keen.client(keen.user()).cookies.get("__Host-keen_csrf")
    client.cookies.delete("__Host-keen_csrf")
    client.cookies.set("__Host-keen_csrf", other)
    borrowed = client.patch(
        "/v1/me/preferences",
        json={"theme": "ocean"},
        headers={"Origin": BASE, "X-CSRF-Token": other},
    )
    assert borrowed.status_code == 403

    client.cookies.delete("__Host-keen_csrf")
    client.cookies.set("__Host-keen_csrf", issued)
    own = client.patch(
        "/v1/me/preferences",
        json={"theme": "ocean"},
        headers={"Origin": BASE, "X-CSRF-Token": issued},
    )
    assert own.status_code == 200 and own.json()["theme"] == "ocean"


def test_only_the_host_bound_csrf_cookie_is_read(keen):  # noqa: F811
    """A site on a sibling subdomain can set ``keen_csrf`` for the parent
    domain and knows its value; a browser lets only this host set
    ``__Host-keen_csrf``. Sign-out runs before authentication, where the
    cookie and header are the whole token check."""
    client = keen.client(keen.user())
    issued = client.cookies.get("__Host-keen_csrf")
    assert issued and client.cookies.get("keen_csrf") is None

    client.cookies.delete("__Host-keen_csrf")
    client.cookies.set("keen_csrf", "planted-by-a-sibling-site")
    planted = client.post(
        "/v1/auth/logout",
        headers={"Origin": BASE, "X-CSRF-Token": "planted-by-a-sibling-site"},
    )
    assert planted.status_code == 403
    assert not planted.headers.get_list("set-cookie")
    assert client.get("/v1/auth/check").status_code == 204

    # Beside the planted cookie, the host-bound one is the one that counts.
    client.cookies.set("__Host-keen_csrf", issued)
    still_planted = client.post(
        "/v1/auth/logout",
        headers={"Origin": BASE, "X-CSRF-Token": "planted-by-a-sibling-site"},
    )
    assert still_planted.status_code == 403
    signed_out = client.post(
        "/v1/auth/logout", headers={"Origin": BASE, "X-CSRF-Token": issued}
    )
    assert signed_out.status_code == 204
    assert client.get("/v1/auth/check").status_code == 401


def test_sign_in_sets_and_sign_out_clears_the_host_bound_csrf_cookie(keen):  # noqa: F811
    client = keen.client()
    user = keen.user()
    signed_in = client.post(
        "/v1/auth/login",
        json={"username": user.username, "password": PASSWORD},
        headers={"Origin": BASE},
    )
    assert signed_in.status_code == 200, signed_in.text
    (cookie,) = _csrf_cookies(signed_in)
    attributes = [part.strip().lower() for part in cookie.split(";")]
    # The attributes a browser demands of a __Host- cookie, and no HttpOnly:
    # the UI reads the token to repeat it in a header.
    assert cookie.startswith("__Host-keen_csrf=")
    assert "secure" in attributes and "path=/" in attributes
    assert not any(a.startswith("domain") for a in attributes)
    assert "httponly" not in attributes and "samesite=strict" in attributes

    signed_out = client.post("/v1/auth/logout", headers=unsafe(client))
    (cleared,) = _csrf_cookies(signed_out)
    assert cleared.startswith("__Host-keen_csrf=") and "Max-Age=0" in cleared
    assert "secure" in cleared.lower() and "domain" not in cleared.lower()


@pytest.mark.parametrize(
    "failure",
    [RedisConnectionError("synthetic outage"), TimeoutError("synthetic outage")],
    ids=["store-refuses", "store-times-out"],
)
def test_a_session_store_outage_is_answered_as_an_outage(keen, monkeypatch, failure):  # noqa: F811
    client = keen.client(keen.user())

    def unavailable(*args, **kwargs):
        raise failure

    with monkeypatch.context() as outage:
        outage.setattr(security_auth, "get_session", unavailable)
        response = client.get("/v1/me")

    # Nobody is recognised, no account is named, and the client knows to retry.
    assert (response.status_code, response.json()) == (
        503,
        {"detail": "Service temporarily unavailable. Please try again later."},
    )
    assert response.headers["retry-after"] == "5"
    assert "set-cookie" not in response.headers
    # The session is still there once the store answers again.
    assert client.get("/v1/me").status_code == 200


def test_a_browser_keeps_working_when_its_replacement_csrf_token_cannot_be_made(
    keen,  # noqa: F811
    monkeypatch,
):
    user = keen.user()
    client = keen.client(user)
    issued = client.cookies.get("__Host-keen_csrf")
    client.cookies.delete("__Host-keen_csrf")
    replaced = client.get("/v1/me")
    # The replacement is the token the session was issued with.
    assert replaced.status_code == 200 and len(_csrf_cookies(replaced)) == 1
    assert client.cookies.get("__Host-keen_csrf") == issued
    client.cookies.delete("__Host-keen_csrf")

    def unavailable(response, token):
        raise OSError("synthetic cookie failure")

    monkeypatch.setattr(main, "set_csrf_cookie", unavailable)

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

    assert (response.status_code, response.json()) == (
        403,
        {"detail": "Admin role required"},
    )
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
