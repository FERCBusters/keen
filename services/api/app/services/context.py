"""Immutable, server-derived actor and provenance supplied by entry points."""

from dataclasses import dataclass

from app.db.models import User
from app.services.errors import DomainError


@dataclass(frozen=True)
class ChangeContext:
    method: str | None = None
    path: str | None = None
    actor: User | None = None
    client_ip: str | None = None
    user_agent: str | None = None
    referer: str | None = None


def signed_in_user(context: ChangeContext) -> User:
    """The account a request acts for; without one, DomainError 401."""
    if not isinstance(context.actor, User):
        raise DomainError(status_code=401, detail="Not authenticated")
    return context.actor
