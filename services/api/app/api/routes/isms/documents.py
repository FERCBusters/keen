"""ISMS documents; see docs/maintainability-review.md for module boundaries."""
from __future__ import annotations

import hashlib
import os
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.api.utils import content_disposition_attachment
from app.db.models import (
    BookStackSectionEvidence,
    IsmsDocument,
    IsmsDocumentComment,
    IsmsDocumentFolder,
    IsmsDocumentRevision,
    User,
)
from app.db.session import get_db
from app.security.rich_text import sanitize_rich_text_html
from app.storage.s3 import get_object_stream, iter_stream, parse_s3_uri, put_bytes

from .access import (
    require_isms_manage,
    require_isms_read,
)
from .common import (
    _by_id_or_404,
    _clean_framework,
    _clean_text,
    _list_response,
    _record,
    _utcnow,
)
from .constants import (
    DOCUMENT_TYPES,
)
from .mutations import (
    _apply_links,
    _delete_entity_links,
)
from .schemas import (
    DocumentCommentPayload,
    DocumentFolderPayload,
    DocumentPayload,
)
from .serializers import (
    _document_out,
    _user_summary,
)

router = APIRouter()


def _document_folder(db: Session, folder_id: uuid.UUID | None) -> uuid.UUID | None:
    if folder_id and db.get(IsmsDocumentFolder, folder_id) is None:
        raise HTTPException(status_code=400, detail="Unknown document folder")
    return folder_id


def _document_tags(tags: list[str] | None) -> list[str]:
    cleaned = list(dict.fromkeys(str(tag).strip() for tag in (tags or []) if str(tag).strip()))
    if len(cleaned) > 30 or any(len(tag) > 64 for tag in cleaned):
        raise HTTPException(status_code=400, detail="Use at most 30 tags, each under 65 characters")
    return cleaned


def _save_document_revision(db: Session, row: IsmsDocument, user: User) -> None:
    db.add(IsmsDocumentRevision(
        document_id=row.id, version=row.content_version, content_html=row.content_html,
        sha256=hashlib.sha256(row.content_html.encode("utf-8")).hexdigest(),
        created_by_user_id=user.id, created_at=_utcnow(),
    ))


@router.get("/v1/isms/document-folders")
def list_document_folders(user=Depends(require_isms_read), db: Session = Depends(get_db)):
    return [{"id": str(row.id), "name": row.name, "parent_id": str(row.parent_id) if row.parent_id else None}
            for row in db.query(IsmsDocumentFolder).order_by(IsmsDocumentFolder.name).all()]


@router.post("/v1/isms/document-folders")
def create_document_folder(payload: DocumentFolderPayload, user=Depends(require_isms_manage), db: Session = Depends(get_db)):
    parent = _document_folder(db, payload.parent_id)
    row = IsmsDocumentFolder(name=payload.name.strip(), parent_id=parent)
    if not row.name:
        raise HTTPException(status_code=400, detail="Folder name is required")
    db.add(row)
    db.commit()
    return {"id": str(row.id), "name": row.name, "parent_id": str(parent) if parent else None}


@router.patch("/v1/isms/document-folders/{folder_id}")
def update_document_folder(folder_id: uuid.UUID, payload: DocumentFolderPayload, user=Depends(require_isms_manage), db: Session = Depends(get_db)):
    row = db.get(IsmsDocumentFolder, folder_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Folder not found")
    parent = _document_folder(db, payload.parent_id)
    current = parent
    while current is not None:
        if current == row.id:
            raise HTTPException(status_code=400, detail="A folder cannot contain itself")
        current = db.get(IsmsDocumentFolder, current).parent_id
    row.name = payload.name.strip()
    if not row.name:
        raise HTTPException(status_code=400, detail="Folder name is required")
    row.parent_id = parent
    db.commit()
    return {"id": str(row.id), "name": row.name, "parent_id": str(parent) if parent else None}


@router.delete("/v1/isms/document-folders/{folder_id}")
def delete_document_folder(folder_id: uuid.UUID, user=Depends(require_isms_manage), db: Session = Depends(get_db)):
    row = db.get(IsmsDocumentFolder, folder_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Folder not found")
    if db.query(IsmsDocument.id).filter(IsmsDocument.folder_id == row.id).first() or db.query(IsmsDocumentFolder.id).filter(IsmsDocumentFolder.parent_id == row.id).first():
        raise HTTPException(status_code=409, detail="Move documents and child folders before deleting this folder")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/v1/isms/documents/{document_id}/revisions")
def list_document_revisions(document_id: uuid.UUID, user=Depends(require_isms_read), db: Session = Depends(get_db)):
    if db.get(IsmsDocument, document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return [{"version": row.version, "sha256": row.sha256, "created_at": row.created_at.isoformat(), "created_by_user_id": str(row.created_by_user_id) if row.created_by_user_id else None}
            for row in db.query(IsmsDocumentRevision).filter_by(document_id=document_id).order_by(IsmsDocumentRevision.version.desc()).all()]


@router.get("/v1/isms/documents/{document_id}/revisions/{version}")
def get_document_revision(document_id: uuid.UUID, version: int, user=Depends(require_isms_read), db: Session = Depends(get_db)):
    row = db.query(IsmsDocumentRevision).filter_by(document_id=document_id, version=version).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    return {"version": row.version, "sha256": row.sha256, "content_html": row.content_html, "created_at": row.created_at.isoformat()}


@router.get("/v1/isms/documents/{document_id}/comments")
def list_document_comments(document_id: uuid.UUID, user=Depends(require_isms_read), db: Session = Depends(get_db)):
    if db.get(IsmsDocument, document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return [{"id": str(row.id), "body": row.body, "author": _user_summary(row.author), "created_at": row.created_at.isoformat()}
            for row in db.query(IsmsDocumentComment).filter_by(document_id=document_id).order_by(IsmsDocumentComment.created_at.asc()).all()]


@router.post("/v1/isms/documents/{document_id}/comments")
def create_document_comment(document_id: uuid.UUID, payload: DocumentCommentPayload, user=Depends(require_isms_manage), db: Session = Depends(get_db)):
    if db.get(IsmsDocument, document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    row = IsmsDocumentComment(document_id=document_id, body=payload.body.strip(), author_user_id=user.id, created_at=_utcnow())
    if not row.body:
        raise HTTPException(status_code=400, detail="Comment cannot be empty")
    db.add(row)
    db.commit()
    return {"id": str(row.id), "body": row.body, "created_at": row.created_at.isoformat()}


@router.post("/v1/isms/documents")
def create_document(
    payload: DocumentPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    dtype = (payload.document_type or "policy").strip().lower() or "policy"
    if dtype not in DOCUMENT_TYPES:
        raise HTTPException(status_code=400, detail="Invalid document_type")
    row = IsmsDocument(
        title=_clean_text(payload.title, max_len=256, required=True, label="title"),
        document_type=dtype,
        description=_clean_text(payload.description, max_len=20000),
        external_url=_clean_text(payload.external_url, max_len=2048) or None,
        folder_id=_document_folder(db, payload.folder_id),
        tags=_document_tags(payload.tags),
        content_html=sanitize_rich_text_html(payload.content_html or "") if payload.content_html else "",
        content_version=1 if payload.content_html else 0,
        created_by_user_id=user.id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    db.flush()
    if row.content_version:
        _save_document_revision(db, row, user)
    _apply_links(db, "document", row.id, fw, payload, user)
    after = _document_out(db, row, fw)
    _record(db, "document", row, "created", None, after, user, request)
    db.commit()
    db.refresh(row)
    return _document_out(db, row, fw)


@router.get("/v1/isms/documents")
def list_documents(
    q: str = "",
    framework: str = settings.default_framework_slug,
    limit: int = 200,
    offset: int = 0,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    limit = max(1, min(int(limit or 200), 1000))
    offset = max(0, int(offset or 0))
    qry = db.query(IsmsDocument)
    if q.strip():
        needle = f"%{q.strip()}%"
        qry = qry.filter(
            or_(
                IsmsDocument.title.ilike(needle),
                IsmsDocument.description.ilike(needle),
                IsmsDocument.external_url.ilike(needle),
            )
        )
    total = int(qry.count() or 0)
    rows = (
        qry.order_by(IsmsDocument.updated_at.desc(), IsmsDocument.title.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return _list_response(
        [_document_out(db, r, fw) for r in rows], total, limit, offset, fw
    )


@router.get("/v1/isms/documents/{document_id}")
def get_document(
    document_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return _document_out(
        db,
        _by_id_or_404(db, IsmsDocument, document_id, "Document"),
        _clean_framework(framework),
    )


@router.patch("/v1/isms/documents/{document_id}")
def update_document(
    document_id: str,
    payload: DocumentPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(db, IsmsDocument, document_id, "Document")
    before = _document_out(db, row, fw)
    fields = set(payload.model_fields_set or set())
    if "title" in fields:
        row.title = _clean_text(
            payload.title, max_len=256, required=True, label="title"
        )
    if "document_type" in fields:
        dtype = (payload.document_type or "policy").strip().lower() or "policy"
        if dtype not in DOCUMENT_TYPES:
            raise HTTPException(status_code=400, detail="Invalid document_type")
        row.document_type = dtype
    if "description" in fields:
        row.description = _clean_text(payload.description, max_len=20000)
    if "external_url" in fields:
        row.external_url = _clean_text(payload.external_url, max_len=2048) or None
    if "folder_id" in fields:
        row.folder_id = _document_folder(db, payload.folder_id)
    if "tags" in fields:
        row.tags = _document_tags(payload.tags)
    if "content_html" in fields:
        if payload.expected_content_version is None or payload.expected_content_version != row.content_version:
            raise HTTPException(status_code=409, detail="Document changed since it was opened. Reload before saving.")
        html = sanitize_rich_text_html(payload.content_html or "")
        if html != (row.content_html or ""):
            row.content_html = html
            row.content_version += 1
            _save_document_revision(db, row, user)
    row.updated_at = _utcnow()
    db.add(row)
    db.flush()
    _apply_links(db, "document", row.id, fw, payload, user)
    after = _document_out(db, row, fw)
    _record(db, "document", row, "updated", before, after, user, request)
    db.commit()
    db.refresh(row)
    return _document_out(db, row, fw)


@router.post("/v1/isms/documents/{document_id}/file")
async def upload_document_file(
    document_id: str,
    request: Request,
    file: UploadFile = File(...),
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(db, IsmsDocument, document_id, "Document")
    before = _document_out(db, row, fw)
    filename = os.path.basename(getattr(file, "filename", None) or "isms-document")[
        :255
    ]
    content_type = getattr(file, "content_type", None) or "application/octet-stream"
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(data) > 100 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="ISMS document is too large")
    ext = os.path.splitext(filename)[1]
    stored = put_bytes(
        f"isms-documents/{row.id}/{uuid.uuid4()}{ext}", data, content_type
    )
    now = _utcnow()
    row.storage_uri = stored.uri
    row.filename = filename
    row.content_type = content_type
    row.sha256 = stored.sha256
    row.size_bytes = stored.size_bytes
    row.uploaded_at = now
    row.uploaded_by_user_id = user.id
    row.updated_at = now
    db.add(row)
    db.flush()
    after = _document_out(db, row, fw)
    _record(db, "document", row, "updated", before, after, user, request)
    db.commit()
    db.refresh(row)
    return {"ok": True, "document": _document_out(db, row, fw)}


@router.get("/v1/isms/documents/{document_id}/file")
def download_document_file(
    document_id: str, user=Depends(require_isms_read), db: Session = Depends(get_db)
):
    row = _by_id_or_404(db, IsmsDocument, document_id, "Document")
    if not row.storage_uri:
        raise HTTPException(status_code=404, detail="No file uploaded")
    bucket, key = parse_s3_uri(row.storage_uri)
    obj = get_object_stream(bucket, key)
    fname = row.filename or "isms-document"
    media = row.content_type or "application/octet-stream"
    return StreamingResponse(
        iter_stream(obj["Body"]),
        media_type=media,
        headers={"Content-Disposition": content_disposition_attachment(fname)},
    )


@router.delete("/v1/isms/documents/{document_id}")
def delete_document(
    document_id: str,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(db, IsmsDocument, document_id, "Document")
    if db.query(BookStackSectionEvidence.id).filter(BookStackSectionEvidence.document_id == row.id).first():
        raise HTTPException(status_code=409, detail="This document has captured BookStack policy evidence and cannot be deleted")
    before = _document_out(db, row, fw)
    _record(db, "document", row, "deleted", before, None, user, request)
    _delete_entity_links(db, "document", row.id, fw)
    db.delete(row)
    db.commit()
    return {"ok": True}
