"""HTTP adapters for ISMS documents."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, File, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from mosp.api.routing import Router
from mosp.services.context import ChangeContext
from sqlalchemy.orm import Session

from app.api.dependencies.isms import require_isms_manage, require_isms_read
from app.core.config import settings
from app.db.session import get_db
from app.services.isms import diagrams
from app.services.isms import documents as service
from app.services.isms.diagrams import DiagramInput
from app.services.isms.schemas import (
    DocumentCommentPayload,
    DocumentFolderPayload,
    DocumentPayload,
)
from app.storage.filenames import (
    attachment_media_type,
    content_disposition_attachment,
)
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
        media_type=attachment_media_type(media),
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


# Diagrams from MIDENA shown with a document (app.services.isms.diagrams).


@router.get("/v1/isms/midena-diagrams")
def midena_diagram_catalog(
    q: Annotated[str, Query(max_length=200)] = "",
    offset: Annotated[int, Query(ge=0, le=diagrams.MAX_CATALOG_OFFSET)] = 0,
    user=Depends(require_isms_manage),
):
    """The MIDENA diagrams an ISMS manager may choose from."""
    return diagrams.catalog(q=q, offset=offset)


@router.get("/v1/isms/documents/{document_id}/diagrams")
def list_document_diagrams(
    document_id: str, user=Depends(require_isms_read), db: Session = Depends(get_db)
):
    return diagrams.list_diagrams(document_id=document_id, db=db)


@router.get("/v1/isms/documents/{document_id}/diagrams/{diagram_id}")
def get_document_diagram(
    document_id: str,
    diagram_id: str,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return diagrams.get_diagram(document_id=document_id, diagram_id=diagram_id, db=db)


@router.post("/v1/isms/documents/{document_id}/diagrams", status_code=201)
def attach_document_diagram(
    document_id: str,
    payload: DiagramInput,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return diagrams.attach_diagram(
        document_id=document_id,
        payload=payload,
        framework=framework,
        user=user,
        db=db,
        context=ChangeContext(method=request.method, path=request.url.path),
    )


@router.post("/v1/isms/documents/{document_id}/diagrams/{diagram_id}/refresh")
def refresh_document_diagram(
    document_id: str,
    diagram_id: str,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return diagrams.refresh_diagram(
        document_id=document_id,
        diagram_id=diagram_id,
        framework=framework,
        user=user,
        db=db,
        context=ChangeContext(method=request.method, path=request.url.path),
    )


@router.delete("/v1/isms/documents/{document_id}/diagrams/{diagram_id}")
def detach_document_diagram(
    document_id: str,
    diagram_id: str,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return diagrams.detach_diagram(
        document_id=document_id,
        diagram_id=diagram_id,
        framework=framework,
        user=user,
        db=db,
        context=ChangeContext(method=request.method, path=request.url.path),
    )
