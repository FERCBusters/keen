from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import func

from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.db.models import (
    Audit,
    User,
)
from app.security.rich_text import sanitize_rich_text_html
from app.services.audit_schedules import (
    FUZZY_MONTH_DATE_RULES,
    SCHEDULE_DATE_RULES,
    SCHEDULE_RECURRENCES,
    first_scheduled_rule_date,
)
from app.services.audits.errors import AuditError

AUDIT_SAMPLE_ENTITY_LABELS = {
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


AUDIT_SAMPLE_ENTITY_ALIASES = {
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


ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


FINDING_STATUSES = {"open", "closed", "accepted"}


LOCKED_AUDIT_STATUSES = {"completed", "archived"}


MUTABLE_AUDIT_STATUSES = {"open", "in_progress"}


AUDIT_TYPES = {"internal", "external"}


AUDIT_STATUSES = {"open", "in_progress", "completed", "archived", "template"}


FINDING_KINDS = {"major_nc", "minor_nc", "ofi", "best_practice"}


def utcnow() -> datetime:
    return utc_now_naive()


def clean_text(
    raw: str | None, *, max_len: int, required: bool = False, label: str = "value"
) -> str:
    v = (raw or "").strip()
    if required and not v:
        raise AuditError(status_code=400, detail=f"{label} is required")
    if len(v) > max_len:
        raise AuditError(status_code=400, detail=f"{label} is too long")
    return v


def clean_rich_text(raw: str | None, *, max_len: int, label: str = "value") -> str:
    """Trim, length-check, and allowlist-sanitise a rich-text HTML field.

    Audit report notes, executive summaries, and finding descriptions are
    rendered as HTML in the UI. KEEN's security model requires the backend to be
    the authoritative sanitiser (the client sanitiser is defence-in-depth only),
    so every stored value passes through the shared allowlist sanitiser here.
    """
    v = clean_text(raw, max_len=max_len, label=label)
    cleaned = sanitize_rich_text_html(v)
    if len(cleaned) > max_len:
        raise AuditError(status_code=400, detail=f"{label} is too long")
    return cleaned


def clean_framework(raw: str | None) -> str:
    v = (raw or settings.default_framework_slug or "").strip()
    if not v:
        raise AuditError(status_code=400, detail="framework_slug is required")
    if len(v) > 64 or not re.match(r"^[A-Za-z0-9._:-]+$", v):
        raise AuditError(status_code=400, detail="Invalid framework_slug")
    return v


def clean_status(raw: str | None, allowed: set[str], *, default: str) -> str:
    v = (raw or default).strip().lower()
    if v not in allowed:
        raise AuditError(status_code=400, detail="Invalid status")
    return v


def clean_audit_type(raw: str | None, *, default: str = "internal") -> str:
    v = (raw or default).strip().lower()
    if v not in AUDIT_TYPES:
        raise AuditError(status_code=400, detail="Invalid audit_type")
    return v


def clean_schedule_recurrence(raw: str | None, *, default: str = "once") -> str:
    v = (raw or default).strip().lower()
    if v not in SCHEDULE_RECURRENCES:
        raise AuditError(status_code=400, detail="Invalid schedule_recurrence")
    return v


def clean_schedule_interval(raw: int | None) -> int:
    try:
        value = int(raw or 1)
    except Exception:
        raise AuditError(status_code=400, detail="Invalid schedule_interval")
    if value < 1 or value > 120:
        raise AuditError(
            status_code=400, detail="schedule_interval must be between 1 and 120"
        )
    return value


def clean_schedule_date_rule(raw: str | None, *, default: str = "exact") -> str:
    v = (raw or default).strip().lower()
    if v not in SCHEDULE_DATE_RULES:
        raise AuditError(status_code=400, detail="Invalid schedule_date_rule")
    return v


def clean_schedule_anchor_month(raw: int | None, *, required: bool) -> int | None:
    if raw is None or str(raw).strip() == "":
        if required:
            raise AuditError(
                status_code=400,
                detail="schedule_anchor_month is required for first weekday schedules",
            )
        return None
    try:
        value = int(raw)
    except Exception:
        raise AuditError(status_code=400, detail="Invalid schedule_anchor_month")
    if value < 1 or value > 12:
        raise AuditError(
            status_code=400, detail="schedule_anchor_month must be between 1 and 12"
        )
    return value


def schedule_start_for_rule(
    start_date: date, *, date_rule: str, anchor_month: int | None
) -> date:
    if date_rule in FUZZY_MONTH_DATE_RULES:
        return first_scheduled_rule_date(
            start_date.year, anchor_month or start_date.month, date_rule
        )
    return start_date


def clean_email(raw: str | None, *, label: str = "email") -> str | None:
    v = (raw or "").strip()
    if not v:
        return None
    if len(v) > 256 or "@" not in v or v.startswith("@") or v.endswith("@"):
        raise AuditError(status_code=400, detail=f"Invalid {label}")
    if any(ord(c) < 32 or ord(c) == 127 for c in v):
        raise AuditError(status_code=400, detail=f"Invalid {label}")
    return v


def looks_like_email(raw: str | None) -> bool:
    v = (raw or "").strip()
    return bool(
        v
        and "@" in v
        and not v.startswith("@")
        and not v.endswith("@")
        and len(v) <= 256
    )


def user_email_snapshot(user: User | None) -> str | None:
    if user is None:
        return None
    email = getattr(user, "email", None)
    if looks_like_email(email):
        return str(email).strip()
    # Backwards-compatible fallback for deployments where the Keen username is
    # the user's email address. If usernames are non-email identifiers, admins
    # can set the dedicated user email field instead.
    username = getattr(user, "username", None)
    if looks_like_email(username):
        return str(username).strip()
    return None


def audit_status_value(audit: Audit | None) -> str:
    return str(getattr(audit, "status", None) or "open").strip().lower()


def is_locked_audit(audit: Audit | None) -> bool:
    return audit_status_value(audit) in LOCKED_AUDIT_STATUSES


def require_mutable_audit(audit: Audit, *, action: str = "modify") -> None:
    if is_locked_audit(audit):
        status = audit_status_value(audit).replace("_", " ")
        raise AuditError(
            status_code=409,
            detail=(
                f"This audit is {status} and cannot be {action}. "
                "Change its status back to Open or In progress before editing it."
            ),
        )


def validate_dates(start: date | None, end: date | None) -> None:
    if start and end and end < start:
        raise AuditError(status_code=400, detail="end_date cannot be before start_date")


def clean_url(raw: str | None) -> str | None:
    v = (raw or "").strip()
    if not v:
        return None
    if len(v) > 2048:
        raise AuditError(status_code=400, detail="evidence_url is too long")
    if re.match(r"^(?:javascript|data|vbscript):", v, flags=re.I):
        raise AuditError(status_code=400, detail="Unsupported evidence_url scheme")
    if v.startswith("//"):
        raise AuditError(
            status_code=400, detail="Protocol-relative evidence_url is not allowed"
        )
    if v.startswith("/"):
        return v
    try:
        u = urlsplit(v)
    except Exception:
        raise AuditError(status_code=400, detail="Invalid evidence_url")
    if u.scheme.lower() not in {"http", "https"} or not u.netloc:
        raise AuditError(
            status_code=400, detail="evidence_url must be http(s) or a same-origin path"
        )
    return v


def event_id_from_url(raw: str | None) -> uuid.UUID | None:
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
        return try_uuid(str(val)) if val else None
    except Exception:
        return None


def date_overlaps(
    qry,
    *,
    start: date,
    end: date,
):
    return qry.filter(Audit.start_date <= end).filter(
        func.coalesce(Audit.end_date, Audit.start_date) >= start
    )


def try_uuid(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None
