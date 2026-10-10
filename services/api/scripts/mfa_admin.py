#!/usr/bin/env python3
"""Explicit operator recovery for a local account (run inside keen-api)."""

import argparse

from app.db.models import MfaChallenge, MfaCredential, MfaRecoveryCode, User
from app.db.session import SessionLocal
from app.security.mfa import audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username")
    parser.add_argument("--reset", action="store_true", required=True)
    parser.add_argument("--confirm-username", required=True)
    args = parser.parse_args()
    if args.username != args.confirm_username:
        parser.error("Confirmation must exactly match the username")
    with SessionLocal() as db:
        user = (
            db.query(User)
            .filter(User.username == args.username)
            .with_for_update()
            .one_or_none()
        )
        if user is None:
            parser.error("User not found")
        for model in (MfaCredential, MfaRecoveryCode, MfaChallenge):
            db.query(model).filter(model.user_id == user.id).delete(
                synchronize_session=False
            )
        user.mfa_enabled = False
        user.mfa_totp_secret = None
        user.mfa_totp_last_step = -1
        user.mfa_version += 1
        audit(db, user, "operator-reset")
        db.commit()
    print(
        "Local authenticators and recovery codes reset; local sessions revoked. Required MFA enrolment applies at next sign-in."
    )


if __name__ == "__main__":
    main()
