"""ISMS documents domain operations."""

from __future__ import annotations

import hashlib
import os
import uuid
from typing import BinaryIO

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.datetime_utils import utc_now_naive as _utcnow
from app.db.models import (
    BookStackSectionEvidence,
    IsmsDocument,
    IsmsDocumentComment,
    IsmsDocumentFolder,
    IsmsDocumentRevision,
    User,
)
from app.db.session import track_unpublished_object
from app.security.request_limits import MAX_FILE_UPLOAD
from app.security.rich_text import sanitize_rich_text_html
from app.services.context import ChangeContext
from app.services.errors import DomainError
from app.services.frameworks import framework_parameter
from app.services.hierarchy import validate_parent
from app.services.text import clean_text
from app.services.uploads import upload_size
from app.storage.filenames import safe_download_ext
from app.storage.s3 import put_file

from .common import _by_id_or_404, _list_response, _record
from .constants import DOCUMENT_TYPES
from .mutations import _apply_links, _delete_entity_links
from .schemas import DocumentCommentPayload, DocumentFolderPayload, DocumentPayload
from .serializers import _document_out, _user_summary

MAX_DOCUMENT_BYTES = MAX_FILE_UPLOAD


def _document_folder(db: Session, folder_id: uuid.UUID | None) -> uuid.UUID | None:
    if folder_id and db.get(IsmsDocumentFolder, folder_id) is None:
        raise DomainError(status_code=400, detail="Unknown document folder")
    return folder_id


def _document_tags(tags: list[str] | None) -> list[str]:
    cleaned = list(
        dict.fromkeys(str(tag).strip() for tag in (tags or []) if str(tag).strip())
    )
    if len(cleaned) > 30 or any(len(tag) > 64 for tag in cleaned):
        raise DomainError(
            status_code=400, detail="Use at most 30 tags, each under 65 characters"
        )
    return cleaned


def _save_document_revision(db: Session, row: IsmsDocument, user: User) -> None:
    db.add(
        IsmsDocumentRevision(
            document_id=row.id,
            version=row.content_version,
            content_html=row.content_html,
            sha256=hashlib.sha256(row.content_html.encode("utf-8")).hexdigest(),
            created_by_user_id=user.id,
            created_at=_utcnow(),
        )
    )


def list_document_folders(*, user: User, db: Session):
    return [
        {
            "id": str(row.id),
            "name": row.name,
            "parent_id": str(row.parent_id) if row.parent_id else None,
        }
        for row in db.query(IsmsDocumentFolder).order_by(IsmsDocumentFolder.name).all()
    ]


def create_document_folder(*, payload: DocumentFolderPayload, user: User, db: Session):
    parent = _document_folder(db, payload.parent_id)
    validate_parent(db, IsmsDocumentFolder, parent)
    row = IsmsDocumentFolder(name=payload.name.strip(), parent_id=parent)
    if not row.name:
        raise DomainError(status_code=400, detail="Folder name is required")
    db.add(row)
    db.commit()
    return {
        "id": str(row.id),
        "name": row.name,
        "parent_id": str(parent) if parent else None,
    }


def update_document_folder(
    *, folder_id: uuid.UUID, payload: DocumentFolderPayload, user: User, db: Session
):
    row = db.get(IsmsDocumentFolder, folder_id)
    if row is None:
        raise DomainError(status_code=404, detail="Folder not found")
    parent = _document_folder(db, payload.parent_id)
    validate_parent(db, IsmsDocumentFolder, parent, row.id)
    row.name = payload.name.strip()
    if not row.name:
        raise DomainError(status_code=400, detail="Folder name is required")
    row.parent_id = parent
    db.commit()
    return {
        "id": str(row.id),
        "name": row.name,
        "parent_id": str(parent) if parent else None,
    }


def delete_document_folder(*, folder_id: uuid.UUID, user: User, db: Session):
    row = db.get(IsmsDocumentFolder, folder_id)
    if row is None:
        raise DomainError(status_code=404, detail="Folder not found")
    if (
        db.query(IsmsDocument.id).filter(IsmsDocument.folder_id == row.id).first()
        or db.query(IsmsDocumentFolder.id)
        .filter(IsmsDocumentFolder.parent_id == row.id)
        .first()
    ):
        raise DomainError(
            status_code=409,
            detail="Move documents and child folders before deleting this folder",
        )
    db.delete(row)
    db.commit()
    return {"ok": True}


def list_document_revisions(*, document_id: uuid.UUID, user: User, db: Session):
    if db.get(IsmsDocument, document_id) is None:
        raise DomainError(status_code=404, detail="Document not found")
    return [
        {
            "version": row.version,
            "sha256": row.sha256,
            "created_at": row.created_at.isoformat(),
            "created_by_user_id": str(row.created_by_user_id)
            if row.created_by_user_id
            else None,
        }
        for row in db.query(IsmsDocumentRevision)
        .filter_by(document_id=document_id)
        .order_by(IsmsDocumentRevision.version.desc())
        .all()
    ]


def get_document_revision(
    *, document_id: uuid.UUID, version: int, user: User, db: Session
):
    row = (
        db.query(IsmsDocumentRevision)
        .filter_by(document_id=document_id, version=version)
        .first()
    )
    if row is None:
        raise DomainError(status_code=404, detail="Revision not found")
    return {
        "version": row.version,
        "sha256": row.sha256,
        "content_html": row.content_html,
        "created_at": row.created_at.isoformat(),
    }


def list_document_comments(*, document_id: uuid.UUID, user: User, db: Session):
    if db.get(IsmsDocument, document_id) is None:
        raise DomainError(status_code=404, detail="Document not found")
    return [
        {
            "id": str(row.id),
            "body": row.body,
            "author": _user_summary(row.author),
            "created_at": row.created_at.isoformat(),
        }
        for row in db.query(IsmsDocumentComment)
        .filter_by(document_id=document_id)
        .order_by(IsmsDocumentComment.created_at.asc())
        .all()
    ]


def create_document_comment(
    *, document_id: uuid.UUID, payload: DocumentCommentPayload, user: User, db: Session
):
    if db.get(IsmsDocument, document_id) is None:
        raise DomainError(status_code=404, detail="Document not found")
    row = IsmsDocumentComment(
        document_id=document_id,
        body=payload.body.strip(),
        author_user_id=user.id,
        created_at=_utcnow(),
    )
    if not row.body:
        raise DomainError(status_code=400, detail="Comment cannot be empty")
    db.add(row)
    db.commit()
    return {
        "id": str(row.id),
        "body": row.body,
        "created_at": row.created_at.isoformat(),
    }


def create_document(
    *,
    payload: DocumentPayload,
    context: ChangeContext,
    framework: str,
    user: User,
    db: Session,
):
    fw = framework_parameter(framework)
    dtype = (payload.document_type or "policy").strip().lower() or "policy"
    if dtype not in DOCUMENT_TYPES:
        raise DomainError(status_code=400, detail="Invalid document_type")
    row = IsmsDocument(
        title=clean_text(payload.title, max_len=256, required=True, label="title"),
        document_type=dtype,
        description=clean_text(payload.description, max_len=20000),
        external_url=clean_text(payload.external_url, max_len=2048) or None,
        folder_id=_document_folder(db, payload.folder_id),
        tags=_document_tags(payload.tags),
        content_html=sanitize_rich_text_html(payload.content_html or "")
        if payload.content_html
        else "",
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
    _record(db, "document", row, "created", None, after, user, context)
    db.commit()
    db.refresh(row)
    return _document_out(db, row, fw)


def list_documents(
    *, q: str, framework: str, limit: int, offset: int, user: User, db: Session
):
    fw = framework_parameter(framework)
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


def get_document(*, document_id: str, framework: str, user: User, db: Session):
    return _document_out(
        db,
        _by_id_or_404(db, IsmsDocument, document_id, "Document"),
        framework_parameter(framework),
    )


def update_document(
    *,
    document_id: str,
    payload: DocumentPayload,
    context: ChangeContext,
    framework: str,
    user: User,
    db: Session,
):
    fw = framework_parameter(framework)
    # Saves of one document queue on its row, so each checks the version the
    # previous save committed.
    row = _by_id_or_404(db, IsmsDocument, document_id, "Document", lock=True)
    before = _document_out(db, row, fw)
    fields = set(payload.model_fields_set or set())
    if "title" in fields:
        row.title = clean_text(payload.title, max_len=256, required=True, label="title")
    if "document_type" in fields:
        dtype = (payload.document_type or "policy").strip().lower() or "policy"
        if dtype not in DOCUMENT_TYPES:
            raise DomainError(status_code=400, detail="Invalid document_type")
        row.document_type = dtype
    if "description" in fields:
        row.description = clean_text(payload.description, max_len=20000)
    if "external_url" in fields:
        row.external_url = clean_text(payload.external_url, max_len=2048) or None
    if "folder_id" in fields:
        row.folder_id = _document_folder(db, payload.folder_id)
    if "tags" in fields:
        row.tags = _document_tags(payload.tags)
    if "content_html" in fields:
        if (
            payload.expected_content_version is None
            or payload.expected_content_version != row.content_version
        ):
            raise DomainError(
                status_code=409,
                detail="Document changed since it was opened. Reload before saving.",
            )
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
    _record(db, "document", row, "updated", before, after, user, context)
    db.commit()
    db.refresh(row)
    return _document_out(db, row, fw)


def upload_document_file(
    *,
    document_id: str,
    content: BinaryIO,
    filename: str | None,
    content_type: str,
    framework: str,
    user: User,
    db: Session,
    context: ChangeContext,
):
    fw = framework_parameter(framework)
    # Locked so a concurrent replacement or deletion retires this file in turn.
    row = _by_id_or_404(db, IsmsDocument, document_id, "Document", lock=True)
    before = _document_out(db, row, fw)
    filename = os.path.basename(filename or "isms-document")[:255]
    size = upload_size(content)
    if not size:
        raise DomainError(400, "Uploaded file is empty")
    if size > MAX_DOCUMENT_BYTES:
        raise DomainError(413, "ISMS document is too large")
    ext = safe_download_ext(os.path.splitext(filename)[1])
    stored = put_file(
        f"isms-documents/{row.id}/{uuid.uuid4()}{ext}", content, content_type
    )
    track_unpublished_object(db, stored.uri)
    from app.services.evidence_retention import queue_object

    queue_object(db, row.storage_uri)  # the replaced file is no longer reachable
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
    _record(db, "document", row, "updated", before, after, user, context)
    db.commit()
    db.refresh(row)
    return {"ok": True, "document": _document_out(db, row, fw)}


def document_file(*, db: Session, document_id: str):
    row = _by_id_or_404(db, IsmsDocument, document_id, "Document")
    if not row.storage_uri:
        raise DomainError(404, "No file uploaded")
    return (
        row.storage_uri,
        row.filename or "isms-document",
        row.content_type or "application/octet-stream",
    )


def delete_document(
    *, document_id: str, context: ChangeContext, framework: str, user: User, db: Session
):
    fw = framework_parameter(framework)
    row = _by_id_or_404(db, IsmsDocument, document_id, "Document", lock=True)
    if (
        db.query(BookStackSectionEvidence.id)
        .filter(BookStackSectionEvidence.document_id == row.id)
        .first()
    ):
        raise DomainError(
            status_code=409,
            detail="This document has captured BookStack policy evidence and cannot be deleted",
        )
    before = _document_out(db, row, fw)
    _record(db, "document", row, "deleted", before, None, user, context)
    _delete_entity_links(db, "document", row.id, fw)
    from app.services.evidence_retention import queue_object

    queue_object(db, row.storage_uri)
    db.delete(row)
    db.commit()
    return {"ok": True}
