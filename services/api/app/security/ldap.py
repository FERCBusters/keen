"""Read-only directory authentication. Credentials are never persisted."""

import base64
import re
import ssl
import uuid
from urllib.parse import urlsplit

from fastapi import HTTPException
from ldap3 import ANONYMOUS, DEREF_NEVER, NONE, SIMPLE, SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import parse_dn
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.models import User, UserIdentity


def directory_identity(username, password):
    if (
        not settings.ldap_enabled
        or not username
        or not password
        or len(username) > 128
        or len(password) > 1024
    ):
        return None
    search = bound = None
    try:
        url = urlsplit(settings.ldap_url)
        if (
            url.scheme not in {"ldap", "ldaps"}
            or not url.hostname
            or url.username
            or url.password
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise ValueError("Invalid LDAP URL")
        if not settings.ldap_base_dn.strip():
            raise ValueError("LDAP search base is required")
        parse_dn(settings.ldap_base_dn)
        attrs = [
            settings.ldap_username_attribute,
            settings.ldap_email_attribute,
            settings.ldap_id_attribute,
        ]
        if any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", a) for a in attrs):
            raise ValueError("Invalid LDAP attribute")
        if bool(settings.ldap_bind_dn) != bool(settings.ldap_bind_password):
            raise ValueError("Supply both service bind DN and password")
        tls = Tls(
            validate=ssl.CERT_REQUIRED, ca_certs_file=settings.ldap_ca_cert_path or None
        )
        server = Server(
            url.hostname,
            port=url.port or (636 if url.scheme == "ldaps" else 389),
            use_ssl=url.scheme == "ldaps",
            tls=tls,
            get_info=NONE,
            connect_timeout=settings.ldap_timeout_seconds,
        )

        def connect(dn, secret):
            c = Connection(
                server,
                user=dn or None,
                password=secret or None,
                authentication=SIMPLE if dn else ANONYMOUS,
                auto_referrals=False,
                read_only=True,
                receive_timeout=settings.ldap_timeout_seconds,
                raise_exceptions=False,
            )
            try:
                c.open()
                if c.closed or (url.scheme == "ldap" and not c.start_tls()):
                    raise ValueError("Secure LDAP connection failed")
                if not c.bind():
                    if c.result.get("result") == 49:
                        c.unbind()
                        return None
                    raise ValueError("Directory bind failed")
                return c
            except Exception:
                c.unbind()
                raise

        search = connect(settings.ldap_bind_dn, settings.ldap_bind_password)
        if search is None:
            raise ValueError("Service bind failed")
        query = f"(&{settings.ldap_user_filter}({attrs[0]}={escape_filter_chars(username.strip())}))"
        search.search(
            settings.ldap_base_dn,
            query,
            search_scope=SUBTREE,
            attributes=attrs,
            dereference_aliases=DEREF_NEVER,
            size_limit=2,
            time_limit=settings.ldap_timeout_seconds,
        )
        # No partial results, referrals, or ambiguous identities are accepted.
        if search.result.get("result") != 0 or len(search.entries) != 1:
            return None
        entry = search.entries[0]
        values = entry.entry_attributes_as_dict

        def single(name):
            value = values.get(name, [])
            if not isinstance(value, list):
                value = [value]
            return value[0] if len(value) == 1 else None

        uid, email, subject = [single(a) for a in attrs]
        if isinstance(subject, bytes):
            subject = base64.b64encode(subject).decode("ascii")
        if (
            not isinstance(subject, str)
            or not subject
            or len(subject) > 512
            or not isinstance(uid, str)
            or not uid
            or len(uid) > 128
        ):
            return None
        bound = connect(entry.entry_dn, password)
        if bound is None:
            return None
        issuer = f"ldap:{url.scheme}://{url.hostname.lower()}:{url.port or (636 if url.scheme == 'ldaps' else 389)}"
        if len(issuer) > 512:
            raise ValueError("LDAP URL too long")
        return {
            "issuer": issuer,
            "subject": subject,
            "username": uid,
            "email": email
            if isinstance(email, str) and len(email) <= 256 and "@" in email
            else None,
        }
    except (LDAPException, ValueError, OSError):
        # Library exceptions may contain DNs or server diagnostics; keep them out of responses.
        raise HTTPException(
            503,
            "LDAP authentication unavailable; check directory configuration and connectivity",
        ) from None
    finally:
        for connection in (bound, search):
            if connection is not None:
                connection.unbind()


def authenticate_ldap(db, username, password):
    identity = directory_identity(username, password)
    if not identity:
        return None
    row = (
        db.query(UserIdentity)
        .filter_by(
            provider="ldap", issuer=identity["issuer"], subject=identity["subject"]
        )
        .one_or_none()
    )
    if row:
        user = row.user
        if not user.is_active or user.auth_backend != "ldap":
            return None
        row.preferred_username = identity["username"]
        row.email = identity["email"]
        db.commit()
        return user
    if not settings.ldap_auto_provision:
        return None
    # A new directory identity never inherits an existing local account or its privileges.
    name = "ldap:" + identity["username"]
    if len(name) > 128 or db.query(User.id).filter_by(username=name).first():
        name = "ldap:" + uuid.uuid4().hex
    user = User(
        username=name,
        password_hash="!LDAP",
        auth_backend="ldap",
        email=identity["email"],
        role="normal",
        is_active=True,
    )
    try:
        db.add(user)
        db.flush()
        db.add(
            UserIdentity(
                user_id=user.id,
                provider="ldap",
                issuer=identity["issuer"],
                subject=identity["subject"],
                preferred_username=identity["username"],
                email=identity["email"],
            )
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            409, "Account was created concurrently; please sign in again"
        ) from None
    return user


def verify_ldap_user(db, user, password):
    row = (
        db.query(UserIdentity).filter_by(user_id=user.id, provider="ldap").one_or_none()
    )
    if not row:
        return False
    identity = directory_identity(row.preferred_username, password)
    return bool(
        identity
        and identity["issuer"] == row.issuer
        and identity["subject"] == row.subject
    )
