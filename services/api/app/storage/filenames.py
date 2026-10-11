from __future__ import annotations

import mimetypes
import os
import re
from typing import Any
from urllib.parse import quote

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
    if re.fullmatch(r"\.[a-z0-9]{1,10}", ext):
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
