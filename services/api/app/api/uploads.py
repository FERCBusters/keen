"""Adapt request-owned uploads without eagerly copying all files into memory."""

from app.services.uploads import Upload


def upload_inputs(files):
    return [
        Upload(
            filename=getattr(file, "filename", None) or "attachment",
            content_type=getattr(file, "content_type", None)
            or "application/octet-stream",
            # The spooled file itself: operations read it in the thread pool.
            read=file.file.read,
        )
        for file in files
        if callable(getattr(getattr(file, "file", None), "read", None))
    ]
