"""Races against single-use and counted state through the real database.

Each check sends identical requests at the same moment over separate
connections. Row locks must let exactly one (or the configured number) succeed.
SQLite-backed test runs cannot establish this; a PostgreSQL deployment can.
"""

from __future__ import annotations

import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pyotp
import pytest
from keen_e2e import agent
from keen_e2e.client import Session

RACERS = 8


def _race(calls):
    """Run callables together, released by one barrier; returns their results."""
    barrier = threading.Barrier(len(calls))

    def run(call):
        barrier.wait(timeout=30)
        return call()

    with ThreadPoolExecutor(len(calls)) as pool:
        return list(pool.map(run, calls))


def _clone(session, label):
    """Another connection carrying the same cookies, like a second browser tab."""
    other = Session(session.target, label)
    for cookie in session.http.cookies.jar:
        other.http.cookies.set(
            cookie.name, cookie.value, domain=cookie.domain, path=cookie.path
        )
    other.session_cookie, other.csrf_cookie = (
        session.session_cookie,
        session.csrf_cookie,
    )
    return other


def _next_step(after):
    """Wait for a TOTP step later than ``after`` so the code has not been used."""
    while int(time.time()) // 30 <= after:
        time.sleep(1)
    return int(time.time()) // 30


@pytest.fixture
def mfa_account(accounts, admin, target):
    """A synthetic account with an authenticator enrolled through the API."""
    session = accounts("mfa_user")
    start = session.post(
        "/api/v1/auth/mfa/manage/start", json={"password": session.password}
    )
    session.ok(start, 200)
    secret = session.ok(session.post("/api/v1/auth/mfa/totp/start"), 200).json()[
        "secret"
    ]
    step = int(time.time()) // 30
    confirm = session.post(
        "/api/v1/auth/mfa/totp/confirm", json={"code": pyotp.TOTP(secret).at(step * 30)}
    )
    session.ok(confirm, 200)
    session.post("/api/v1/auth/mfa/finish")
    return session, secret, step


def _password_stage(target, username, password):
    session = Session(target, "mfa-race")
    response = session.ok(session.password_login(username, password), 200)
    assert response.json().get("mfa_required"), (
        "account did not require a second factor"
    )
    return session


def test_totp_code_and_mfa_finish_are_single_use(mfa_account, target):
    owner, secret, used_step = mfa_account
    pending = _password_stage(target, owner.username, owner.password)
    step = _next_step(used_step)
    code = pyotp.TOTP(secret).at(step * 30)
    tabs = [_clone(pending, f"tab{i}") for i in range(RACERS)]
    try:
        verified = _race(
            [
                lambda tab=tab: (
                    tab.post(
                        "/api/v1/auth/mfa/verify-code",
                        json={"code": code, "kind": "totp"},
                        unsafe=False,
                    ).status_code
                )
                for tab in tabs
            ]
        )
        assert verified.count(200) == 1, verified
        finished = _race(
            [
                lambda tab=tab: (
                    tab.post("/api/v1/auth/mfa/finish", unsafe=False).status_code
                )
                for tab in tabs
            ]
        )
        assert finished.count(200) == 1, finished
    finally:
        for tab in tabs:
            tab.close()
        pending.close()

    # The same code cannot start a second sign-in either.
    again = _password_stage(target, owner.username, owner.password)
    try:
        reuse = again.post(
            "/api/v1/auth/mfa/verify-code",
            json={"code": code, "kind": "totp"},
            unsafe=False,
        )
        assert reuse.status_code == 400, reuse.status_code
    finally:
        again.close()


def test_enrollment_limit_holds_under_concurrency(admin, needs_writes, run_id, target):
    created = admin.post(
        "/api/v1/admin/agent-enrollment-profiles",
        json={"name": f"{run_id}-race", "max_enrollments": 1, "token_days": 1},
    )
    if created.status_code in (403, 404, 409):
        pytest.skip("Agent enrollment is unavailable on this installation")
    admin.ok(created, 201)
    body = created.json()
    ident, key = body["profile"]["id"], body["bootstrap_key"]
    try:
        clients = [
            httpx.Client(base_url=target.origin, verify=target.verify, timeout=30)
            for _ in range(RACERS)
        ]
        try:
            # Keen accepts no plaintext enrolment, as keen-agent 0.2 sent it.
            plain = clients[0].post(
                "/api/v1/agents/enroll",
                json={"name": f"{run_id}-plain", "nonce": secrets.token_urlsafe(32)},
                headers={"Authorization": "Bearer " + key},
            )
            assert plain.status_code == 415, plain.status_code
            statement = agent.server_key(clients[0], key)
            results = _race(
                [
                    lambda client=client, i=i: agent.enroll(
                        client,
                        statement,
                        key,
                        name=f"{run_id}-agent-{i}",
                        nonce=secrets.token_urlsafe(32),
                    )
                    for i, client in enumerate(clients)
                ]
            )
        finally:
            for client in clients:
                client.close()
        assert results.count(201) == 1, results
        assert all(status in (201, 403, 429) for status in results), results
    finally:
        # Revoking the profile disables any agent it enrolled.
        admin.post(f"/api/v1/admin/agent-enrollment-profiles/{ident}/revoke")


def test_concurrent_sign_ins_receive_distinct_sessions(accounts, target):
    user = accounts("plain")
    sessions = [Session(target, f"parallel{i}") for i in range(RACERS)]
    try:
        _race([lambda s=s: s.login(user.username, user.password) for s in sessions])
        ids = {s.http.cookies.get(s.session_cookie) for s in sessions}
        assert len(ids) == RACERS
        # Signing one out leaves the others untouched, and revokes only itself.
        sessions[0].ok(sessions[0].logout(), 200, 204)
        assert sessions[0].get("/api/v1/me").status_code == 401
        assert all(s.get("/api/v1/me").status_code == 200 for s in sessions[1:])
    finally:
        for s in sessions:
            s.close()
