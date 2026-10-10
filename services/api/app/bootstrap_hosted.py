"""Run after migrations, before API/worker/beat: python -m app.bootstrap_hosted."""

import os
import secrets

from sqlalchemy import text

from app.core.config import settings
from app.db.models import User, UserIdentity
from app.db.session import SessionLocal
from app.security.passwords import hash_password


def bootstrap(db, *, issuer, subject, email):
    if not subject or not issuer.startswith("https://") or not email:
        raise ValueError("Verified issuer, subject and email required")
    # Transaction-scoped lock: safe against two simultaneous bootstrap invocations.
    db.execute(text("SELECT pg_advisory_xact_lock(70923002)"))
    identity = (
        db.query(UserIdentity).filter_by(issuer=issuer, subject=subject).one_or_none()
    )
    if identity:
        user = db.query(User).filter_by(id=identity.user_id).one_or_none()
        if (
            not user
            or not user.is_active
            or user.role != "admin"
            or identity.provider != "oidc"
        ):
            raise RuntimeError("Existing owner does not match a usable admin identity")
        return user
    if db.query(User).count():
        raise RuntimeError("Refusing to claim an existing KEEN installation")
    user = User(
        username="workspace-owner",
        email=email,
        role="admin",
        is_active=True,
        password_hash=hash_password(secrets.token_urlsafe(48)),
    )
    db.add(user)
    db.flush()
    db.add(
        UserIdentity(
            user_id=user.id,
            provider="oidc",
            issuer=issuer,
            subject=subject,
            email=email,
            claims={},
        )
    )
    db.flush()
    return user


def main():
    if not settings.hosted_mode:
        raise RuntimeError("Hosted mode is required")
    with SessionLocal.begin() as db:
        bootstrap(
            db,
            issuer=settings.oidc_issuer,
            subject=settings.hosted_owner_subject,
            email=os.environ["KEEN_HOSTED_OWNER_EMAIL"],
        )
    print("Hosted workspace owner is ready")


if __name__ == "__main__":
    main()
