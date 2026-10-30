"""Cloud posture on a Vendor's page, refresh requests, and the risks recorded
against failing checks.

A Vendor shows each cloud provider linked to it: every scope's latest checks
and the identities the vendor lists, the provider's recent activity, and the
risks recorded against a check or one subject within it. An accepted risk
shows its check or subject as Risk accepted until the acceptance lapses.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from pydantic import Field
from sqlalchemy import func

from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.db.models import (
    CloudPostureScope,
    CloudProvider,
    Event,
    IsmsPerson,
    IsmsVendor,
)
from app.ingest.cloud import KEYS
from app.ingest.cloud import provider as cloud_provider
from app.ingest.cloud.base import REASONS
from app.ingest.cloud.runner import enabled
from app.security.auth import ROLE_ADMIN
from app.security.permissions import has_permission
from app.security.roles import compute_effective_role
from app.services import posture_risks
from app.services.errors import DomainError
from app.services.features import state as feature_state
from app.services.frameworks import framework_parameter

ACTIVITY_SHOWN = 20
# One refresh request per provider in this window.
REFRESH_SPACING = timedelta(minutes=2)
ASSET_NAMESPACE = uuid.UUID("0f1c2d3e-4b5a-4c6d-8e7f-9a0b1c2d3e4f")
CATEGORY = "Cloud services"


def _iso(value):
    return value.isoformat() + "Z" if value is not None else None


def _require_read(db, user):
    # Posture is derived evidence; Vendor access alone never discloses it.
    from app.services.isms.access import _can_read_isms

    if not _can_read_isms(db, user) or not has_permission(db, user, "events.read"):
        raise DomainError(403, "ISMS read access and events.read are required")
    feature_state(db).require("vendors", "evidence")


def _is_admin(db, user) -> bool:
    role = (
        getattr(user, "effective_role", None)
        or compute_effective_role(db, user)
        or user.role
    )
    return (role or "") == ROLE_ADMIN


def _vendor(db, vendor_id) -> IsmsVendor:
    vendor = db.get(IsmsVendor, vendor_id)
    if vendor is None:
        raise DomainError(404, "Vendor not found")
    return vendor


def _linked(db, vendor_id) -> list[CloudProvider]:
    return (
        db.query(CloudProvider)
        .filter(
            CloudProvider.vendor_id == vendor_id,
            CloudProvider.provider.in_(KEYS),
        )
        .order_by(CloudProvider.provider)
        .all()
    )


def has_posture(db, vendor_id) -> bool:
    return (
        db.query(CloudProvider.provider)
        .filter(CloudProvider.vendor_id == vendor_id, CloudProvider.provider.in_(KEYS))
        .first()
        is not None
    )


def _people(db, scopes) -> dict:
    emails = {
        str(i.get("email") or "").lower()
        for s in scopes
        for i in s.identities or []
        if i.get("email")
    }
    if not emails:
        return {}
    rows = (
        db.query(IsmsPerson.id, IsmsPerson.name, IsmsPerson.email)
        .filter(func.lower(IsmsPerson.email).in_(sorted(emails)))
        .order_by(IsmsPerson.name, IsmsPerson.id)
        .all()
    )
    out = {}
    for ident, name, email in rows:
        out.setdefault(email.lower(), {"id": str(ident), "name": name})
    return out


def _accepted(risks) -> bool:
    return any(r["accepted"] for r in risks or [])


def _scope_out(provider, scope, links, people) -> dict:
    checks = []
    for check in scope.checks or []:
        key = check.get("key")
        risks = links.get((provider.key, scope.scope_key, key, ""), [])
        subjects = [
            {
                "name": name,
                "risks": links.get((provider.key, scope.scope_key, key, name), []),
            }
            for name in check.get("subjects") or []
        ]
        status = check.get("status")
        accepted = _accepted(risks) or (
            subjects
            and check.get("subjects_total") == len(subjects)
            and all(_accepted(s["risks"]) for s in subjects)
        )
        if status == "attention" and accepted:
            status = "accepted"
        checks.append(
            {
                "key": key,
                "label": check.get("label") or provider.checks.get(key, key),
                "status": status,
                "detail": check.get("detail") or "",
                "reason": REASONS.get(check.get("reason") or "", ""),
                "subjects": subjects,
                "subjects_total": check.get("subjects_total") or 0,
                "event_id": check.get("event_id"),
                "observed_at": check.get("observed_at"),
                "risks": risks,
            }
        )
    identities = []
    for identity in scope.identities or []:
        email = str(identity.get("email") or "").lower()
        identities.append({**identity, "person": people.get(email) if email else None})
    judged = [c for c in checks if c["status"] in ("met", "attention", "accepted")]
    return {
        "key": scope.scope_key,
        "label": scope.label,
        "kind": scope.kind or provider.scope_noun,
        "checked_at": _iso(scope.checked_at),
        "met": sum(1 for c in judged if c["status"] == "met"),
        "judged": len(judged),
        "checks": checks,
        "identities": identities,
    }


def _activity(db, key) -> list[dict]:
    rows = (
        db.query(Event)
        .filter(Event.source == key, ~Event.action.like("posture.%"))
        .order_by(Event.timestamp.desc(), Event.id.desc())
        .limit(ACTIVITY_SHOWN)
        .all()
    )
    return [
        {
            "id": str(e.id),
            "timestamp": _iso(e.timestamp),
            "actor": e.actor or "",
            "action": e.action or "",
            "outcome": e.outcome or "",
            "summary": e.summary or "",
        }
        for e in rows
    ]


def vendor_posture(*, db, user, vendor_id):
    _require_read(db, user)
    _vendor(db, vendor_id)
    rows = _linked(db, vendor_id)
    risks_on = feature_state(db).enabled("risk_cia")
    links = (
        posture_risks.linked(
            db,
            "cloud",
            [r.provider for r in rows],
            titles=posture_risks.can_read(db, user),
        )
        if risks_on and rows
        else {}
    )
    now = utc_now_naive()
    interval = timedelta(seconds=settings.cloud_posture_poll_seconds)
    out = []
    for row in rows:
        provider = cloud_provider(row.provider)
        scopes = (
            db.query(CloudPostureScope)
            .filter_by(provider=row.provider)
            .order_by(CloudPostureScope.label, CloudPostureScope.scope_key)
            .all()
        )
        people = _people(db, scopes)
        out.append(
            {
                "provider": provider.key,
                "label": provider.label,
                "scope_noun": provider.scope_noun,
                "enabled": enabled(provider),
                "checked_at": _iso(row.checked_at),
                "attempted_at": _iso(row.attempted_at),
                "error": REASONS.get(row.error, "") if row.error else "",
                "stale": bool(row.checked_at and now - row.checked_at > 2 * interval),
                "reads_activity": provider.activity is not None,
                "activity_at": _iso(row.activity_at),
                "activity_error": REASONS.get(row.activity_error, "")
                if row.activity_error
                else "",
                "activity_truncated": bool(row.activity_truncated),
                "scopes": [_scope_out(provider, s, links, people) for s in scopes],
                "activity": _activity(db, provider.key),
            }
        )
    return {
        "providers": out,
        "interval_seconds": settings.cloud_posture_poll_seconds,
        "can_refresh": _is_admin(db, user),
        "can_create_risk": risks_on and posture_risks.can_record(db, user),
    }


NOT_QUEUED = (
    "No refresh was queued: the provider is turned off or paused, or a "
    "refresh was requested in the last two minutes."
)


def _claim(db, row: CloudProvider, now) -> bool:
    """Whether a refresh of the provider may be queued now; claims the
    request window when it may. The caller holds the row's lock."""
    from app.services.ingestion_pause import is_paused

    provider = cloud_provider(row.provider)
    if not enabled(provider) or is_paused(db, provider.key):
        return False
    if row.refresh_requested_at and now - row.refresh_requested_at < REFRESH_SPACING:
        return False
    row.refresh_requested_at = now
    return True


def _dispatch(keys: list[str]) -> None:
    """Queue a refresh and an activity read of each provider, after the
    claims commit."""
    from app.worker import tasks

    for key in keys:
        tasks.cloud_posture_refresh_task.apply_async(args=[key], expires=300)
        if cloud_provider(key).activity is not None:
            tasks.cloud_activity_task.apply_async(args=[key], expires=300)


def request_refresh(*, db, user, vendor_id):
    """Queue a refresh of every enabled provider linked to the Vendor. The
    route admits administrators only."""
    _require_read(db, user)
    _vendor(db, vendor_id)
    now = utc_now_naive()
    rows = (
        db.query(CloudProvider)
        .filter(CloudProvider.vendor_id == vendor_id, CloudProvider.provider.in_(KEYS))
        .order_by(CloudProvider.provider)
        .with_for_update()
        .all()
    )
    queued = [row.provider for row in rows if _claim(db, row, now)]
    db.commit()
    if not queued:
        raise DomainError(409, NOT_QUEUED)
    _dispatch(queued)
    return {"queued": queued}


def run_provider(*, db, key: str) -> dict:
    """Queue one provider's refresh and activity read from Admin's ingest
    runners, before any Vendor shows it. The route admits administrators
    only, and the same request window applies as on the Vendor page."""
    from app.ingest.cloud.runner import provider_row

    if key not in KEYS:
        raise DomainError(404, "Cloud provider not found")
    provider = cloud_provider(key)
    if not enabled(provider):
        raise DomainError(409, "Source type is disabled")
    if not _claim(db, provider_row(db, provider), utc_now_naive()):
        db.rollback()
        raise DomainError(409, NOT_QUEUED)
    db.commit()
    _dispatch([key])
    return {
        "queued": True,
        "providers": [key],
        "note": f"{provider.label} posture refresh queued on the HTTP ingestion "
        "worker; results appear on its Vendor page when it completes.",
    }


class CloudRiskInput(posture_risks.PostureRiskInput):
    provider: str = Field(min_length=1, max_length=32)
    scope_key: str = Field(min_length=1, max_length=128)
    check: str = Field(min_length=1, max_length=64)
    subject_key: str = Field(default="", max_length=256)


def _scope_asset(db, provider, scope: CloudPostureScope, vendor_id):
    kind = scope.kind or provider.scope_noun
    label = scope.label if scope.label != scope.scope_key else ""
    name = f"{provider.label} {kind} · " + (
        f"{label} ({scope.scope_key})" if label else scope.scope_key
    )
    return posture_risks.category_asset(
        db,
        asset_id=uuid.uuid5(ASSET_NAMESPACE, f"{provider.key}:{scope.scope_key}"),
        name=name[:256],
        category=CATEGORY,
        subcategory=f"{provider.label} {kind}s",
        description=(
            f"{provider.label} {kind} {scope.scope_key}. KEEN created this asset "
            "when a risk was recorded against its posture."
        ),
        vendor_id=vendor_id,
    )


def create_cloud_risk(*, db, user, vendor_id, body: CloudRiskInput):
    """Record a risk for a cloud check, or one subject within it."""
    _require_read(db, user)
    if not posture_risks.can_record(db, user):
        raise DomainError(403, "risk.manage is required to record a risk")
    feature_state(db).require("risk_cia")
    _vendor(db, vendor_id)
    row = db.get(CloudProvider, body.provider)
    if row is None or row.vendor_id != vendor_id or body.provider not in KEYS:
        raise DomainError(404, "This vendor shows no posture from that provider")
    provider = cloud_provider(body.provider)
    if body.check not in provider.checks:
        raise DomainError(400, "Unknown posture check")
    scope = (
        db.query(CloudPostureScope)
        .filter_by(provider=body.provider, scope_key=body.scope_key)
        .one_or_none()
    )
    check = next(
        (c for c in (scope.checks if scope else []) if c.get("key") == body.check),
        None,
    )
    if check is None:
        raise DomainError(404, "No result for this check")
    if body.subject_key and body.subject_key not in (check.get("subjects") or []):
        raise DomainError(404, "That subject is not listed for this check")
    framework = framework_parameter(body.framework)
    asset = _scope_asset(db, provider, scope, vendor_id)
    risk = posture_risks.record(
        db,
        user,
        body,
        origin="cloud",
        origin_key=body.provider,
        scope_key=body.scope_key,
        check_key=body.check,
        subject_key=body.subject_key,
        asset_id=asset.id,
        controls=posture_risks.mapped_controls(
            db, [check["event_id"]] if check.get("event_id") else [], framework
        ),
        framework=framework,
        request_path=f"/v1/isms/vendors/{vendor_id}/cloud-posture/risks",
    )
    db.commit()
    return {"risk_id": str(risk.id), "asset_id": str(asset.id)}
