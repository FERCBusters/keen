"""Fail-closed owner restriction for dedicated hosted KEEN installations."""

from fastapi import HTTPException
from app.core.config import settings


def enforce_hosted_identity(provider_key: str, issuer: str, subject: str) -> None:
    if not settings.hosted_mode:
        return
    if (
        provider_key != "oidc"
        or issuer != settings.oidc_issuer
        or subject != settings.hosted_owner_subject
    ):
        raise HTTPException(
            status_code=403, detail="This identity cannot access this workspace"
        )


def hosted_session_allowed(db, user, session: dict) -> bool:
    if not settings.hosted_mode:
        return True
    if session.get("auth_method") != "oidc":
        return False
    from app.db.models import UserIdentity

    return (
        db.query(UserIdentity)
        .filter(
            UserIdentity.user_id == user.id,
            UserIdentity.provider == "oidc",
            UserIdentity.issuer == settings.oidc_issuer,
            UserIdentity.subject == settings.hosted_owner_subject,
        )
        .one_or_none()
        is not None
    )
