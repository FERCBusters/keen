"""Security contracts at browser, proxy and session-cache boundaries."""

from unittest.mock import Mock

import fakeredis
import pytest
from app.core.config import settings
from app.security import csrf, sessions
from app.security.client_ip import normalise_ip, request_ip
from app.security.rate_limit import fixed_window_allow
from fastapi import Request, Response


def request(headers=None, peer="192.0.2.5"):
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [
                (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
            ],
            "client": (peer, 1234),
        }
    )


@pytest.mark.parametrize(
    "headers,valid",
    [
        ({}, False),
        ({"cookie": "keen_csrf=a"}, False),
        ({"x-csrf-token": "a"}, False),
        ({"cookie": "keen_csrf=a", "x-csrf-token": "b"}, False),
        ({"cookie": "keen_csrf=a", "x-csrf-token": "a"}, True),
        ({"cookie": "keen_csrf=a", "x-csrftoken": "a"}, True),
        ({"cookie": "keen_csrf=a", "x-xsrf-token": "a"}, True),
    ],
)
def test_csrf_requires_matching_cookie_and_header(monkeypatch, headers, valid):
    monkeypatch.setattr(settings, "hosted_mode", False)
    monkeypatch.setattr(settings, "csrf_cookie_name", "keen_csrf")
    monkeypatch.setattr(settings, "csrf_header_name", "X-CSRF-Token")
    assert csrf.csrf_valid(request(headers)) is valid


@pytest.mark.parametrize(
    "origin,valid",
    [
        ("https://tenant.example", True),
        ("https://other.example", False),
        ("http://tenant.example", False),
        ("https://tenant.example.evil.test", False),
        ("", False),
    ],
)
def test_hosted_csrf_requires_exact_tenant_origin(monkeypatch, origin, valid):
    monkeypatch.setattr(settings, "hosted_mode", True)
    monkeypatch.setattr(settings, "public_base_url", "https://tenant.example/")
    assert (
        csrf.csrf_valid(
            request({"cookie": "keen_csrf=a", "x-csrf-token": "a", "origin": origin})
        )
        is valid
    )


def test_csrf_custom_names_and_cookie_flags(monkeypatch):
    monkeypatch.setattr(settings, "hosted_mode", False)
    monkeypatch.setattr(settings, "csrf_cookie_name", "custom_csrf")
    monkeypatch.setattr(settings, "csrf_header_name", "X-Custom-Csrf")
    monkeypatch.setattr(settings, "cookie_secure", True)
    monkeypatch.setattr(settings, "csrf_cookie_samesite", "invalid")
    assert csrf.csrf_valid(
        request({"cookie": "custom_csrf=token", "x-custom-csrf": "token"})
    )
    response = Response()
    csrf.set_csrf_cookie(response, "token")
    cookie = response.headers["set-cookie"]
    assert (
        "Secure" in cookie and "SameSite=strict" in cookie and "HttpOnly" not in cookie
    )
    response = Response()
    csrf.clear_csrf_cookie(response)
    assert "Max-Age=0" in response.headers["set-cookie"]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("::ffff:192.0.2.7", "192.0.2.7"),
        ("2001:0db8::1", "2001:db8::1"),
        ("127.0.0.1", "127.0.0.1"),
        ("invalid", None),
        (None, None),
        ("", None),
    ],
)
def test_ip_normalisation(raw, expected):
    assert normalise_ip(raw) == expected


@pytest.mark.parametrize(
    "peer,chain,expected",
    [
        ("192.0.2.5", "1.2.3.4", "192.0.2.5"),
        ("10.0.0.1", "192.0.2.5", "192.0.2.5"),
        ("10.0.0.1", "1.2.3.4, 192.0.2.5, 10.0.0.2", "192.0.2.5"),
        ("10.0.0.1", "bad, 10.0.0.2", "10.0.0.1"),
        ("10.0.0.1", "", "10.0.0.1"),
        ("10.0.0.1", "x" * 2049, "10.0.0.1"),
        ("::ffff:10.0.0.1", "192.0.2.5", "192.0.2.5"),
        ("not-an-ip", "192.0.2.5", None),
    ],
)
def test_forwarded_ip_stops_at_first_untrusted_hop(monkeypatch, peer, chain, expected):
    monkeypatch.setattr(settings, "security_trusted_proxy_cidrs", "10.0.0.0/8")
    assert request_ip(request({"x-forwarded-for": chain}, peer)) == expected


def test_rate_limit_window_and_key_isolation():
    r = fakeredis.FakeRedis()
    assert fixed_window_allow(r, "a", 2, 60) == (True, 0)
    assert 0 < r.ttl("a") <= 60
    assert fixed_window_allow(r, "a", 2, 60) == (True, 0)
    allowed, retry = fixed_window_allow(r, "a", 2, 60)
    assert not allowed and 0 < retry <= 60
    assert fixed_window_allow(r, "b", 2, 60) == (True, 0)
    r.delete("a")
    assert fixed_window_allow(r, "a", 2, 60) == (True, 0)


@pytest.mark.parametrize("closed,expected", [(False, (True, 0)), (True, (False, 60))])
def test_rate_limit_outage_policy(closed, expected):
    r = Mock()
    r.pipeline.side_effect = ConnectionError("offline")
    assert fixed_window_allow(r, "a", 2, 60, fail_closed=closed) == expected


@pytest.mark.parametrize("limit,window", [(0, 60), (-1, 60), (10, 0), (10, -1)])
def test_disabled_limiter_does_not_access_redis(limit, window):
    r = Mock()
    assert fixed_window_allow(r, "a", limit, window) == (True, 0)
    r.pipeline.assert_not_called()


def test_limiter_repairs_missing_expiry():
    r = fakeredis.FakeRedis()
    r.set("a", 1)
    assert fixed_window_allow(r, "a", 2, 60) == (True, 0)
    assert 0 < r.ttl("a") <= 60


def test_session_roundtrip_refresh_update_delete_and_isolation():
    r = fakeredis.FakeRedis()
    sid = sessions.create_session(
        r,
        user_id="one",
        ttl_seconds=120,
        mfa_version=4,
        mfa_verified=True,
        permission_codes=["events.read", "events.read"],
        effective_role="reader",
        authz_version=2,
    )
    other = sessions.create_session(r, user_id="two", ttl_seconds=120)
    assert sid != other
    original = sessions.get_session(r, sid, refresh_ttl_seconds=300)
    assert original["user_id"] == "one" and original["mfa_verified"] is True
    assert original["permission_codes"] == ["events.read"]
    assert r.ttl(sessions.session_key(sid)) > 120
    sessions.update_session_authz(
        r,
        sid,
        effective_role="admin",
        permission_codes=["b", "a", "a"],
        authz_version=3,
        ttl_seconds=120,
    )
    updated = sessions.get_session(r, sid)
    assert updated["permission_codes"] == ["a", "b"] and updated["authz_version"] == 3
    assert (
        updated["mfa_version"] == 4 and updated["created_at"] == original["created_at"]
    )
    sessions.delete_session(r, sid)
    assert sessions.get_session(r, sid) is None
    assert sessions.get_session(r, other)["user_id"] == "two"


@pytest.mark.parametrize("raw", ["not json", "null", "[]", '"text"', "42"])
def test_malformed_session_cannot_be_used_or_resurrected(raw):
    r = fakeredis.FakeRedis()
    r.set(sessions.session_key("bad"), raw)
    assert sessions.get_session(r, "bad") is None
    sessions.update_session_authz(
        r,
        "bad",
        effective_role="admin",
        permission_codes=[],
        authz_version=1,
        ttl_seconds=60,
    )
    assert r.get(sessions.session_key("bad")).decode() == raw


def test_authz_versions_deduplicate_users_and_fail_to_authoritative_lookup():
    r = fakeredis.FakeRedis()
    assert sessions.get_authz_version(r, "one") == 0
    sessions.bump_authz_versions(r, ["one", "one", "two", None])
    assert sessions.get_authz_version(r, "one") == 1
    assert sessions.get_authz_version(r, "two") == 1
    r.set(sessions.authz_version_key("one"), "corrupt")
    assert sessions.get_authz_version(r, "one") is None
    failing = Mock()
    failing.get.side_effect = ConnectionError("offline")
    assert sessions.get_authz_version(failing, "one") is None
