"""Post-authentication destinations and server redirects stay on the Keen origin."""

from __future__ import annotations

from urllib.parse import quote, urlsplit

import pytest
from keen_e2e.client import retry_after

HOSTILE_NEXT = [
    "https://evil.example/",
    "//evil.example/",
    "/\\evil.example/",
    "/\t/evil.example/",
    "\\\\evil.example",
    "javascript:alert(1)",
    "/%2F%2Fevil.example",
    "/login.html?next=https://evil.example",
    "https:evil.example",
]


@pytest.mark.parametrize("value", HOSTILE_NEXT)
def test_login_next_parameter_stays_on_origin(page_as, accounts, target, value):
    user = accounts("plain")
    page = page_as()
    for _ in range(12):
        page.goto(target.origin + "/login.html?next=" + quote(value, safe=""))
        page.fill("input[name=username], #username", user.username)
        page.fill("input[type=password]", user.password)
        with page.expect_response(
            lambda r: r.url.endswith("/api/v1/auth/login")
        ) as sign_in:
            page.keyboard.press("Enter")
        if sign_in.value.status != 429:
            break
        # Wait out the sign-in rate limit rather than report it as a redirect failure.
        page.wait_for_timeout(retry_after(sign_in.value) * 1000 + 1000)
    assert sign_in.value.status == 200, sign_in.value.status
    page.wait_for_load_state("networkidle", timeout=30000)
    page.wait_for_timeout(500)
    assert urlsplit(page.url).hostname == target.host, (value, page.url)


@pytest.mark.parametrize("value", HOSTILE_NEXT[:4])
@pytest.mark.parametrize(
    "start", ["/api/v1/auth/sso/oidc/start", "/api/v1/auth/oidc/start"]
)
def test_sso_start_never_redirects_back_off_origin(anonymous, start, value):
    response = anonymous.get(start + "?next=" + quote(value, safe=""))
    if response.status_code == 404:
        pytest.skip("OIDC not configured")
    location = response.headers.get("location", "")
    # The provider is off-origin by design; the requested destination must not
    # be passed through to it unvalidated.
    assert "evil.example" not in location, location


def test_mfa_page_next_parameter_is_validated(page_as, target):
    page = page_as()
    page.goto(
        target.origin + "/mfa.html?next=" + quote("https://evil.example/", safe="")
    )
    page.wait_for_load_state("networkidle", timeout=30000)
    assert urlsplit(page.url).hostname == target.host


def test_api_redirects_are_relative_or_same_origin(admin, target):
    for path in ("/api/v1/auth/logout", "/api/v1/auth/oidc/logout"):
        response = admin.get(path)
        location = response.headers.get("location")
        if location:
            parsed = urlsplit(location)
            assert not parsed.netloc or parsed.hostname == target.host, (path, location)
