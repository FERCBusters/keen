"""Local MFA behaviour tests; PostgreSQL also exercises the shipped migrations.

Without KEEN_MFA_TEST_DATABASE_URL, run behaviour checks on in-memory SQLite.
SQLite does not validate database locks or concurrency.

KEEN_MFA_TEST_DATABASE_URL=postgresql+psycopg2://... python -m unittest tests.test_local_mfa
Creates and drops a random schema; never uses production tables.
"""

import hashlib
import importlib.util
import json
import os
import struct
import unittest
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app.api.context as api_context
import app.services.login as login_operations
import app.services.me as me_operations
import app.services.mfa_management as mfa_operations
import cbor2
import fakeredis
import pyotp
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.api.routes import auth as login_routes
from app.api.routes import mfa as routes
from app.core.config import settings
from app.db.models import (
    AuditLog,
    MfaChallenge,
    MfaCredential,
    MfaRecoveryCode,
    SecurityNotification,
    User,
)
from app.db.session import Base, get_db
from app.security import auth as identity
from app.security import mfa
from app.security.auth import cookie_name as session_cookie_name
from app.security.passwords import hash_password
from app.security.sessions import create_session
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from tests.db_helpers import schema_engine

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (  # noqa: F401
    BASE,
    PASSWORD,
    keen,
)

URL = os.environ.get("KEEN_MFA_TEST_DATABASE_URL")

INVALID_CODE = (400, {"detail": "Invalid or already-used code"})
EXPIRED = (401, {"detail": "Verification expired; start again"})
ACCEPTED = (200, {"ok": True})
ORIGIN_REFUSED = (403, {"detail": "Authentication request origin is not allowed"})
UNVERIFIED = (403, {"detail": "Complete second-factor verification or enrolment first"})
KEY_REFUSED = (
    400,
    {"detail": "Security key verification failed; request another challenge"},
)
NOT_VERIFIED = (403, {"detail": "Verify your existing second factor first"})


def outcome(response):
    return response.status_code, response.json()


class LocalMfaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not URL:
            from sqlalchemy.pool import StaticPool

            from tests.db_helpers import create_sqlite_schema

            cls.engine = create_engine(
                "sqlite://",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
            create_sqlite_schema(cls.engine)
            cls.Session = sessionmaker(bind=cls.engine)
        else:
            cls.engine = create_engine(URL, connect_args={"options": "-c timezone=UTC"})
            cls.schema = "test_mfa_" + uuid.uuid4().hex
            with cls.engine.begin() as conn:
                conn.execute(text(f"CREATE SCHEMA {cls.schema}"))
            cls.engine.dispose()
            cls.engine = schema_engine(URL, cls.schema)
            cls.Session = sessionmaker(bind=cls.engine)
            names = {
                "users",
                "groups",
                "permissions",
                "user_groups",
                "user_permissions",
                "group_permissions",
                "audit_logs",
            }
            Base.metadata.create_all(
                cls.engine,
                tables=[t for t in Base.metadata.sorted_tables if t.name in names],
            )
            # Exercise the shipped migration against the pre-MFA user shape.
            with cls.engine.begin() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE users DROP COLUMN mfa_enabled,DROP COLUMN mfa_version,DROP COLUMN mfa_totp_secret,DROP COLUMN mfa_totp_last_step,DROP COLUMN mfa_code_failures,DROP COLUMN mfa_code_failures_since"
                    )
                )
                spec = importlib.util.spec_from_file_location(
                    "mfa_migration",
                    Path(__file__).parents[1]
                    / "tests/fixtures/historical_migrations/0084_local_mfa.py",
                )
                migration = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(migration)
                with patch.object(
                    migration, "op", Operations(MigrationContext.configure(conn))
                ):
                    migration.upgrade()
                for name, path in (
                    (
                        "notification_migration",
                        "tests/fixtures/historical_migrations/0085_security_notifications.py",
                    ),
                    # The shipped migration counting each account's wrong codes.
                    (
                        "code_failures_migration",
                        "alembic/versions/0041_mfa_code_failures.py",
                    ),
                ):
                    spec = importlib.util.spec_from_file_location(
                        name, Path(__file__).parents[1] / path
                    )
                    migration = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(migration)
                    with patch.object(
                        migration, "op", Operations(MigrationContext.configure(conn))
                    ):
                        migration.upgrade()
        cls.app = FastAPI()
        cls.app.include_router(login_routes.router)
        cls.app.include_router(routes.router)

        def database():
            with cls.Session() as db:
                yield db

        cls.app.dependency_overrides[get_db] = database

        @cls.app.get("/protected")
        def protected(request: Request, db=Depends(get_db)):
            user = identity.get_current_user_from_request(request, db)
            return {"authenticated": user is not None}

    @classmethod
    def tearDownClass(cls):
        if URL:
            with cls.engine.begin() as conn:
                conn.execute(text(f"DROP SCHEMA {cls.schema} CASCADE"))
        cls.engine.dispose()

    def setUp(self):
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        self.patches = [
            patch.object(mfa, "get_valkey", return_value=self.redis),
            patch.object(identity, "get_valkey", return_value=self.redis),
            patch.object(login_operations, "get_valkey", return_value=self.redis),
            patch.object(login_routes, "get_valkey", return_value=self.redis),
            patch.object(settings, "mfa_policy", "optional"),
            patch.object(settings, "public_base_url", "https://keen.example"),
            patch.object(
                settings, "mfa_encryption_key", Fernet.generate_key().decode()
            ),
            patch.object(settings, "local_auth_enabled", True),
            patch.object(settings, "trust_remote_user", False),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(
            self.app,
            base_url="https://keen.example",
            headers={"Origin": "https://keen.example"},
        )
        self.username = "u" + uuid.uuid4().hex
        self.password = "a-long-local-password-123"
        with self.Session() as db:
            user = User(
                username=self.username,
                password_hash=hash_password(self.password),
                role="normal",
            )
            db.add(user)
            db.commit()
            self.uid = user.id

    def post(self, path, data=None):
        return self.client.post("/v1/auth/mfa/" + path, json=data or {})

    def signed_in(self):
        with self.Session() as db:
            user = db.get(User, self.uid)
            return 200, {"user": user.username, "user_id": str(user.id)}

    def login(self):
        return self.client.post(
            "/v1/auth/login",
            json={"username": self.username, "password": self.password},
        )

    def start(self, purpose="manage"):
        with self.Session() as db:
            user = db.get(User, self.uid)
            request = Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/",
                    "headers": [(b"origin", b"https://keen.example")],
                    "client": ("127.0.0.1", 123),
                }
            )
            response = mfa.start(db, user, request, purpose)
            from http.cookies import SimpleCookie

            cookie = SimpleCookie()
            cookie.load(response.headers["set-cookie"])
            self.client.cookies.set(
                mfa.cookie_name(),
                cookie[mfa.cookie_name()].value,
                domain="keen.example",
                path="/",
            )

    def enrol(self):
        self.start()
        response = self.post("totp/start")
        self.assertEqual(response.status_code, 200, response.text)
        self.secret = response.json()["secret"]
        # Kept so a test can replay the exact code that enrolment consumed.
        self.enrol_code = pyotp.TOTP(self.secret).now()
        result = self.post("totp/confirm", {"code": self.enrol_code})
        self.assertEqual(result.status_code, 200, result.text)
        self.codes = result.json()["recovery_codes"]
        self.redis.flushall()

    def allow_next_totp(self):
        with self.Session() as db:
            db.get(User, self.uid).mfa_totp_last_step = -1
            db.commit()

    def test_totp_enrolment_encrypted_and_recovery_hashed(self):
        self.enrol()
        with self.Session() as db:
            user = db.get(User, self.uid)
            self.assertTrue(user.mfa_enabled)
            self.assertNotIn(self.secret, user.mfa_totp_secret)
            self.assertEqual(mfa.decrypt(user.mfa_totp_secret), self.secret)
            hashes_ = [
                x.code_hash
                for x in db.query(MfaRecoveryCode).filter_by(user_id=self.uid)
            ]
            self.assertEqual(len(hashes_), 10)
            self.assertNotIn(self.codes[0], hashes_)
        self.assertEqual(outcome(self.post("finish")), self.signed_in())
        self.assertEqual(self.client.get("/protected").json(), {"authenticated": True})
        self.assertEqual(outcome(self.post("finish")), EXPIRED)

    def test_invalid_enrolment_does_not_enable(self):
        self.start()
        self.post("totp/start")
        self.assertEqual(
            outcome(self.post("totp/confirm", {"code": "invalid"})),
            (400, {"detail": "Invalid authenticator code"}),
        )
        with self.Session() as db:
            self.assertFalse(db.get(User, self.uid).mfa_enabled)
            self.assertIsNone(db.get(User, self.uid).mfa_totp_secret)

    def test_pending_cannot_access_and_totp_cannot_replay(self):
        self.enrol()
        self.client.cookies.clear()
        self.assertEqual(
            self.login().json(), {"mfa_required": True, "next": "/mfa.html"}
        )
        self.assertEqual(self.client.get("/protected").json(), {"authenticated": False})
        self.assertEqual(outcome(self.post("finish")), UNVERIFIED)
        # The code that enrolment consumed is refused, whether or not the
        # 30-second window has rolled over since. A code from a later window is
        # fresh, so the test replays that exact code.
        self.assertEqual(
            outcome(self.post("verify-code", {"code": self.enrol_code})), INVALID_CODE
        )
        self.allow_next_totp()
        self.assertEqual(
            outcome(self.post("verify-code", {"code": pyotp.TOTP(self.secret).now()})),
            ACCEPTED,
        )
        self.assertEqual(outcome(self.post("finish")), self.signed_in())
        self.assertEqual(self.client.get("/protected").json(), {"authenticated": True})

    def test_recovery_is_single_use(self):
        self.enrol()
        self.start("login")
        used = {"kind": "recovery", "code": self.codes[0]}
        self.assertEqual(outcome(self.post("verify-code", used)), ACCEPTED)
        self.start("login")
        self.assertEqual(outcome(self.post("verify-code", used)), INVALID_CODE)
        # Spending one code leaves the others usable.
        unused = {"kind": "recovery", "code": self.codes[1]}
        self.assertEqual(outcome(self.post("verify-code", unused)), ACCEPTED)

    def test_origin_expiry_and_password_change(self):
        self.start()
        self.assertEqual(
            outcome(
                self.client.post(
                    "/v1/auth/mfa/state",
                    headers={"Origin": "https://evil.example"},
                    json={},
                )
            ),
            ORIGIN_REFUSED,
        )
        with self.Session() as db:
            row = db.query(MfaChallenge).filter_by(user_id=self.uid).one()
            row.expires_at = mfa.now() - timedelta(seconds=1)
            db.commit()
        self.assertEqual(outcome(self.post("state")), EXPIRED)
        self.start()
        with self.Session() as db:
            db.get(User, self.uid).password_hash = hash_password("another-password-123")
            db.commit()
        self.assertEqual(outcome(self.post("finish")), EXPIRED)

    def test_policy_blocks_old_local_sessions_but_not_sso(self):
        with self.Session() as db:
            user = db.get(User, self.uid)
            self.assertIs(
                mfa.local_session_allowed(db, user, {"auth_method": "local"}), True
            )
            with patch.object(settings, "mfa_policy", "all"):
                self.assertIs(
                    mfa.local_session_allowed(db, user, {"auth_method": "local"}),
                    False,
                )
                self.assertIs(
                    mfa.local_session_allowed(db, user, {"auth_method": "oidc"}), True
                )
            with patch.object(settings, "mfa_policy", "admins"):
                self.assertIs(mfa.required(db, user), False)
                user.role = "admin"
                self.assertIs(mfa.required(db, user), True)
        with patch.object(settings, "mfa_policy", "all"):
            self.assertEqual(
                self.login().json(), {"mfa_required": True, "next": "/mfa.html"}
            )
            self.assertEqual(
                self.post("state").json(),
                {
                    "stage": "enrol",
                    "purpose": "login",
                    "username": self.username,
                    "totp": False,
                    "required": True,
                    "credentials": [],
                    "recovery_codes_remaining": 0,
                },
            )
            self.assertEqual(outcome(self.post("finish")), UNVERIFIED)

    def test_factor_change_revokes_existing_sessions(self):
        sid = create_session(
            self.redis,
            user_id=str(self.uid),
            ttl_seconds=300,
            auth_method="local",
            mfa_version=0,
        )
        self.client.cookies.set(session_cookie_name(), sid)
        self.assertEqual(self.client.get("/protected").json(), {"authenticated": True})
        self.enrol()
        self.assertEqual(self.client.get("/protected").json(), {"authenticated": False})

    def test_rate_limit_and_missing_key_fail_closed(self):
        self.start()
        with patch.object(settings, "mfa_encryption_key", ""):
            self.assertEqual(
                outcome(self.post("totp/start")),
                (503, {"detail": "TOTP requires a valid KEEN_MFA_ENCRYPTION_KEY"}),
            )
        for _ in range(16):
            result = self.post("totp/start")
        self.assertEqual(
            outcome(result),
            (429, {"detail": "Too many verification attempts; try again later"}),
        )
        with patch.object(mfa, "get_valkey", side_effect=RuntimeError("offline")):
            self.assertEqual(
                outcome(self.post("totp/start")),
                (503, {"detail": "Authentication rate limiter unavailable"}),
            )

    def test_management_requires_password_and_factor(self):
        self.assertEqual(
            outcome(self.post("manage/start", {"password": self.password})),
            (401, {"detail": "Sign in before managing local account security"}),
        )
        self.assertEqual(outcome(self.login()), self.signed_in())
        self.assertEqual(
            outcome(self.post("manage/start", {"password": "wrong"})),
            (401, {"detail": "Invalid password"}),
        )
        started = (200, {"mfa_required": True, "next": "/mfa.html"})
        self.assertEqual(
            outcome(self.post("manage/start", {"password": self.password})), started
        )
        self.enrol()
        self.post("finish")
        self.assertEqual(
            outcome(self.post("manage/start", {"password": self.password})), started
        )
        self.assertEqual(outcome(self.post("recovery/regenerate")), NOT_VERIFIED)
        self.assertEqual(outcome(self.post("remove", {"id": "totp"})), NOT_VERIFIED)

    def test_required_factor_cannot_be_removed_and_codes_replace(self):
        self.enrol()
        with patch.object(settings, "mfa_policy", "all"):
            self.assertEqual(
                outcome(self.post("remove", {"id": "totp"})),
                (
                    400,
                    {
                        "detail": "Register a replacement before removing "
                        "your last required factor"
                    },
                ),
            )
        result = self.post("recovery/regenerate")
        self.assertEqual(result.status_code, 200)
        replaced = result.json()["recovery_codes"]
        self.assertEqual(len(set(replaced) - set(self.codes)), 10)
        self.start("login")
        self.assertEqual(
            outcome(
                self.post("verify-code", {"kind": "recovery", "code": self.codes[0]})
            ),
            INVALID_CODE,
        )
        self.assertEqual(
            outcome(
                self.post("verify-code", {"kind": "recovery", "code": replaced[0]})
            ),
            ACCEPTED,
        )

    def test_removing_factors_keeps_the_rest_and_the_last_turns_mfa_off(self):
        self.enrol()
        # Adding a factor needs the existing one first.
        self.start()
        self.allow_next_totp()
        verified = self.post(
            "verify-code", {"kind": "totp", "code": pyotp.TOTP(self.secret).now()}
        )
        self.assertEqual(outcome(verified), ACCEPTED)
        private, cid, credential = self.webauthn_registration(start=False)
        added = self.post(
            "webauthn/register", {"credential": credential, "name": "Desk key"}
        )
        # A further factor issues no new recovery codes.
        self.assertEqual(outcome(added), (200, {"ok": True, "recovery_codes": None}))
        with self.Session() as db:
            (key,) = db.query(MfaCredential).filter_by(user_id=self.uid).all()
            key_id = str(key.id)

        self.assertEqual(outcome(self.post("remove", {"id": "totp"})), ACCEPTED)
        with self.Session() as db:
            user = db.get(User, self.uid)
            self.assertIsNone(user.mfa_totp_secret)
            self.assertIs(user.mfa_enabled, True)
            self.assertEqual(
                db.query(MfaRecoveryCode).filter_by(user_id=self.uid).count(), 10
            )
        missing = self.post("remove", {"id": str(uuid.uuid4())})
        self.assertEqual(outcome(missing), (404, {"detail": "Authenticator not found"}))

        self.assertEqual(outcome(self.post("remove", {"id": key_id})), ACCEPTED)
        with self.Session() as db:
            user = db.get(User, self.uid)
            self.assertIs(user.mfa_enabled, False)
            self.assertEqual(
                db.query(MfaRecoveryCode).filter_by(user_id=self.uid).count(), 0
            )
            self.assertEqual(
                db.query(MfaCredential).filter_by(user_id=self.uid).count(), 0
            )
        refused = self.post("recovery/regenerate")
        self.assertEqual(
            outcome(refused), (400, {"detail": "Enrol an authenticator first"})
        )

    def test_cancel_and_logout_invalidate_pending_challenge(self):
        self.start()
        token = self.client.cookies.get(mfa.cookie_name())
        self.assertEqual(outcome(self.post("cancel")), ACCEPTED)
        self.client.cookies.set(
            mfa.cookie_name(), token, domain="keen.example", path="/"
        )
        self.assertEqual(outcome(self.post("state")), EXPIRED)
        self.start()
        token = self.client.cookies.get(mfa.cookie_name())
        self.assertEqual(self.client.post("/v1/auth/logout").status_code, 204)
        self.client.cookies.set(
            mfa.cookie_name(), token, domain="keen.example", path="/"
        )
        self.assertEqual(outcome(self.post("state")), EXPIRED)

    def test_full_application_middleware_boundary(self):
        from app import main

        main.app.dependency_overrides[get_db] = self.app.dependency_overrides[get_db]
        self.addCleanup(main.app.dependency_overrides.clear)
        with (
            patch.object(main, "SessionLocal", self.Session),
            patch.object(settings, "mfa_policy", "all"),
        ):
            client = TestClient(
                main.app,
                base_url="https://keen.example",
                headers={"Origin": "https://keen.example"},
            )
            result = client.post(
                "/v1/auth/login",
                json={"username": self.username, "password": self.password},
            )
            self.assertEqual(
                outcome(result), (200, {"mfa_required": True, "next": "/mfa.html"})
            )
            # A pending second factor is no session.
            self.assertEqual(
                outcome(client.get("/v1/auth/check")),
                (401, {"detail": "Not authenticated"}),
            )
            state = client.post("/v1/auth/mfa/state", json={})
            self.assertEqual(state.status_code, 200, state.text)
            self.assertEqual(
                (state.json()["stage"], state.headers.get("cache-control")),
                ("enrol", "no-store"),
            )
            self.assertEqual(
                outcome(
                    client.post(
                        "/v1/auth/mfa/state",
                        headers={"Origin": "https://evil.example"},
                        json={},
                    )
                ),
                ORIGIN_REFUSED,
            )

    def test_security_mail_login_once_per_ip_after_full_mfa(self):
        from app.db.models import SecurityNotification, UserLoginIP
        from app.services import security_notifications as mail

        with self.Session() as db:
            db.get(User, self.uid).email = "alice@example.org"
            db.commit()
        with (
            patch.object(mail, "smtp_configured", return_value=True),
            patch.object(api_context, "request_ip", return_value="203.0.113.7"),
        ):
            self.enrol()
            self.client.cookies.clear()
            self.login()
            with self.Session() as db:
                self.assertEqual(
                    db.query(UserLoginIP).filter_by(user_id=self.uid).count(), 0
                )
            self.assertEqual(
                outcome(
                    self.post(
                        "verify-code", {"kind": "recovery", "code": self.codes[0]}
                    )
                ),
                ACCEPTED,
            )
            self.assertEqual(outcome(self.post("finish")), self.signed_in())
            self.start("login")
            self.post("verify-code", {"kind": "recovery", "code": self.codes[1]})
            self.post("finish")
            with self.Session() as db:
                rows = db.query(SecurityNotification).filter_by(user_id=self.uid).all()
                self.assertEqual(sum("unfamiliar" in x.subject for x in rows), 1)
                self.assertEqual(
                    sum("authenticator was added" in x.subject for x in rows), 1
                )
                self.assertFalse(
                    any(self.secret in x.body or self.codes[0] in x.body for x in rows)
                )
                # Each notice names the installation by its public origin.
                self.assertTrue(
                    all(
                        "Keen installation: https://keen.example\n" in x.body
                        for x in rows
                    )
                )

    def test_security_mail_smtp_failure_retries_then_stops(self):
        from app.db.models import SecurityNotification
        from app.services import security_notifications as mail

        with self.Session() as db:
            user = db.get(User, self.uid)
            user.email = "alice@example.org"
            with patch.object(mail, "smtp_configured", return_value=True):
                mail.enqueue(db, user, "password-changed")
            db.commit()
        with (
            patch.object(mail, "SessionLocal", self.Session),
            patch.object(mail, "smtp_configured", return_value=True),
            patch.object(
                mail, "send_email", side_effect=RuntimeError("secret SMTP detail")
            ),
        ):
            for attempt in range(8):
                with self.Session() as db:
                    row = (
                        db.query(SecurityNotification).filter_by(user_id=self.uid).one()
                    )
                    row.next_attempt_at = mfa.now() - timedelta(seconds=1)
                    db.commit()
                mail.deliver_pending()
        with self.Session() as db:
            row = db.query(SecurityNotification).filter_by(user_id=self.uid).one()
            self.assertEqual(row.attempts, 8)
            self.assertEqual(row.status, "failed")
            self.assertEqual(row.last_error, "RuntimeError")

    def test_security_mail_transaction_and_missing_smtp(self):
        from app.db.models import SecurityNotification
        from app.services import security_notifications as mail

        with self.Session() as db:
            user = db.get(User, self.uid)
            user.email = "alice@example.org"
            db.commit()
            with patch.object(mail, "smtp_configured", return_value=False):
                mail.enqueue(db, user, "password-changed")
            db.commit()
            self.assertEqual(
                db.query(SecurityNotification).filter_by(user_id=self.uid).count(), 0
            )
            with patch.object(mail, "smtp_configured", return_value=True):
                mail.enqueue(db, user, "password-changed")
            db.rollback()
            self.assertEqual(
                db.query(SecurityNotification).filter_by(user_id=self.uid).count(), 0
            )

    def test_security_mail_delivery(self):
        from app.db.models import SecurityNotification
        from app.services import security_notifications as mail

        with self.Session() as db:
            user = db.get(User, self.uid)
            user.email = "alice@example.org"
            with patch.object(mail, "smtp_configured", return_value=True):
                mail.enqueue(db, user, "recovery-codes-regenerated")
            db.commit()
        with (
            patch.object(mail, "SessionLocal", self.Session),
            patch.object(mail, "smtp_configured", return_value=True),
            patch.object(mail, "send_email") as send,
        ):
            mail.deliver_pending()
        # The class shares one database, so other tests' notices go out too.
        (sent,) = [
            call.kwargs
            for call in send.call_args_list
            if f"\nAccount: {self.username}\n" in call.kwargs["body"]
        ]
        self.assertEqual(
            (sent["to_email"], sent["subject"]),
            (
                "alice@example.org",
                "Keen security: Your Keen recovery codes were replaced",
            ),
        )
        self.assertTrue(
            sent["body"].startswith("Your Keen recovery codes were replaced.\n")
        )
        with self.Session() as db:
            row = db.query(SecurityNotification).filter_by(user_id=self.uid).one()
            self.assertEqual(row.status, "sent")
            # A sent message's text is not kept.
            self.assertEqual(row.body, "")

    def test_password_routes_enqueue_security_alerts(self):
        from app.api.routes import me, users
        from app.db.models import SecurityNotification
        from app.schemas import AdminSetPasswordPayload, ChangePasswordPayload
        from app.services import security_notifications as mail

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/v1/me/password",
                "headers": [],
                "client": ("203.0.113.5", 1),
            }
        )
        with self.Session() as db:
            user = db.get(User, self.uid)
            user.email = "alice@example.org"
            user.role = "admin"
            db.commit()
            request.state.user = user
            with (
                patch.object(mail, "smtp_configured", return_value=True),
                patch.object(me_operations, "get_valkey", return_value=self.redis),
            ):
                me.change_my_password(
                    ChangePasswordPayload(
                        current_password=self.password,
                        new_password="a-new-password-12345",
                    ),
                    request,
                    db,
                )
                users.set_user_password_admin(
                    self.uid,
                    AdminSetPasswordPayload(new_password="another-password-12345"),
                    request,
                    db,
                )
            subjects = [
                x.subject
                for x in db.query(SecurityNotification).filter_by(user_id=self.uid)
            ]
            self.assertEqual(
                subjects,
                [
                    "Keen security: Your Keen password was changed",
                    "Keen security: Your Keen password was reset by an administrator",
                ],
            )

    def webauthn_registration(self, start=True, private=None, cid=None):
        if start:
            self.start()
        options = self.post("webauthn/register-options").json()
        private = private or ec.generate_private_key(ec.SECP256R1())
        public = private.public_key().public_numbers()
        cid = cid or os.urandom(32)
        cose = cbor2.dumps(
            {
                1: 2,
                3: -7,
                -1: 1,
                -2: public.x.to_bytes(32, "big"),
                -3: public.y.to_bytes(32, "big"),
            }
        )
        client = json.dumps(
            {
                "type": "webauthn.create",
                "challenge": options["challenge"],
                "origin": "https://keen.example",
                "crossOrigin": False,
            }
        ).encode()
        authdata = (
            hashlib.sha256(b"keen.example").digest()
            + b"\x41"
            + struct.pack(">I", 0)
            + bytes(16)
            + struct.pack(">H", len(cid))
            + cid
            + cose
        )
        credential = {
            "id": mfa.b64(cid),
            "rawId": mfa.b64(cid),
            "type": "public-key",
            "response": {
                "clientDataJSON": mfa.b64(client),
                "attestationObject": mfa.b64(
                    cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": authdata})
                ),
            },
        }
        return private, cid, credential

    def assertion(
        self, private, cid, options, origin="https://keen.example", cross=False
    ):
        client = json.dumps(
            {
                "type": "webauthn.get",
                "challenge": options["challenge"],
                "origin": origin,
                "crossOrigin": cross,
            }
        ).encode()
        authdata = (
            hashlib.sha256(b"keen.example").digest() + b"\x01" + struct.pack(">I", 1)
        )
        signature = private.sign(
            authdata + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": mfa.b64(cid),
            "rawId": mfa.b64(cid),
            "type": "public-key",
            "response": {
                "clientDataJSON": mfa.b64(client),
                "authenticatorData": mfa.b64(authdata),
                "signature": mfa.b64(signature),
                "userHandle": mfa.b64(self.uid.bytes),
            },
        }

    def test_real_webauthn_registration_and_signature(self):
        private, cid, credential = self.webauthn_registration()
        response = self.post(
            "webauthn/register", {"credential": credential, "name": "Test key"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["recovery_codes"]), 10)
        # The registration challenge is spent.
        self.assertEqual(
            outcome(self.post("webauthn/register", {"credential": credential})),
            (400, {"detail": "Registration failed; request another challenge"}),
        )
        self.start("login")
        options = self.post("webauthn/options").json()
        assertion = self.assertion(private, cid, options)
        self.assertEqual(
            outcome(self.post("webauthn/verify", {"credential": assertion})), ACCEPTED
        )
        self.assertEqual(
            outcome(self.post("webauthn/verify", {"credential": assertion})),
            (400, {"detail": "Start verification first"}),
        )
        self.assertEqual(outcome(self.post("finish")), self.signed_in())
        self.assertEqual(self.client.get("/protected").json(), {"authenticated": True})

    def test_webauthn_wrong_origin_cross_origin_wrong_user_and_tampered_signature(self):
        private, cid, credential = self.webauthn_registration()
        self.assertEqual(
            self.post("webauthn/register", {"credential": credential}).status_code, 200
        )
        for kind in ("origin", "cross", "user", "signature"):
            self.redis.flushall()
            self.start("login")
            options = self.post("webauthn/options").json()
            assertion = self.assertion(
                private,
                cid,
                options,
                origin="https://evil.example"
                if kind == "origin"
                else "https://keen.example",
                cross=kind == "cross",
            )
            if kind == "user":
                assertion["response"]["userHandle"] = mfa.b64(uuid.uuid4().bytes)
            if kind == "signature":
                assertion["response"]["signature"] = mfa.b64(b"invalid")
            self.assertEqual(
                outcome(self.post("webauthn/verify", {"credential": assertion})),
                KEY_REFUSED,
                kind,
            )
            self.assertEqual(outcome(self.post("finish")), UNVERIFIED)

    def test_verification_and_management_steps_need_their_own_stage(self):
        # Management-only steps are refused during verification, and
        # verification steps are refused during management.
        self.start()
        refused = self.post("verify-code", {"code": "123456"})
        self.assertEqual(
            outcome(refused),
            (400, {"detail": "Start a second-factor verification first"}),
        )
        self.assertEqual(
            outcome(self.post("webauthn/options")),
            (400, {"detail": "Start verification first"}),
        )
        confirm = self.post("totp/confirm", {"code": "123456"})
        self.assertEqual(
            outcome(confirm), (400, {"detail": "Start authenticator setup first"})
        )
        with self.Session() as db:
            self.assertIsNone(db.get(User, self.uid).mfa_totp_secret)
            self.assertIs(db.get(User, self.uid).mfa_enabled, False)

    def test_a_second_authenticator_needs_the_first_removed(self):
        self.enrol()
        refused = self.post("totp/start")
        self.assertEqual(
            outcome(refused),
            (
                409,
                {"detail": "Remove the existing authenticator before adding another"},
            ),
        )
        self.assertEqual(
            outcome(self.post("totp/confirm", {"code": pyotp.TOTP(self.secret).now()})),
            (400, {"detail": "Start authenticator setup first"}),
        )
        with self.Session() as db:
            row = db.query(MfaChallenge).filter_by(user_id=self.uid).one()
            self.assertNotIn("totp_secret", row.data)

    def test_security_key_verification_needs_a_registered_key_and_a_challenge(self):
        self.enrol()
        self.start("login")
        refused = self.post("webauthn/options")
        self.assertEqual(
            outcome(refused),
            (400, {"detail": "No security key or passkey is registered"}),
        )
        # Without an issued challenge no assertion can verify.
        private = ec.generate_private_key(ec.SECP256R1())
        cid = os.urandom(32)
        stray = self.assertion(private, cid, {"challenge": mfa.b64(os.urandom(32))})
        self.assertEqual(
            outcome(self.post("webauthn/verify", {"credential": stray})), KEY_REFUSED
        )
        self.assertEqual(outcome(self.post("finish")), UNVERIFIED)

    def test_an_assertion_from_an_unregistered_key_is_refused(self):
        private, cid, credential = self.webauthn_registration()
        self.assertEqual(
            self.post("webauthn/register", {"credential": credential}).status_code, 200
        )
        self.redis.flushall()
        self.start("login")
        options = self.post("webauthn/options").json()
        other = ec.generate_private_key(ec.SECP256R1())
        foreign = self.assertion(other, os.urandom(32), options)
        self.assertEqual(
            outcome(self.post("webauthn/verify", {"credential": foreign})), KEY_REFUSED
        )
        self.assertEqual(outcome(self.post("finish")), UNVERIFIED)
        # The ceremony is consumed: the genuine key needs a fresh challenge.
        genuine = self.assertion(private, cid, options)
        self.assertEqual(
            outcome(self.post("webauthn/verify", {"credential": genuine})),
            KEY_REFUSED,
        )

    def test_at_most_ten_security_keys_and_no_duplicate_registrations(self):
        private, cid, credential = self.webauthn_registration()
        self.assertEqual(
            self.post("webauthn/register", {"credential": credential}).status_code, 200
        )
        # Re-registering the same authenticator in a fresh ceremony is a conflict.
        _, _, again = self.webauthn_registration(start=False, private=private, cid=cid)
        duplicate = self.post("webauthn/register", {"credential": again})
        self.assertEqual(
            outcome(duplicate),
            (409, {"detail": "This credential is already registered"}),
        )
        with self.Session() as db:
            self.assertEqual(
                db.query(MfaCredential).filter_by(user_id=self.uid).count(), 1
            )
            for index in range(9):
                db.add(
                    MfaCredential(
                        user_id=self.uid,
                        credential_id=mfa.b64(os.urandom(32)),
                        public_key=mfa.b64(b"synthetic-public-key"),
                        sign_count=0,
                        name=f"Key {index}",
                    )
                )
            db.commit()
        refused = self.post("webauthn/register-options")
        self.assertEqual(
            outcome(refused),
            (
                400,
                {"detail": "A maximum of ten security keys/passkeys can be registered"},
            ),
        )


if __name__ == "__main__":
    unittest.main()


@pytest.mark.parametrize(
    "public,expected",
    [
        ("https://keen.example", ("https://keen.example", "keen.example")),
        ("https://keen.example:8443", ("https://keen.example:8443", "keen.example")),
        ("http://localhost:8080", ("http://localhost:8080", "localhost")),
        ("http://127.0.0.1", ("http://127.0.0.1", "127.0.0.1")),
    ],
)
def test_webauthn_uses_the_public_origin_and_its_host(public, expected):
    with patch.object(settings, "public_base_url", public):
        assert mfa.origin_config() == expected


def test_local_mfa_needs_an_https_public_origin():
    with patch.object(settings, "public_base_url", "http://keen.example"):
        with pytest.raises(HTTPException) as refused:
            mfa.origin_config()
    assert (refused.value.status_code, refused.value.detail) == (
        503,
        "Local MFA requires an HTTPS KEEN_PUBLIC_BASE_URL",
    )


# --- Wrong codes through the assembled application ------------------------------


@pytest.fixture
def ceremony(keen, monkeypatch):  # noqa: F811 - fixture imported above
    """An enrolled TOTP account whose sign-ins stop at the second factor."""
    from app.services import security_notifications

    key = Fernet.generate_key()
    monkeypatch.setattr(settings, "mfa_encryption_key", key.decode())
    monkeypatch.setattr(security_notifications, "smtp_configured", lambda: True)
    secret = pyotp.random_base32()
    user = keen.user()
    user.email = "owner@example.test"
    user.mfa_enabled = True
    user.mfa_totp_secret = Fernet(key).encrypt(secret.encode()).decode()
    codes = ["%032X" % n for n in range(1, 3)]
    keen.db.add_all(
        MfaRecoveryCode(user_id=user.id, code_hash=mfa.digest(code)) for code in codes
    )
    keen.db.commit()

    def sign_in(client=None):
        client = client or keen.client()
        started = client.post(
            "/v1/auth/login",
            json={"username": user.username, "password": PASSWORD},
            headers={"Origin": BASE},
        )
        assert started.json()["mfa_required"] is True, started.text
        return client

    def code(client, value, kind="totp"):
        return client.post(
            "/v1/auth/mfa/verify-code",
            json={"code": value, "kind": kind},
            headers={"Origin": BASE},
        )

    def mail():
        keen.db.expire_all()
        return keen.db.query(SecurityNotification).filter_by(user_id=user.id).all()

    good = pyotp.TOTP(secret).now()
    wrong = [c for c in ("000000", "111111", "222222", "333333", "444444") if c != good]
    return SimpleNamespace(
        keen=keen,
        user=user,
        secret=secret,
        good=good,
        wrong=wrong,
        recovery=codes,
        sign_in=sign_in,
        code=code,
        mail=mail,
    )


def test_five_wrong_codes_end_the_ceremony_and_tell_the_owner(ceremony):
    client = ceremony.sign_in()
    # TOTP and recovery-code guesses both count against the account.
    guesses = [(c, "totp") for c in ceremony.wrong[:3]] + [("F" * 32, "recovery")]
    for value, kind in guesses:
        assert outcome(ceremony.code(client, value, kind)) == INVALID_CODE

    ended = ceremony.code(client, ceremony.wrong[3])

    assert outcome(ended) == (401, {"detail": mfa_operations.CODES_REFUSED})
    # The right code no longer completes the ended ceremony.
    assert outcome(ceremony.code(client, ceremony.good)) == EXPIRED
    origin = {"Origin": BASE}
    assert outcome(client.post("/v1/auth/mfa/finish", headers=origin)) == EXPIRED
    assert outcome(client.get("/v1/auth/check")) == (
        401,
        {"detail": "Not authenticated"},
    )
    db = ceremony.keen.db
    assert db.query(MfaChallenge).filter_by(user_id=ceremony.user.id).count() == 0
    (notice,) = ceremony.mail()
    assert notice.subject == (
        "Keen security: Keen ended a two-factor verification after repeated "
        "incorrect codes"
    )
    assert "\n" not in notice.subject and notice.recipient == "owner@example.test"
    assert "Keen installation: https://keen.example\n" in notice.body
    for value in (*ceremony.wrong, ceremony.good, ceremony.secret):
        assert value not in notice.body
    actions = [
        path
        for (path,) in db.query(AuditLog.path)
        .filter(AuditLog.method == "MFA", AuditLog.username == ceremony.user.username)
        .order_by(AuditLog.ts)
    ]
    assert actions == [
        *["/v1/auth/mfa/rejected-totp"] * 3,
        "/v1/auth/mfa/rejected-recovery",
        "/v1/auth/mfa/verification-ended",
    ]

    # A new sign-in accepts no code for an hour, the right one included, and
    # sends no further mail.
    client = ceremony.sign_in(client)
    refused = ceremony.code(client, ceremony.good)
    assert outcome(refused) == (401, {"detail": mfa_operations.CODES_REFUSED})
    assert len(ceremony.mail()) == 1

    # Once the hour has passed, the right code completes a new sign-in.
    user = db.get(User, ceremony.user.id)
    user.mfa_code_failures_since -= mfa.CODE_LOCKOUT
    db.commit()
    client = ceremony.sign_in(client)
    assert outcome(ceremony.code(client, ceremony.good)) == ACCEPTED
    assert outcome(client.post("/v1/auth/mfa/finish", headers=origin)) == (
        200,
        {"user": user.username, "user_id": str(user.id)},
    )
    checked = client.get("/v1/auth/check")
    assert (checked.status_code, checked.content) == (204, b"")


def test_wrong_codes_count_across_sign_ins(ceremony):
    """Starting again does not reset the count, so guesses stay bounded."""
    client = ceremony.sign_in()
    for value in ceremony.wrong[:4]:
        assert outcome(ceremony.code(client, value)) == INVALID_CODE

    client = ceremony.sign_in(client)
    ended = ceremony.code(client, ceremony.wrong[4 % len(ceremony.wrong)])

    assert outcome(ended) == (401, {"detail": mfa_operations.CODES_REFUSED})
    assert [n.subject for n in ceremony.mail()] == [
        "Keen security: Keen ended a two-factor verification after repeated "
        "incorrect codes"
    ]
    # A third sign-in gets no guess at all.
    client = ceremony.sign_in(client)
    assert outcome(ceremony.code(client, ceremony.wrong[0])) == (
        401,
        {"detail": mfa_operations.CODES_REFUSED},
    )
    ceremony.keen.db.expire_all()
    assert ceremony.keen.db.get(User, ceremony.user.id).mfa_code_failures == 5


def test_fewer_wrong_codes_leave_the_ceremony_usable(ceremony):
    client = ceremony.sign_in()
    for value in ceremony.wrong[:4]:
        assert outcome(ceremony.code(client, value)) == INVALID_CODE
    # Failed security-key assertions are not guesses: each consumes its own
    # challenge, and a forged one needs the key's private half.
    for _ in range(3):
        failed = client.post(
            "/v1/auth/mfa/webauthn/verify",
            json={"credential": {"id": "unknown"}},
            headers={"Origin": BASE},
        )
        assert outcome(failed) == (
            400,
            {"detail": "Security key verification failed; request another challenge"},
        )

    assert outcome(ceremony.code(client, ceremony.good)) == ACCEPTED
    origin = {"Origin": BASE}
    assert outcome(client.post("/v1/auth/mfa/finish", headers=origin)) == (
        200,
        {"user": ceremony.user.username, "user_id": str(ceremony.user.id)},
    )
    # Nothing tells the owner about a ceremony that ends well.
    assert ceremony.mail() == []
    # The right code clears the count.
    ceremony.keen.db.expire_all()
    user = ceremony.keen.db.get(User, ceremony.user.id)
    assert (user.mfa_code_failures, user.mfa_code_failures_since) == (0, None)
    rows = (
        ceremony.keen.db.query(AuditLog)
        .filter_by(path="/action/sign-in", username=ceremony.user.username)
        .order_by(AuditLog.ts)
        .all()
    )
    assert [row.query_string for row in rows] == [
        "method=local&outcome=mfa-required",
        "method=local&outcome=signed-in&mfa=verified",
    ]


def test_using_a_recovery_code_tells_the_owner(ceremony):
    client = ceremony.sign_in()

    used = ceremony.code(client, ceremony.recovery[0], "recovery")

    assert outcome(used) == ACCEPTED
    notices = [n for n in ceremony.mail() if "recovery code was used" in n.subject]
    assert [n.subject for n in notices] == [
        "Keen security: A recovery code was used for your Keen account"
    ]
    assert ceremony.recovery[0] not in notices[0].body
    assert ceremony.recovery[1] not in notices[0].body
    # The code is single use: a second ceremony cannot reuse it.
    client = ceremony.sign_in(ceremony.keen.client())
    assert outcome(ceremony.code(client, ceremony.recovery[0], "recovery")) == (
        INVALID_CODE
    )


def test_a_refused_second_factor_start_still_audits_the_account(ceremony):
    """A right password whose second factor cannot start is still recorded."""
    client = ceremony.keen.client()

    refused = client.post(
        "/v1/auth/login",
        json={"username": ceremony.user.username, "password": PASSWORD},
    )

    assert outcome(refused) == (
        403,
        {"detail": "Authentication request origin is not allowed"},
    )
    db = ceremony.keen.db
    db.expire_all()
    rows = (
        db.query(AuditLog)
        .filter_by(path="/action/sign-in", username=ceremony.user.username)
        .all()
    )
    assert [row.query_string for row in rows] == ["method=local&outcome=mfa-refused"]
    assert db.query(MfaChallenge).filter_by(user_id=ceremony.user.id).count() == 0
