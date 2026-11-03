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
                mfa.COOKIE, cookie[mfa.COOKIE].value, domain="keen.example", path="/"
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
        self.assertEqual(self.post("finish").status_code, 200)
        self.assertTrue(self.client.get("/protected").json()["authenticated"])
        self.assertEqual(self.post("finish").status_code, 401)

    def test_invalid_enrolment_does_not_enable(self):
        self.start()
        self.post("totp/start")
        self.assertEqual(
            self.post("totp/confirm", {"code": "invalid"}).status_code, 400
        )
        with self.Session() as db:
            self.assertFalse(db.get(User, self.uid).mfa_enabled)
            self.assertIsNone(db.get(User, self.uid).mfa_totp_secret)

    def test_pending_cannot_access_and_totp_cannot_replay(self):
        self.enrol()
        self.client.cookies.clear()
        self.assertTrue(self.login().json()["mfa_required"])
        self.assertFalse(self.client.get("/protected").json()["authenticated"])
        self.assertEqual(self.post("finish").status_code, 403)
        # The code that enrolment consumed is refused, whether or not the
        # 30-second window has rolled over since. A code from a later window is
        # fresh, so the test replays that exact code.
        self.assertEqual(
            self.post("verify-code", {"code": self.enrol_code}).status_code, 400
        )
        self.allow_next_totp()
        self.assertEqual(
            self.post(
                "verify-code", {"code": pyotp.TOTP(self.secret).now()}
            ).status_code,
            200,
        )
        self.assertEqual(self.post("finish").status_code, 200)
        self.assertTrue(self.client.get("/protected").json()["authenticated"])

    def test_recovery_is_single_use(self):
        self.enrol()
        self.start("login")
        self.assertEqual(
            self.post(
                "verify-code", {"kind": "recovery", "code": self.codes[0]}
            ).status_code,
            200,
        )
        self.start("login")
        self.assertEqual(
            self.post(
                "verify-code", {"kind": "recovery", "code": self.codes[0]}
            ).status_code,
            400,
        )
        self.assertEqual(
            self.post(
                "verify-code", {"kind": "recovery", "code": self.codes[1]}
            ).status_code,
            200,
        )

    def test_origin_expiry_and_password_change(self):
        self.start()
        self.assertEqual(
            self.client.post(
                "/v1/auth/mfa/state",
                headers={"Origin": "https://evil.example"},
                json={},
            ).status_code,
            403,
        )
        with self.Session() as db:
            row = db.query(MfaChallenge).filter_by(user_id=self.uid).one()
            row.expires_at = mfa.now() - timedelta(seconds=1)
            db.commit()
        self.assertEqual(self.post("state").status_code, 401)
        self.start()
        with self.Session() as db:
            db.get(User, self.uid).password_hash = hash_password("another-password-123")
            db.commit()
        self.assertEqual(self.post("finish").status_code, 401)

    def test_policy_blocks_old_local_sessions_but_not_sso(self):
        with self.Session() as db:
            user = db.get(User, self.uid)
            self.assertTrue(
                mfa.local_session_allowed(db, user, {"auth_method": "local"})
            )
            with patch.object(settings, "mfa_policy", "all"):
                self.assertFalse(
                    mfa.local_session_allowed(db, user, {"auth_method": "local"})
                )
                self.assertTrue(
                    mfa.local_session_allowed(db, user, {"auth_method": "oidc"})
                )
            with patch.object(settings, "mfa_policy", "admins"):
                self.assertFalse(mfa.required(db, user))
                user.role = "admin"
                self.assertTrue(mfa.required(db, user))
        with patch.object(settings, "mfa_policy", "all"):
            self.assertTrue(self.login().json()["mfa_required"])
            self.assertEqual(self.post("state").json()["stage"], "enrol")
            self.assertEqual(self.post("finish").status_code, 403)

    def test_factor_change_revokes_existing_sessions(self):
        sid = create_session(
            self.redis,
            user_id=str(self.uid),
            ttl_seconds=300,
            auth_method="local",
            mfa_version=0,
        )
        self.client.cookies.set(settings.session_cookie_name, sid)
        self.assertTrue(self.client.get("/protected").json()["authenticated"])
        self.enrol()
        self.assertFalse(self.client.get("/protected").json()["authenticated"])

    def test_rate_limit_and_missing_key_fail_closed(self):
        self.start()
        with patch.object(settings, "mfa_encryption_key", ""):
            self.assertEqual(self.post("totp/start").status_code, 503)
        for _ in range(16):
            result = self.post("totp/start")
        self.assertEqual(result.status_code, 429)
        with patch.object(mfa, "get_valkey", side_effect=RuntimeError("offline")):
            self.assertEqual(self.post("totp/start").status_code, 503)

    def test_management_requires_password_and_factor(self):
        self.assertEqual(
            self.post("manage/start", {"password": self.password}).status_code, 401
        )
        self.assertEqual(self.login().status_code, 200)
        self.assertEqual(
            self.post("manage/start", {"password": "wrong"}).status_code, 401
        )
        self.assertEqual(
            self.post("manage/start", {"password": self.password}).status_code, 200
        )
        self.enrol()
        self.post("finish")
        self.assertEqual(
            self.post("manage/start", {"password": self.password}).status_code, 200
        )
        self.assertEqual(self.post("recovery/regenerate").status_code, 403)
        self.assertEqual(self.post("remove", {"id": "totp"}).status_code, 403)

    def test_required_factor_cannot_be_removed_and_codes_replace(self):
        self.enrol()
        with patch.object(settings, "mfa_policy", "all"):
            self.assertEqual(self.post("remove", {"id": "totp"}).status_code, 400)
        result = self.post("recovery/regenerate")
        self.assertEqual(result.status_code, 200)
        self.start("login")
        self.assertEqual(
            self.post(
                "verify-code", {"kind": "recovery", "code": self.codes[0]}
            ).status_code,
            400,
        )
        self.assertEqual(
            self.post(
                "verify-code",
                {"kind": "recovery", "code": result.json()["recovery_codes"][0]},
            ).status_code,
            200,
        )

    def test_removing_factors_keeps_the_rest_and_the_last_turns_mfa_off(self):
        self.enrol()
        # Adding a factor needs the existing one first.
        self.start()
        self.allow_next_totp()
        verified = self.post(
            "verify-code", {"kind": "totp", "code": pyotp.TOTP(self.secret).now()}
        )
        self.assertEqual(verified.status_code, 200, verified.text)
        private, cid, credential = self.webauthn_registration(start=False)
        added = self.post(
            "webauthn/register", {"credential": credential, "name": "Desk key"}
        )
        self.assertEqual(added.status_code, 200, added.text)
        # A further factor issues no new recovery codes.
        self.assertIsNone(added.json()["recovery_codes"])
        with self.Session() as db:
            (key,) = db.query(MfaCredential).filter_by(user_id=self.uid).all()
            key_id = str(key.id)

        self.assertEqual(self.post("remove", {"id": "totp"}).status_code, 200)
        with self.Session() as db:
            user = db.get(User, self.uid)
            self.assertIsNone(user.mfa_totp_secret)
            self.assertTrue(user.mfa_enabled)
            self.assertEqual(
                db.query(MfaRecoveryCode).filter_by(user_id=self.uid).count(), 10
            )
        missing = self.post("remove", {"id": str(uuid.uuid4())})
        self.assertEqual(missing.status_code, 404)

        self.assertEqual(self.post("remove", {"id": key_id}).status_code, 200)
        with self.Session() as db:
            user = db.get(User, self.uid)
            self.assertFalse(user.mfa_enabled)
            self.assertEqual(
                db.query(MfaRecoveryCode).filter_by(user_id=self.uid).count(), 0
            )
            self.assertEqual(
                db.query(MfaCredential).filter_by(user_id=self.uid).count(), 0
            )
        refused = self.post("recovery/regenerate")
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.json()["detail"], "Enrol an authenticator first")

    def test_cancel_and_logout_invalidate_pending_challenge(self):
        self.start()
        token = self.client.cookies.get(mfa.COOKIE)
        self.assertEqual(self.post("cancel").status_code, 200)
        self.client.cookies.set(mfa.COOKIE, token, domain="keen.example", path="/")
        self.assertEqual(self.post("state").status_code, 401)
        self.start()
        token = self.client.cookies.get(mfa.COOKIE)
        self.assertEqual(self.client.post("/v1/auth/logout").status_code, 204)
        self.client.cookies.set(mfa.COOKIE, token, domain="keen.example", path="/")
        self.assertEqual(self.post("state").status_code, 401)

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
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(client.get("/v1/auth/check").status_code, 401)
            state = client.post("/v1/auth/mfa/state", json={})
            self.assertEqual(state.status_code, 200, state.text)
            self.assertEqual(state.headers.get("cache-control"), "no-store")
            self.assertEqual(
                client.post(
                    "/v1/auth/mfa/state",
                    headers={"Origin": "https://evil.example"},
                    json={},
                ).status_code,
                403,
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
                self.post(
                    "verify-code", {"kind": "recovery", "code": self.codes[0]}
                ).status_code,
                200,
            )
            self.assertEqual(self.post("finish").status_code, 200)
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
            self.assertTrue(send.called)
            self.assertIn("recovery codes", send.call_args.kwargs["body"])
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
            self.assertTrue(any("password was changed" in x for x in subjects))
            self.assertTrue(any("password was reset" in x for x in subjects))

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
        self.assertEqual(
            self.post("webauthn/register", {"credential": credential}).status_code, 400
        )
        self.start("login")
        options = self.post("webauthn/options").json()
        assertion = self.assertion(private, cid, options)
        self.assertEqual(
            self.post("webauthn/verify", {"credential": assertion}).status_code, 200
        )
        self.assertEqual(
            self.post("webauthn/verify", {"credential": assertion}).status_code, 400
        )
        self.assertEqual(self.post("finish").status_code, 200)
        self.assertTrue(self.client.get("/protected").json()["authenticated"])

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
                self.post("webauthn/verify", {"credential": assertion}).status_code, 400
            )
            self.assertEqual(self.post("finish").status_code, 403)

    def test_verification_and_management_steps_need_their_own_stage(self):
        # Management-only steps are refused during verification, and
        # verification steps are refused during management.
        self.start()
        refused = self.post("verify-code", {"code": "123456"})
        self.assertEqual(refused.status_code, 400)
        self.assertIn("Start a second-factor verification first", refused.text)
        self.assertEqual(self.post("webauthn/options").status_code, 400)
        confirm = self.post("totp/confirm", {"code": "123456"})
        self.assertEqual(confirm.status_code, 400)
        self.assertIn("Start authenticator setup first", confirm.text)
        with self.Session() as db:
            self.assertIsNone(db.get(User, self.uid).mfa_totp_secret)
            self.assertFalse(db.get(User, self.uid).mfa_enabled)

    def test_a_second_authenticator_needs_the_first_removed(self):
        self.enrol()
        refused = self.post("totp/start")
        self.assertEqual(refused.status_code, 409)
        self.assertIn("Remove the existing authenticator", refused.text)
        self.assertEqual(
            self.post(
                "totp/confirm", {"code": pyotp.TOTP(self.secret).now()}
            ).status_code,
            400,
        )
        with self.Session() as db:
            row = db.query(MfaChallenge).filter_by(user_id=self.uid).one()
            self.assertNotIn("totp_secret", row.data)

    def test_security_key_verification_needs_a_registered_key_and_a_challenge(self):
        self.enrol()
        self.start("login")
        refused = self.post("webauthn/options")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("No security key or passkey is registered", refused.text)
        # Without an issued challenge no assertion can verify.
        private = ec.generate_private_key(ec.SECP256R1())
        cid = os.urandom(32)
        stray = self.assertion(private, cid, {"challenge": mfa.b64(os.urandom(32))})
        self.assertEqual(
            self.post("webauthn/verify", {"credential": stray}).status_code, 400
        )
        self.assertEqual(self.post("finish").status_code, 403)

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
            self.post("webauthn/verify", {"credential": foreign}).status_code, 400
        )
        self.assertEqual(self.post("finish").status_code, 403)
        # The ceremony is consumed: the genuine key needs a fresh challenge.
        genuine = self.assertion(private, cid, options)
        self.assertEqual(
            self.post("webauthn/verify", {"credential": genuine}).status_code, 400
        )

    def test_at_most_ten_security_keys_and_no_duplicate_registrations(self):
        private, cid, credential = self.webauthn_registration()
        self.assertEqual(
            self.post("webauthn/register", {"credential": credential}).status_code, 200
        )
        # Re-registering the same authenticator in a fresh ceremony is a conflict.
        _, _, again = self.webauthn_registration(start=False, private=private, cid=cid)
        duplicate = self.post("webauthn/register", {"credential": again})
        self.assertEqual(duplicate.status_code, 409, duplicate.text)
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
        self.assertEqual(refused.status_code, 400)
        self.assertIn("maximum of ten", refused.text)


class SecurityIpTests(unittest.TestCase):
    """Proxy trust and IP normalisation do not require a database."""

    def test_security_ip_trust_and_ipv6_normalisation(self):
        from app.security.client_ip import normalise_ip, request_ip

        request = Request(
            {
                "type": "http",
                "headers": [(b"x-forwarded-for", b"192.0.2.99, 203.0.113.8, 10.0.0.2")],
                "client": ("10.0.0.3", 1),
            }
        )
        with patch.object(settings, "security_trusted_proxy_cidrs", ""):
            self.assertEqual(request_ip(request), "10.0.0.3")
        with patch.object(settings, "security_trusted_proxy_cidrs", "10.0.0.0/24"):
            self.assertEqual(request_ip(request), "203.0.113.8")
        self.assertEqual(normalise_ip("::ffff:203.0.113.8"), "203.0.113.8")


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
        refused = ceremony.code(client, value, kind)
        assert refused.status_code == 400
        assert refused.json()["detail"] == "Invalid or already-used code"

    ended = ceremony.code(client, ceremony.wrong[3])

    assert ended.status_code == 401
    assert ended.json()["detail"] == mfa_operations.CODES_REFUSED
    # The right code no longer completes the ended ceremony.
    assert ceremony.code(client, ceremony.good).status_code == 401
    origin = {"Origin": BASE}
    assert client.post("/v1/auth/mfa/finish", headers=origin).status_code == 401
    assert client.get("/v1/auth/check").status_code == 401
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
    assert refused.status_code == 401
    assert refused.json()["detail"] == mfa_operations.CODES_REFUSED
    assert len(ceremony.mail()) == 1

    # Once the hour has passed, the right code completes a new sign-in.
    user = db.get(User, ceremony.user.id)
    user.mfa_code_failures_since -= mfa.CODE_LOCKOUT
    db.commit()
    client = ceremony.sign_in(client)
    assert ceremony.code(client, ceremony.good).status_code == 200
    assert client.post("/v1/auth/mfa/finish", headers=origin).status_code == 200
    assert client.get("/v1/auth/check").status_code == 204


def test_wrong_codes_count_across_sign_ins(ceremony):
    """Starting again does not reset the count, so guesses stay bounded."""
    client = ceremony.sign_in()
    for value in ceremony.wrong[:4]:
        assert ceremony.code(client, value).status_code == 400

    client = ceremony.sign_in(client)
    ended = ceremony.code(client, ceremony.wrong[4 % len(ceremony.wrong)])

    assert ended.status_code == 401
    assert ended.json()["detail"] == mfa_operations.CODES_REFUSED
    assert [n.subject for n in ceremony.mail()] == [
        "Keen security: Keen ended a two-factor verification after repeated "
        "incorrect codes"
    ]
    # A third sign-in gets no guess at all.
    client = ceremony.sign_in(client)
    assert ceremony.code(client, ceremony.wrong[0]).status_code == 401
    ceremony.keen.db.expire_all()
    assert ceremony.keen.db.get(User, ceremony.user.id).mfa_code_failures == 5


def test_fewer_wrong_codes_leave_the_ceremony_usable(ceremony):
    client = ceremony.sign_in()
    for value in ceremony.wrong[:4]:
        assert ceremony.code(client, value).status_code == 400
    # Failed security-key assertions are not guesses: each consumes its own
    # challenge, and a forged one needs the key's private half.
    for _ in range(3):
        failed = client.post(
            "/v1/auth/mfa/webauthn/verify",
            json={"credential": {"id": "unknown"}},
            headers={"Origin": BASE},
        )
        assert failed.status_code == 400

    assert ceremony.code(client, ceremony.good).status_code == 200
    origin = {"Origin": BASE}
    assert client.post("/v1/auth/mfa/finish", headers=origin).status_code == 200
    assert not any("two-factor verification" in n.subject for n in ceremony.mail())
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

    assert used.status_code == 200
    notices = [n for n in ceremony.mail() if "recovery code was used" in n.subject]
    assert [n.subject for n in notices] == [
        "Keen security: A recovery code was used for your Keen account"
    ]
    assert ceremony.recovery[0] not in notices[0].body
    assert ceremony.recovery[1] not in notices[0].body
    # The code is single use: a second ceremony cannot reuse it.
    client = ceremony.sign_in(ceremony.keen.client())
    assert ceremony.code(client, ceremony.recovery[0], "recovery").status_code == 400


def test_a_refused_second_factor_start_still_audits_the_account(ceremony):
    """A right password whose second factor cannot start is still recorded."""
    client = ceremony.keen.client()

    refused = client.post(
        "/v1/auth/login",
        json={"username": ceremony.user.username, "password": PASSWORD},
    )

    assert refused.status_code == 403
    db = ceremony.keen.db
    db.expire_all()
    rows = (
        db.query(AuditLog)
        .filter_by(path="/action/sign-in", username=ceremony.user.username)
        .all()
    )
    assert [row.query_string for row in rows] == ["method=local&outcome=mfa-refused"]
    assert db.query(MfaChallenge).filter_by(user_id=ceremony.user.id).count() == 0
