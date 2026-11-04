from __future__ import annotations

import mimetypes
import os
from typing import Any
from urllib.parse import quote

from app.security.regex import scan_pattern

# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def safe_download_ext(ext: str) -> str:
    """Return a safe lowercase file extension (including leading dot) or empty."""
    if not ext:
        return ""
    if not ext.startswith("."):
        ext = "." + ext
    ext = ext.strip().lower()
    if scan_pattern(r"\.[a-z0-9]{1,10}").fullmatch(ext):
        return ext
    return ""


def guess_extension(content_type: str | None, storage_key: str | None) -> str:
    """Best-effort extension inference from storage key or MIME type."""
    if storage_key:
        ext = safe_download_ext(os.path.splitext(storage_key)[1])
        if ext:
            return ext

    ctype = (content_type or "").split(";")[0].strip().lower()
    if not ctype:
        return ""

    if ctype in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    if ctype == "image/svg+xml":
        return ".svg"

    ext = mimetypes.guess_extension(ctype) or ""
    return safe_download_ext(ext)


def download_filename_for_artifact(artifact: Any, storage_key: str | None) -> str:
    ext = guess_extension(getattr(artifact, "content_type", None), storage_key)
    return f"artifact-{getattr(artifact, 'id')}{ext}"


# Media types a browser only displays or saves. A download keeps its recorded
# type only when it is one of these. Any other type, such as script, style sheet
# or markup, is served as application/octet-stream: with nosniff, a browser then
# refuses the file as same-origin script or style, whatever an uploader declared.
PASSIVE_DOWNLOAD_TYPES = frozenset(
    {
        "application/json",
        "application/msword",
        "application/pdf",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
        "application/vnd.ms-word.document.82",
        "application/vnd.oasis.opendocument.presentation",
        "application/vnd.oasis.opendocument.spreadsheet",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/x-ndjson",
        "application/zip",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
        "text/csv",
        "text/plain",
    }
)


def attachment_media_type(content_type: str | None) -> str:
    """The media type a download is served with.

    Every route that streams a stored or computed file sets its type through
    this function; a contract test refuses any other non-constant media type.
    """
    base = (content_type or "").split(";", 1)[0].strip().lower()
    return base if base in PASSIVE_DOWNLOAD_TYPES else "application/octet-stream"


def content_disposition_attachment(filename: str) -> str:
    """RFC 5987-friendly Content-Disposition for downloads."""
    safe = "".join(ch for ch in filename if ord(ch) >= 32 and ord(ch) != 127)
    safe = (
        safe.replace('"', "").replace("\\", "_").replace("/", "_")[:255] or "download"
    )
    fallback = safe.encode("ascii", "replace").decode("ascii")
    return (
        f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(safe, safe='')}"
    )
