"""Subject pseudonymisation keys for UEU evidence.

Pairwise subjects use a key each agent generates and keeps, so the same
account on two devices yields unrelated pseudonyms. Public subjects use one
key KEEN issues to every agent configured for them, so the same account
yields the same pseudonym on every such device. KEEN stores that key
encrypted with the integration secret key and sends it only to agents in an
enrolment or renewal response.
"""

from __future__ import annotations

import secrets

from cryptography.fernet import InvalidToken

from app.db.models import UeuSettings
from app.integrations.errors import IntegrationError
from app.integrations.transport import decrypt, encrypt
from app.security.agent_signatures import b64url, unb64url
from app.services.errors import DomainError


def public_subject_key(db) -> str:
    """Return the public subject key, creating it on first use."""
    row = (
        db.query(UeuSettings).filter_by(id=1).populate_existing().with_for_update()
    ).one_or_none()
    try:
        if row is not None and row.public_subject_key:
            key = decrypt(row.public_subject_key)["key"]
            if len(unb64url(key)) != 32:
                raise ValueError("invalid key")
            return key
        key = b64url(secrets.token_bytes(32))
        sealed = encrypt({"key": key})
    except IntegrationError:
        raise DomainError(
            503,
            "Public subjects need KEEN_INTEGRATION_SECRET_KEY on the API",
        ) from None
    except (InvalidToken, ValueError, KeyError, TypeError):
        raise DomainError(
            503,
            "The public subject key cannot be read with the configured "
            "KEEN_INTEGRATION_SECRET_KEY",
        ) from None
    if row is None:
        db.add(UeuSettings(id=1, public_subject_key=sealed))
    else:
        row.public_subject_key = sealed
    db.flush()
    return key


def subject_grant(db, subject_type: str) -> dict:
    if subject_type == "public":
        return {"type": "public", "key": public_subject_key(db)}
    return {"type": "pairwise"}
