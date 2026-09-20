from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.api.utils import (
    content_disposition_attachment as _content_disposition_attachment,
    download_filename_for_artifact as _download_filename_for_artifact,
    try_uuid as _try_uuid,
)
from app.core.config import settings
from app.db.models import Artifact, Event, User
from app.db.session import get_db
from app.security.diary_visibility import is_diary_event_visible
from app.security.permissions import has_permission
from app.security.roles import is_effective_admin
from app.security.redaction import is_textual_content_type, mask_event_data_bytes
from app.storage.s3 import get_object_stream, iter_stream, parse_s3_uri

router = APIRouter()


_PREVIEW_CACHE_DIR = Path(os.getenv("KEEN_PREVIEW_CACHE_DIR", "/var/lib/keen/previews"))

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


def _ensure_preview_cache_dir() -> None:
    try:
        _PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        # If we can't create a cache dir, we will still try generating previews
        # into a temp directory and return them directly.
        pass


@router.get("/v1/artifacts/{artifact_id}/download")
def download_artifact(
    request: Request,
    artifact_id: str,
    sample_export: bool = False,
    db: Session = Depends(get_db),
):
    aid = _try_uuid(artifact_id)
    if not aid:
        raise HTTPException(status_code=400, detail="artifact_id must be a UUID")

    art = db.query(Artifact).filter(Artifact.id == aid).one_or_none()
    if not art:
        raise HTTPException(status_code=404, detail="Artifact not found")

    user = _require_events_read(db, request)

    # Diary visibility: diary artifacts require an active authenticated user.
    ev = db.query(Event).filter(Event.id == art.event_id).one_or_none()
    if ev and ev.source == "diary" and not is_diary_event_visible(db, user, ev.id):
        raise HTTPException(status_code=404, detail="Artifact not found")

    try:
        bucket, key = parse_s3_uri(art.storage_uri)
        obj = get_object_stream(bucket, key)
        body = obj["Body"]
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"Failed to fetch artifact from storage: {e}"
        )

    filename = _download_filename_for_artifact(art, key)
    headers = {
        "Content-Disposition": _content_disposition_attachment(filename),
        "X-Artifact-SHA256": art.sha256,
    }

    should_mask_sample = bool(sample_export) and (
        settings.event_data_masking or "false"
    ) in {"samples", "true"}
    if should_mask_sample and is_textual_content_type(art.content_type):
        data = b"".join(iter_stream(body))
        masked, mask_status = mask_event_data_bytes(data, art.content_type)
        if mask_status == "redacted":
            headers["X-Artifact-Original-SHA256"] = art.sha256
            headers["X-Artifact-SHA256"] = hashlib.sha256(masked).hexdigest()
            headers["X-Keen-Event-Data-Masked"] = "1"
        return StreamingResponse(
            iter([masked]),
            media_type=art.content_type or "application/octet-stream",
            headers=headers,
        )

    return StreamingResponse(
        iter_stream(body),
        media_type=art.content_type or "application/octet-stream",
        headers=headers,
    )


@router.get("/v1/artifacts/{artifact_id}/preview")
def preview_pdf_first_page(
    request: Request, artifact_id: str, db: Session = Depends(get_db)
):
    """Render a PNG preview (page 1) for PDF artifacts.

    This enables the UI to show a thumbnail that can be captured in evidence PDFs.
    Uses poppler's `pdftoppm`.
    """
    aid = _try_uuid(artifact_id)
    if not aid:
        raise HTTPException(status_code=400, detail="artifact_id must be a UUID")

    art = db.query(Artifact).filter(Artifact.id == aid).one_or_none()
    if not art:
        raise HTTPException(status_code=404, detail="Artifact not found")

    user = _require_events_read(db, request)

    # Diary visibility: diary artifacts require an active authenticated user.
    ev = db.query(Event).filter(Event.id == art.event_id).one_or_none()
    if ev and ev.source == "diary" and not is_diary_event_visible(db, user, ev.id):
        raise HTTPException(status_code=404, detail="Artifact not found")

    ct = (art.content_type or "").lower()
    if "application/pdf" not in ct:
        raise HTTPException(
            status_code=415, detail="Preview is only supported for PDFs"
        )

    _ensure_preview_cache_dir()
    cache_path = _PREVIEW_CACHE_DIR / f"{art.sha256}.png"
    if cache_path.exists():
        return FileResponse(str(cache_path), media_type="image/png")

    # Fetch the PDF to a temp file
    try:
        bucket, key = parse_s3_uri(art.storage_uri)
        obj = get_object_stream(bucket, key)
        body = obj["Body"]
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"Failed to fetch artifact from storage: {e}"
        )

    try:
        with tempfile.TemporaryDirectory(prefix="keen_pdfprev_") as td:
            td_path = Path(td)
            in_path = td_path / "input.pdf"
            out_prefix = td_path / "preview"

            with open(in_path, "wb") as f:
                for chunk in iter_stream(body):
                    f.write(chunk)

            # Render page 1 as PNG.
            # -singlefile produces <out_prefix>.png
            cmd = [
                "pdftoppm",
                "-f",
                "1",
                "-l",
                "1",
                "-singlefile",
                "-png",
                str(in_path),
                str(out_prefix),
            ]
            try:
                subprocess.run(
                    cmd,
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                raise HTTPException(
                    status_code=500, detail="pdftoppm is not installed in the API image"
                )
            except subprocess.CalledProcessError:
                raise HTTPException(
                    status_code=500, detail="Failed to render PDF preview"
                )

            out_path = td_path / "preview.png"
            if not out_path.exists():
                raise HTTPException(
                    status_code=500, detail="Preview renderer did not produce output"
                )

            # Cache if possible; otherwise return directly.
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(out_path), str(cache_path))
                return FileResponse(str(cache_path), media_type="image/png")
            except Exception:
                return FileResponse(str(out_path), media_type="image/png")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to build preview: {e}")
