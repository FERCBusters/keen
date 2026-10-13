"""BookStack policy-section references with retained page-version evidence."""

from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.dependencies.isms import require_isms_manage, require_isms_read
from app.api.routing import Router
from app.db.session import get_db
from app.security.auth import require_authenticated
from app.services import bookstack_sections as service
from app.services.bookstack_sections import SectionInput
from app.storage.s3 import get_object_stream, iter_stream, parse_s3_uri

router = Router()


@router.get("/v1/isms/bookstack-sections")
def list_sections(
    document_id: uuid.UUID | None = None,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.list_sections(document_id=document_id, user=user, db=db)


@router.post("/v1/isms/bookstack-sections", status_code=201)
def capture_section(
    payload: SectionInput,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.capture_section(payload=payload, user=user, db=db)


@router.patch("/v1/isms/bookstack-sections/{section_id}")
def archive_section(
    section_id: uuid.UUID,
    archived: bool,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.archive_section(
        section_id=section_id, archived=archived, user=user, db=db
    )


@router.get("/v1/isms/bookstack-sections/{section_id}/snapshot")
def download_section(
    section_id: uuid.UUID,
    user=Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    row = service.download_section(section_id=section_id, user=user, db=db)
    bucket, key = parse_s3_uri(row.storage_uri)
    try:
        obj = get_object_stream(bucket, key)
    except Exception as exc:
        raise HTTPException(502, "Policy snapshot storage unavailable") from exc
    return StreamingResponse(
        iter_stream(obj["Body"]),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="bookstack-page-{row.page_id}-{row.id}.json"',
            "X-Artifact-SHA256": row.sha256,
        },
    )


@router.get("/v1/isms/bookstack-catalog")
def document_catalog(
    kind: str = "books",
    book_id: int | None = None,
    offset: int = 0,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    "Allow authorised document managers to select BookStack material."
    return service.document_catalog(
        kind=kind, book_id=book_id, offset=offset, user=user, db=db
    )
