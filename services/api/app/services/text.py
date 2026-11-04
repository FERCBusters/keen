"""Plain-text request values, trimmed and length-checked."""

from __future__ import annotations

import uuid

from app.core.email_address import single_mailbox
from app.core.references import try_uuid
from app.services.errors import DomainError


def clean_text(
    raw: str | None,
    *,
    max_len: int,
    required: bool = False,
    label: str = "value",
    single_line: bool = False,
) -> str:
    """Return ``raw`` trimmed, refusing a missing required value or one too long.

    ``single_line`` folds line breaks and runs of spaces into single spaces, for
    titles that reach email subjects and calendar entries.
    """
    value = (raw or "").strip()
    if single_line:
        value = " ".join(value.split())
    if required and not value:
        raise DomainError(status_code=400, detail=f"{label} is required")
    if len(value) > max_len:
        raise DomainError(status_code=400, detail=f"{label} is too long")
    return value


def clean_email(
    raw: str | None, *, label: str = "email address", required: bool = False
) -> str | None:
    """Return ``raw`` as exactly one email address, or None when it is blank.

    Recipient fields reach SMTP headers, where a list, display name or comment
    would add recipients, so every stored address is one mailbox
    (app.core.email_address.single_mailbox).
    """
    if not (raw or "").strip():
        if required:
            raise DomainError(status_code=400, detail=f"{label} is required")
        return None
    email = single_mailbox(raw)
    if email is None:
        raise DomainError(status_code=400, detail=f"Invalid {label}")
    return email


def split_filter_values(value: str | None) -> list[str]:
    """Split a comma-separated query filter into its non-blank values."""
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def uuid_filter_values(value: str | None, *, label: str) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    for part in split_filter_values(value):
        parsed = try_uuid(part)
        if not parsed:
            raise DomainError(
                status_code=400, detail=f"{label} must contain UUID values"
            )
        out.append(parsed)
    return out
