"""Artifacts application operations."""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
from collections import OrderedDict
from pathlib import Path
from threading import BoundedSemaphore, Lock

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.references import try_uuid as _try_uuid
from app.db.models import Artifact, Event
from app.security.diary_visibility import is_diary_event_visible
from app.security.redaction import is_textual_content_type, mask_event_data_bytes
from app.services.errors import DomainError
from app.services.exports import Export
from app.storage.filenames import (
    content_disposition_attachment as _content_disposition_attachment,
)
from app.storage.filenames import (
    download_filename_for_artifact as _download_filename_for_artifact,
)
from app.storage.s3 import get_object_stream, iter_stream, parse_s3_uri

_PREVIEW_CACHE = OrderedDict()
_PREVIEW_CACHE_LOCK = Lock()
_PREVIEW_CACHE_BYTES = 32 * 1024 * 1024
_PREVIEW_CACHE_ENTRIES = 128
_PREVIEW_SLOTS = BoundedSemaphore(2)
EVENTS_READ_PERMISSION = "events.read"


def _run_preview(cmd):
    if not _PREVIEW_SLOTS.acquire(blocking=False):
        raise DomainError(
            429, "Preview renderer busy; retry shortly", headers={"Retry-After": "5"}
        )
    try:
        subprocess.run(
            cmd,
            check=True,
            timeout=15,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    finally:
        _PREVIEW_SLOTS.release()


def _cache_preview(key, data):
    if len(data) > _PREVIEW_CACHE_BYTES:
        return
    with _PREVIEW_CACHE_LOCK:
        _PREVIEW_CACHE.pop(key, None)
        while _PREVIEW_CACHE and (
            len(_PREVIEW_CACHE) >= _PREVIEW_CACHE_ENTRIES
            or sum(map(len, _PREVIEW_CACHE.values())) + len(data) > _PREVIEW_CACHE_BYTES
        ):
            _PREVIEW_CACHE.popitem(last=False)
        _PREVIEW_CACHE[key] = data


def download_artifact(*, artifact_id: str, sample_export: bool, db: Session, user):
    aid = _try_uuid(artifact_id)
    if not aid:
        raise DomainError(status_code=400, detail="artifact_id must be a UUID")

    art = db.query(Artifact).filter(Artifact.id == aid).one_or_none()
    if not art:
        raise DomainError(status_code=404, detail="Artifact not found")

    # Diary visibility: diary artifacts require an active authenticated user.
    ev = db.query(Event).filter(Event.id == art.event_id).one_or_none()
    if ev and ev.source == "diary" and not is_diary_event_visible(db, user, ev.id):
        raise DomainError(status_code=404, detail="Artifact not found")

    try:
        bucket, key = parse_s3_uri(art.storage_uri)
        obj = get_object_stream(bucket, key)
        body = obj["Body"]
    except Exception:
        raise DomainError(
            status_code=502, detail="Failed to fetch artifact from storage"
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
        # Structured masking needs a complete document. Bound actual bytes,
        # including objects whose stored size metadata is stale or incorrect.
        stream = iter_stream(body)
        data = bytearray()
        try:
            for chunk in stream:
                if len(data) + len(chunk) > 20 * 1024 * 1024:
                    raise DomainError(413, "Artifact exceeds the 20 MiB masking limit")
                data.extend(chunk)
        finally:
            stream.close()
        data = bytes(data)
        masked, mask_status = mask_event_data_bytes(data, art.content_type)
        if mask_status == "redacted":
            headers["X-Artifact-Original-SHA256"] = art.sha256
            headers["X-Artifact-SHA256"] = hashlib.sha256(masked).hexdigest()
            headers["X-Keen-Event-Data-Masked"] = "1"
        return Export(
            iter([masked]),
            content_type=art.content_type or "application/octet-stream",
            attributes=headers,
        )

    return Export(
        iter_stream(body),
        content_type=art.content_type or "application/octet-stream",
        attributes=headers,
    )


def preview_pdf_first_page(*, artifact_id: str, db: Session, user):
    """Render a PNG preview (page 1) for PDF artifacts.

    This enables the UI to show a thumbnail that can be captured in evidence PDFs.
    Uses poppler's `pdftoppm`.
    """
    aid = _try_uuid(artifact_id)
    if not aid:
        raise DomainError(status_code=400, detail="artifact_id must be a UUID")

    art = db.query(Artifact).filter(Artifact.id == aid).one_or_none()
    if not art:
        raise DomainError(status_code=404, detail="Artifact not found")

    # Diary visibility: diary artifacts require an active authenticated user.
    ev = db.query(Event).filter(Event.id == art.event_id).one_or_none()
    if ev and ev.source == "diary" and not is_diary_event_visible(db, user, ev.id):
        raise DomainError(status_code=404, detail="Artifact not found")

    ct = (art.content_type or "").lower()
    if ct.split(";", 1)[0].strip() != "application/pdf":
        raise DomainError(status_code=415, detail="Preview is only supported for PDFs")

    with _PREVIEW_CACHE_LOCK:
        cached = _PREVIEW_CACHE.get(art.sha256)
        if cached is not None:
            _PREVIEW_CACHE.move_to_end(art.sha256)
            return cached

    # Fetch the PDF to a temp file
    try:
        bucket, key = parse_s3_uri(art.storage_uri)
        obj = get_object_stream(bucket, key)
        body = obj["Body"]
    except Exception:
        raise DomainError(
            status_code=502, detail="Failed to fetch artifact from storage"
        )

    try:
        with tempfile.TemporaryDirectory(prefix="keen_pdfprev_") as td:
            td_path = Path(td)
            in_path = td_path / "input.pdf"
            out_prefix = td_path / "preview"

            with open(in_path, "wb") as f:
                size = 0
                stream = iter_stream(body)
                try:
                    for chunk in stream:
                        size += len(chunk)
                        if size > 20 * 1024 * 1024:
                            raise DomainError(
                                413,
                                "PDF preview exceeds 20 MiB; download the original instead",
                            )
                        f.write(chunk)
                finally:
                    stream.close()

            # Render page 1 as PNG.
            # -singlefile produces <out_prefix>.png
            cmd = [
                sys.executable,
                "-m",
                "app.render.pdf_preview",
                str(in_path),
                str(out_prefix),
            ]
            try:
                _run_preview(cmd)
            except FileNotFoundError:
                raise DomainError(
                    status_code=500, detail="pdftoppm is not installed in the API image"
                )
            except subprocess.TimeoutExpired:
                raise DomainError(
                    status_code=422,
                    detail="PDF preview exceeded the rendering time limit",
                )
            except subprocess.CalledProcessError:
                raise DomainError(
                    status_code=500, detail="Failed to render PDF preview"
                )

            out_path = td_path / "preview.png"
            if not out_path.exists():
                raise DomainError(
                    status_code=500, detail="Preview renderer did not produce output"
                )

            data = out_path.read_bytes()
            _cache_preview(art.sha256, data)
            return data
    except DomainError:
        raise
    except Exception:
        raise DomainError(status_code=500, detail="Failed to build preview")
