from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.api.utils import try_uuid as _try_uuid
from app.core.config import settings
from app.db.models import (
    Audit,
    AuditAttendee,
    AuditEvidence,
    AuditFinding,
    AuditScopedClause,
    AuditScopedControl,
    AuditScopedIsmsDocument,
    ControlClauseLink,
    BookStackSectionEvidence,
    ControlItem,
    Event,
    FrameworkClause,
    InterestedParty,
    InterestedPartyControlLink,
    IsmsAccessControlMatrixEntry,
    IsmsApplicationConfigurationEntry,
    IsmsDocument,
    IsmsEffectivenessMeasure,
    IsmsEffectivenessMetricEntry,
    IsmsEntityClauseLink,
    IsmsEntityControlLink,
    IsmsMeeting,
    IsmsObjective,
    IsmsOrgNode,
    Mapping,
    PestleClauseRelevance,
    PestleItem,
    PestleRelevanceLevel,
    Risk,
    RiskAsset,
    RiskControlLink,
    User,
)
from app.db.session import get_db
from app.services.audit_schedules import (
    FUZZY_MONTH_DATE_RULES,
    SCHEDULE_DATE_RULES,
    SCHEDULE_RECURRENCES,
    first_scheduled_rule_date,
    occurrences_between,
)
from app.security.auth import require_authenticated
from app.security.diary_visibility import is_diary_event_visible
from app.security.permissions import has_permission
from app.security.rich_text import sanitize_rich_text_html
from app.security.roles import is_effective_admin
from app.storage.s3 import get_object_stream, iter_stream, parse_s3_uri, put_bytes

router = APIRouter()

AUDIT_READ_PERMISSION = "audits.read"
AUDIT_MANAGE_PERMISSION = "audits.manage"
_FINDING_KINDS = {"major_nc", "minor_nc", "ofi", "best_practice"}
_AUDIT_STATUSES = {"open", "in_progress", "completed", "archived", "template"}
_AUDIT_TYPES = {"internal", "external"}
_MUTABLE_AUDIT_STATUSES = {"open", "in_progress"}
_LOCKED_AUDIT_STATUSES = {"completed", "archived"}
_FINDING_STATUSES = {"open", "closed", "accepted"}
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _utcnow() -> datetime:
    return datetime.utcnow()


def _can_read_audits(db: Session, user: User | None) -> bool:
    if not isinstance(user, User) or not getattr(user, "is_active", False):
        return False
    # audits.manage implies read access, and admins are granted implicitly by has_permission().
    return has_permission(db, user, AUDIT_MANAGE_PERMISSION) or has_permission(
        db, user, AUDIT_READ_PERMISSION
    )


def _can_manage_audits(db: Session, user: User | None) -> bool:
    if not isinstance(user, User) or not getattr(user, "is_active", False):
        return False
    return has_permission(db, user, AUDIT_MANAGE_PERMISSION)


def _is_audit_attendee(db: Session, user: User | None, audit_id: uuid.UUID) -> bool:
    if not isinstance(user, User) or not getattr(user, "is_active", False):
        return False
    return bool(
        db.query(AuditAttendee.id)
        .filter(AuditAttendee.audit_id == audit_id, AuditAttendee.user_id == user.id)
        .first()
    )


def _can_read_audit(db: Session, user: User | None, audit: Audit) -> bool:
    return bool(_can_read_audits(db, user) or _is_audit_attendee(db, user, audit.id))


def _require_audit_access(db: Session, user: User | None, audit: Audit) -> None:
    if not _can_read_audit(db, user, audit):
        raise HTTPException(status_code=403, detail="Audit access required")


def require_audit_read(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_authenticated(request)
    if not _can_read_audits(db, user):
        raise HTTPException(status_code=403, detail="audits.read permission required")
    return user


def require_audit_manage(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_authenticated(request)
    if not _can_manage_audits(db, user):
        raise HTTPException(status_code=403, detail="audits.manage permission required")
    return user


def require_audit_admin(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_authenticated(request)
    if not is_effective_admin(db, user):
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


def _clean_text(
    raw: str | None, *, max_len: int, required: bool = False, label: str = "value"
) -> str:
    v = (raw or "").strip()
    if required and not v:
        raise HTTPException(status_code=400, detail=f"{label} is required")
    if len(v) > max_len:
        raise HTTPException(status_code=400, detail=f"{label} is too long")
    return v


def _clean_rich_text(raw: str | None, *, max_len: int, label: str = "value") -> str:
    """Trim, length-check, and allowlist-sanitise a rich-text HTML field.

    Audit report notes, executive summaries, and finding descriptions are
    rendered as HTML in the UI. KEEN's security model requires the backend to be
    the authoritative sanitiser (the client sanitiser is defence-in-depth only),
    so every stored value passes through the shared allowlist sanitiser here.
    """
    v = _clean_text(raw, max_len=max_len, label=label)
    cleaned = sanitize_rich_text_html(v)
    if len(cleaned) > max_len:
        raise HTTPException(status_code=400, detail=f"{label} is too long")
    return cleaned


def _clean_framework(raw: str | None) -> str:
    v = (raw or settings.default_framework_slug or "").strip()
    if not v:
        raise HTTPException(status_code=400, detail="framework_slug is required")
    if len(v) > 64 or not re.match(r"^[A-Za-z0-9._:-]+$", v):
        raise HTTPException(status_code=400, detail="Invalid framework_slug")
    return v


def _clean_status(raw: str | None, allowed: set[str], *, default: str) -> str:
    v = (raw or default).strip().lower()
    if v not in allowed:
        raise HTTPException(status_code=400, detail="Invalid status")
    return v


def _clean_audit_type(raw: str | None, *, default: str = "internal") -> str:
    v = (raw or default).strip().lower()
    if v not in _AUDIT_TYPES:
        raise HTTPException(status_code=400, detail="Invalid audit_type")
    return v


def _clean_schedule_recurrence(raw: str | None, *, default: str = "once") -> str:
    v = (raw or default).strip().lower()
    if v not in SCHEDULE_RECURRENCES:
        raise HTTPException(status_code=400, detail="Invalid schedule_recurrence")
    return v


def _clean_schedule_interval(raw: int | None) -> int:
    try:
        value = int(raw or 1)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid schedule_interval")
    if value < 1 or value > 120:
        raise HTTPException(
            status_code=400, detail="schedule_interval must be between 1 and 120"
        )
    return value


def _clean_schedule_date_rule(raw: str | None, *, default: str = "exact") -> str:
    v = (raw or default).strip().lower()
    if v not in SCHEDULE_DATE_RULES:
        raise HTTPException(status_code=400, detail="Invalid schedule_date_rule")
    return v


def _clean_schedule_anchor_month(raw: int | None, *, required: bool) -> int | None:
    if raw is None or str(raw).strip() == "":
        if required:
            raise HTTPException(
                status_code=400,
                detail="schedule_anchor_month is required for first weekday schedules",
            )
        return None
    try:
        value = int(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid schedule_anchor_month")
    if value < 1 or value > 12:
        raise HTTPException(
            status_code=400, detail="schedule_anchor_month must be between 1 and 12"
        )
    return value


def _schedule_start_for_rule(
    start_date: date, *, date_rule: str, anchor_month: int | None
) -> date:
    if date_rule in FUZZY_MONTH_DATE_RULES:
        return first_scheduled_rule_date(
            start_date.year, anchor_month or start_date.month, date_rule
        )
    return start_date


def _clean_email(raw: str | None, *, label: str = "email") -> str | None:
    v = (raw or "").strip()
    if not v:
        return None
    if len(v) > 256 or "@" not in v or v.startswith("@") or v.endswith("@"):
        raise HTTPException(status_code=400, detail=f"Invalid {label}")
    if any(ord(c) < 32 or ord(c) == 127 for c in v):
        raise HTTPException(status_code=400, detail=f"Invalid {label}")
    return v


def _looks_like_email(raw: str | None) -> bool:
    v = (raw or "").strip()
    return bool(
        v
        and "@" in v
        and not v.startswith("@")
        and not v.endswith("@")
        and len(v) <= 256
    )


def _user_email_snapshot(user: User | None) -> str | None:
    if user is None:
        return None
    email = getattr(user, "email", None)
    if _looks_like_email(email):
        return str(email).strip()
    # Backwards-compatible fallback for deployments where the Keen username is
    # the user's email address. If usernames are non-email identifiers, admins
    # can set the dedicated user email field instead.
    username = getattr(user, "username", None)
    if _looks_like_email(username):
        return str(username).strip()
    return None


def _audit_status_value(audit: Audit | None) -> str:
    return str(getattr(audit, "status", None) or "open").strip().lower()


def _is_locked_audit(audit: Audit | None) -> bool:
    return _audit_status_value(audit) in _LOCKED_AUDIT_STATUSES


def _require_mutable_audit(audit: Audit, *, action: str = "modify") -> None:
    if _is_locked_audit(audit):
        status = _audit_status_value(audit).replace("_", " ")
        raise HTTPException(
            status_code=409,
            detail=(
                f"This audit is {status} and cannot be {action}. "
                "Change its status back to Open or In progress before editing it."
            ),
        )


def _validate_dates(start: date | None, end: date | None) -> None:
    if start and end and end < start:
        raise HTTPException(
            status_code=400, detail="end_date cannot be before start_date"
        )


def _clean_url(raw: str | None) -> str | None:
    v = (raw or "").strip()
    if not v:
        return None
    if len(v) > 2048:
        raise HTTPException(status_code=400, detail="evidence_url is too long")
    if re.match(r"^(?:javascript|data|vbscript):", v, flags=re.I):
        raise HTTPException(status_code=400, detail="Unsupported evidence_url scheme")
    if v.startswith("//"):
        raise HTTPException(
            status_code=400, detail="Protocol-relative evidence_url is not allowed"
        )
    if v.startswith("/"):
        return v
    try:
        u = urlsplit(v)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid evidence_url")
    if u.scheme.lower() not in {"http", "https"} or not u.netloc:
        raise HTTPException(
            status_code=400, detail="evidence_url must be http(s) or a same-origin path"
        )
    return v


def _event_id_from_url(raw: str | None) -> uuid.UUID | None:
    """Infer an event id only from URLs that actually point at event pages.

    Entity pages such as /risk.html?id=... and /isms-document.html?id=... also
    use an ``id`` query parameter. Treating every page id as an event id causes
    non-event audit samples to fail with "Event not found" before the entity
    reference can be resolved.
    """

    if not raw:
        return None
    try:
        parts = urlsplit(raw)
        path = (parts.path or "").rstrip("/")
        if path and not path.endswith("/event.html") and path != "/event.html":
            return None
        qs = parse_qs(parts.query or "")
        val = (qs.get("id") or [None])[0]
        return _try_uuid(str(val)) if val else None
    except Exception:
        return None


def _audit_or_404(db: Session, audit_id: str) -> Audit:
    aid = _try_uuid(audit_id)
    if not aid:
        raise HTTPException(status_code=400, detail="audit_id must be a UUID")
    row = db.query(Audit).filter(Audit.id == aid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Audit not found")
    return row


def _control_or_404(db: Session, framework: str, control_id_or_ref: str) -> ControlItem:
    val = (control_id_or_ref or "").strip()
    cid = _try_uuid(val)
    q = db.query(ControlItem).filter(ControlItem.framework_slug == framework)
    if cid:
        row = q.filter(ControlItem.id == cid).one_or_none()
    else:
        row = q.filter(ControlItem.ref == val).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail=f"Control not found: {val}")
    return row


def _clause_or_404(
    db: Session, framework: str, clause_id_or_ref: str
) -> FrameworkClause:
    val = (clause_id_or_ref or "").strip()
    cid = _try_uuid(val)
    q = db.query(FrameworkClause).filter(FrameworkClause.framework_slug == framework)
    if cid:
        row = q.filter(FrameworkClause.id == cid).one_or_none()
    else:
        row = q.filter(FrameworkClause.ref == val).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail=f"Clause not found: {val}")
    return row


def _dedupe_scope_values(raw_values: list[str] | None, *, label: str) -> list[str]:
    vals: list[str] = []
    seen: set[str] = set()
    for raw in raw_values or []:
        v = (raw or "").strip()
        if not v or v in seen:
            continue
        seen.add(v)
        vals.append(v)
    if len(vals) > 1000:
        raise HTTPException(status_code=400, detail=f"Too many {label}")
    return vals


def _control_out(c: ControlItem) -> dict[str, Any]:
    return {"id": str(c.id), "ref": c.ref, "title": c.title, "type": c.type}


def _clause_out(c: FrameworkClause) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "ref": c.ref,
        "title": c.title,
        "parent_id": str(c.parent_clause_id) if c.parent_clause_id else None,
        "parent_ref": c.parent.ref if getattr(c, "parent", None) else None,
    }


def _document_or_404(
    db: Session, framework: str, document_id_or_title: str
) -> IsmsDocument:
    val = (document_id_or_title or "").strip()
    did = _try_uuid(val)
    q = db.query(IsmsDocument)
    row = (
        q.filter(IsmsDocument.id == did).one_or_none()
        if did
        else q.filter(IsmsDocument.title == val).one_or_none()
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"ISMS document not found: {val}")
    return row


def _document_out(d: IsmsDocument) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "title": d.title,
        "document_type": d.document_type,
        "external_url": d.external_url,
        "filename": d.filename,
    }


_AUDIT_SAMPLE_ENTITY_ALIASES = {
    "risk": "risk",
    "cia_risk": "risk",
    "pestle": "pestle_item",
    "pestle_item": "pestle_item",
    "interested_party": "interested_party",
    "interested-party": "interested_party",
    "objective": "isms_objective",
    "isms_objective": "isms_objective",
    "document": "isms_document",
    "isms_document": "isms_document",
    "bookstack_section": "bookstack_section",
    "org": "isms_org_node",
    "org_node": "isms_org_node",
    "isms_org_node": "isms_org_node",
    "asset": "isms_asset",
    "isms_asset": "isms_asset",
    "access": "isms_access_control_matrix",
    "access_control_matrix": "isms_access_control_matrix",
    "isms_access_control_matrix": "isms_access_control_matrix",
    "application_configuration": "isms_application_configuration",
    "app_config": "isms_application_configuration",
    "isms_application_configuration": "isms_application_configuration",
    "meeting": "isms_meeting",
    "isms_meeting": "isms_meeting",
    "effectiveness": "isms_effectiveness_measure",
    "effectiveness_measure": "isms_effectiveness_measure",
    "isms_effectiveness_measure": "isms_effectiveness_measure",
    "metric": "isms_effectiveness_metric",
    "effectiveness_metric": "isms_effectiveness_metric",
    "isms_effectiveness_metric": "isms_effectiveness_metric",
}

_AUDIT_SAMPLE_ENTITY_LABELS = {
    "risk": "CIA Triad risk",
    "pestle_item": "PESTLE(E) item",
    "interested_party": "Interested party",
    "isms_objective": "ISMS objective",
    "isms_document": "ISMS document",
    "bookstack_section": "BookStack policy snapshot",
    "isms_org_node": "Organisation chart node",
    "isms_asset": "ISMS asset",
    "isms_access_control_matrix": "Access control matrix row",
    "isms_application_configuration": "Application configuration row",
    "isms_meeting": "ISMS meeting",
    "isms_effectiveness_measure": "ISMS effectiveness measure",
    "isms_effectiveness_metric": "ISMS effectiveness metric",
}


def _short_text(raw: str | None, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", (raw or "").strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _normalise_audit_sample_entity_type(raw: str | None) -> str | None:
    value = (raw or "").strip().lower().replace("-", "_")
    if not value:
        return None
    entity_type = _AUDIT_SAMPLE_ENTITY_ALIASES.get(value)
    if not entity_type:
        raise HTTPException(status_code=400, detail="Unsupported sampled entity type")
    return entity_type


def _clean_optional_entity_id(raw: uuid.UUID | str | None) -> uuid.UUID | None:
    if raw is None:
        return None
    if isinstance(raw, uuid.UUID):
        return raw
    return _try_uuid(str(raw))


def _entity_href(entity_type: str, entity_id: uuid.UUID, framework: str) -> str:
    eid = str(entity_id)
    fw = framework or settings.default_framework_slug or ""
    fwq = f"&framework={fw}" if fw else ""
    fwfirst = f"?framework={fw}" if fw else ""
    if entity_type == "risk":
        return f"/risk.html?id={eid}{fwq}"
    if entity_type == "pestle_item":
        return f"/pestle_item.html?id={eid}{fwq}"
    if entity_type == "interested_party":
        return f"/interested_party.html?id={eid}{fwq}"
    if entity_type == "isms_objective":
        return (
            f"/isms.html{fwfirst}&tab=objectives" if fw else "/isms.html?tab=objectives"
        )
    if entity_type == "isms_document":
        return (
            f"/isms.html{fwfirst}&tab=documents" if fw else "/isms.html?tab=documents"
        )
    if entity_type == "bookstack_section":
        return f"/api/v1/isms/bookstack-sections/{eid}/snapshot"
    if entity_type == "isms_org_node":
        return f"/isms.html{fwfirst}&tab=org" if fw else "/isms.html?tab=org"
    if entity_type == "isms_asset":
        return f"/isms.html{fwfirst}&tab=assets" if fw else "/isms.html?tab=assets"
    if entity_type == "isms_access_control_matrix":
        return f"/isms.html{fwfirst}&tab=access" if fw else "/isms.html?tab=access"
    if entity_type == "isms_application_configuration":
        return (
            f"/isms.html{fwfirst}&tab=appconfig" if fw else "/isms.html?tab=appconfig"
        )
    if entity_type == "isms_effectiveness_measure":
        return f"/isms-effectiveness-measure.html?id={eid}{fwq}"
    if entity_type == "isms_effectiveness_metric":
        return f"/isms-effectiveness-metric.html?id={eid}{fwq}"
    if entity_type == "isms_meeting":
        return f"/isms-meeting.html?id={eid}{fwq}"
    return "/audits.html"


def _risk_sample_controls(
    db: Session, risk_id: uuid.UUID, framework: str
) -> list[dict[str, Any]]:
    rows = (
        db.query(ControlItem)
        .join(RiskControlLink, RiskControlLink.control_item_id == ControlItem.id)
        .filter(
            RiskControlLink.risk_id == risk_id,
            RiskControlLink.framework_slug == framework,
            ControlItem.framework_slug == framework,
        )
        .order_by(ControlItem.ref.asc())
        .all()
    )
    return [_control_out(c) for c in rows]


def _pestle_sample_controls(
    db: Session, item_id: uuid.UUID, framework: str
) -> list[dict[str, Any]]:
    rows = (
        db.query(ControlItem)
        .join(ControlClauseLink, ControlClauseLink.control_item_id == ControlItem.id)
        .join(
            PestleClauseRelevance,
            PestleClauseRelevance.clause_id == ControlClauseLink.clause_id,
        )
        .join(
            PestleRelevanceLevel,
            PestleRelevanceLevel.id == PestleClauseRelevance.relevance_id,
        )
        .filter(
            PestleClauseRelevance.pestle_item_id == item_id,
            ControlItem.framework_slug == framework,
            PestleRelevanceLevel.code != "na",
        )
        .distinct()
        .order_by(ControlItem.ref.asc())
        .all()
    )
    return [_control_out(c) for c in rows]


def _interested_party_sample_controls(
    db: Session, party_id: uuid.UUID, framework: str
) -> list[dict[str, Any]]:
    rows = (
        db.query(ControlItem)
        .join(
            InterestedPartyControlLink,
            InterestedPartyControlLink.control_item_id == ControlItem.id,
        )
        .filter(
            InterestedPartyControlLink.interested_party_id == party_id,
            InterestedPartyControlLink.framework_slug == framework,
            ControlItem.framework_slug == framework,
        )
        .order_by(ControlItem.ref.asc())
        .all()
    )
    return [_control_out(c) for c in rows]


def _isms_sample_controls(
    db: Session, entity_type: str, entity_id: uuid.UUID, framework: str
) -> list[dict[str, Any]]:
    isms_type = entity_type.replace("isms_", "", 1)
    if isms_type == "asset":
        isms_type = "asset"
    rows = (
        db.query(ControlItem)
        .join(
            IsmsEntityControlLink,
            IsmsEntityControlLink.control_item_id == ControlItem.id,
        )
        .filter(
            IsmsEntityControlLink.entity_type == isms_type,
            IsmsEntityControlLink.entity_id == entity_id,
            IsmsEntityControlLink.framework_slug == framework,
            ControlItem.framework_slug == framework,
        )
        .order_by(ControlItem.ref.asc())
        .all()
    )
    return [_control_out(c) for c in rows]


def _audit_sample_entity_reference(
    db: Session,
    *,
    framework: str,
    entity_type: str | None,
    entity_id: uuid.UUID | None,
    required: bool = True,
) -> dict[str, Any] | None:
    if not entity_type and not entity_id:
        return None
    if not entity_type or not entity_id:
        raise HTTPException(
            status_code=400,
            detail="entity_type and entity_id must be provided together",
        )
    entity_type = _normalise_audit_sample_entity_type(entity_type)
    if entity_type is None:
        return None
    label = _AUDIT_SAMPLE_ENTITY_LABELS.get(entity_type, "Keen item")
    controls: list[dict[str, Any]] = []
    title = label
    subtitle = ""
    row: Any = None

    if entity_type == "risk":
        row = db.query(Risk).filter(Risk.id == entity_id).one_or_none()
        if row:
            asset = getattr(row, "asset", None)
            title = f"CIA Triad risk: {getattr(asset, 'name', '') or 'Asset'}"
            if row.threat_summary:
                subtitle = _short_text(row.threat_summary, 220)
            controls = _risk_sample_controls(db, row.id, framework)
    elif entity_type == "pestle_item":
        row = (
            db.query(PestleItem)
            .filter(PestleItem.id == entity_id, PestleItem.framework_slug == framework)
            .one_or_none()
        )
        if row:
            title = f"PESTLE(E): {_short_text(row.item, 180) or row.type}"
            subtitle = f"{row.type} · {row.lens}".strip(" ·")
            controls = _pestle_sample_controls(db, row.id, framework)
    elif entity_type == "interested_party":
        row = (
            db.query(InterestedParty)
            .filter(
                InterestedParty.id == entity_id,
                InterestedParty.framework_slug == framework,
            )
            .one_or_none()
        )
        if row:
            name = getattr(getattr(row, "name", None), "name", "") or "Interested party"
            nature = (
                getattr(getattr(row, "nature", None), "name", "")
                or "Nature of interest"
            )
            title = f"Interested party: {name} — {nature}"
            subtitle = _short_text(row.note, 220)
            controls = _interested_party_sample_controls(db, row.id, framework)
    elif entity_type == "isms_objective":
        row = (
            db.query(IsmsObjective).filter(IsmsObjective.id == entity_id).one_or_none()
        )
        if row:
            title = f"ISMS objective: {_short_text(row.goal or row.requirement, 180)}"
            subtitle = _short_text(row.metric or row.requirement, 220)
            controls = _isms_sample_controls(db, entity_type, row.id, framework)
    elif entity_type == "isms_document":
        row = db.query(IsmsDocument).filter(IsmsDocument.id == entity_id).one_or_none()
        if row:
            title = f"ISMS document: {row.title or 'Document'}"
            subtitle = row.document_type or ""
            controls = _isms_sample_controls(db, entity_type, row.id, framework)
    elif entity_type == "bookstack_section":
        row = db.query(BookStackSectionEvidence).filter(BookStackSectionEvidence.id == entity_id).one_or_none()
        if row and row.target_control and row.target_control.framework_slug == framework:
            controls = [_control_out(row.target_control)]
        elif row and row.target_clause and row.target_clause.framework_slug == framework:
            linked = db.query(ControlItem).join(ControlClauseLink, ControlClauseLink.control_item_id == ControlItem.id).filter(
                ControlClauseLink.clause_id == row.target_clause_id,
                ControlItem.framework_slug == framework,
            ).all()
            controls = [_control_out(c) for c in linked]
        else:
            row = None  # A snapshot may only be sampled under its target framework.
        if row:
            title = f"BookStack policy: {row.page_title}"
            subtitle = f"Revision {row.revision_count or 'unknown'} · SHA-256 {row.sha256}"
    elif entity_type == "isms_org_node":
        row = db.query(IsmsOrgNode).filter(IsmsOrgNode.id == entity_id).one_or_none()
        if row:
            title = f"Organisation chart: {row.name or 'Node'}"
            subtitle = row.node_type or ""
            controls = _isms_sample_controls(db, entity_type, row.id, framework)
    elif entity_type == "isms_asset":
        row = db.query(RiskAsset).filter(RiskAsset.id == entity_id).one_or_none()
        if row:
            title = f"ISMS asset: {row.name or 'Asset'}"
            subtitle = " / ".join(
                x
                for x in [
                    getattr(getattr(row, "category", None), "name", ""),
                    getattr(getattr(row, "subcategory", None), "name", ""),
                ]
                if x
            )
            controls = _isms_sample_controls(db, entity_type, row.id, framework)
    elif entity_type == "isms_access_control_matrix":
        row = (
            db.query(IsmsAccessControlMatrixEntry)
            .filter(IsmsAccessControlMatrixEntry.id == entity_id)
            .one_or_none()
        )
        if row:
            title = f"Access control matrix: {_short_text(row.task_action, 180)}"
            subtitle = row.status or ""
    elif entity_type == "isms_application_configuration":
        row = (
            db.query(IsmsApplicationConfigurationEntry)
            .filter(IsmsApplicationConfigurationEntry.id == entity_id)
            .one_or_none()
        )
        if row:
            process = getattr(getattr(row, "business_process", None), "name", "")
            source = (
                getattr(getattr(row, "document", None), "title", "")
                or getattr(getattr(row, "user", None), "username", "")
                or getattr(getattr(row, "asset", None), "name", "")
                or getattr(getattr(row, "org_node", None), "name", "")
                or row.source_type
            )
            title = f"Application configuration: {source}"
            subtitle = " · ".join(x for x in [process, row.value] if x)
    elif entity_type == "isms_effectiveness_measure":
        row = (
            db.query(IsmsEffectivenessMeasure)
            .filter(
                IsmsEffectivenessMeasure.id == entity_id,
                IsmsEffectivenessMeasure.framework_slug == framework,
            )
            .one_or_none()
        )
        if row:
            title = f"ISMS effectiveness measure: {_short_text(row.summary or row.metric, 180)}"
            subtitle = _short_text(row.effectiveness_measure, 220)
            controls = _isms_sample_controls(db, entity_type, row.id, framework)
    elif entity_type == "isms_effectiveness_metric":
        row = (
            db.query(IsmsEffectivenessMetricEntry)
            .join(
                IsmsEffectivenessMeasure,
                IsmsEffectivenessMeasure.id == IsmsEffectivenessMetricEntry.measure_id,
            )
            .filter(
                IsmsEffectivenessMetricEntry.id == entity_id,
                IsmsEffectivenessMeasure.framework_slug == framework,
            )
            .one_or_none()
        )
        if row:
            measure = row.measure
            title = (
                f"ISMS metric: {_short_text(measure.metric if measure else '', 160)}"
            )
            value_parts = []
            if row.metric_value is not None:
                value_parts.append(f"{row.metric_value:g}")
            if row.metric_unit:
                value_parts.append(row.metric_unit)
            if row.qualitative_value:
                value_parts.append(_short_text(row.qualitative_value, 120))
            subtitle = (
                " ".join(value_parts).strip() or row.source_title or "Metric entry"
            )
            if measure:
                controls = _isms_sample_controls(
                    db, "isms_effectiveness_measure", measure.id, framework
                )
    elif entity_type == "isms_meeting":
        row = db.query(IsmsMeeting).filter(IsmsMeeting.id == entity_id).one_or_none()
        if row:
            title = f"ISMS meeting: {row.title or 'Meeting'}"
            subtitle = row.date.isoformat() if row.date else ""
            controls = _isms_sample_controls(db, entity_type, row.id, framework)

    if row is None:
        if required:
            raise HTTPException(status_code=404, detail="Sampled entity not found")
        return {
            "type": entity_type,
            "id": str(entity_id),
            "kind": label,
            "title": label,
            "subtitle": "This sampled item no longer exists.",
            "href": _entity_href(entity_type, entity_id, framework),
            "controls": [],
            "missing": True,
        }

    return {
        "type": entity_type,
        "id": str(entity_id),
        "kind": label,
        "title": title,
        "subtitle": subtitle,
        "href": _entity_href(entity_type, entity_id, framework),
        "controls": controls,
        "missing": False,
    }


def _linked_scope_for_documents(
    db: Session, audit: Audit, documents: list[IsmsDocument]
) -> tuple[list[ControlItem], list[FrameworkClause]]:
    doc_ids = [x.id for x in documents or []]
    if not doc_ids:
        return [], []
    controls = (
        db.query(ControlItem)
        .join(
            IsmsEntityControlLink,
            IsmsEntityControlLink.control_item_id == ControlItem.id,
        )
        .filter(
            IsmsEntityControlLink.entity_type == "document",
            IsmsEntityControlLink.entity_id.in_(doc_ids),
            IsmsEntityControlLink.framework_slug == audit.framework_slug,
            ControlItem.framework_slug == audit.framework_slug,
        )
        .order_by(ControlItem.type.asc(), ControlItem.ref.asc())
        .all()
    )
    clauses = (
        db.query(FrameworkClause)
        .join(
            IsmsEntityClauseLink, IsmsEntityClauseLink.clause_id == FrameworkClause.id
        )
        .filter(
            IsmsEntityClauseLink.entity_type == "document",
            IsmsEntityClauseLink.entity_id.in_(doc_ids),
            IsmsEntityClauseLink.framework_slug == audit.framework_slug,
            FrameworkClause.framework_slug == audit.framework_slug,
        )
        .order_by(FrameworkClause.sort_order.asc(), FrameworkClause.ref.asc())
        .all()
    )
    return controls, clauses


def _linked_controls_for_clauses(
    db: Session, audit: Audit, clause_ids: list[uuid.UUID]
) -> list[ControlItem]:
    if not clause_ids:
        return []
    return (
        db.query(ControlItem)
        .join(ControlClauseLink, ControlClauseLink.control_item_id == ControlItem.id)
        .filter(ControlClauseLink.clause_id.in_(clause_ids))
        .filter(ControlItem.framework_slug == audit.framework_slug)
        .order_by(ControlItem.type.asc(), ControlItem.ref.asc())
        .all()
    )


def _selectable_clause_descendants(
    db: Session, framework: str, clause: FrameworkClause
) -> list[FrameworkClause]:
    """Return the selectable descendants for an audit-scope clause.

    Framework parent clauses such as ``8`` are headings/groupings. Controls are
    linked at the individual child-clause level, so audit scope should never
    persist a parent clause when that parent has children. Accepting a parent in
    API input is still useful: it becomes a shortcut for selecting all leaf
    descendants underneath that parent.
    """

    children = (
        db.query(FrameworkClause)
        .filter(FrameworkClause.framework_slug == framework)
        .filter(FrameworkClause.parent_clause_id == clause.id)
        .order_by(FrameworkClause.sort_order.asc(), FrameworkClause.ref.asc())
        .all()
    )
    if not children:
        return [clause]

    out: list[FrameworkClause] = []
    for child in children:
        out.extend(_selectable_clause_descendants(db, framework, child))
    return out


def _normalise_scope_clauses(
    db: Session, framework: str, clauses: list[FrameworkClause]
) -> list[FrameworkClause]:
    """Expand parent clauses to leaf/selectable clauses and deduplicate."""

    by_id: dict[uuid.UUID, FrameworkClause] = {}
    for clause in clauses:
        for selectable in _selectable_clause_descendants(db, framework, clause):
            by_id.setdefault(selectable.id, selectable)
    out = list(by_id.values())
    out.sort(key=lambda c: (int(c.sort_order or 0), str(c.ref or "")))
    return out


def _replace_audit_scope(
    db: Session,
    audit: Audit,
    *,
    control_values: list[str] | None,
    clause_values: list[str] | None,
    document_values: list[str] | None = None,
    replace_controls: bool = True,
    replace_clauses: bool = True,
    replace_documents: bool = True,
) -> tuple[list[ControlItem], list[FrameworkClause], list[IsmsDocument]]:
    documents: list[IsmsDocument] = []
    if replace_documents:
        document_refs = _dedupe_scope_values(document_values, label="documents")
        by_id: dict[uuid.UUID, IsmsDocument] = {}
        for d in [_document_or_404(db, audit.framework_slug, v) for v in document_refs]:
            by_id[d.id] = d
        documents = sorted(
            by_id.values(),
            key=lambda d: (str(d.document_type or ""), str(d.title or "")),
        )
        db.query(AuditScopedIsmsDocument).filter(
            AuditScopedIsmsDocument.audit_id == audit.id
        ).delete()
    else:
        documents = (
            db.query(IsmsDocument)
            .join(
                AuditScopedIsmsDocument,
                AuditScopedIsmsDocument.document_id == IsmsDocument.id,
            )
            .filter(AuditScopedIsmsDocument.audit_id == audit.id)
            .order_by(IsmsDocument.document_type.asc(), IsmsDocument.title.asc())
            .all()
        )

    document_controls, document_clauses = _linked_scope_for_documents(
        db, audit, documents
    )
    effective_replace_clauses = replace_clauses or bool(
        replace_documents and document_clauses
    )
    effective_replace_controls = replace_controls or bool(
        replace_documents and (document_controls or document_clauses)
    )

    controls: list[ControlItem] = []
    clauses: list[FrameworkClause] = []

    if effective_replace_clauses:
        requested_clauses: list[FrameworkClause] = []
        if replace_clauses:
            clause_refs = _dedupe_scope_values(clause_values, label="clauses")
            requested_clauses.extend(
                [_clause_or_404(db, audit.framework_slug, v) for v in clause_refs]
            )
        else:
            requested_clauses.extend(
                db.query(FrameworkClause)
                .join(
                    AuditScopedClause, AuditScopedClause.clause_id == FrameworkClause.id
                )
                .filter(AuditScopedClause.audit_id == audit.id)
                .all()
            )
        requested_clauses.extend(document_clauses)
        clauses = _normalise_scope_clauses(db, audit.framework_slug, requested_clauses)
        db.query(AuditScopedClause).filter(
            AuditScopedClause.audit_id == audit.id
        ).delete()

    if effective_replace_controls:
        control_by_id: dict[uuid.UUID, ControlItem] = {}
        if replace_controls:
            control_refs = _dedupe_scope_values(control_values, label="controls")
            for c in [
                _control_or_404(db, audit.framework_slug, v) for v in control_refs
            ]:
                control_by_id[c.id] = c
        else:
            for c in (
                db.query(ControlItem)
                .join(
                    AuditScopedControl,
                    AuditScopedControl.control_item_id == ControlItem.id,
                )
                .filter(AuditScopedControl.audit_id == audit.id)
                .all()
            ):
                control_by_id[c.id] = c
        for c in document_controls:
            control_by_id.setdefault(c.id, c)

        auto_clause_ids = (
            [x.id for x in clauses]
            if effective_replace_clauses
            else [
                x[0]
                for x in db.query(AuditScopedClause.clause_id)
                .filter(AuditScopedClause.audit_id == audit.id)
                .all()
            ]
        )
        if auto_clause_ids:
            for c in _linked_controls_for_clauses(db, audit, auto_clause_ids):
                control_by_id.setdefault(c.id, c)

        controls = list(control_by_id.values())
        controls.sort(key=lambda c: (str(c.type or ""), str(c.ref or "")))
        db.query(AuditScopedControl).filter(
            AuditScopedControl.audit_id == audit.id
        ).delete()

    now = _utcnow()
    if effective_replace_controls:
        for c in controls:
            db.add(
                AuditScopedControl(
                    audit_id=audit.id, control_item_id=c.id, created_at=now
                )
            )
    if effective_replace_clauses:
        for c in clauses:
            db.add(AuditScopedClause(audit_id=audit.id, clause_id=c.id, created_at=now))
    if replace_documents:
        for d in documents:
            db.add(
                AuditScopedIsmsDocument(
                    audit_id=audit.id, document_id=d.id, created_at=now
                )
            )

    audit.updated_at = now
    db.add(audit)
    return controls, clauses, documents


def _scope_response(
    controls: list[ControlItem],
    clauses: list[FrameworkClause],
    documents: list[IsmsDocument] | None = None,
) -> dict[str, Any]:
    return {
        "ok": True,
        "scoped_controls": [_control_out(c) for c in controls],
        "scoped_clauses": [_clause_out(c) for c in clauses],
        "scoped_documents": [_document_out(d) for d in (documents or [])],
    }


def _can_see_event(db: Session, user: User, event_id: uuid.UUID) -> Event:
    row = db.query(Event).filter(Event.id == event_id).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")
    if row.source == "diary" and not is_diary_event_visible(db, user, event_id):
        raise HTTPException(status_code=404, detail="Event not found")
    return row


def _usernames(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    rows = db.query(User.id, User.username).filter(User.id.in_(ids)).all()
    return {uid: uname for uid, uname in rows}


def _iso_dt(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


def _iso_date(v: date | None) -> str | None:
    return v.isoformat() if v else None


def _audit_summary(
    db: Session, a: Audit, authors: dict[uuid.UUID, str] | None = None
) -> dict[str, Any]:
    authors = authors or {}
    control_scope_count = int(
        db.query(func.count(AuditScopedControl.control_item_id))
        .filter(AuditScopedControl.audit_id == a.id)
        .scalar()
        or 0
    )
    clause_scope_count = int(
        db.query(func.count(AuditScopedClause.clause_id))
        .filter(AuditScopedClause.audit_id == a.id)
        .scalar()
        or 0
    )
    document_scope_count = int(
        db.query(func.count(AuditScopedIsmsDocument.document_id))
        .filter(AuditScopedIsmsDocument.audit_id == a.id)
        .scalar()
        or 0
    )
    counts = {
        "evidence_count": int(
            db.query(func.count(AuditEvidence.id))
            .filter(AuditEvidence.audit_id == a.id)
            .scalar()
            or 0
        ),
        "scope_count": control_scope_count + clause_scope_count + document_scope_count,
        "control_scope_count": control_scope_count,
        "clause_scope_count": clause_scope_count,
        "document_scope_count": document_scope_count,
        "attendee_count": int(
            db.query(func.count(AuditAttendee.id))
            .filter(AuditAttendee.audit_id == a.id)
            .scalar()
            or 0
        ),
        "finding_count": int(
            db.query(func.count(AuditFinding.id))
            .filter(AuditFinding.audit_id == a.id)
            .scalar()
            or 0
        ),
    }
    return {
        "id": str(a.id),
        "title": a.title,
        "framework_slug": a.framework_slug,
        "status": a.status,
        "audit_type": a.audit_type or "internal",
        "start_date": _iso_date(a.start_date),
        "end_date": _iso_date(a.end_date),
        "schedule_recurrence": a.schedule_recurrence or "once",
        "schedule_interval": int(a.schedule_interval or 1),
        "schedule_date_rule": a.schedule_date_rule or "exact",
        "schedule_anchor_month": a.schedule_anchor_month,
        "schedule_next_run_date": _iso_date(a.schedule_next_run_date),
        "schedule_until_date": _iso_date(a.schedule_until_date),
        "schedule_last_run_date": _iso_date(a.schedule_last_run_date),
        "schedule_last_created_audit_id": (
            str(a.schedule_last_created_audit_id)
            if a.schedule_last_created_audit_id
            else None
        ),
        "created_by_user_id": (
            str(a.created_by_user_id) if a.created_by_user_id else None
        ),
        "created_by_username": (
            authors.get(a.created_by_user_id, "") if a.created_by_user_id else ""
        ),
        "created_at": _iso_dt(a.created_at),
        "updated_at": _iso_dt(a.updated_at),
        "notes": a.notes or "",
        "report_notes": a.notes or "",
        "executive_summary": a.executive_summary or "",
        "has_final_report": bool(a.final_report_storage_uri),
        "final_report": _report_dict(a),
        **counts,
    }


def _report_dict(a: Audit) -> dict[str, Any] | None:
    if not a.final_report_storage_uri:
        return None
    return {
        "filename": a.final_report_filename,
        "content_type": a.final_report_content_type,
        "sha256": a.final_report_sha256,
        "size_bytes": a.final_report_size_bytes,
        "uploaded_at": _iso_dt(a.final_report_uploaded_at),
        "download_url": f"/api/v1/audits/{a.id}/report",
    }


def _evidence_dict(
    db: Session, e: AuditEvidence, added_by: dict[uuid.UUID, str] | None = None
) -> dict[str, Any]:
    added_by = added_by or {}
    controls: list[dict[str, Any]] = []
    ev = e.event
    entity = _audit_sample_entity_reference(
        db,
        framework=e.audit.framework_slug,
        entity_type=e.entity_type,
        entity_id=e.entity_id,
        required=False,
    )
    if ev is not None:
        rows = (
            db.query(ControlItem)
            .join(Mapping, Mapping.control_item_id == ControlItem.id)
            .filter(
                Mapping.event_id == ev.id,
                ControlItem.framework_slug == e.audit.framework_slug,
            )
            .order_by(ControlItem.ref.asc())
            .all()
        )
        controls = [{"id": str(c.id), "ref": c.ref, "title": c.title} for c in rows]
    elif entity is not None:
        controls = list(entity.get("controls") or [])
    return {
        "id": str(e.id),
        "audit_id": str(e.audit_id),
        "event_id": str(e.event_id) if e.event_id else None,
        "entity_type": e.entity_type,
        "entity_id": str(e.entity_id) if e.entity_id else None,
        "evidence_url": e.evidence_url,
        "title": e.title,
        "notes": e.notes or "",
        "added_by_user_id": str(e.added_by_user_id) if e.added_by_user_id else None,
        "added_by_username": (
            added_by.get(e.added_by_user_id, "") if e.added_by_user_id else ""
        ),
        "added_at": _iso_dt(e.added_at),
        "event": (
            None
            if ev is None
            else {
                "id": str(ev.id),
                "timestamp": _iso_dt(ev.timestamp),
                "source": ev.source,
                "system": ev.system,
                "actor": ev.actor,
                "action": ev.action,
                "outcome": ev.outcome,
                "severity": ev.severity,
                "summary": ev.summary,
                "controls": controls,
            }
        ),
        "entity": entity,
        "controls": controls,
    }


def _attendee_dict(x: AuditAttendee) -> dict[str, Any]:
    u = getattr(x, "user", None)
    return {
        "id": str(x.id),
        "audit_id": str(x.audit_id),
        "user_id": str(x.user_id) if x.user_id else None,
        "username": u.username if u else None,
        "name": x.name,
        "email": x.email or _user_email_snapshot(u),
        "role": x.role,
        "created_at": _iso_dt(x.created_at),
    }


def _finding_dict(
    x: AuditFinding, authors: dict[uuid.UUID, str] | None = None
) -> dict[str, Any]:
    c = x.control
    authors = authors or {}
    return {
        "id": str(x.id),
        "audit_id": str(x.audit_id),
        "kind": x.kind,
        "title": x.title,
        "description": x.description or "",
        "status": x.status,
        "control": (
            None if c is None else {"id": str(c.id), "ref": c.ref, "title": c.title}
        ),
        "created_by_user_id": (
            str(x.created_by_user_id) if x.created_by_user_id else None
        ),
        "created_by_username": (
            authors.get(x.created_by_user_id, "") if x.created_by_user_id else ""
        ),
        "created_at": _iso_dt(x.created_at),
        "updated_at": _iso_dt(x.updated_at),
    }


class AuditCreatePayload(BaseModel):
    title: str = Field(..., min_length=1, max_length=256)
    framework_slug: str | None = Field(default=None, max_length=64)
    audit_type: str | None = Field(default="internal", max_length=16)
    start_date: date | None = None
    end_date: date | None = None
    notes: str | None = Field(default="", max_length=20000)
    report_notes: str | None = Field(default=None, max_length=20000)
    executive_summary: str | None = Field(default="", max_length=20000)
    controls: list[str] = Field(default_factory=list)
    clauses: list[str] = Field(default_factory=list)
    documents: list[str] = Field(default_factory=list)


class AuditUpdatePayload(BaseModel):
    title: str | None = Field(default=None, max_length=256)
    framework_slug: str | None = Field(default=None, max_length=64)
    status: str | None = Field(default=None, max_length=32)
    audit_type: str | None = Field(default=None, max_length=16)
    start_date: date | None = None
    end_date: date | None = None
    notes: str | None = Field(default=None, max_length=20000)
    report_notes: str | None = Field(default=None, max_length=20000)
    executive_summary: str | None = Field(default=None, max_length=20000)


class AuditEvidencePayload(BaseModel):
    event_id: uuid.UUID | None = None
    entity_type: str | None = Field(default=None, max_length=64)
    entity_id: uuid.UUID | None = None
    evidence_url: str | None = Field(default=None, max_length=2048)
    title: str | None = Field(default=None, max_length=512)
    notes: str | None = Field(default="", max_length=20000)


class AuditEvidenceUpdatePayload(BaseModel):
    evidence_url: str | None = Field(default=None, max_length=2048)
    title: str | None = Field(default=None, max_length=512)
    notes: str | None = Field(default=None, max_length=20000)


class AuditControlsPayload(BaseModel):
    controls: list[str] = Field(default_factory=list)


class AuditScopePayload(BaseModel):
    controls: list[str] = Field(default_factory=list)
    clauses: list[str] = Field(default_factory=list)
    documents: list[str] = Field(default_factory=list)


class AuditAttendeePayload(BaseModel):
    user_id: uuid.UUID | None = None
    name: str | None = Field(default=None, max_length=256)
    email: str | None = Field(default=None, max_length=256)
    role: str | None = Field(default=None, max_length=128)


class AuditFindingPayload(BaseModel):
    kind: str = Field(..., max_length=32)
    title: str = Field(..., min_length=1, max_length=256)
    description: str | None = Field(default="", max_length=50000)
    control: str | None = Field(default=None, max_length=128)
    status: str | None = Field(default="open", max_length=32)


class AuditFindingUpdatePayload(BaseModel):
    kind: str | None = Field(default=None, max_length=32)
    title: str | None = Field(default=None, max_length=256)
    description: str | None = Field(default=None, max_length=50000)
    control: str | None = Field(default=None, max_length=128)
    status: str | None = Field(default=None, max_length=32)


class ScheduledAuditAttendeePayload(BaseModel):
    user_id: uuid.UUID | None = None
    name: str | None = Field(default=None, max_length=256)
    email: str | None = Field(default=None, max_length=256)
    role: str | None = Field(default=None, max_length=128)


class ScheduledAuditPayload(BaseModel):
    title: str = Field(..., min_length=1, max_length=256)
    framework_slug: str | None = Field(default=None, max_length=64)
    audit_type: str | None = Field(default="internal", max_length=16)
    start_date: date
    end_date: date | None = None
    schedule_recurrence: str | None = Field(default="once", max_length=32)
    schedule_interval: int | None = Field(default=1, ge=1, le=120)
    schedule_date_rule: str | None = Field(default="exact", max_length=32)
    schedule_anchor_month: int | None = Field(default=None, ge=1, le=12)
    schedule_until_date: date | None = None
    report_notes: str | None = Field(default="", max_length=20000)
    executive_summary: str | None = Field(default="", max_length=20000)
    controls: list[str] = Field(default_factory=list)
    clauses: list[str] = Field(default_factory=list)
    documents: list[str] = Field(default_factory=list)
    attendees: list[ScheduledAuditAttendeePayload] = Field(default_factory=list)


class ScheduledAuditUpdatePayload(BaseModel):
    title: str | None = Field(default=None, max_length=256)
    framework_slug: str | None = Field(default=None, max_length=64)
    audit_type: str | None = Field(default=None, max_length=16)
    start_date: date | None = None
    end_date: date | None = None
    schedule_recurrence: str | None = Field(default=None, max_length=32)
    schedule_interval: int | None = Field(default=None, ge=1, le=120)
    schedule_date_rule: str | None = Field(default=None, max_length=32)
    schedule_anchor_month: int | None = Field(default=None, ge=1, le=12)
    schedule_until_date: date | None = None
    report_notes: str | None = Field(default=None, max_length=20000)
    executive_summary: str | None = Field(default=None, max_length=20000)
    controls: list[str] | None = None
    clauses: list[str] | None = None
    documents: list[str] | None = None
    attendees: list[ScheduledAuditAttendeePayload] | None = None


def _schedule_summary(
    db: Session, a: Audit, authors: dict[uuid.UUID, str] | None = None
) -> dict[str, Any]:
    item = _audit_summary(db, a, authors)
    item["attendees"] = [
        _attendee_dict(x)
        for x in db.query(AuditAttendee)
        .filter(AuditAttendee.audit_id == a.id)
        .order_by(AuditAttendee.name.asc())
        .all()
    ]
    return item


def _replace_template_attendees(
    db: Session, *, audit: Audit, attendees: list[ScheduledAuditAttendeePayload]
) -> list[AuditAttendee]:
    db.query(AuditAttendee).filter(AuditAttendee.audit_id == audit.id).delete()
    rows: list[AuditAttendee] = []
    now = _utcnow()
    for payload in attendees or []:
        linked_user = None
        if payload.user_id is not None:
            linked_user = (
                db.query(User)
                .filter(User.id == payload.user_id, User.is_active.is_(True))
                .one_or_none()
            )
            if not linked_user:
                raise HTTPException(
                    status_code=400, detail="Unknown or inactive attendee user"
                )
        display_name = _clean_text(
            payload.name,
            max_len=256,
            required=linked_user is None,
            label="attendee name",
        )
        if linked_user is not None and not display_name:
            display_name = linked_user.username
        explicit_email = _clean_email(payload.email, label="attendee email")
        row = AuditAttendee(
            audit_id=audit.id,
            user_id=linked_user.id if linked_user is not None else None,
            name=display_name,
            email=explicit_email or _user_email_snapshot(linked_user),
            role=_clean_text(payload.role, max_len=128, label="attendee role") or None,
            created_at=now,
        )
        db.add(row)
        rows.append(row)
    return rows


def _apply_schedule_fields(
    audit: Audit,
    *,
    recurrence: str,
    interval: int,
    next_run_date: date | None,
    until_date: date | None,
    date_rule: str = "exact",
    anchor_month: int | None = None,
) -> None:
    if until_date and next_run_date and until_date < next_run_date:
        raise HTTPException(
            status_code=400,
            detail="schedule_until_date cannot be before the next run date",
        )
    if date_rule in FUZZY_MONTH_DATE_RULES and recurrence == "weekly":
        raise HTTPException(
            status_code=400,
            detail="First weekday schedules can recur monthly, quarterly or yearly",
        )
    audit.schedule_recurrence = recurrence
    audit.schedule_interval = interval
    audit.schedule_date_rule = date_rule
    audit.schedule_anchor_month = anchor_month if date_rule != "exact" else None
    audit.schedule_next_run_date = next_run_date
    audit.schedule_until_date = until_date


def _template_occurrences_payload(
    audit: Audit, *, start: date, end: date
) -> list[dict[str, Any]]:
    return [
        {
            "date": d.isoformat(),
            "audit_id": str(audit.id),
            "title": audit.title,
            "audit_type": audit.audit_type or "internal",
            "framework_slug": audit.framework_slug,
        }
        for d in occurrences_between(audit, start=start, end=end, limit=100)
    ]


@router.get("/v1/me/audits")
def list_my_attendee_audits(
    request: Request,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated),
):
    """List audits where the current user is explicitly marked as an attendee."""

    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))

    attendee_audit_ids = (
        db.query(AuditAttendee.audit_id)
        .filter(AuditAttendee.user_id == user.id)
        .distinct()
        .subquery()
    )

    total = int(
        db.query(func.count(Audit.id))
        .join(attendee_audit_ids, Audit.id == attendee_audit_ids.c.audit_id)
        .filter(Audit.status != "template")
        .scalar()
        or 0
    )

    rows = (
        db.query(Audit)
        .join(attendee_audit_ids, Audit.id == attendee_audit_ids.c.audit_id)
        .filter(Audit.status != "template")
        .order_by(desc(Audit.updated_at))
        .limit(limit)
        .offset(offset)
        .all()
    )
    authors = _usernames(
        db, [x.created_by_user_id for x in rows if x.created_by_user_id]
    )

    attendee_rows = (
        db.query(AuditAttendee)
        .filter(
            AuditAttendee.user_id == user.id,
            AuditAttendee.audit_id.in_([x.id for x in rows]),
        )
        .order_by(AuditAttendee.created_at.asc())
        .all()
        if rows
        else []
    )
    attendee_by_audit: dict[uuid.UUID, AuditAttendee] = {}
    for attendee in attendee_rows:
        attendee_by_audit.setdefault(attendee.audit_id, attendee)

    items = []
    for audit in rows:
        item = _audit_summary(db, audit, authors)
        attendee = attendee_by_audit.get(audit.id)
        item["my_attendee"] = _attendee_dict(attendee) if attendee is not None else None
        items.append(item)

    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.get("/v1/audits/users")
def list_audit_attendee_users(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    rows = (
        db.query(User)
        .filter(User.is_active.is_(True))
        .order_by(User.username.asc())
        .all()
    )
    return {
        "items": [
            {
                "id": str(u.id),
                "username": u.username,
                "email": _user_email_snapshot(u) or "",
            }
            for u in rows
        ]
    }


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
    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))
    qry = db.query(Audit)
    if framework:
        qry = qry.filter(Audit.framework_slug == _clean_framework(framework))
    if status:
        qry = qry.filter(
            Audit.status == _clean_status(status, _AUDIT_STATUSES, default="open")
        )
    else:
        qry = qry.filter(Audit.status != "template")
    if q:
        qq = f"%{q.strip()}%"
        qry = qry.filter(Audit.title.ilike(qq))
    total = int(qry.order_by(None).count())
    rows = qry.order_by(desc(Audit.updated_at)).limit(limit).offset(offset).all()
    authors = _usernames(
        db, [x.created_by_user_id for x in rows if x.created_by_user_id]
    )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_audit_summary(db, x, authors) for x in rows],
    }


def _date_overlaps(
    qry,
    *,
    start: date,
    end: date,
):
    return qry.filter(Audit.start_date <= end).filter(
        func.coalesce(Audit.end_date, Audit.start_date) >= start
    )


def _coverage_source_out(
    audit: Audit, *, occurrence_date: date | None = None
) -> dict[str, Any]:
    source = (
        "scheduled" if audit.status == "template" or occurrence_date else "completed"
    )
    return {
        "id": str(audit.id),
        "audit_id": str(audit.id),
        "title": audit.title,
        "status": audit.status,
        "source": source,
        "audit_type": audit.audit_type or "internal",
        "start_date": (
            occurrence_date.isoformat()
            if occurrence_date
            else _iso_date(audit.start_date)
        ),
        "occurrence_date": occurrence_date.isoformat() if occurrence_date else None,
        "end_date": _iso_date(audit.end_date),
        "framework_slug": audit.framework_slug,
    }


def _coverage_item_out(item: Any, sources: list[dict[str, Any]]) -> dict[str, Any]:
    if isinstance(item, ControlItem):
        base = _control_out(item)
        label = f"{item.ref or ''} {item.title or ''}".strip()
    elif isinstance(item, FrameworkClause):
        base = _clause_out(item)
        label = f"{item.ref or ''} {item.title or ''}".strip()
    else:
        base = _document_out(item)
        label = getattr(item, "title", None) or "ISMS document"
    base["label"] = label
    base["coverage_count"] = len(sources)
    base["sources"] = sources
    return base


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
    fw = _clean_framework(framework)
    today = date.today()
    window_end = end or today
    window_start = start or (window_end - timedelta(days=365))
    if window_end < window_start:
        raise HTTPException(status_code=400, detail="end cannot be before start")
    if (window_end - window_start).days > 3660:
        raise HTTPException(
            status_code=400, detail="Coverage window cannot exceed 10 years"
        )
    coverage_mode = str(mode or "both").strip().lower()
    if coverage_mode not in {"completed", "scheduled", "both"}:
        raise HTTPException(
            status_code=400, detail="mode must be completed, scheduled or both"
        )

    audits: list[Audit] = []
    scheduled_occurrences: list[tuple[Audit, date]] = []
    if coverage_mode in {"completed", "both"}:
        completed_qry = db.query(Audit).filter(
            Audit.framework_slug == fw,
            Audit.status == "completed",
            Audit.start_date.isnot(None),
        )
        audits.extend(
            _date_overlaps(completed_qry, start=window_start, end=window_end)
            .order_by(Audit.start_date.asc(), Audit.title.asc())
            .all()
        )
    if coverage_mode in {"scheduled", "both"}:
        templates = (
            db.query(Audit)
            .filter(Audit.framework_slug == fw, Audit.status == "template")
            .order_by(Audit.schedule_next_run_date.asc().nullslast(), Audit.title.asc())
            .all()
        )
        for template in templates:
            dates = occurrences_between(
                template, start=window_start, end=window_end, limit=500
            )
            for occurrence in dates:
                scheduled_occurrences.append((template, occurrence))

    source_by_audit_id: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for audit in audits:
        source_by_audit_id.setdefault(audit.id, []).append(_coverage_source_out(audit))
    for template, occurrence in scheduled_occurrences:
        source_by_audit_id.setdefault(template.id, []).append(
            _coverage_source_out(template, occurrence_date=occurrence)
        )

    audit_ids = list(source_by_audit_id.keys())
    control_sources: dict[uuid.UUID, list[dict[str, Any]]] = {}
    clause_sources: dict[uuid.UUID, list[dict[str, Any]]] = {}
    document_sources: dict[uuid.UUID, list[dict[str, Any]]] = {}

    if audit_ids:
        for audit_id, control_id in (
            db.query(AuditScopedControl.audit_id, AuditScopedControl.control_item_id)
            .join(ControlItem, ControlItem.id == AuditScopedControl.control_item_id)
            .filter(AuditScopedControl.audit_id.in_(audit_ids))
            .filter(
                ControlItem.framework_slug == fw,
                ControlItem.type != "clause",
                ControlItem.in_scope
                == True,  # noqa: E712 - SQLAlchemy boolean expression
            )
            .all()
        ):
            control_sources.setdefault(control_id, []).extend(
                source_by_audit_id.get(audit_id, [])
            )
        scoped_clause_rows = (
            db.query(AuditScopedClause.audit_id, FrameworkClause)
            .join(FrameworkClause, FrameworkClause.id == AuditScopedClause.clause_id)
            .filter(AuditScopedClause.audit_id.in_(audit_ids))
            .filter(FrameworkClause.framework_slug == fw)
            .all()
        )
        for audit_id, clause in scoped_clause_rows:
            # Older audit scope rows may still contain parent clauses.  Scope
            # coverage reports reviewable leaf clauses only, so treat a parent
            # scoped clause as coverage for its leaf descendants rather than
            # emitting the parent heading itself.
            for leaf in _selectable_clause_descendants(db, fw, clause):
                clause_sources.setdefault(leaf.id, []).extend(
                    source_by_audit_id.get(audit_id, [])
                )
        for audit_id, document_id in (
            db.query(
                AuditScopedIsmsDocument.audit_id, AuditScopedIsmsDocument.document_id
            )
            .filter(AuditScopedIsmsDocument.audit_id.in_(audit_ids))
            .all()
        ):
            document_sources.setdefault(document_id, []).extend(
                source_by_audit_id.get(audit_id, [])
            )

    controls = (
        db.query(ControlItem)
        .filter(
            ControlItem.framework_slug == fw,
            ControlItem.type != "clause",
            ControlItem.in_scope == True,  # noqa: E712 - SQLAlchemy boolean expression
        )
        .order_by(ControlItem.ref.asc())
        .all()
    )
    clauses = (
        db.query(FrameworkClause)
        .filter(FrameworkClause.framework_slug == fw)
        .filter(~FrameworkClause.children.any())
        .order_by(FrameworkClause.sort_order.asc(), FrameworkClause.ref.asc())
        .all()
    )
    documents = (
        db.query(IsmsDocument)
        .order_by(IsmsDocument.document_type.asc(), IsmsDocument.title.asc())
        .all()
    )

    def split(items: list[Any], source_map: dict[uuid.UUID, list[dict[str, Any]]]):
        included = []
        gaps = []
        for item in items:
            sources = source_map.get(item.id, [])
            out = _coverage_item_out(item, sources)
            if sources:
                included.append(out)
            else:
                gaps.append(out)
        return included, gaps

    included_controls, gap_controls = split(controls, control_sources)
    included_clauses, gap_clauses = split(clauses, clause_sources)
    included_documents, gap_documents = split(documents, document_sources)

    return {
        "framework_slug": fw,
        "mode": coverage_mode,
        "start": window_start.isoformat(),
        "end": window_end.isoformat(),
        "source_audit_count": len(source_by_audit_id),
        "scheduled_occurrence_count": len(scheduled_occurrences),
        "summary": {
            "controls": {
                "total": len(controls),
                "included": len(included_controls),
                "gaps": len(gap_controls),
            },
            "clauses": {
                "total": len(clauses),
                "included": len(included_clauses),
                "gaps": len(gap_clauses),
            },
            "documents": {
                "total": len(documents),
                "included": len(included_documents),
                "gaps": len(gap_documents),
            },
        },
        "included": {
            "controls": included_controls,
            "clauses": included_clauses,
            "documents": included_documents,
        },
        "gaps": {
            "controls": gap_controls,
            "clauses": gap_clauses,
            "documents": gap_documents,
        },
    }


@router.get("/v1/audits/by-event/{event_id}")
def list_audits_for_event(
    event_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_read),
):
    eid = _try_uuid(event_id)
    if not eid:
        raise HTTPException(status_code=400, detail="event_id must be a UUID")
    _can_see_event(db, user, eid)

    rows = (
        db.query(AuditEvidence)
        .join(Audit, Audit.id == AuditEvidence.audit_id)
        .filter(AuditEvidence.event_id == eid)
        .order_by(desc(Audit.updated_at), desc(AuditEvidence.added_at))
        .all()
    )
    user_ids = [
        x.audit.created_by_user_id
        for x in rows
        if x.audit is not None and x.audit.created_by_user_id
    ]
    user_ids += [x.added_by_user_id for x in rows if x.added_by_user_id]
    authors = _usernames(db, list(set(user_ids)))

    return {
        "event_id": str(eid),
        "items": [
            {
                "audit": _audit_summary(db, x.audit, authors),
                "evidence": {
                    "id": str(x.id),
                    "notes": x.notes or "",
                    "evidence_url": x.evidence_url,
                    "title": x.title,
                    "added_at": _iso_dt(x.added_at),
                    "added_by_user_id": (
                        str(x.added_by_user_id) if x.added_by_user_id else None
                    ),
                    "added_by_username": (
                        authors.get(x.added_by_user_id, "")
                        if x.added_by_user_id
                        else ""
                    ),
                },
            }
            for x in rows
            if x.audit is not None
        ],
    }


@router.get("/v1/audits/by-entity/{entity_type}/{entity_id}")
def list_audits_for_entity(
    entity_type: str,
    entity_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_read),
):
    sampled_type = _normalise_audit_sample_entity_type(entity_type)
    sampled_id = _clean_optional_entity_id(entity_id)
    if not sampled_type or not sampled_id:
        raise HTTPException(status_code=400, detail="entity_id must be a UUID")

    rows = (
        db.query(AuditEvidence)
        .join(Audit, Audit.id == AuditEvidence.audit_id)
        .filter(
            AuditEvidence.entity_type == sampled_type,
            AuditEvidence.entity_id == sampled_id,
        )
        .order_by(desc(Audit.updated_at), desc(AuditEvidence.added_at))
        .all()
    )
    readable_rows = [
        x for x in rows if x.audit is not None and _can_read_audit(db, user, x.audit)
    ]
    user_ids = [
        x.audit.created_by_user_id
        for x in readable_rows
        if x.audit is not None and x.audit.created_by_user_id
    ]
    user_ids += [x.added_by_user_id for x in readable_rows if x.added_by_user_id]
    authors = _usernames(db, list(set(user_ids)))

    return {
        "entity_type": sampled_type,
        "entity_id": str(sampled_id),
        "items": [
            {
                "audit": _audit_summary(db, x.audit, authors),
                "evidence": {
                    "id": str(x.id),
                    "notes": x.notes or "",
                    "evidence_url": x.evidence_url,
                    "title": x.title,
                    "entity_type": x.entity_type,
                    "entity_id": str(x.entity_id) if x.entity_id else None,
                    "added_at": _iso_dt(x.added_at),
                    "added_by_user_id": (
                        str(x.added_by_user_id) if x.added_by_user_id else None
                    ),
                    "added_by_username": (
                        authors.get(x.added_by_user_id, "")
                        if x.added_by_user_id
                        else ""
                    ),
                },
            }
            for x in readable_rows
        ],
    }


@router.post("/v1/audits")
def create_audit(
    payload: AuditCreatePayload,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    framework = _clean_framework(payload.framework_slug)
    _validate_dates(payload.start_date, payload.end_date)
    a = Audit(
        title=_clean_text(payload.title, max_len=256, required=True, label="title"),
        framework_slug=framework,
        audit_type=_clean_audit_type(payload.audit_type),
        start_date=payload.start_date,
        end_date=payload.end_date,
        created_by_user_id=user.id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
        notes=_clean_rich_text(
            payload.report_notes if payload.report_notes is not None else payload.notes,
            max_len=20000,
            label="report_notes",
        ),
        executive_summary=_clean_rich_text(
            payload.executive_summary, max_len=20000, label="executive_summary"
        ),
    )
    db.add(a)
    db.flush()
    _replace_audit_scope(
        db,
        a,
        control_values=payload.controls,
        clause_values=payload.clauses,
        document_values=payload.documents,
    )
    db.commit()
    db.refresh(a)
    return _audit_summary(db, a, {user.id: user.username})


@router.get("/v1/audits/scheduled")
def list_scheduled_audits(
    request: Request,
    start: date | None = None,
    end: date | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_read),
):
    qry = db.query(Audit).filter(Audit.status == "template")
    if q:
        qq = f"%{q.strip()}%"
        qry = qry.filter(Audit.title.ilike(qq))
    rows = qry.order_by(
        Audit.schedule_next_run_date.asc().nullslast(), Audit.title.asc()
    ).all()
    authors = _usernames(
        db, [x.created_by_user_id for x in rows if x.created_by_user_id]
    )

    today = date.today()
    window_start = start or date(today.year, today.month, 1)
    window_end = end or (window_start.replace(day=28) + timedelta(days=4))
    if end is None:
        # The UI normally supplies explicit dates; this fallback keeps the API useful by defaulting to the current month.
        window_end = window_end.replace(day=1) - timedelta(days=1)
    if window_end < window_start:
        raise HTTPException(status_code=400, detail="end cannot be before start")

    items = [_schedule_summary(db, x, authors) for x in rows]
    occurrences: list[dict[str, Any]] = []
    for template in rows:
        if window_start and window_end:
            occurrences.extend(
                _template_occurrences_payload(
                    template, start=window_start, end=window_end
                )
            )
    occurrences.sort(key=lambda x: (x["date"], x["title"]))
    return {"total": len(rows), "items": items, "occurrences": occurrences}


@router.post("/v1/audits/scheduled")
def create_scheduled_audit(
    payload: ScheduledAuditPayload,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    framework = _clean_framework(payload.framework_slug)
    date_rule = _clean_schedule_date_rule(payload.schedule_date_rule)
    anchor_month = _clean_schedule_anchor_month(
        payload.schedule_anchor_month, required=(date_rule in FUZZY_MONTH_DATE_RULES)
    )
    start_date = _schedule_start_for_rule(
        payload.start_date, date_rule=date_rule, anchor_month=anchor_month
    )
    _validate_dates(start_date, payload.end_date)
    recurrence = _clean_schedule_recurrence(payload.schedule_recurrence)
    interval = _clean_schedule_interval(payload.schedule_interval)
    if payload.schedule_until_date and payload.schedule_until_date < start_date:
        raise HTTPException(
            status_code=400, detail="schedule_until_date cannot be before start_date"
        )
    now = _utcnow()
    a = Audit(
        title=_clean_text(payload.title, max_len=256, required=True, label="title"),
        framework_slug=framework,
        status="template",
        audit_type=_clean_audit_type(payload.audit_type),
        start_date=start_date,
        end_date=payload.end_date,
        created_by_user_id=user.id,
        created_at=now,
        updated_at=now,
        notes=_clean_rich_text(
            payload.report_notes, max_len=20000, label="report_notes"
        ),
        executive_summary=_clean_rich_text(
            payload.executive_summary, max_len=20000, label="executive_summary"
        ),
    )
    _apply_schedule_fields(
        a,
        recurrence=recurrence,
        interval=interval,
        next_run_date=start_date,
        until_date=payload.schedule_until_date,
        date_rule=date_rule,
        anchor_month=anchor_month,
    )
    db.add(a)
    db.flush()
    _replace_audit_scope(
        db,
        a,
        control_values=payload.controls,
        clause_values=payload.clauses,
        document_values=payload.documents,
    )
    _replace_template_attendees(db, audit=a, attendees=payload.attendees)
    db.commit()
    db.refresh(a)
    return _schedule_summary(db, a, {user.id: user.username})


@router.patch("/v1/audits/scheduled/{audit_id}")
def update_scheduled_audit(
    audit_id: str,
    payload: ScheduledAuditUpdatePayload,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    a = _audit_or_404(db, audit_id)
    if a.status != "template":
        raise HTTPException(status_code=400, detail="Audit is not a scheduled template")
    fields = set(payload.model_fields_set or set())
    if "title" in fields:
        a.title = _clean_text(payload.title, max_len=256, required=True, label="title")
    if "framework_slug" in fields:
        a.framework_slug = _clean_framework(payload.framework_slug)
    if "audit_type" in fields:
        a.audit_type = _clean_audit_type(
            payload.audit_type, default=a.audit_type or "internal"
        )
    if "start_date" in fields:
        if payload.start_date is None:
            raise HTTPException(status_code=400, detail="start_date is required")
        a.start_date = payload.start_date
    if "end_date" in fields:
        a.end_date = payload.end_date
    if "report_notes" in fields:
        a.notes = _clean_rich_text(
            payload.report_notes, max_len=20000, label="report_notes"
        )
    if "executive_summary" in fields:
        a.executive_summary = _clean_rich_text(
            payload.executive_summary, max_len=20000, label="executive_summary"
        )

    date_rule = (
        _clean_schedule_date_rule(
            payload.schedule_date_rule, default=a.schedule_date_rule or "exact"
        )
        if "schedule_date_rule" in fields
        else (a.schedule_date_rule or "exact")
    )
    anchor_month = _clean_schedule_anchor_month(
        (
            payload.schedule_anchor_month
            if "schedule_anchor_month" in fields
            else a.schedule_anchor_month
        ),
        required=(date_rule in FUZZY_MONTH_DATE_RULES),
    )
    if a.start_date is None:
        raise HTTPException(status_code=400, detail="start_date is required")
    a.start_date = _schedule_start_for_rule(
        a.start_date, date_rule=date_rule, anchor_month=anchor_month
    )
    _validate_dates(a.start_date, a.end_date)

    recurrence = (
        _clean_schedule_recurrence(
            payload.schedule_recurrence, default=a.schedule_recurrence or "once"
        )
        if "schedule_recurrence" in fields
        else (a.schedule_recurrence or "once")
    )
    interval = (
        _clean_schedule_interval(payload.schedule_interval)
        if "schedule_interval" in fields
        else int(a.schedule_interval or 1)
    )
    until = (
        payload.schedule_until_date
        if "schedule_until_date" in fields
        else a.schedule_until_date
    )
    if {
        "start_date",
        "schedule_date_rule",
        "schedule_anchor_month",
    } & fields:
        # Editing a scheduled template's date/rule is an explicit instruction to
        # move the future run date. Do this even when the template has already
        # materialised earlier audits; otherwise a user cannot reschedule the next
        # occurrence from the template page after the first run.
        next_run = a.start_date
    else:
        next_run = a.schedule_next_run_date or a.start_date
    _apply_schedule_fields(
        a,
        recurrence=recurrence,
        interval=interval,
        next_run_date=next_run,
        until_date=until,
        date_rule=date_rule,
        anchor_month=anchor_month,
    )

    if (
        payload.controls is not None
        or payload.clauses is not None
        or payload.documents is not None
    ):
        _replace_audit_scope(
            db,
            a,
            control_values=payload.controls or [],
            clause_values=payload.clauses or [],
            document_values=payload.documents or [],
            replace_controls=payload.controls is not None,
            replace_clauses=payload.clauses is not None,
            replace_documents=payload.documents is not None,
        )
    if payload.attendees is not None:
        _replace_template_attendees(db, audit=a, attendees=payload.attendees)

    a.updated_at = _utcnow()
    db.add(a)
    db.commit()
    db.refresh(a)
    return _schedule_summary(
        db, a, _usernames(db, [a.created_by_user_id] if a.created_by_user_id else [])
    )


@router.delete("/v1/audits/scheduled/{audit_id}")
def delete_scheduled_audit(
    audit_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    a = _audit_or_404(db, audit_id)
    if a.status != "template":
        raise HTTPException(status_code=400, detail="Audit is not a scheduled template")
    db.delete(a)
    db.commit()
    return {"ok": True}


@router.delete("/v1/audits/{audit_id}")
def delete_audit(
    audit_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_admin),
):
    a = _audit_or_404(db, audit_id)
    if a.status == "template":
        raise HTTPException(
            status_code=400,
            detail="Use the scheduled audit template delete endpoint for templates",
        )

    db.query(Audit).filter(Audit.schedule_last_created_audit_id == a.id).update(
        {Audit.schedule_last_created_audit_id: None}, synchronize_session=False
    )
    db.delete(a)
    db.commit()
    return {"ok": True}


@router.get("/v1/audits/{audit_id}/scope-map")
def get_audit_scope_map(
    audit_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated),
):
    a = _audit_or_404(db, audit_id)
    _require_audit_access(db, user, a)

    scoped_controls = (
        db.query(ControlItem)
        .join(AuditScopedControl, AuditScopedControl.control_item_id == ControlItem.id)
        .filter(AuditScopedControl.audit_id == a.id)
        .order_by(ControlItem.type.asc(), ControlItem.ref.asc())
        .all()
    )
    scoped_clauses = (
        db.query(FrameworkClause)
        .join(AuditScopedClause, AuditScopedClause.clause_id == FrameworkClause.id)
        .filter(AuditScopedClause.audit_id == a.id)
        .order_by(FrameworkClause.sort_order.asc(), FrameworkClause.ref.asc())
        .all()
    )
    scoped_documents = (
        db.query(IsmsDocument)
        .join(
            AuditScopedIsmsDocument,
            AuditScopedIsmsDocument.document_id == IsmsDocument.id,
        )
        .filter(AuditScopedIsmsDocument.audit_id == a.id)
        .order_by(IsmsDocument.document_type.asc(), IsmsDocument.title.asc())
        .all()
    )

    control_ids = [c.id for c in scoped_controls]
    clause_ids = [c.id for c in scoped_clauses]
    links: list[dict[str, Any]] = []
    if control_ids and clause_ids:
        rows = (
            db.query(ControlClauseLink, FrameworkClause, ControlItem)
            .join(FrameworkClause, FrameworkClause.id == ControlClauseLink.clause_id)
            .join(ControlItem, ControlItem.id == ControlClauseLink.control_item_id)
            .filter(ControlClauseLink.clause_id.in_(clause_ids))
            .filter(ControlClauseLink.control_item_id.in_(control_ids))
            .filter(FrameworkClause.framework_slug == a.framework_slug)
            .filter(ControlItem.framework_slug == a.framework_slug)
            .order_by(
                FrameworkClause.sort_order.asc(),
                FrameworkClause.ref.asc(),
                ControlItem.ref.asc(),
            )
            .all()
        )
        for link, clause, control in rows:
            links.append(
                {
                    "clause_id": str(clause.id),
                    "clause_ref": clause.ref,
                    "control_id": str(control.id),
                    "control_ref": control.ref,
                    "applicability": link.applicability,
                }
            )

    linked_control_ids = {x["control_id"] for x in links}
    linked_clause_ids = {x["clause_id"] for x in links}
    return {
        "audit_id": str(a.id),
        "framework_slug": a.framework_slug,
        "audit_type": a.audit_type or "internal",
        "clauses": [_clause_out(c) for c in scoped_clauses],
        "controls": [_control_out(c) for c in scoped_controls],
        "documents": [_document_out(d) for d in scoped_documents],
        "links": links,
        "summary": {
            "clause_count": len(scoped_clauses),
            "control_count": len(scoped_controls),
            "document_count": len(scoped_documents),
            "relationship_count": len(links),
            "controls_linked_to_selected_clauses": len(linked_control_ids),
            "controls_in_scope_without_selected_clause_link": max(
                0, len(scoped_controls) - len(linked_control_ids)
            ),
            "clauses_without_selected_control_link": max(
                0, len(scoped_clauses) - len(linked_clause_ids)
            ),
        },
    }


@router.get("/v1/audits/{audit_id}")
def get_audit(
    audit_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated),
):
    a = _audit_or_404(db, audit_id)
    _require_audit_access(db, user, a)
    scoped_controls = (
        db.query(ControlItem)
        .join(AuditScopedControl, AuditScopedControl.control_item_id == ControlItem.id)
        .filter(AuditScopedControl.audit_id == a.id)
        .order_by(ControlItem.ref.asc())
        .all()
    )
    scoped_clauses = (
        db.query(FrameworkClause)
        .join(AuditScopedClause, AuditScopedClause.clause_id == FrameworkClause.id)
        .filter(AuditScopedClause.audit_id == a.id)
        .order_by(FrameworkClause.sort_order.asc(), FrameworkClause.ref.asc())
        .all()
    )
    scoped_documents = (
        db.query(IsmsDocument)
        .join(
            AuditScopedIsmsDocument,
            AuditScopedIsmsDocument.document_id == IsmsDocument.id,
        )
        .filter(AuditScopedIsmsDocument.audit_id == a.id)
        .order_by(IsmsDocument.document_type.asc(), IsmsDocument.title.asc())
        .all()
    )
    evidence = (
        db.query(AuditEvidence)
        .filter(AuditEvidence.audit_id == a.id)
        .order_by(desc(AuditEvidence.added_at))
        .all()
    )
    attendees = (
        db.query(AuditAttendee)
        .filter(AuditAttendee.audit_id == a.id)
        .order_by(AuditAttendee.name.asc())
        .all()
    )
    findings = (
        db.query(AuditFinding)
        .filter(AuditFinding.audit_id == a.id)
        .order_by(desc(AuditFinding.created_at))
        .all()
    )

    user_ids = [a.created_by_user_id] if a.created_by_user_id else []
    user_ids += [x.added_by_user_id for x in evidence if x.added_by_user_id]
    user_ids += [x.created_by_user_id for x in findings if x.created_by_user_id]
    authors = _usernames(db, list(set(user_ids)))

    out = _audit_summary(db, a, authors)
    out["scoped_controls"] = [_control_out(c) for c in scoped_controls]
    out["scoped_clauses"] = [_clause_out(c) for c in scoped_clauses]
    out["scoped_documents"] = [_document_out(d) for d in scoped_documents]
    out["evidence"] = [_evidence_dict(db, x, authors) for x in evidence]
    out["attendees"] = [_attendee_dict(x) for x in attendees]
    out["findings"] = [_finding_dict(x, authors) for x in findings]
    return out


@router.patch("/v1/audits/{audit_id}")
def update_audit(
    audit_id: str,
    payload: AuditUpdatePayload,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    a = _audit_or_404(db, audit_id)
    fields = set(payload.model_fields_set or set())
    if _is_locked_audit(a):
        if fields != {"status"}:
            _require_mutable_audit(a)
        new_status = _clean_status(
            payload.status, _AUDIT_STATUSES, default=a.status or "open"
        )
        if new_status not in _MUTABLE_AUDIT_STATUSES:
            raise HTTPException(
                status_code=409,
                detail="Completed or archived audits can only be reopened to Open or In progress.",
            )
        a.status = new_status
        a.updated_at = _utcnow()
        db.add(a)
        db.commit()
        db.refresh(a)
        return _audit_summary(
            db,
            a,
            _usernames(db, [a.created_by_user_id] if a.created_by_user_id else []),
        )
    if "title" in fields:
        a.title = _clean_text(payload.title, max_len=256, required=True, label="title")
    if "framework_slug" in fields:
        a.framework_slug = _clean_framework(payload.framework_slug)
    if "status" in fields:
        a.status = _clean_status(
            payload.status, _AUDIT_STATUSES, default=a.status or "open"
        )
    if "audit_type" in fields:
        a.audit_type = _clean_audit_type(
            payload.audit_type, default=a.audit_type or "internal"
        )
    if "start_date" in fields:
        a.start_date = payload.start_date
    if "end_date" in fields:
        a.end_date = payload.end_date
    if "notes" in fields or "report_notes" in fields:
        a.notes = _clean_rich_text(
            payload.report_notes if "report_notes" in fields else payload.notes,
            max_len=20000,
            label="report_notes",
        )
    if "executive_summary" in fields:
        a.executive_summary = _clean_rich_text(
            payload.executive_summary, max_len=20000, label="executive_summary"
        )
    _validate_dates(a.start_date, a.end_date)
    a.updated_at = _utcnow()
    db.add(a)
    db.commit()
    db.refresh(a)
    return _audit_summary(
        db, a, _usernames(db, [a.created_by_user_id] if a.created_by_user_id else [])
    )


@router.put("/v1/audits/{audit_id}/scope")
def replace_audit_scope(
    audit_id: str,
    payload: AuditScopePayload,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    a = _audit_or_404(db, audit_id)
    _require_mutable_audit(a)
    controls, clauses, documents = _replace_audit_scope(
        db,
        a,
        control_values=payload.controls,
        clause_values=payload.clauses,
        document_values=payload.documents,
    )
    db.commit()
    return _scope_response(controls, clauses, documents)


@router.put("/v1/audits/{audit_id}/controls")
def replace_audit_controls(
    audit_id: str,
    payload: AuditControlsPayload,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    a = _audit_or_404(db, audit_id)
    _require_mutable_audit(a)
    controls, _, _ = _replace_audit_scope(
        db,
        a,
        control_values=payload.controls,
        clause_values=None,
        document_values=None,
        replace_clauses=False,
        replace_documents=False,
    )
    db.commit()
    return {"ok": True, "scoped_controls": [_control_out(c) for c in controls]}


@router.post("/v1/audits/{audit_id}/evidence")
def add_audit_evidence(
    audit_id: str,
    payload: AuditEvidencePayload,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    a = _audit_or_404(db, audit_id)
    _require_mutable_audit(a, action="sampled into")
    evidence_url = _clean_url(payload.evidence_url)
    entity_type = _normalise_audit_sample_entity_type(payload.entity_type)
    entity_id = _clean_optional_entity_id(payload.entity_id)
    eid = payload.event_id
    if not eid and not (entity_type and entity_id):
        eid = _event_id_from_url(evidence_url)
    ev = _can_see_event(db, user, eid) if eid else None

    entity = _audit_sample_entity_reference(
        db,
        framework=a.framework_slug,
        entity_type=entity_type,
        entity_id=entity_id,
        required=True,
    )

    if not ev and not entity and not evidence_url:
        raise HTTPException(
            status_code=400,
            detail="event_id, entity_type/entity_id, or evidence_url is required",
        )

    title = _clean_text(payload.title, max_len=512, label="title")
    if not title and ev:
        title = ev.summary[:512]
    if not title and entity:
        title = _short_text(entity.get("title"), 512)
    if not evidence_url and entity:
        evidence_url = _clean_url(entity.get("href"))
    notes = _clean_rich_text(payload.notes, max_len=20000, label="notes")

    if ev:
        existing = (
            db.query(AuditEvidence)
            .filter(AuditEvidence.audit_id == a.id, AuditEvidence.event_id == ev.id)
            .one_or_none()
        )
        if existing:
            if evidence_url:
                existing.evidence_url = evidence_url
            if title:
                existing.title = title
            if notes:
                existing.notes = notes
            a.updated_at = _utcnow()
            db.add(existing)
            db.add(a)
            db.commit()
            db.refresh(existing)
            return _evidence_dict(db, existing, {user.id: user.username})

    if entity_type and entity_id:
        existing = (
            db.query(AuditEvidence)
            .filter(
                AuditEvidence.audit_id == a.id,
                AuditEvidence.entity_type == entity_type,
                AuditEvidence.entity_id == entity_id,
            )
            .one_or_none()
        )
        if existing:
            if evidence_url:
                existing.evidence_url = evidence_url
            if title:
                existing.title = title
            if notes:
                existing.notes = notes
            a.updated_at = _utcnow()
            db.add(existing)
            db.add(a)
            db.commit()
            db.refresh(existing)
            return _evidence_dict(db, existing, {user.id: user.username})

    row = AuditEvidence(
        audit_id=a.id,
        event_id=ev.id if ev else None,
        entity_type=entity_type,
        entity_id=entity_id,
        evidence_url=evidence_url,
        title=title or evidence_url,
        notes=notes,
        added_by_user_id=user.id,
        added_at=_utcnow(),
    )
    a.updated_at = _utcnow()
    db.add(row)
    db.add(a)
    db.commit()
    db.refresh(row)
    return _evidence_dict(db, row, {user.id: user.username})


@router.patch("/v1/audits/{audit_id}/evidence/{evidence_id}")
def update_audit_evidence(
    audit_id: str,
    evidence_id: str,
    payload: AuditEvidenceUpdatePayload,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    a = _audit_or_404(db, audit_id)
    _require_mutable_audit(a)
    evid = _try_uuid(evidence_id)
    if not evid:
        raise HTTPException(status_code=400, detail="evidence_id must be a UUID")
    row = (
        db.query(AuditEvidence)
        .filter(AuditEvidence.audit_id == a.id, AuditEvidence.id == evid)
        .one_or_none()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Evidence not found")
    fields = set(payload.model_fields_set or set())
    if "evidence_url" in fields:
        row.evidence_url = _clean_url(payload.evidence_url)
    if "title" in fields:
        row.title = _clean_text(payload.title, max_len=512, label="title") or None
    if "notes" in fields:
        row.notes = _clean_rich_text(payload.notes, max_len=20000, label="notes")
    a.updated_at = _utcnow()
    db.add(row)
    db.add(a)
    db.commit()
    db.refresh(row)
    return _evidence_dict(db, row, {user.id: user.username})


@router.delete("/v1/audits/{audit_id}/evidence/{evidence_id}")
def delete_audit_evidence(
    audit_id: str,
    evidence_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    a = _audit_or_404(db, audit_id)
    _require_mutable_audit(a)
    evid = _try_uuid(evidence_id)
    if not evid:
        raise HTTPException(status_code=400, detail="evidence_id must be a UUID")
    row = (
        db.query(AuditEvidence)
        .filter(AuditEvidence.audit_id == a.id, AuditEvidence.id == evid)
        .one_or_none()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Evidence not found")
    db.delete(row)
    a.updated_at = _utcnow()
    db.add(a)
    db.commit()
    return {"ok": True}


@router.post("/v1/audits/{audit_id}/attendees")
def add_attendee(
    audit_id: str,
    payload: AuditAttendeePayload,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    a = _audit_or_404(db, audit_id)
    _require_mutable_audit(a)
    linked_user = None
    if payload.user_id is not None:
        linked_user = (
            db.query(User)
            .filter(User.id == payload.user_id, User.is_active.is_(True))
            .one_or_none()
        )
        if not linked_user:
            raise HTTPException(
                status_code=400, detail="Unknown or inactive attendee user"
            )

    display_name = _clean_text(
        payload.name,
        max_len=256,
        required=linked_user is None,
        label="name",
    )
    if linked_user is not None and not display_name:
        display_name = linked_user.username

    explicit_email = _clean_email(payload.email, label="email")
    row = AuditAttendee(
        audit_id=a.id,
        user_id=linked_user.id if linked_user is not None else None,
        name=display_name,
        email=explicit_email or _user_email_snapshot(linked_user),
        role=_clean_text(payload.role, max_len=128, label="role") or None,
        created_at=_utcnow(),
    )
    a.updated_at = _utcnow()
    db.add(row)
    db.add(a)
    db.commit()
    db.refresh(row)
    return _attendee_dict(row)


@router.delete("/v1/audits/{audit_id}/attendees/{attendee_id}")
def delete_attendee(
    audit_id: str,
    attendee_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    a = _audit_or_404(db, audit_id)
    _require_mutable_audit(a)
    tid = _try_uuid(attendee_id)
    if not tid:
        raise HTTPException(status_code=400, detail="attendee_id must be a UUID")
    row = (
        db.query(AuditAttendee)
        .filter(AuditAttendee.audit_id == a.id, AuditAttendee.id == tid)
        .one_or_none()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Attendee not found")
    db.delete(row)
    a.updated_at = _utcnow()
    db.add(a)
    db.commit()
    return {"ok": True}


@router.post("/v1/audits/{audit_id}/findings")
def add_finding(
    audit_id: str,
    payload: AuditFindingPayload,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    a = _audit_or_404(db, audit_id)
    _require_mutable_audit(a)
    kind = (payload.kind or "").strip().lower()
    if kind not in _FINDING_KINDS:
        raise HTTPException(status_code=400, detail="Invalid finding kind")
    ctrl = (
        _control_or_404(db, a.framework_slug, payload.control)
        if payload.control
        else None
    )
    row = AuditFinding(
        audit_id=a.id,
        kind=kind,
        title=_clean_text(payload.title, max_len=256, required=True, label="title"),
        description=_clean_rich_text(
            payload.description, max_len=50000, label="description"
        ),
        control_item_id=ctrl.id if ctrl else None,
        status=_clean_status(payload.status, _FINDING_STATUSES, default="open"),
        created_by_user_id=user.id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    a.updated_at = _utcnow()
    db.add(row)
    db.add(a)
    db.commit()
    db.refresh(row)
    return _finding_dict(row, {user.id: user.username})


@router.patch("/v1/audits/{audit_id}/findings/{finding_id}")
def update_finding(
    audit_id: str,
    finding_id: str,
    payload: AuditFindingUpdatePayload,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    a = _audit_or_404(db, audit_id)
    _require_mutable_audit(a)
    fid = _try_uuid(finding_id)
    if not fid:
        raise HTTPException(status_code=400, detail="finding_id must be a UUID")
    row = (
        db.query(AuditFinding)
        .filter(AuditFinding.audit_id == a.id, AuditFinding.id == fid)
        .one_or_none()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Finding not found")
    fields = set(payload.model_fields_set or set())
    if "kind" in fields:
        kind = (payload.kind or "").strip().lower()
        if kind not in _FINDING_KINDS:
            raise HTTPException(status_code=400, detail="Invalid finding kind")
        row.kind = kind
    if "title" in fields:
        row.title = _clean_text(
            payload.title, max_len=256, required=True, label="title"
        )
    if "description" in fields:
        row.description = _clean_rich_text(
            payload.description, max_len=50000, label="description"
        )
    if "control" in fields:
        ctrl = (
            _control_or_404(db, a.framework_slug, payload.control)
            if payload.control
            else None
        )
        row.control_item_id = ctrl.id if ctrl else None
    if "status" in fields:
        row.status = _clean_status(
            payload.status, _FINDING_STATUSES, default=row.status or "open"
        )
    row.updated_at = _utcnow()
    a.updated_at = _utcnow()
    db.add(row)
    db.add(a)
    db.commit()
    db.refresh(row)
    return _finding_dict(row, {user.id: user.username})


@router.delete("/v1/audits/{audit_id}/findings/{finding_id}")
def delete_finding(
    audit_id: str,
    finding_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    a = _audit_or_404(db, audit_id)
    _require_mutable_audit(a)
    fid = _try_uuid(finding_id)
    if not fid:
        raise HTTPException(status_code=400, detail="finding_id must be a UUID")
    row = (
        db.query(AuditFinding)
        .filter(AuditFinding.audit_id == a.id, AuditFinding.id == fid)
        .one_or_none()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Finding not found")
    db.delete(row)
    a.updated_at = _utcnow()
    db.add(a)
    db.commit()
    return {"ok": True}


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read()
    return data or b""


@router.post("/v1/audits/{audit_id}/report")
async def upload_final_report(
    audit_id: str,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_manage),
):
    a = _audit_or_404(db, audit_id)
    _require_mutable_audit(a)
    filename = os.path.basename(getattr(file, "filename", None) or "audit-report")[:255]
    content_type = getattr(file, "content_type", None) or "application/octet-stream"
    data = await _read_upload(file)
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(data) > 100 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Final report is too large")
    ext = os.path.splitext(filename)[1]
    key = f"audit-reports/{a.id}/{uuid.uuid4()}{ext}"
    stored = put_bytes(key, data, content_type)
    now = _utcnow()
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
    return {"ok": True, "final_report": _report_dict(a)}


@router.get("/v1/audits/{audit_id}/report")
def download_final_report(
    audit_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated),
):
    a = _audit_or_404(db, audit_id)
    _require_audit_access(db, user, a)
    if not a.final_report_storage_uri:
        raise HTTPException(status_code=404, detail="No final report has been uploaded")
    bucket, key = parse_s3_uri(a.final_report_storage_uri)
    obj = get_object_stream(bucket, key)
    body = obj["Body"]
    fname = a.final_report_filename or "audit-report"
    media = a.final_report_content_type or "application/octet-stream"
    headers = {"Content-Disposition": f'attachment; filename="{fname}"'}
    return StreamingResponse(iter_stream(body), media_type=media, headers=headers)
