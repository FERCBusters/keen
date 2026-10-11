"""Immutable, server-derived actor and provenance supplied by entry points."""

from dataclasses import dataclass

from app.db.models import User


@dataclass(frozen=True)
class ChangeContext:
    method: str | None = None
    path: str | None = None
    actor: User | None = None
    client_ip: str | None = None
    user_agent: str | None = None
    referer: str | None = None
