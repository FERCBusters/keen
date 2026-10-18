"""HTTP adapters for ISMS documents."""

from __future__ import annotations

import uuid

from fastapi import Depends, File, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.dependencies.isms import require_isms_manage, require_isms_read
from app.api.routing import Router
from app.core.config import settings
from app.db.session import get_db
from app.services.context import ChangeContext
from app.services.isms import documents as service
from app.services.isms.schemas import (
    DocumentCommentPayload,
    DocumentFolderPayload,
    DocumentPayload,
)
from app.storage.filenames import content_disposition_attachment
from app.storage.s3 import get_object_stream, iter_stream, parse_s3_uri

router = Router()


@router.get("/v1/isms/document-folders")
def list_document_folders(
    user=Depends(require_isms_read), db: Session = Depends(get_db)
):
    return service.list_document_folders(user=user, db=db)


@router.post("/v1/isms/document-folders")
def create_document_folder(
    payload: DocumentFolderPayload,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.create_document_folder(payload=payload, user=user, db=db)


@router.patch("/v1/isms/document-folders/{folder_id}")
def update_document_folder(
    folder_id: uuid.UUID,
    payload: DocumentFolderPayload,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.update_document_folder(
        folder_id=folder_id, payload=payload, user=user, db=db
    )


@router.delete("/v1/isms/document-folders/{folder_id}")
def delete_document_folder(
    folder_id: uuid.UUID,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.delete_document_folder(folder_id=folder_id, user=user, db=db)


@router.get("/v1/isms/documents/{document_id}/revisions")
def list_document_revisions(
    document_id: uuid.UUID,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.list_document_revisions(document_id=document_id, user=user, db=db)


@router.get("/v1/isms/documents/{document_id}/revisions/{version}")
def get_document_revision(
    document_id: uuid.UUID,
    version: int,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.get_document_revision(
        document_id=document_id, version=version, user=user, db=db
    )


@router.get("/v1/isms/documents/{document_id}/comments")
def list_document_comments(
    document_id: uuid.UUID,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.list_document_comments(document_id=document_id, user=user, db=db)


@router.post("/v1/isms/documents/{document_id}/comments")
def create_document_comment(
    document_id: uuid.UUID,
    payload: DocumentCommentPayload,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.create_document_comment(
        document_id=document_id, payload=payload, user=user, db=db
    )


@router.post("/v1/isms/documents")
def create_document(
    payload: DocumentPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.create_document(
        payload=payload,
        context=ChangeContext(method=request.method, path=request.url.path),
        framework=framework,
        user=user,
        db=db,
    )


@router.get("/v1/isms/documents")
def list_documents(
    q: str = "",
    framework: str = settings.default_framework_slug,
    limit: int = 200,
    offset: int = 0,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.list_documents(
        q=q, framework=framework, limit=limit, offset=offset, user=user, db=db
    )


@router.get("/v1/isms/documents/{document_id}")
def get_document(
    document_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.get_document(
        document_id=document_id, framework=framework, user=user, db=db
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
    return service.update_document(
        document_id=document_id,
        payload=payload,
        context=ChangeContext(method=request.method, path=request.url.path),
        framework=framework,
        user=user,
        db=db,
    )


@router.post("/v1/isms/documents/{document_id}/file")
def upload_document_file(
    document_id: str,
    request: Request,
    file: UploadFile = File(...),
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    # A synchronous handler keeps the storage write off the event loop.
    return service.upload_document_file(
        document_id=document_id,
        content=file.file,
        filename=file.filename,
        content_type=file.content_type or "application/octet-stream",
        framework=framework,
        user=user,
        db=db,
        context=ChangeContext(method=request.method, path=request.url.path),
    )


@router.get("/v1/isms/documents/{document_id}/file")
def download_document_file(
    document_id: str, user=Depends(require_isms_read), db: Session = Depends(get_db)
):
    uri, filename, media = service.document_file(db=db, document_id=document_id)
    bucket, key = parse_s3_uri(uri)
    obj = get_object_stream(bucket, key)
    return StreamingResponse(
        iter_stream(obj["Body"]),
        media_type=media,
        headers={"Content-Disposition": content_disposition_attachment(filename)},
    )


@router.delete("/v1/isms/documents/{document_id}")
def delete_document(
    document_id: str,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.delete_document(
        document_id=document_id,
        context=ChangeContext(method=request.method, path=request.url.path),
        framework=framework,
        user=user,
        db=db,
    )
