"""Unauthenticated surface: every API route, every page and the response policy."""

from __future__ import annotations

import re

import pytest
from keen_e2e.inventory import (
    ADMIN_PAGES,
    PUBLIC_API,
    PUBLIC_PAGES,
    concrete,
    html_pages,
    is_public,
)


def test_every_non_public_api_operation_requires_a_session(anonymous, openapi):
    findings = []
    for method, path, _ in openapi:
        if is_public(method, path):
            continue
        response = anonymous.request(method, "/api" + concrete(path), unsafe=False)
        if response.status_code != 401:
            findings.append((method, path, response.status_code))
    assert not findings, f"Reachable without a session: {findings}"


def test_reviewed_public_routes_do_not_fail(anonymous):
    """The reviewed public routes answer anonymous probes without server errors."""
    unexpected = []
    for method, paths in PUBLIC_API.items():
        for path in paths:
            response = anonymous.request(
                method, "/api" + concrete(path, "keen-e2e"), unsafe=False
            )
            if response.status_code >= 500:
                unexpected.append((method, path, response.status_code))
    assert not unexpected, f"Public routes failing with server errors: {unexpected}"


@pytest.mark.parametrize(
    "path",
    [
        "/api/openapi.json",
        "/api/v1/openapi.json",
        "/api/docs",
        "/api/redoc",
        "/api/v1/docs",
    ],
)
def test_api_documentation_requires_a_session(anonymous, path):
    response = anonymous.get(path)
    assert response.status_code == 401, (path, response.status_code)


def test_pages_require_sign_in(anonymous):
    leaks = []
    for page in html_pages():
        response = anonymous.get("/" + page)
        if page in PUBLIC_PAGES:
            if response.status_code != 200:
                leaks.append((page, "public page unavailable", response.status_code))
            continue
        # nginx answers 403 with the login page for unauthenticated page requests.
        if response.status_code == 200 or "Login" not in response.text[:4000]:
            leaks.append((page, response.status_code))
    assert not leaks, f"Pages served without sign-in: {leaks}"


def test_admin_pages_hidden_from_ordinary_users(accounts):
    session = accounts("plain")
    shown = []
    for page in sorted(ADMIN_PAGES | {"pages/admin.js", "pages/agents.js"}):
        status = session.get("/" + page).status_code
        if status == 200:
            shown.append((page, status))
    assert not shown, f"Admin UI served to an ordinary user: {shown}"


REQUIRED_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": None,
    "referrer-policy": None,
    "content-security-policy": None,
}


@pytest.mark.parametrize(
    "path",
    ["/login.html", "/api/v1/me", "/api/health", "/pages/login.js", "/styles.css"],
)
def test_response_policy_headers(anonymous, target, path):
    response = anonymous.get(path)
    missing = []
    for name, value in REQUIRED_HEADERS.items():
        got = response.headers.get(name)
        if not got or (value and got.lower() != value):
            missing.append(name)
    if target.origin.startswith("https") and not response.headers.get(
        "strict-transport-security"
    ):
        missing.append("strict-transport-security")
    assert not missing, (path, missing)
    csp = response.headers.get("content-security-policy", "")
    for directive in (
        "script-src 'self'",
        "object-src 'none'",
        "frame-ancestors",
        "base-uri",
    ):
        assert directive in csp, (path, directive)
    assert "unsafe-eval" not in csp and "script-src 'self' 'unsafe-inline'" not in csp


def test_api_responses_are_not_cached(anonymous):
    for path in ("/api/v1/me", "/api/health", "/api/v1/auth/methods"):
        assert "no-store" in anonymous.get(path).headers.get("cache-control", ""), path


@pytest.mark.parametrize(
    "variant",
    [
        "/api/v1/admin/groups/",
        "/api//v1/admin/groups",
        "/api/v1/ADMIN/groups",
        "/api/v1/admin%2Fgroups",
        "/api/v1/admin/groups%2F..%2F..%2Fme",
        "/api/v1/admin/groups;x",
        "/api/v1/admin/groups/..;/groups",
        "/api/v1/./admin/groups",
        "/api/v1/users/../admin/groups",
    ],
)
def test_path_variants_do_not_reach_admin_data(accounts, admin, variant):
    """Encoded, doubled and dotted paths never reach the administrator listing.

    nginx normalises dot segments before proxying, so some variants resolve to a
    different ordinary route; those must still return no administrator data.
    """
    groups = admin.ok(admin.get("/api/v1/admin/groups"), 200).text
    group_ids = set(re.findall(r'"id"\s*:\s*"([^"]+)"', groups))
    session = accounts("plain")
    response = session.get(variant)
    if response.status_code in (307, 308):
        response = session.get(response.headers["location"])
    assert response.status_code < 500, (variant, response.status_code)
    if response.status_code == 200:
        leaked = [ident for ident in group_ids if ident in response.text]
        assert not leaked, (variant, "administrator group data returned")


@pytest.mark.parametrize("method", ["TRACE", "TRACK", "CONNECT", "PROPFIND"])
def test_unusual_methods_are_refused(anonymous, method):
    response = anonymous.request(method, "/api/v1/me", unsafe=False)
    assert response.status_code in (400, 401, 403, 404, 405, 501), (
        method,
        response.status_code,
    )


def test_oversized_bodies_rejected_before_authentication(anonymous):
    big = b"x" * (41 * 1024 * 1024)
    response = anonymous.post(
        "/api/v1/events",
        content=big,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code in (401, 413), response.status_code


def test_server_identity_is_not_advertised(anonymous):
    response = anonymous.get("/login.html")
    server = response.headers.get("server", "")
    assert not any(ch.isdigit() for ch in server), server
    assert "x-powered-by" not in response.headers


def test_login_brand_image_is_public_png_without_redirect(anonymous):
    response = anonymous.get("/keen-mitigator-64.png")
    assert response.status_code == 200
    assert response.headers.get("content-type", "").split(";")[0] == "image/png"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert not response.history
    assert "content-security-policy" in response.headers
