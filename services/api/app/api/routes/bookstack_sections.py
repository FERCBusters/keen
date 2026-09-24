"""BookStack policy-section references with retained page-version evidence."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.routes.isms import require_isms_manage, require_isms_read
from app.core.config import settings
from app.db.models import AuditEvidence, BookStackSectionEvidence, ControlItem, FrameworkClause, IsmsDocument
from app.db.session import get_db
from app.ingest.bookstack import _client
from app.security.auth import require_authenticated
from app.security.permissions import has_permission
from app.storage.s3 import get_object_stream, iter_stream, parse_s3_uri, put_bytes

router = APIRouter()


class SectionInput(BaseModel):
    document_id: uuid.UUID
    page_id: int = Field(gt=0)
    anchor: str = Field(default="", max_length=256)
    target_control_id: uuid.UUID | None = None
    target_clause_id: uuid.UUID | None = None


def _out(row):
    return {"id": str(row.id), "document_id": str(row.document_id),
            "document_title": row.document.title,
            "target_control_id": str(row.target_control_id) if row.target_control_id else None,
            "target_clause_id": str(row.target_clause_id) if row.target_clause_id else None,
            "target_framework": (row.target_control.framework_slug if row.target_control else
                                 row.target_clause.framework_slug if row.target_clause else None),
            "target_ref": (row.target_control.ref if row.target_control else
                           row.target_clause.ref if row.target_clause else None),
            "page_id": row.page_id, "page_title": row.page_title, "anchor": row.anchor,
            "permalink": row.permalink, "revision_count": row.revision_count,
            "page_updated_at": row.page_updated_at, "sha256": row.sha256,
            "archived": row.archived, "captured_at": row.captured_at.isoformat()}


@router.get("/v1/isms/bookstack-sections")
def list_sections(document_id: uuid.UUID | None = None, user=Depends(require_isms_read), db: Session = Depends(get_db)):
    qry = db.query(BookStackSectionEvidence)
    if document_id: qry = qry.filter(BookStackSectionEvidence.document_id == document_id)
    return {"items": [_out(row) for row in qry.order_by(BookStackSectionEvidence.captured_at.desc()).limit(500).all()]}


@router.post("/v1/isms/bookstack-sections", status_code=201)
def capture_section(payload: SectionInput, user=Depends(require_isms_manage), db: Session = Depends(get_db)):
    document = db.get(IsmsDocument, payload.document_id)
    if not document: raise HTTPException(400, "Select an ISMS policy or process document")
    if bool(payload.target_control_id) == bool(payload.target_clause_id):
        raise HTTPException(400, "Select exactly one framework control or clause")
    control = db.get(ControlItem, payload.target_control_id) if payload.target_control_id else None
    clause = db.get(FrameworkClause, payload.target_clause_id) if payload.target_clause_id else None
    if payload.target_control_id and not control or payload.target_clause_id and not clause:
        raise HTTPException(400, "Unknown framework target")
    anchor = payload.anchor.strip().removeprefix("#")
    if anchor and not re.fullmatch(r"[A-Za-z0-9_:-]{1,256}", anchor):
        raise HTTPException(400, "Use a BookStack section anchor such as bkmrk-access-review")
    try:
        with _client() as client:
            response = client.get(f"/api/pages/{payload.page_id}")
            response.raise_for_status()
            if len(response.content) > 5_000_000:
                raise HTTPException(413, "BookStack page snapshot exceeds 5 MB")
            page = response.json()
    except (ValueError, httpx.HTTPError) as exc:
        raise HTTPException(502, "Could not read this page from the configured BookStack instance") from exc
    if not isinstance(page, dict) or page.get("id") != payload.page_id:
        raise HTTPException(502, "BookStack returned an unexpected page")
    base_url = settings.bookstack_base_url.rstrip("/")
    permalink = f"{base_url}/link/{payload.page_id}" + (f"#{anchor}" if anchor else "")
    captured = datetime.utcnow()
    snapshot = {"captured_at": captured.isoformat() + "Z", "page_id": payload.page_id,
                "anchor": anchor, "permalink": permalink, "bookstack_page": page}
    raw = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    stored = put_bytes(f"bookstack-policy/{document.id}/{uuid.uuid4()}.json", raw, "application/json")
    row = BookStackSectionEvidence(document_id=document.id, page_id=payload.page_id,
        target_control_id=control.id if control else None, target_clause_id=clause.id if clause else None,
        anchor=anchor, permalink=permalink, page_title=str(page.get("name") or "Page")[:256],
        revision_count=page.get("revision_count") if isinstance(page.get("revision_count"), int) else None,
        page_updated_at=str(page.get("updated_at") or "")[:128],
        storage_uri=stored.uri, sha256=stored.sha256, captured_at=captured, captured_by_user_id=user.id)
    db.add(row); db.commit(); db.refresh(row)
    return _out(row)


@router.patch("/v1/isms/bookstack-sections/{section_id}")
def archive_section(section_id: uuid.UUID, archived: bool, user=Depends(require_isms_manage), db: Session = Depends(get_db)):
    row = db.get(BookStackSectionEvidence, section_id)
    if not row: raise HTTPException(404, "Policy section snapshot not found")
    row.archived = archived
    db.commit(); db.refresh(row)
    return _out(row)


@router.get("/v1/isms/bookstack-sections/{section_id}/snapshot")
def download_section(section_id: uuid.UUID, user=Depends(require_authenticated), db: Session = Depends(get_db)):
    row = db.get(BookStackSectionEvidence, section_id)
    if not row: raise HTTPException(404, "Policy section snapshot not found")
    can_read_isms = has_permission(db, user, "isms.read") or has_permission(db, user, "isms.manage")
    audit_sampled = db.query(AuditEvidence.id).filter(
        AuditEvidence.entity_type == "bookstack_section", AuditEvidence.entity_id == section_id
    ).first() is not None
    can_read_audit = audit_sampled and (
        has_permission(db, user, "audits.read") or has_permission(db, user, "audits.manage")
    )
    if not can_read_isms and not can_read_audit:
        raise HTTPException(403, "ISMS or sampled-audit read permission required")
    bucket, key = parse_s3_uri(row.storage_uri)
    try: obj = get_object_stream(bucket, key)
    except Exception as exc: raise HTTPException(502, "Policy snapshot storage unavailable") from exc
    return StreamingResponse(iter_stream(obj["Body"]), media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="bookstack-page-{row.page_id}-{row.id}.json"',
                 "X-Artifact-SHA256": row.sha256})
