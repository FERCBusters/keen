"""Plain-text request values, trimmed and length-checked."""

from __future__ import annotations

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
