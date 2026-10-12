"""Final-report validation, storage and audit metadata."""

import os
import uuid

from sqlalchemy.orm import Session

from app.db.models import User
from app.services.audits.access import (
    editable_audit_or_404,
)
from app.services.audits.serializers import report_dict
from app.services.audits.validation import require_mutable_audit, utcnow
from app.services.errors import DomainError
from app.storage.filenames import safe_download_ext
from app.storage.s3 import put_bytes

MAX_REPORT_BYTES = 100 * 1024 * 1024


def save_final_report(
    *,
    audit_id: str,
    db: Session,
    user: User,
    filename: str | None,
    content_type: str | None,
    data: bytes,
):
    a = editable_audit_or_404(db, user, audit_id)
    require_mutable_audit(a)
    filename = os.path.basename(filename or "audit-report")[:255]
    content_type = content_type or "application/octet-stream"
    if not data:
        raise DomainError(status_code=400, detail="Uploaded file is empty")
    if len(data) > MAX_REPORT_BYTES:
        raise DomainError(status_code=413, detail="Final report is too large")
    ext = safe_download_ext(os.path.splitext(filename)[1])
    key = f"audit-reports/{a.id}/{uuid.uuid4()}{ext}"
    stored = put_bytes(key, data, content_type)
    now = utcnow()
    a.final_report_storage_uri = stored.uri
    a.final_report_filename = filename or "audit-report"
    a.final_report_content_type = content_type
    a.final_report_sha256 = stored.sha256
    a.final_report_size_bytes = stored.size_bytes
    a.final_report_uploaded_at = now
    a.final_report_uploaded_by_user_id = user.id
    a.updated_at = now
    db.add(a)
    db.commit()
    db.refresh(a)
    return {"ok": True, "final_report": report_dict(a)}
