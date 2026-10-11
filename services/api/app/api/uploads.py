"""Adapt request-owned uploads without eagerly copying all files into memory."""

from app.services.uploads import Upload


def upload_inputs(files):
    return [
        Upload(
            filename=getattr(file, "filename", None) or "attachment",
            content_type=getattr(file, "content_type", None)
            or "application/octet-stream",
            read=file.read,
        )
        for file in files
        if callable(getattr(file, "read", None))
    ]
