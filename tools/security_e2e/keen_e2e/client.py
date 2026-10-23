"""HTTP access to a Keen installation through its public origin (nginx + API).

Credentials stay in memory. Reports and assertion messages carry status codes,
paths and synthetic identifiers only, never cookies, tokens or response bodies.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

import httpx
import pyotp


class KeenError(AssertionError):
    pass


def _detail(response):
    """A short validation message from a Keen error body, without echoing payloads."""
    try:
        detail = response.json().get("detail")
    except Exception:
        return ""
    if isinstance(detail, str):
        return f": {detail[:160]}"
    if isinstance(detail, list):
        fields = [
            ".".join(str(x) for x in d.get("loc", [])) + " " + str(d.get("type", ""))
            for d in detail[:5]
            if isinstance(d, dict)
        ]
        return ": " + "; ".join(fields)
    return ""


def retry_after(response, default=30):
    try:
        # httpx headers ignore case; Playwright lower-cases header names.
        return max(1, min(int(response.headers.get("retry-after") or default), 330))
    except ValueError:
        return default


@dataclass
class Target:
    base_url: str
    verify: object = True
    request_delay: float = 0.05
    host: str = field(init=False)
    origin: str = field(init=False)

    def __post_init__(self):
        parts = urlsplit(self.base_url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("KEEN_E2E_BASE_URL must be an http(s) origin")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("KEEN_E2E_BASE_URL must not contain credentials or query")
        self.host = parts.hostname
        default = 443 if parts.scheme == "https" else 80
        port = f":{parts.port}" if parts.port and parts.port != default else ""
        self.origin = f"{parts.scheme}://{parts.hostname}{port}"


class Session:
    """One browser-like identity: its own cookie jar and CSRF token."""

    def __init__(self, target: Target, label: str):
        self.target = target
        self.label = label
        self.http = httpx.Client(
            base_url=target.origin,
            verify=target.verify,
            follow_redirects=False,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"User-Agent": "keen-security-e2e"},
        )
        self.session_cookie = None
        self.csrf_cookie = None
        self.username = None

    def close(self):
        self.http.close()

    # -- requests -------------------------------------------------------------

    def request(self, method, path, *, unsafe=None, origin=True, **kwargs):
        """Send a request; unsafe methods carry the CSRF token and Origin."""
        headers = dict(kwargs.pop("headers", {}) or {})
        unsafe = (
            method.upper() not in {"GET", "HEAD", "OPTIONS"}
            if unsafe is None
            else unsafe
        )
        if origin and "Origin" not in headers:
            headers["Origin"] = self.target.origin
        if unsafe and self.csrf_cookie and "X-CSRF-Token" not in headers:
            token = self.http.cookies.get(self.csrf_cookie)
            if token:
                headers["X-CSRF-Token"] = token
        time.sleep(self.target.request_delay)
        return self.http.request(method, path, headers=headers, **kwargs)

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, **kw):
        return self.request("POST", path, **kw)

    def put(self, path, **kw):
        return self.request("PUT", path, **kw)

    def patch(self, path, **kw):
        return self.request("PATCH", path, **kw)

    def delete(self, path, **kw):
        return self.request("DELETE", path, **kw)

    def ok(self, response, *expected):
        expected = expected or (200, 201, 204)
        if response.status_code not in expected:
            raise KeenError(
                f"{self.label}: {response.request.method} {urlsplit(str(response.request.url)).path}"
                f" returned {response.status_code}, expected {expected}{_detail(response)}"
            )
        return response

    # -- authentication -------------------------------------------------------

    def _record_cookie_names(self, response):
        for raw in response.headers.get_list("set-cookie"):
            cookie = SimpleCookie()
            try:
                cookie.load(raw)
            except Exception:
                continue
            for name, morsel in cookie.items():
                lowered = raw.lower()
                if name == "keen_mfa_pending" or "max-age=0" in lowered:
                    continue
                if morsel["httponly"] or "httponly" in lowered:
                    self.session_cookie = name
                else:
                    self.csrf_cookie = name

    def password_login(self, username, password, *, patience=360):
        """Submit the password form, waiting out Keen's sign-in rate limit."""
        deadline = time.monotonic() + patience
        while True:
            response = self.request(
                "POST",
                "/api/v1/auth/login",
                json={"username": username, "password": password},
                unsafe=False,
            )
            if response.status_code != 429 or time.monotonic() >= deadline:
                return response
            wait = retry_after(response)
            time.sleep(min(wait, max(1, deadline - time.monotonic())) + 1)

    def login(self, username, password, totp_secret=None):
        response = self.ok(self.password_login(username, password), 200)
        body = response.json()
        if body.get("mfa_required"):
            if not totp_secret:
                raise KeenError(
                    f"{self.label}: account requires MFA; supply its TOTP secret"
                )
            state = self.ok(
                self.request("POST", "/api/v1/auth/mfa/state", unsafe=False), 200
            )
            if state.json().get("stage") != "verify":
                raise KeenError(f"{self.label}: unexpected MFA stage")
            code = pyotp.TOTP(totp_secret).now()
            self.ok(
                self.request(
                    "POST",
                    "/api/v1/auth/mfa/verify-code",
                    json={"code": code, "kind": "totp"},
                    unsafe=False,
                ),
                200,
            )
            response = self.ok(
                self.request("POST", "/api/v1/auth/mfa/finish", unsafe=False), 200
            )
        self._record_cookie_names(response)
        if not self.session_cookie:
            raise KeenError(f"{self.label}: sign-in did not set a session cookie")
        self.username = username
        return response

    def logout(self):
        return self.request("POST", "/api/v1/auth/logout")
