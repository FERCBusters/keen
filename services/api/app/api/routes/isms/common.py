"""ISMS common; see docs/maintainability-review.md for module boundaries."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.utils import try_uuid as _try_uuid
from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.db.models import ControlItem, FrameworkClause, User
from app.security.rich_text import sanitize_rich_text_html
from app.services.entity_changelog import record_entity_changelog

from .constants import (
    ENTITY_TYPES,
    ISMS_SECTION_ALIASES,
)


def _clean_isms_section(section: str | None) -> str | None:
    if section is None:
        return None
    raw = str(section or "").strip()
    if not raw:
        return None
    key = raw.lower().replace(" ", "_")
    normalized = key.replace("-", "_")
    value = ISMS_SECTION_ALIASES.get(key) or ISMS_SECTION_ALIASES.get(normalized)
    if not value:
        raise HTTPException(status_code=400, detail="Unknown ISMS section")
    return value


def _utcnow() -> datetime:
    return utc_now_naive()


def _clean_text(
    raw: str | None, *, max_len: int, required: bool = False, label: str = "value"
) -> str:
    value = (raw or "").strip()
    if required and not value:
        raise HTTPException(status_code=400, detail=f"{label} is required")
    if len(value) > max_len:
        raise HTTPException(status_code=400, detail=f"{label} is too long")
    return value


def _clean_minutes_html(raw: str | None) -> str:
    # Trim/length-check the raw input, then apply the shared server-side
    # rich-text allowlist sanitiser (single source of truth for all KEEN
    # rich-text fields; see app.security.rich_text).
    value = _clean_text(raw, max_len=50000)
    cleaned = sanitize_rich_text_html(value)
    if len(cleaned) > 50000:
        raise HTTPException(status_code=400, detail="agenda/minutes/notes is too long")
    return cleaned


def _clean_framework(raw: str | None) -> str:
    value = (raw or settings.default_framework_slug or "").strip()
    if not value or len(value) > 64 or not re.match(r"^[A-Za-z0-9._:-]+$", value):
        raise HTTPException(status_code=400, detail="Invalid framework")
    return value


def _by_id_or_404(db: Session, model, raw_id: str, label: str):
    rid = _try_uuid(raw_id)
    if not rid:
        raise HTTPException(status_code=400, detail=f"{label} id must be a UUID")
    row = db.query(model).filter(model.id == rid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail=f"{label} not found")
    return row


def _control_by_ref_or_id(db: Session, raw: str, framework: str) -> ControlItem:
    value = (raw or "").strip()
    cid = _try_uuid(value)
    qry = db.query(ControlItem).filter(ControlItem.framework_slug == framework)
    row = (
        qry.filter(ControlItem.id == cid).one_or_none()
        if cid
        else qry.filter(func.lower(ControlItem.ref) == value.lower()).one_or_none()
    )
    if not row:
        raise HTTPException(status_code=400, detail=f"Unknown control: {value}")
    return row


def _clause_by_ref_or_id(db: Session, raw: str, framework: str) -> FrameworkClause:
    value = (raw or "").strip()
    cid = _try_uuid(value)
    qry = db.query(FrameworkClause).filter(FrameworkClause.framework_slug == framework)
    row = (
        qry.filter(FrameworkClause.id == cid).one_or_none()
        if cid
        else qry.filter(func.lower(FrameworkClause.ref) == value.lower()).one_or_none()
    )
    if not row:
        raise HTTPException(status_code=400, detail=f"Unknown clause: {value}")
    return row


def _record(
    db: Session,
    entity_type: str,
    row: Any,
    action: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    user: User,
    request: Request | None = None,
) -> None:
    _, changelog_type = ENTITY_TYPES[entity_type]
    record_entity_changelog(
        db,
        entity_type=changelog_type,
        entity_id=row.id,
        action=action,
        before=before,
        after=after,
        user=user,
        request_method=getattr(request, "method", None),
        request_path=str(getattr(getattr(request, "url", None), "path", "") or "")
        or None,
    )


def _list_response(
    items: list[dict[str, Any]], total: int, limit: int, offset: int, framework: str
) -> dict[str, Any]:
    return {
        "framework": framework,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
    }
