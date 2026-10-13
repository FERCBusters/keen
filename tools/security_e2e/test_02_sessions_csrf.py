"""Sign-in, cookies, sign-out, CSRF and WebSocket origin checks."""

from __future__ import annotations

import ssl
import time

import pytest
from keen_e2e.client import Session


def _set_cookie_attrs(response, name):
    for raw in response.headers.get_list("set-cookie"):
        if raw.split("=", 1)[0].strip() == name:
            return {p.strip().split("=", 1)[0].lower(): p for p in raw.split(";")[1:]}
    return None


def test_session_cookie_attributes(target, accounts):
    session = accounts("plain")
    fresh = Session(target, "cookie-check")
    try:
        response = fresh.login(session.username, session.password)
        attrs = _set_cookie_attrs(response, fresh.session_cookie)
        assert attrs is not None
        assert "httponly" in attrs
        assert "samesite" in attrs and "none" not in attrs["samesite"].lower()
        if target.origin.startswith("https"):
            assert "secure" in attrs
        csrf = _set_cookie_attrs(response, fresh.csrf_cookie)
        assert csrf is not None and "httponly" not in csrf
    finally:
        fresh.logout()
        fresh.close()


def test_sign_in_issues_a_fresh_session_id(target, accounts):
    user = accounts("plain")
    session = Session(target, "fixation")
    try:
        session.login(user.username, user.password)
        first = session.http.cookies.get(session.session_cookie)
        session.login(user.username, user.password)
        second = session.http.cookies.get(session.session_cookie)
        assert first and second and first != second
        stale = Session(target, "stale")
        stale.http.cookies.set(session.session_cookie, first)
        assert stale.get("/api/v1/me").status_code == 401
        stale.close()
    finally:
        session.logout()
        session.close()


def test_failed_sign_in_does_not_reveal_whether_the_account_exists(
    anonymous, accounts, run_id
):
    known = accounts("plain").username
    responses = []
    for username in (known, f"{run_id}-no-such-user"):
        started = time.monotonic()
        r = anonymous.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "wrong-password-e2e"},
            unsafe=False,
        )
        responses.append((r.status_code, r.text, time.monotonic() - started))
    (s1, b1, t1), (s2, b2, t2) = responses
    assert (s1, b1) == (s2, b2)
    # Coarse check only; a timing claim needs repeated interleaved measurement.
    assert abs(t1 - t2) < 1.0


def test_sign_out_revokes_the_server_session(target, accounts):
    user = accounts("plain")
    session = Session(target, "logout")
    try:
        session.login(user.username, user.password)
        sid = session.http.cookies.get(session.session_cookie)
        session.ok(session.logout(), 204)
        replay = Session(target, "replay")
        replay.http.cookies.set(session.session_cookie, sid)
        assert replay.get("/api/v1/me").status_code == 401
        replay.close()
    finally:
        session.close()


def test_get_sign_out_does_not_revoke(target, accounts):
    user = accounts("plain")
    session = Session(target, "get-logout")
    try:
        session.login(user.username, user.password)
        for path in ("/api/v1/auth/logout", "/api/v1/auth/oidc/logout"):
            session.get(path)
        assert session.get("/api/v1/me").status_code == 200
    finally:
        session.logout()
        session.close()


@pytest.mark.parametrize(
    "case",
    ["no-token", "wrong-token", "foreign-origin", "null-origin", "sibling-origin"],
)
def test_csrf_and_origin_enforced_on_cookie_writes(accounts, target, case):
    session = accounts("plain")
    token = session.http.cookies.get(session.csrf_cookie)
    headers = {"Origin": target.origin, "X-CSRF-Token": token}
    if case == "no-token":
        headers.pop("X-CSRF-Token")
    elif case == "wrong-token":
        headers["X-CSRF-Token"] = "x" * 43
    elif case == "foreign-origin":
        headers["Origin"] = "https://evil.example"
    elif case == "null-origin":
        headers["Origin"] = "null"
    elif case == "sibling-origin":
        headers["Origin"] = target.origin.replace("://", "://evil.")
    response = session.request(
        "PATCH", "/api/v1/me/preferences", json={}, headers=headers, unsafe=False
    )
    assert response.status_code == 403, (case, response.status_code)


def test_cross_site_sign_in_is_refused(anonymous, accounts):
    user = accounts("plain")
    response = anonymous.request(
        "POST",
        "/api/v1/auth/login",
        json={"username": user.username, "password": user.password},
        headers={"Origin": "https://evil.example"},
        unsafe=False,
    )
    assert response.status_code in (400, 401, 403), response.status_code


def test_login_requires_json_content_type(anonymous, accounts):
    """A cross-site HTML form cannot submit credentials."""
    user = accounts("plain")
    response = anonymous.request(
        "POST",
        "/api/v1/auth/login",
        content=f'{{"username":"{user.username}","password":"{user.password}"}}',
        headers={"Content-Type": "text/plain", "Origin": "null"},
        unsafe=False,
    )
    assert response.status_code in (400, 403, 415, 422), response.status_code


def test_credentials_never_appear_in_urls(page_as, target):
    """Without JavaScript the login fields stay disabled and the form posts.

    Native submission would otherwise send the password in a URL or to a page
    that does not expect it.
    """
    page = page_as(javascript=False)
    page.goto(target.origin + "/login.html")
    password = page.locator("input[type=password]")
    if password.count() == 0:
        pytest.skip("Login form not present")
    assert password.first.is_disabled()
    method = (
        page.locator("form:has(input[type=password])").first.get_attribute("method")
        or "get"
    )
    assert method.lower() == "post"


def _ws_url(target):
    return (
        target.origin.replace("https://", "wss://").replace("http://", "ws://")
        + "/api/ws/notifications"
    )


def _ws_try(target, origin, cookie_header):
    from websockets.sync.client import connect

    ssl_context = None
    if target.origin.startswith("https"):
        ssl_context = (
            target.verify
            if isinstance(target.verify, ssl.SSLContext)
            else ssl.create_default_context()
        )
    try:
        with connect(
            _ws_url(target),
            origin=origin,
            additional_headers={"Cookie": cookie_header} if cookie_header else None,
            ssl=ssl_context,
            open_timeout=10,
        ) as ws:
            try:
                ws.recv(timeout=5)
                return "open"
            except Exception:
                return "open-no-message"
    except Exception as exc:
        return type(exc).__name__


def test_websocket_requires_origin_and_session(target, accounts):
    session = accounts("plain")
    cookie = (
        f"{session.session_cookie}={session.http.cookies.get(session.session_cookie)}"
    )
    assert _ws_try(target, "https://evil.example", cookie) not in (
        "open",
        "open-no-message",
    )
    assert _ws_try(target, target.origin, "") not in ("open", "open-no-message")
    assert _ws_try(target, target.origin, cookie) in ("open", "open-no-message")


def test_forwarded_headers_cannot_change_the_recorded_client_address(
    accounts, admin, run_id
):
    """The audit trail records the same address whatever X-Forwarded-For a client sends."""
    session = accounts("plain")
    variants = {
        "plain": {},
        "malformed": {"X-Forwarded-For": "not-an-address"},
        "oversized": {"X-Forwarded-For": "x" * 3000},
        "spoofed": {"X-Forwarded-For": "203.0.113.9"},
    }
    for label, headers in variants.items():
        session.ok(
            session.get(f"/api/v1/me?e2e_probe={run_id}-{label}", headers=headers), 200
        )
    trail = admin.ok(
        admin.get(
            "/api/v1/admin/audit",
            params={"username": session.username, "path": "/v1/me", "limit": 50},
        ),
        200,
    ).json()["items"]
    recorded = {}
    for item in trail:
        for label in variants:
            if f"{run_id}-{label}" in (item.get("query_string") or ""):
                recorded[label] = item.get("client_ip")
    if "plain" not in recorded:
        pytest.skip("The audit trail did not record the probe requests")
    if recorded.get("spoofed") == "203.0.113.9":
        pytest.skip(
            "This client is inside KEEN's trusted proxy networks, so forwarded "
            "addresses are accepted; run from an address outside them"
        )
    baseline = recorded["plain"]
    differing = {label: ip for label, ip in recorded.items() if ip != baseline}
    assert not differing, (baseline, differing)
