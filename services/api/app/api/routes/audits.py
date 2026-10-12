from __future__ import annotations

from datetime import date

from fastapi import Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.routing import Router
from app.db.models import User
from app.db.session import get_db
from app.security.auth import require_authenticated
from app.services.audits import operations, queries, reports, scheduling
from app.services.audits.access import (
    audit_or_404,
    can_admin_audits,
    can_manage_audits,
    can_read_audits,
    editable_audit_or_404,
    require_audit_access,
)
from app.services.audits.schemas import (
    AuditAttendeePayload,
    AuditControlsPayload,
    AuditCreatePayload,
    AuditEvidencePayload,
    AuditEvidenceUpdatePayload,
    AuditFindingPayload,
    AuditFindingUpdatePayload,
    AuditScopePayload,
    AuditUpdatePayload,
    ScheduledAuditPayload,
    ScheduledAuditUpdatePayload,
)
from app.services.audits.validation import require_mutable_audit
from app.storage.filenames import content_disposition_attachment
from app.storage.s3 import get_object_stream, iter_stream, parse_s3_uri

router = Router()


def require_audit_read(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_authenticated(request)
    if not can_read_audits(db, user):
        raise HTTPException(status_code=403, detail="audits.read permission required")
    return user


def require_audit_manage(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_authenticated(request)
    if not can_manage_audits(db, user):
        raise HTTPException(status_code=403, detail="audits.manage permission required")
    return user


def require_audit_admin(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_authenticated(request)
    if not can_admin_audits(db, user):
        raise HTTPException(status_code=403, detail="audits.admin permission required")
    return user


@router.get("/v1/me/audits")
def list_my_attendee_audits(
    request: Request,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated),
):
    "List audits where the current user is explicitly marked as an attendee."
    return queries.list_my_attendee_audits(limit=limit, offset=offset, db=db, user=user)


@router.get("/v1/audits/users")
def list_audit_attendee_users(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    return queries.list_audit_attendee_users(db=db, user=user)


@router.get("/v1/audits")
def list_audits(
    request: Request,
    framework: str | None = None,
    q: str | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_read),
):
    return queries.list_audits(
        framework=framework,
        q=q,
        status=status,
        limit=limit,
        offset=offset,
        db=db,
        user=user,
    )


@router.get("/v1/audits/scope-coverage")
def get_audit_scope_coverage(
    request: Request,
    framework: str | None = None,
    start: date | None = None,
    end: date | None = None,
    mode: str = "both",
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_read),
):
    return queries.get_audit_scope_coverage(
        framework=framework, start=start, end=end, mode=mode, db=db, user=user
    )


@router.get("/v1/audits/by-event/{event_id}")
def list_audits_for_event(
    event_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_read),
):
    return queries.list_audits_for_event(event_id=event_id, db=db, user=user)


@router.get("/v1/audits/by-entity/{entity_type}/{entity_id}")
def list_audits_for_entity(
    entity_type: str,
    entity_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_read),
):
    return queries.list_audits_for_entity(
        entity_type=entity_type, entity_id=entity_id, db=db, user=user
    )


@router.post("/v1/audits")
def create_audit(
    payload: AuditCreatePayload,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    return operations.create_audit(payload=payload, db=db, user=user)


@router.get("/v1/audits/scheduled")
def list_scheduled_audits(
    request: Request,
    start: date | None = None,
    end: date | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_read),
):
    return scheduling.list_scheduled_audits(start=start, end=end, q=q, db=db, user=user)


@router.post("/v1/audits/scheduled")
def create_scheduled_audit(
    payload: ScheduledAuditPayload,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    return scheduling.create_scheduled_audit(payload=payload, db=db, user=user)


@router.patch("/v1/audits/scheduled/{audit_id}")
def update_scheduled_audit(
    audit_id: str,
    payload: ScheduledAuditUpdatePayload,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    return scheduling.update_scheduled_audit(
        audit_id=audit_id, payload=payload, db=db, user=user
    )


@router.delete("/v1/audits/scheduled/{audit_id}")
def delete_scheduled_audit(
    audit_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_admin),
):
    return scheduling.delete_scheduled_audit(audit_id=audit_id, db=db, user=user)


@router.delete("/v1/audits/{audit_id}")
def delete_audit(
    audit_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_admin),
):
    return operations.delete_audit(audit_id=audit_id, db=db, user=user)


@router.get("/v1/audits/{audit_id}/scope-map")
def get_audit_scope_map(
    audit_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated),
):
    return queries.get_audit_scope_map(audit_id=audit_id, db=db, user=user)


@router.get("/v1/audits/{audit_id}")
def get_audit(
    audit_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated),
):
    return queries.get_audit(audit_id=audit_id, db=db, user=user)


@router.patch("/v1/audits/{audit_id}")
def update_audit(
    audit_id: str,
    payload: AuditUpdatePayload,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    return operations.update_audit(audit_id=audit_id, payload=payload, db=db, user=user)


@router.put("/v1/audits/{audit_id}/scope")
def replace_audit_scope(
    audit_id: str,
    payload: AuditScopePayload,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    return operations.replace_audit_scope(
        audit_id=audit_id, payload=payload, db=db, user=user
    )


@router.put("/v1/audits/{audit_id}/controls")
def replace_audit_controls(
    audit_id: str,
    payload: AuditControlsPayload,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    return operations.replace_audit_controls(
        audit_id=audit_id, payload=payload, db=db, user=user
    )


@router.post("/v1/audits/{audit_id}/evidence")
def add_audit_evidence(
    audit_id: str,
    payload: AuditEvidencePayload,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    return operations.add_audit_evidence(
        audit_id=audit_id, payload=payload, db=db, user=user
    )


@router.patch("/v1/audits/{audit_id}/evidence/{evidence_id}")
def update_audit_evidence(
    audit_id: str,
    evidence_id: str,
    payload: AuditEvidenceUpdatePayload,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    return operations.update_audit_evidence(
        audit_id=audit_id, evidence_id=evidence_id, payload=payload, db=db, user=user
    )


@router.delete("/v1/audits/{audit_id}/evidence/{evidence_id}")
def delete_audit_evidence(
    audit_id: str,
    evidence_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    return operations.delete_audit_evidence(
        audit_id=audit_id, evidence_id=evidence_id, db=db, user=user
    )


@router.post("/v1/audits/{audit_id}/attendees")
def add_attendee(
    audit_id: str,
    payload: AuditAttendeePayload,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    return operations.add_attendee(audit_id=audit_id, payload=payload, db=db, user=user)


@router.delete("/v1/audits/{audit_id}/attendees/{attendee_id}")
def delete_attendee(
    audit_id: str,
    attendee_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    return operations.delete_attendee(
        audit_id=audit_id, attendee_id=attendee_id, db=db, user=user
    )


@router.post("/v1/audits/{audit_id}/findings")
def add_finding(
    audit_id: str,
    payload: AuditFindingPayload,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    return operations.add_finding(audit_id=audit_id, payload=payload, db=db, user=user)


@router.patch("/v1/audits/{audit_id}/findings/{finding_id}")
def update_finding(
    audit_id: str,
    finding_id: str,
    payload: AuditFindingUpdatePayload,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    return operations.update_finding(
        audit_id=audit_id, finding_id=finding_id, payload=payload, db=db, user=user
    )


@router.delete("/v1/audits/{audit_id}/findings/{finding_id}")
def delete_finding(
    audit_id: str,
    finding_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    return operations.delete_finding(
        audit_id=audit_id, finding_id=finding_id, db=db, user=user
    )


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read(reports.MAX_REPORT_BYTES + 1)
    return data or b""


@router.post("/v1/audits/{audit_id}/report")
async def upload_final_report(
    audit_id: str,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    # Check the audit before reading the upload; the service also enforces mutability.
    require_mutable_audit(editable_audit_or_404(db, user, audit_id))
    return reports.save_final_report(
        audit_id=audit_id,
        db=db,
        user=user,
        filename=file.filename,
        content_type=file.content_type,
        data=await _read_upload(file),
    )


@router.get("/v1/audits/{audit_id}/report")
def download_final_report(
    audit_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated),
):
    a = audit_or_404(db, audit_id)
    require_audit_access(db, user, a)
    if not a.final_report_storage_uri:
        raise HTTPException(status_code=404, detail="No final report has been uploaded")
    bucket, key = parse_s3_uri(a.final_report_storage_uri)
    obj = get_object_stream(bucket, key)
    body = obj["Body"]
    fname = a.final_report_filename or "audit-report"
    media = a.final_report_content_type or "application/octet-stream"
    headers = {"Content-Disposition": content_disposition_attachment(fname)}
    return StreamingResponse(iter_stream(body), media_type=media, headers=headers)
