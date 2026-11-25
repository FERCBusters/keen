"""MIDENA diagrams shown with ISMS policy and process documents.

An operator connects Keen to a MIDENA installation with KEEN_MIDENA_BASE_URL
and KEEN_MIDENA_API_TOKEN, a token a MIDENA administrator issued. The token
reads as the MIDENA account it was issued for, so Keen finds and shows only
the diagrams that account can read, and MIDENA refuses it anything but
listing and opening diagrams.

Keen keeps the drawing of the version it captured, so a document shows that
version until an ISMS manager refreshes it, and still shows it while MIDENA
is unreachable or the diagram is gone. MIDENA's answers are untrusted: they
are read through the collectors' HTTP boundary (``app.ingest.http``: HTTPS
with verified certificates, the destination resolved and pinned, no
redirects, no proxy environment, no cookies kept, one deadline and a bounded
answer), decoded with ``decode_json``, clipped to each column, and the
drawing is rebuilt by ``mosp.security.svg`` before it is stored. Pages show
it only as an image. No database transaction spans a request to MIDENA.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import httpx2 as httpx
from mosp.core.datetime_utils import iso_utc, utc_now_naive
from mosp.core.identifiers import try_uuid
from mosp.security.json_shape import decode_json
from mosp.security.svg import SvgError, sanitise_svg
from mosp.services.context import ChangeContext
from mosp.services.errors import DomainError
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import IsmsDocument, IsmsDocumentDiagram, User
from app.ingest.http import client as ingestion_client
from app.ingest.http import no_cookies
from app.services.frameworks import framework_parameter

from .common import _by_id_or_404, _record
from .serializers import _document_out, _user_summary

MAX_DIAGRAMS_PER_DOCUMENT = 20
# MIDENA stores drawings of at most 3 MiB.
MAX_SVG_BYTES = 3 * 1024 * 1024
CATALOG_PAGE = 50
MAX_CATALOG_OFFSET = 10_000


class DiagramInput(BaseModel):
    diagram_uuid: uuid.UUID


def status() -> dict[str, Any]:
    return {
        "enabled": settings.midena_enabled,
        "base_url": settings.midena_base_url or None,
    }


def _require_enabled() -> None:
    if not settings.midena_enabled:
        raise DomainError(409, "MIDENA is not configured for this Keen")


def _client() -> httpx.Client:
    return ingestion_client(
        base_url=f"{settings.midena_base_url}/api",
        headers={
            "Authorization": f"Bearer {settings.midena_api_token}",
            "Accept": "application/json",
        },
        timeout=30.0,
        cookies=no_cookies(),
    )


def _read(path: str, params: dict[str, Any] | None = None) -> Any:
    """MIDENA's decoded answer to one GET, or a DomainError naming the failure
    without MIDENA's response body."""
    try:
        with _client() as client:
            response = client.get(path, params=params)
            if response.status_code in (401, 403):
                raise DomainError(
                    502,
                    "MIDENA refused Keen's API token. Ask a MIDENA administrator "
                    "for a current token.",
                )
            if response.status_code == 404:
                raise DomainError(
                    404,
                    "MIDENA has no such diagram, or the token's account cannot read it",
                )
            if response.status_code != 200:
                raise DomainError(
                    502, f"MIDENA answered with status {response.status_code}"
                )
            return decode_json(response.content)
    except (ValueError, OSError, httpx.HTTPError) as exc:
        raise DomainError(
            502, "Could not read from the configured MIDENA installation"
        ) from exc


def _text(value: Any, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _count(value: Any) -> int | None:
    # bool is an int; a version number is not.
    if type(value) is int and 0 <= value <= 2_147_483_647:
        return value
    return None


def catalog(*, q: str, offset: int) -> dict[str, Any]:
    """A page of the MIDENA diagrams the token's account can read."""
    _require_enabled()
    offset = max(0, min(offset, MAX_CATALOG_OFFSET))
    data = _read(
        "/v1/diagrams",
        {"q": q.strip()[:200], "limit": CATALOG_PAGE, "offset": offset},
    )
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise DomainError(502, "MIDENA returned an unexpected diagram list")
    items = []
    for item in data["items"][:CATALOG_PAGE]:
        found = try_uuid(item.get("uuid")) if isinstance(item, dict) else None
        if found is None:
            continue
        items.append(
            {
                "uuid": str(found),
                "name": _text(item.get("name"), 256) or "Untitled diagram",
                "diagram_type": _text(item.get("diagram_type"), 32),
                "folder_name": _text(item.get("folder_name"), 256) or None,
                "version_number": _count(item.get("current_version_number")),
                "updated_at": _text(item.get("updated_at"), 64),
            }
        )
    total = _count(data.get("total"))
    return {
        "items": items,
        "total": len(items) if total is None else total,
        "offset": offset,
        "limit": CATALOG_PAGE,
    }


def _fetch(diagram_uuid: uuid.UUID) -> dict[str, Any]:
    """The current version of one MIDENA diagram, as Keen stores it."""
    data = _read(f"/v1/diagrams/{diagram_uuid}")
    if not isinstance(data, dict) or try_uuid(data.get("uuid")) != diagram_uuid:
        raise DomainError(502, "MIDENA returned an unexpected diagram")
    drawing = data.get("svg_xml")
    if not isinstance(drawing, str) or not drawing.strip():
        raise DomainError(
            409, "This diagram has no saved drawing in MIDENA yet. Save it there first."
        )
    try:
        svg = sanitise_svg(drawing, max_bytes=MAX_SVG_BYTES)
    except SvgError as exc:
        raise DomainError(502, "MIDENA returned a drawing Keen cannot show") from exc
    return {
        "name": _text(data.get("name"), 256) or "Untitled diagram",
        "diagram_type": _text(data.get("diagram_type"), 32),
        "description": _text(data.get("description"), 20_000),
        "version_number": _count(data.get("current_version_number")),
        "source_updated_at": _text(data.get("updated_at"), 64),
        "svg": svg,
        "sha256": hashlib.sha256(svg.encode("utf-8")).hexdigest(),
    }


def _midena_url(row: IsmsDocumentDiagram) -> str | None:
    base = settings.midena_base_url
    return f"{base}/diagram.html?id={row.diagram_uuid}" if base else None


def _out(row: IsmsDocumentDiagram, *, svg: bool = False) -> dict[str, Any]:
    out = {
        "id": str(row.id),
        "document_id": str(row.document_id),
        "diagram_uuid": str(row.diagram_uuid),
        "name": row.name,
        "diagram_type": row.diagram_type,
        "description": row.description,
        "version_number": row.version_number,
        "source_updated_at": row.source_updated_at,
        "sha256": row.sha256,
        "captured_at": iso_utc(row.captured_at),
        "captured_by": _user_summary(row.captured_by),
        "midena_url": _midena_url(row),
    }
    if svg:
        out["svg"] = row.svg
    return out


def _document_id(db: Session, document_id: str) -> uuid.UUID:
    return _by_id_or_404(db, IsmsDocument, document_id, "Document").id


def _diagram(
    db: Session, document_id: uuid.UUID, diagram_id: str
) -> IsmsDocumentDiagram:
    row = (
        db.query(IsmsDocumentDiagram)
        .filter(
            IsmsDocumentDiagram.id == try_uuid(diagram_id),
            IsmsDocumentDiagram.document_id == document_id,
        )
        .one_or_none()
    )
    if row is None:
        raise DomainError(404, "Diagram not found on this document")
    return row


def list_diagrams(*, document_id: str, db: Session) -> dict[str, Any]:
    found = _document_id(db, document_id)
    rows = (
        db.query(IsmsDocumentDiagram)
        .filter(IsmsDocumentDiagram.document_id == found)
        .order_by(IsmsDocumentDiagram.captured_at)
        .all()
    )
    return {"items": [_out(row) for row in rows], **status()}


def get_diagram(*, document_id: str, diagram_id: str, db: Session) -> dict[str, Any]:
    return _out(_diagram(db, _document_id(db, document_id), diagram_id), svg=True)


def _check_attachable(db: Session, document_id: uuid.UUID, diagram_uuid) -> None:
    shown = (
        db.query(IsmsDocumentDiagram.diagram_uuid)
        .filter(IsmsDocumentDiagram.document_id == document_id)
        .all()
    )
    if any(row.diagram_uuid == diagram_uuid for row in shown):
        raise DomainError(409, "This document already shows that diagram")
    if len(shown) >= MAX_DIAGRAMS_PER_DOCUMENT:
        raise DomainError(
            409,
            f"A document shows at most {MAX_DIAGRAMS_PER_DOCUMENT} diagrams",
        )


def attach_diagram(
    *,
    document_id: str,
    payload: DiagramInput,
    framework: str,
    user: User,
    db: Session,
    context: ChangeContext,
) -> dict[str, Any]:
    _require_enabled()
    fw = framework_parameter(framework)
    found = _document_id(db, document_id)
    _check_attachable(db, found, payload.diagram_uuid)
    db.commit()  # No database transaction spans the request to MIDENA.
    fetched = _fetch(payload.diagram_uuid)
    document = _by_id_or_404(db, IsmsDocument, str(found), "Document", lock=True)
    # Checked again under the document's lock: another capture may have
    # finished while MIDENA answered.
    _check_attachable(db, found, payload.diagram_uuid)
    before = _document_out(db, document, fw)
    now = utc_now_naive()
    row = IsmsDocumentDiagram(
        document_id=found,
        diagram_uuid=payload.diagram_uuid,
        captured_at=now,
        captured_by_user_id=user.id,
        **fetched,
    )
    db.add(row)
    document.updated_at = now
    db.flush()
    db.expire(document, ["diagrams"])
    _record(
        db,
        "document",
        document,
        "updated",
        before,
        _document_out(db, document, fw),
        user,
        context,
    )
    db.commit()
    db.refresh(row)
    return _out(row)


def refresh_diagram(
    *,
    document_id: str,
    diagram_id: str,
    framework: str,
    user: User,
    db: Session,
    context: ChangeContext,
) -> dict[str, Any]:
    """Capture MIDENA's current version in place of the one shown."""
    _require_enabled()
    fw = framework_parameter(framework)
    found = _document_id(db, document_id)
    diagram_uuid = _diagram(db, found, diagram_id).diagram_uuid
    db.commit()  # No database transaction spans the request to MIDENA.
    fetched = _fetch(diagram_uuid)
    document = _by_id_or_404(db, IsmsDocument, str(found), "Document", lock=True)
    row = _diagram(db, found, diagram_id)
    before = _document_out(db, document, fw)
    now = utc_now_naive()
    for key, value in fetched.items():
        setattr(row, key, value)
    row.captured_at = now
    row.captured_by_user_id = user.id
    db.flush()
    after = _document_out(db, document, fw)
    if after != before:
        document.updated_at = now
        _record(db, "document", document, "updated", before, after, user, context)
    db.commit()
    db.refresh(row)
    return _out(row)


def detach_diagram(
    *,
    document_id: str,
    diagram_id: str,
    framework: str,
    user: User,
    db: Session,
    context: ChangeContext,
) -> dict[str, Any]:
    fw = framework_parameter(framework)
    document = _by_id_or_404(db, IsmsDocument, document_id, "Document", lock=True)
    row = _diagram(db, document.id, diagram_id)
    before = _document_out(db, document, fw)
    db.delete(row)
    document.updated_at = utc_now_naive()
    db.flush()
    db.expire(document, ["diagrams"])
    _record(
        db,
        "document",
        document,
        "updated",
        before,
        _document_out(db, document, fw),
        user,
        context,
    )
    db.commit()
    return {"ok": True}
