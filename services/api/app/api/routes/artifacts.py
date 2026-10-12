from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from app.api.routing import Router
from app.db.models import User
from app.db.session import get_db
from app.security.permissions import has_permission
from app.security.roles import is_effective_admin
from app.services import artifacts as service

router = Router()


# Renderer concurrency is bounded per API process by the service. Deployment
# worker counts and container limits determine the aggregate resource budget.


EVENTS_READ_PERMISSION = "events.read"


def _require_events_read(db: Session, request: Request) -> User:
    user = getattr(request.state, "user", None)
    if not isinstance(user, User):
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not is_effective_admin(db, user) and not has_permission(
        db, user, EVENTS_READ_PERMISSION
    ):
        raise HTTPException(status_code=403, detail="events.read permission required")
    return user


@router.get("/v1/artifacts/{artifact_id}/download")
def download_artifact(
    request: Request,
    artifact_id: str,
    sample_export: bool = False,
    db: Session = Depends(get_db),
):
    user = _require_events_read(db, request)
    result = service.download_artifact(
        user=user, artifact_id=artifact_id, sample_export=sample_export, db=db
    )
    return StreamingResponse(
        result.content, media_type=result.content_type, headers=result.attributes
    )


@router.get("/v1/artifacts/{artifact_id}/preview")
def preview_pdf_first_page(
    request: Request, artifact_id: str, db: Session = Depends(get_db)
):
    "Render a PNG preview (page 1) for PDF artifacts.\n\n    This enables the UI to show a thumbnail that can be captured in evidence PDFs.\n    Uses poppler's `pdftoppm`.\n"
    user = _require_events_read(db, request)
    result = service.preview_pdf_first_page(user=user, artifact_id=artifact_id, db=db)
    return Response(result, media_type="image/png")
