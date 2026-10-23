"""Fixtures for the Keen security end-to-end suite.

Configuration comes from environment variables so credentials never appear in
process listings. See README.md before running against any installation.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import ssl
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from keen_e2e.client import KeenError, Session, Target  # noqa: E402
from keen_e2e.payloads import Payloads  # noqa: E402

# Permission sets for the synthetic accounts the suite creates.
ROLES = {
    "plain": [],
    "events_reader": ["events.read"],
    "questioner": ["events.read", "question.create"],
    "risk_reader": ["risk.read"],
    "risk_manager": ["risk.read", "risk.manage"],
    "isms_reader": ["isms.read"],
    "isms_manager": ["isms.read", "isms.manage"],
    "audit_reader": ["audits.read"],
    "audit_manager": ["audits.manage"],
    "pestle_reader": ["pestle.read"],
    "parties_reader": ["interested_parties.read"],
    "audit_trail_reader": ["audittrail.read"],
}
# Accounts used by individual checks rather than the authorisation matrix.
EXTRA_ROLES = {"mfa_user": []}


def _env(name, default=None):
    value = os.environ.get(name, default)
    return value.strip() if isinstance(value, str) else value


def pytest_report_header(config):
    url = _env("KEEN_E2E_BASE_URL") or "(unset)"
    writes = "enabled" if _env("KEEN_E2E_ALLOW_WRITES") == "1" else "disabled"
    return [f"Keen target: {url}; synthetic writes: {writes}"]


@pytest.fixture(scope="session")
def run_id():
    return "e2e" + secrets.token_hex(4)


@pytest.fixture(scope="session")
def target():
    url = _env("KEEN_E2E_BASE_URL")
    if not url:
        pytest.exit(
            "Set KEEN_E2E_BASE_URL to the origin of a Keen installation you control", 2
        )
    ca_file = _env("KEEN_E2E_CA_FILE")
    verify = ssl.create_default_context(cafile=ca_file) if ca_file else True
    t = Target(
        url.rstrip("/"),
        verify=verify,
        request_delay=float(_env("KEEN_E2E_REQUEST_DELAY", "0.05")),
    )
    # Safety interlock: the operator repeats the hostname they are authorised to test.
    if _env("KEEN_E2E_CONFIRM_HOST") != t.host:
        pytest.exit(
            "Set KEEN_E2E_CONFIRM_HOST to the target hostname to confirm you control this installation",
            2,
        )
    return t


@pytest.fixture(scope="session")
def payloads(run_id):
    return Payloads(run_id)


@pytest.fixture(scope="session")
def writes_allowed():
    return _env("KEEN_E2E_ALLOW_WRITES") == "1"


@pytest.fixture
def needs_writes(writes_allowed):
    if not writes_allowed:
        pytest.skip(
            "Synthetic writes disabled; set KEEN_E2E_ALLOW_WRITES=1 on a disposable installation"
        )


@pytest.fixture(scope="session")
def anonymous(target):
    session = Session(target, "anonymous")
    yield session
    session.close()


@pytest.fixture(scope="session")
def admin(target):
    username = _env("KEEN_E2E_ADMIN_USERNAME")
    password = os.environ.get("KEEN_E2E_ADMIN_PASSWORD")
    if not username or not password:
        pytest.skip(
            "Set KEEN_E2E_ADMIN_USERNAME and KEEN_E2E_ADMIN_PASSWORD (environment only)"
        )
    session = Session(target, "admin")
    session.login(username, password, _env("KEEN_E2E_ADMIN_TOTP_SECRET"))
    me = session.ok(session.get("/api/v1/me"), 200).json()
    if not me.get("is_admin"):
        pytest.exit("KEEN_E2E_ADMIN_USERNAME must be an effective administrator", 2)
    yield session
    session.close()


class Cleanup:
    """Records synthetic objects and removes them, newest first, at the end."""

    def __init__(self):
        self.items = []

    def add(self, path):
        self.items.append(path)

    def run(self, admin):
        failures = []
        for path in reversed(self.items):
            try:
                response = admin.delete(path)
                if response.status_code not in (200, 204, 404):
                    failures.append((path, response.status_code))
            except Exception as exc:
                failures.append((path, type(exc).__name__))
        return failures


@pytest.fixture(scope="session")
def cleanup(admin):
    registry = Cleanup()
    yield registry
    failures = registry.run(admin)
    if failures:
        print("\nKeen e2e cleanup could not remove:", failures)


@pytest.fixture(scope="session")
def accounts(request, target, run_id, writes_allowed, cleanup):
    """Synthetic users per ROLES, created through the admin API and signed in."""
    if not writes_allowed:
        pytest.skip("Role accounts need KEEN_E2E_ALLOW_WRITES=1")
    admin = request.getfixturevalue("admin")
    password = "e2e-" + secrets.token_urlsafe(18)
    sessions = {}

    def get(role):
        if role in sessions:
            return sessions[role]
        username = f"{run_id}-{role}".replace("_", "-")
        created = admin.ok(
            admin.post(
                "/api/v1/users",
                json={"username": username, "password": password, "role": "normal"},
            ),
            200,
        ).json()
        cleanup.add(f"/api/v1/users/{created['id']}")
        admin.ok(
            admin.put(
                f"/api/v1/users/{created['id']}/access",
                json={"explicit_permission_codes": {**ROLES, **EXTRA_ROLES}[role]},
            ),
            200,
        )
        session = Session(target, role)
        session.login(username, password)
        session.user_id = created["id"]
        session.password = password
        sessions[role] = session
        return session

    yield get
    for session in sessions.values():
        session.close()


@pytest.fixture(scope="session")
def evidence_writes(writes_allowed):
    return writes_allowed and _env("KEEN_E2E_ALLOW_EVIDENCE_WRITES") == "1"


@pytest.fixture(scope="session")
def seed(request, writes_allowed, evidence_writes, payloads, cleanup):
    if not writes_allowed:
        pytest.skip("Seed records need KEEN_E2E_ALLOW_WRITES=1")
    from keen_e2e import seed as seeding

    return seeding.build(
        request.getfixturevalue("admin"),
        payloads,
        cleanup,
        evidence_writes=evidence_writes,
    )


@pytest.fixture(scope="session")
def openapi(admin):
    schema = admin.ok(admin.get("/api/openapi.json"), 200).json()
    operations = []
    for path, item in schema.get("paths", {}).items():
        for method, op in item.items():
            if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                operations.append((method.upper(), path, op))
    return operations


def _spki_pin(cert_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    cert = x509.load_pem_x509_certificate(Path(cert_path).read_bytes())
    der = cert.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return base64.b64encode(hashlib.sha256(der).digest()).decode()


@pytest.fixture(scope="session")
def browser(target):
    from playwright.sync_api import sync_playwright

    # The suite talks only to the target; keep the browser's own services quiet.
    args = [
        "--disable-background-networking",
        "--disable-features=AutofillServerCommunication",
    ]
    pin_cert = _env("KEEN_E2E_BROWSER_TRUST_CERT")
    if pin_cert:
        # Accept only this certificate's key for a lab installation; other TLS
        # errors still fail. Prefer a publicly trusted certificate where possible.
        args.append("--ignore-certificate-errors-spki-list=" + _spki_pin(pin_cert))
    with sync_playwright() as p:
        b = p.chromium.launch(
            executable_path=_env("KEEN_E2E_BROWSER_EXECUTABLE") or None, args=args
        )
        yield b
        b.close()


@pytest.fixture
def page_as(browser, target):
    """A fresh browser context that carries an API session's cookies."""
    contexts = []

    def make(session=None, *, javascript=True):
        context = browser.new_context(java_script_enabled=javascript)
        contexts.append(context)
        if session is not None:
            cookies = []
            for cookie in session.http.cookies.jar:
                cookies.append(
                    {
                        "name": cookie.name,
                        "value": cookie.value,
                        # A URL makes host-only cookies, which __Host- names require.
                        "url": target.origin + "/",
                        "secure": target.origin.startswith("https"),
                        "httpOnly": cookie.name == session.session_cookie,
                        "sameSite": "Lax",
                    }
                )
            context.add_cookies(cookies)
        page = context.new_page()
        page.keen_errors = []
        page.on("pageerror", lambda exc: page.keen_errors.append(str(exc)[:200]))
        return page

    yield make
    for context in contexts:
        context.close()


__all__ = ["KeenError", "ROLES"]
