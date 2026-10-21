"""Security boundary regressions using disposable databases and synthetic identities."""

import asyncio
import uuid as uuid_module
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import app.services.sso_emails as sso_emails_operations
import pytest
from app.api.routes import questions, sso_emails
from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.db.models import (
    Event,
    EventQuestionPost,
    EventQuestionThread,
    SsoEmailChallenge,
    User,
    UserIdentity,
    UserSsoEmail,
)
from app.security import oidc
from app.security.client_ip import request_ip
from app.services.errors import DomainError
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.requests import Request
from starlette.responses import Response

from tests.db_helpers import create_sqlite_schema


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    create_sqlite_schema(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def user(db):
    u = User(
        username="alice",
        email="alice@company.example",
        password_hash="synthetic",
        role="normal",
        is_active=True,
    )
    db.add(u)
    db.commit()
    return u


def req(user=None, cookie="", query="", peer="10.0.0.2", xff=""):
    headers = []
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    if xff:
        headers.append((b"x-forwarded-for", xff.encode()))
    r = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("keen.example", 443),
            "path": "/v1/auth/sso/google/callback",
            "query_string": query.encode(),
            "headers": headers,
            "client": (peer, 1234),
        }
    )
    r.state.user = user
    return r


def link(db, email, verified=True, provider=None, subject="external"):
    return oidc._get_or_create_user_for_identity(
        db,
        provider=provider or oidc._google_provider(),
        subject=subject,
        claims={
            "email": email,
            "email_verified": verified,
            "preferred_username": "alice",
        },
    )


@pytest.mark.parametrize("email", ["alice@attacker.example", "alice@company.example"])
def test_no_username_or_unverified_local_address(db, user, email):
    user.role = "admin"
    user.mfa_enabled = True
    db.commit()
    with pytest.raises((HTTPException, DomainError)):
        link(db, email)
    assert db.query(UserIdentity).count() == 0


@pytest.mark.parametrize("verified", [False, None, "true", 1])
def test_provider_verification_required(db, user, verified):
    db.add(UserSsoEmail(email=user.email, user_id=user.id))
    db.commit()
    with pytest.raises((HTTPException, DomainError)):
        link(db, user.email, verified)


def test_verified_alias_existing_identity_and_configuration(db, user):
    db.add(UserSsoEmail(email="other@example.org", user_id=user.id))
    db.commit()
    for p in [
        replace(oidc._google_provider(), auto_link_existing=False),
        replace(oidc._google_provider(), allowed_email_domains="company.example"),
    ]:
        with pytest.raises((HTTPException, DomainError)):
            link(db, "other@example.org", provider=p)
    assert link(db, "Other@Example.org").id == user.id
    db.query(UserSsoEmail).delete()
    db.commit()
    assert (
        link(
            db,
            "changed@example.org",
            False,
            provider=replace(oidc._google_provider(), auto_link_existing=False),
        ).id
        == user.id
    )


@pytest.mark.parametrize(
    "provider,secret",
    [("google", ""), ("google", "wrong"), ("github", "browser-secret")],
)
def test_other_browser_or_provider_rejected_before_exchange(
    db, user, monkeypatch, provider, secret
):
    monkeypatch.setattr(
        oidc, "_require_provider_config", lambda _: oidc._google_provider()
    )
    state = oidc._browser_state(provider, "browser-secret")
    oidc.create_login_state(db, next_url="/", state=state)
    exchange = AsyncMock()
    monkeypatch.setattr(oidc, "_handle_oidc_callback", exchange)
    with pytest.raises((HTTPException, DomainError)):
        asyncio.run(
            oidc.handle_callback(
                req(
                    cookie=f"{oidc.sso_binding_cookie()}={secret}",
                    query=f"state={state}",
                ),
                db,
                "google",
            )
        )
    exchange.assert_not_called()
    assert oidc.pop_login_state(db, state=state) is not None


def test_correct_browser_single_use_and_expiry(db, user, monkeypatch):
    monkeypatch.setattr(
        oidc, "_require_provider_config", lambda _: oidc._google_provider()
    )
    state = oidc._browser_state("google", "secret")
    oidc.create_login_state(db, next_url="/", state=state)

    async def exchange(request, db, provider, row):
        assert row.nonce and row.code_verifier
        return user, "/", None

    monkeypatch.setattr(oidc, "_handle_oidc_callback", exchange)
    r = req(cookie=f"{oidc.sso_binding_cookie()}=secret", query=f"state={state}")
    assert asyncio.run(oidc.handle_callback(r, db, "google"))[0].id == user.id
    with pytest.raises((HTTPException, DomainError)):
        asyncio.run(oidc.handle_callback(r, db, "google"))
    row = oidc.create_login_state(db, next_url="/")
    state = row.state
    row.expires_at = utc_now_naive() - timedelta(seconds=1)
    db.commit()
    assert oidc.pop_login_state(db, state=state) is None


def test_cookie_flags(monkeypatch):
    monkeypatch.setattr(settings, "cookie_secure", True)
    r = req()
    r.state.sso_browser_secret = "secret"
    header = oidc.set_sso_binding(Response(), r).headers["set-cookie"]
    assert (
        all(
            x in header
            for x in ["__Host-keen_sso=", "HttpOnly", "Secure", "SameSite=lax"]
        )
        and "Domain=" not in header
    )


def test_event_questions_and_owner_listing(db, user):
    ev = Event(
        timestamp=utc_now_naive(), source="loki", summary="private", external_id="1"
    )
    db.add(ev)
    db.flush()
    thread = EventQuestionThread(event_id=ev.id, created_by_user_id=user.id)
    db.add(thread)
    db.flush()
    db.add(
        EventQuestionPost(thread_id=thread.id, author_user_id=user.id, body="secret")
    )
    db.commit()
    with pytest.raises((HTTPException, DomainError)) as e:
        questions.list_event_questions(str(ev.id), req(user), db)
    assert e.value.status_code == 403
    assert "private" not in str(questions.my_questions_list(req(user), db))
    admin = User(
        username="administrator",
        password_hash="synthetic",
        role="admin",
        is_active=True,
    )
    db.add(admin)
    db.commit()
    user = admin
    assert "secret" in str(questions.list_event_questions(str(ev.id), req(user), db))


@pytest.mark.parametrize(
    "peer,xff,expected",
    [
        ("198.51.100.5", "1.2.3.4", "198.51.100.5"),
        ("10.0.0.2", "1.2.3.4, 198.51.100.5", "198.51.100.5"),
        ("10.0.0.2", "198.51.100.5, 10.0.0.3", "198.51.100.5"),
        ("10.0.0.2", "garbage", "10.0.0.2"),
        ("10.0.0.2", "", "10.0.0.2"),
    ],
)
def test_shared_proxy_interpretation(monkeypatch, peer, xff, expected):
    monkeypatch.setattr(settings, "security_trusted_proxy_cidrs", "10.0.0.0/24")
    from app.api.context import change_context
    from app.main import _client_ip
    from app.security.rate_limit import client_ip

    for fn in [
        request_ip,
        _client_ip,
        client_ip,
    ]:
        assert fn(req(peer=peer, xff=xff)) == expected
    assert change_context(req(peer=peer, xff=xff)).client_ip == expected


@pytest.fixture
def mail(monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "https://keen.example")
    monkeypatch.setattr(sso_emails_operations.mailer, "smtp_configured", lambda: True)
    monkeypatch.setattr(sso_emails_operations, "recent_signin", lambda *a: None)
    monkeypatch.setattr(sso_emails_operations, "limit", lambda *a: None)
    messages = []
    monkeypatch.setattr(
        sso_emails_operations.mailer, "send_email", lambda **kw: messages.append(kw)
    )
    return messages


def send(db, user, mail, email="alias@example.org"):
    sso_emails.request_address(sso_emails.Address(email=email), req(user), user, db)
    return mail[-1]["body"].split("#token=")[1].split()[0]


def confirm(db, user, token):
    return sso_emails.confirm_address(
        sso_emails.Proof(token=token), req(user), user, db
    )


def test_ownership_confirmation_replay_and_removal(db, user, mail):
    token = send(db, user, mail)
    assert (
        db.get(SsoEmailChallenge, sso_emails_operations.digest(token))
        and db.query(UserSsoEmail).count() == 0
    )
    with pytest.raises((HTTPException, DomainError)):
        link(db, "alias@example.org")
    other = User(username="other", password_hash="synthetic", is_active=True)
    db.add(other)
    db.commit()
    with pytest.raises((HTTPException, DomainError)):
        confirm(db, other, token)
    confirm(db, user, token)
    with pytest.raises((HTTPException, DomainError)):
        confirm(db, user, token)
    assert link(db, "alias@example.org").id == user.id
    sso_emails.remove_address(
        sso_emails.Address(email="alias@example.org"), req(user), user, db
    )
    with pytest.raises((HTTPException, DomainError)):
        link(db, "alias@example.org", subject="new")
    assert link(db, "alias@example.org").id == user.id


@pytest.mark.parametrize("change", ["expire", "password", "mfa", "replace"])
def test_invalidated_email_proofs(db, user, mail, change):
    token = send(db, user, mail)
    row = db.get(SsoEmailChallenge, sso_emails_operations.digest(token))
    if change == "expire":
        row.expires_at = utc_now_naive() - timedelta(seconds=1)
    if change == "password":
        user.password_hash = "changed"
    if change == "mfa":
        user.mfa_version += 1
    if change == "replace":
        send(db, user, mail)
    db.commit()
    with pytest.raises((HTTPException, DomainError)):
        confirm(db, user, token)


def test_verification_links_need_an_https_public_origin(db, user, mail, monkeypatch):
    send(db, user, mail)
    assert "\nhttps://keen.example/sso-email.html#token=" in mail[-1]["body"]

    monkeypatch.setattr(settings, "public_base_url", "http://keen.example")
    with pytest.raises((HTTPException, DomainError)) as refused:
        send(db, user, mail)

    assert refused.value.status_code == 503
    assert len(mail) == 1 and db.query(SsoEmailChallenge).count() == 1


def test_conflict(db, user, mail):
    other = User(
        username="other",
        email="taken@example.org",
        password_hash="synthetic",
        is_active=True,
    )
    db.add(other)
    db.commit()
    with pytest.raises((HTTPException, DomainError)):
        send(db, user, mail, "taken@example.org")
    token = send(db, user, mail)
    db.add(UserSsoEmail(email="alias@example.org", user_id=other.id))
    db.commit()
    with pytest.raises((HTTPException, DomainError)):
        confirm(db, user, token)


def test_smtp_failure(db, user, mail, monkeypatch):
    monkeypatch.setattr(sso_emails_operations.mailer, "smtp_configured", lambda: False)
    with pytest.raises((HTTPException, DomainError)):
        send(db, user, mail)
    monkeypatch.setattr(sso_emails_operations.mailer, "smtp_configured", lambda: True)

    def fail(**kw):
        raise RuntimeError("sensitive SMTP detail")

    monkeypatch.setattr(sso_emails_operations.mailer, "send_email", fail)
    with pytest.raises((HTTPException, DomainError)) as exc:
        send(db, user, mail)
    assert (
        "sensitive" not in str(exc.value) and db.query(SsoEmailChallenge).count() == 0
    )


@pytest.mark.parametrize(
    "age,identity,allowed",
    [(0, True, True), (301, True, False), (-10, True, False), (0, False, False)],
)
def test_recent_login(user, monkeypatch, age, identity, allowed):
    monkeypatch.setattr(sso_emails_operations, "get_valkey", lambda: None)
    monkeypatch.setattr(
        sso_emails_operations,
        "get_session",
        lambda *a: {
            "user_id": str(user.id) if identity else "other",
            "created_at": (
                datetime.now(timezone.utc) - timedelta(seconds=age)
            ).isoformat(),
        },
    )
    if allowed:
        sso_emails_operations.recent_signin(req(user), user)
    else:
        with pytest.raises((HTTPException, DomainError)):
            sso_emails_operations.recent_signin(req(user), user)


def test_http_self_service_requires_authentication_and_csrf(db, user, monkeypatch):
    from app import main
    from app.db.session import get_db
    from app.security.csrf import csrf_cookie_name, csrf_header_name
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=db.get_bind())
    uid = user.id
    monkeypatch.setattr(main, "SessionLocal", factory)
    monkeypatch.setattr(main, "ensure_session_authorization_current", lambda *a: None)
    monkeypatch.setattr(
        main, "get_current_user_from_request", lambda request, session: None
    )

    def dependency():
        with factory() as session:
            yield session

    main.app.dependency_overrides[get_db] = dependency
    try:
        client = TestClient(main.app, raise_server_exceptions=False)
        payload = {"token": "not-a-real-token-123456789"}
        assert client.post("/v1/me/sso-emails/confirm", json=payload).status_code == 401
        monkeypatch.setattr(
            main,
            "get_current_user_from_request",
            lambda request, session: session.get(User, uid),
        )
        assert client.post("/v1/me/sso-emails/confirm", json=payload).status_code == 403
        client.cookies.set(csrf_cookie_name(), "csrf-test")
        response = client.post(
            "/v1/me/sso-emails/confirm",
            json=payload,
            headers={csrf_header_name(): "csrf-test"},
        )
        assert response.status_code == 400, response.text
    finally:
        main.app.dependency_overrides.pop(get_db, None)


@pytest.mark.parametrize(
    "email",
    ["plain-address", "two@at@example.org", "a b@example.org", '"quoted"@example.org'],
)
def test_malformed_sso_addresses_are_refused_before_any_write(db, user, mail, email):
    with pytest.raises(DomainError) as refused:
        send(db, user, mail, email)
    assert refused.value.status_code == 400
    assert mail == [] and db.query(SsoEmailChallenge).count() == 0


def test_inactive_accounts_cannot_manage_sso_addresses(db, user, mail):
    user.is_active = False
    db.commit()
    with pytest.raises(DomainError) as refused:
        send(db, user, mail)
    assert refused.value.status_code == 403
    with pytest.raises(DomainError) as removal:
        sso_emails.remove_address(
            sso_emails.Address(email="alias@example.org"), req(user), user, db
        )
    assert removal.value.status_code == 403
    assert mail == []


@pytest.mark.parametrize(
    "session",
    [None, {}, {"user_id": "x"}, {"user_id": "x", "created_at": "yesterday"}],
)
def test_recent_signin_treats_unusable_sessions_as_stale(user, monkeypatch, session):
    # A missing session, or one without a parseable sign-in time, never counts
    # as a recent sign-in.
    monkeypatch.setattr(sso_emails_operations, "get_valkey", lambda: None)
    monkeypatch.setattr(sso_emails_operations, "get_session", lambda *a: session)
    with pytest.raises(DomainError) as refused:
        sso_emails_operations.recent_signin("sid", user)
    assert refused.value.status_code == 403


def test_verification_requests_are_rate_limited_per_account(user, monkeypatch):
    # Five verification emails an hour per account; the sixth is refused and
    # another account keeps its own allowance.
    import fakeredis

    store = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(sso_emails_operations, "get_valkey", lambda: store)
    for _ in range(5):
        sso_emails_operations.limit(user)
    with pytest.raises(DomainError) as refused:
        sso_emails_operations.limit(user)
    assert refused.value.status_code == 429
    other = User(username="other", password_hash="synthetic", is_active=True)
    other.id = uuid_module.uuid4()
    sso_emails_operations.limit(other)


def test_an_address_already_verified_needs_no_new_email(db, user, mail):
    token = send(db, user, mail)
    confirm(db, user, token)
    result = sso_emails.request_address(
        sso_emails.Address(email="Alias@Example.org"), req(user), user, db
    )
    assert result == {"ok": True, "already_verified": True}
    assert len(mail) == 1 and db.query(SsoEmailChallenge).count() == 0


def test_an_account_holds_at_most_ten_sso_addresses(db, user, mail):
    for index in range(10):
        db.add(UserSsoEmail(email=f"alias{index}@example.org", user_id=user.id))
    db.commit()
    with pytest.raises(DomainError) as refused:
        send(db, user, mail, "eleventh@example.org")
    assert refused.value.status_code == 400
    assert mail == [] and db.query(SsoEmailChallenge).count() == 0
    sso_emails.remove_address(
        sso_emails.Address(email="alias0@example.org"), req(user), user, db
    )
    send(db, user, mail, "eleventh@example.org")
    assert len(mail) == 1


def test_confirming_an_address_the_account_already_holds_adds_nothing(db, user, mail):
    token = send(db, user, mail)
    db.add(UserSsoEmail(email="alias@example.org", user_id=user.id))
    db.commit()
    assert confirm(db, user, token) == {"ok": True, "email": "alias@example.org"}
    assert db.query(UserSsoEmail).count() == 1
    assert db.query(SsoEmailChallenge).count() == 0


def test_a_concurrent_claim_of_the_address_is_reported_as_a_conflict(
    db, user, mail, monkeypatch
):
    # When the commit itself collides with another account's claim, the
    # caller sees a conflict and the session is rolled back.
    from sqlalchemy.exc import IntegrityError

    token = send(db, user, mail)
    rolled_back = []
    real_rollback = db.rollback

    def failing_commit():
        raise IntegrityError("INSERT", {}, Exception("duplicate key"))

    def rollback():
        rolled_back.append(True)
        real_rollback()

    monkeypatch.setattr(db, "commit", failing_commit)
    monkeypatch.setattr(db, "rollback", rollback)
    with pytest.raises(DomainError) as refused:
        confirm(db, user, token)
    assert refused.value.status_code == 409
    assert "duplicate" not in refused.value.detail
    assert rolled_back == [True]
    monkeypatch.undo()
    assert db.query(UserSsoEmail).count() == 0


def test_removing_an_unknown_address_sends_no_notification(db, user, mail, monkeypatch):
    from app.services import security_notifications

    sent = []
    monkeypatch.setattr(
        security_notifications, "enqueue", lambda *a, **kw: sent.append(a[2])
    )
    sso_emails.remove_address(
        sso_emails.Address(email="never@example.org"), req(user), user, db
    )
    assert sent == []
    token = send(db, user, mail)
    confirm(db, user, token)
    sso_emails.remove_address(
        sso_emails.Address(email="alias@example.org"), req(user), user, db
    )
    assert sent == ["sso-email-added", "sso-email-removed"]
    assert db.query(UserSsoEmail).count() == 0


def test_migration_upgrade_and_downgrade():
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect, text

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id UUID PRIMARY KEY)"))
        path = (
            Path(__file__).parents[1]
            / "alembic/legacy_versions/0087_sso_verified_emails.py"
        )
        spec = importlib.util.spec_from_file_location("sso_email_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert {"user_sso_emails", "sso_email_challenges"} <= set(
                inspect(connection).get_table_names()
            )
            migration.downgrade()
            assert inspect(connection).get_table_names() == ["users"]
    engine.dispose()
