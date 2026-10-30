"""Scheduled and requested cloud posture runs.

A refresh asks a provider for every scope's checks and identities, keeps the
latest result for each scope, and records a check's result as an event when
it changes, or once a day while it holds, so mapping rules can attach it to
controls. An activity read records each new vendor activity entry as an
event. Collection happens before any database write, so no transaction waits
on a vendor's API.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.db.models import (
    CloudPostureScope,
    CloudProvider,
    IngestionCursor,
    IsmsVendor,
)
from app.ingest.cloud import base
from app.ingest.cloud import provider as cloud_provider
from app.ingest.common import store_event_with_artifact
from app.ingest.connections import Connection, connection_scope, namespace, run_lock
from app.services.errors import DomainError
from app.services.ingestion_pause import is_paused

log = logging.getLogger(__name__)

VENDOR_NAMESPACE = uuid.UUID("8b6e1e0a-3f0e-4d5b-9b7c-1d2e3f405162")
# Results not refreshed for this long are removed with their identities.
KEEP_DAYS = 30
# A first activity read, or one after a long gap, looks back at most this far,
# and one read covers at most this window.
FIRST_LOOKBACK = timedelta(days=1)
MAX_WINDOW = timedelta(hours=6)
ARTIFACT_LIMIT = 256 * 1024


def enabled(provider) -> bool:
    return bool(getattr(settings, provider.setting, False))


def connection(provider) -> Connection:
    """The deployment's own connection for the provider; its identity goes on
    every event, external ID and artifact the run stores."""
    return Connection(
        "env:" + provider.key,
        provider.key,
        "Environment default",
        {},
        {},
        None,
        True,
        "environment",
    )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def _json(value) -> bytes:
    data = json.dumps(value, default=str, sort_keys=True).encode()
    return (
        data
        if len(data) <= ARTIFACT_LIMIT
        else json.dumps({"truncated": True}).encode()
    )


def provider_row(db, provider) -> CloudProvider:
    """The provider's row, locked so a refresh and an activity read of the
    same provider update it one after the other."""

    def locked():
        return (
            db.query(CloudProvider)
            .filter_by(provider=provider.key)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )

    row = locked()
    if row is None:
        try:
            with db.begin_nested():
                db.add(CloudProvider(provider=provider.key))
        except IntegrityError:
            pass  # Another run added it meanwhile.
        row = locked()
    return row


def vendor_id_for(provider) -> uuid.UUID:
    return uuid.uuid5(VENDOR_NAMESPACE, "cloud-vendor:" + provider.key)


def link_vendor(db, provider, row: CloudProvider) -> uuid.UUID:
    """The Vendor whose page shows the provider: the linked one, else one KEEN
    created before (even if renamed), else one named after the vendor, else a
    new one with the vendor's fixed name and website."""
    if row.vendor_id is not None and db.get(IsmsVendor, row.vendor_id) is not None:
        return row.vendor_id
    ident = vendor_id_for(provider)
    if db.get(IsmsVendor, ident) is None:
        names = [n.lower() for n in (provider.vendor, *provider.aliases)]
        match = (
            db.query(IsmsVendor.id)
            .filter(func.lower(IsmsVendor.name).in_(names))
            .order_by(IsmsVendor.created_at, IsmsVendor.id)
            .first()
        )
        if match is not None:
            ident = match[0]
        else:
            try:
                with db.begin_nested():
                    db.add(
                        IsmsVendor(
                            id=ident,
                            name=provider.vendor,
                            website=provider.website,
                            description=(
                                f"Supplier of the organisation's {provider.label} "
                                "services. KEEN created this vendor to show cloud "
                                "posture."
                            ),
                        )
                    )
            except IntegrityError:
                # An administrator saved a vendor of that name meanwhile.
                found = (
                    db.query(IsmsVendor.id)
                    .filter(func.lower(IsmsVendor.name).in_(names))
                    .first()
                )
                if found is None:
                    raise
                ident = found[0]
    row.vendor_id = ident
    return ident


def _record_check(db, provider, scope, check: dict, now: datetime) -> str:
    payload = {
        "provider": provider.key,
        "scope": scope.key,
        "scope_label": scope.label,
        **{k: check[k] for k in ("key", "label", "status", "detail")},
        "subjects": check["subjects"],
        "subjects_total": check["subjects_total"],
    }
    stamp = now.isoformat()
    stored = store_event_with_artifact(
        db,
        timestamp=now,
        source=provider.key,
        system=f"{provider.key}:{scope.key}",
        actor=None,
        action="posture." + check["key"],
        outcome=check["status"],
        severity=None,
        summary=f"{check['label']} · {scope.label}: {check['detail']}",
        raw_pointer={
            "provider": provider.key,
            "scope": scope.key,
            "check": check["key"],
        },
        normalized_payload=payload,
        external_id=f"posture:{provider.key}:{scope.key}:{check['key']}:{stamp}",
        artifact_kind="cloud_posture",
        artifact_bytes=_json(payload),
        artifact_content_type="application/json",
        artifact_key=(
            f"cloud/{provider.key}/posture/{_digest(scope.key)}/"
            f"{_digest(check['key'] + stamp)}.json"
        ),
        captured_by="keen-cloud-posture",
        commit=False,
    )
    return stored["event_id"]


def store(db, provider, results: list[base.ScopeResult], now: datetime) -> None:
    """Keep each scope's latest result and record changed results as events;
    scopes the provider no longer returns are removed."""
    row = provider_row(db, provider)
    link_vendor(db, provider, row)
    today = now.date().isoformat()
    seen = set()
    for scope in results[: base.MAX_SCOPES]:
        key = base.clip(scope.key, 128)
        if not key or key in seen:
            continue
        seen.add(key)
        current = (
            db.query(CloudPostureScope)
            .filter_by(provider=provider.key, scope_key=key)
            .one_or_none()
        )
        before = {c.get("key"): c for c in (current.checks if current else [])}
        checks = []
        for check in scope.checks:
            if check.key not in provider.checks:
                continue
            item = check.as_dict()
            old = before.get(item["key"]) or {}
            if item["status"] == "unknown":
                item["event_id"], item["observed_at"] = None, None
            elif (
                old.get("event_id")
                and old.get("status") == item["status"]
                and old.get("detail") == item["detail"]
                and str(old.get("observed_at") or "")[:10] == today
            ):
                item["event_id"], item["observed_at"] = (
                    old["event_id"],
                    old["observed_at"],
                )
            else:
                item["event_id"] = _record_check(db, provider, scope, item, now)
                item["observed_at"] = now.isoformat()
            checks.append(item)
        identities = [i.as_dict() for i in scope.identities[: base.MAX_IDENTITIES]]
        if current is None:
            current = CloudPostureScope(provider=provider.key, scope_key=key)
            db.add(current)
        current.label = base.clip(scope.label or key)
        current.kind = base.clip(scope.kind, 32)
        current.checked_at = now
        current.checks = checks
        current.identities = identities
    stale = db.query(CloudPostureScope).filter(
        CloudPostureScope.provider == provider.key
    )
    if seen:
        stale = stale.filter(CloudPostureScope.scope_key.notin_(seen))
    stale.delete(synchronize_session=False)
    row.attempted_at = row.checked_at = now
    row.error = ""


def _ready(db, provider, *, force: bool) -> str:
    """Why the run should not go ahead, or an empty string."""
    if not force and not enabled(provider):
        return "disabled"
    if is_paused(db, provider.key):
        return "paused"
    return ""


def _results(provider) -> list[base.ScopeResult]:
    if settings.demo_mode:
        # Demo environments never call a vendor; they answer with fictional data.
        from app.ingest.cloud import demo

        return demo.results(provider.key)
    return provider.collect()


def refresh(db, key: str, *, force: bool = False) -> dict:
    """Read and store one provider's posture. ``force`` runs a provider that
    is not enabled, which only the demo seed does."""
    provider = cloud_provider(key)
    reason = _ready(db, provider, force=force)
    db.rollback()
    if reason:
        return {"provider": key, "skipped": True, "reason": reason}
    with connection_scope(connection(provider)):
        with run_lock(db, "cloud-posture:" + key) as acquired:
            if not acquired:
                return {"provider": key, "skipped": True, "reason": "running"}
            try:
                results, failure = _results(provider), ""
            except base.Unavailable as exc:
                failure = exc.reason
            except Exception as exc:
                # Exception text can carry vendor responses or credentials.
                log.warning(
                    "Cloud posture read failed for %s: %s", key, type(exc).__name__
                )
                failure = "unavailable"
            if failure:
                row = provider_row(db, provider)
                # Linked even when the first read fails, so the Vendor page
                # can show why.
                link_vendor(db, provider, row)
                row.attempted_at = utc_now_naive()
                row.error = failure
                db.commit()
                return {"provider": key, "error": failure}
            try:
                store(db, provider, results, utc_now_naive())
                db.commit()
            except DomainError:
                # Ingestion paused for an evidence purge; the next run stores.
                db.rollback()
                return {"provider": key, "skipped": True, "reason": "paused"}
    return {"provider": key, "scopes": len(results)}


def _activity(provider, since, until):
    if settings.demo_mode:
        from app.ingest.cloud import demo

        return demo.activity(provider.key, since, until)
    return provider.activity(since, until)


def record_activity(db, provider, entry: base.Activity) -> None:
    when = entry.at.replace(tzinfo=None)
    detail = {k: v for k, v in (entry.detail or {}).items() if isinstance(k, str)}
    store_event_with_artifact(
        db,
        timestamp=when,
        source=provider.key,
        system=f"{provider.key}:{base.clip(entry.scope, 128)}",
        actor=base.clip(entry.actor, 512) or None,
        action=base.clip(entry.action, 128),
        outcome=entry.outcome,
        severity=None,
        summary=base.clip(entry.summary, 512),
        raw_pointer={"provider": provider.key, "scope": entry.scope, "id": entry.id},
        normalized_payload={"provider": provider.key, "scope": entry.scope, **detail},
        external_id=f"activity:{provider.key}:{entry.id}",
        artifact_kind="cloud_activity",
        artifact_bytes=_json(entry.raw or detail),
        artifact_content_type="application/json",
        artifact_key=f"cloud/{provider.key}/activity/{_digest(entry.id)}.json",
        captured_by="keen-cloud-activity",
        commit=False,
    )


def collect_activity(db, key: str, *, force: bool = False) -> dict:
    """Read one provider's activity since the last read and record it."""
    provider = cloud_provider(key)
    if provider.activity is None:
        return {"provider": key, "skipped": True, "reason": "no activity"}
    reason = _ready(db, provider, force=force)
    db.rollback()
    if reason:
        return {"provider": key, "skipped": True, "reason": reason}
    with connection_scope(connection(provider)):
        name = namespace("cloud-activity:" + key)
        with run_lock(db, "cloud-activity:" + key) as acquired:
            if not acquired:
                return {"provider": key, "skipped": True, "reason": "running"}
            cursor = db.query(IngestionCursor).filter_by(name=name).one_or_none()
            now = utc_now_naive()
            until = now - provider.activity_lag
            since = max(
                (
                    cursor.last_ts
                    if cursor and cursor.last_ts
                    else until - FIRST_LOOKBACK
                ),
                until - FIRST_LOOKBACK,
            )
            until = min(until, since + MAX_WINDOW)
            db.rollback()
            if until <= since:
                return {"provider": key, "records": 0}
            try:
                (entries, complete), failure = _activity(provider, since, until), ""
            except base.Unavailable as exc:
                failure = exc.reason
            except Exception as exc:
                log.warning(
                    "Cloud activity read failed for %s: %s", key, type(exc).__name__
                )
                failure = "unavailable"
            if failure:
                row = provider_row(db, provider)
                link_vendor(db, provider, row)
                row.activity_error = failure
                db.commit()
                return {"provider": key, "error": failure}
            try:
                for entry in entries[: base.MAX_ACTIVITY]:
                    record_activity(db, provider, entry)
                cursor = db.query(IngestionCursor).filter_by(name=name).one_or_none()
                if cursor is None:
                    cursor = IngestionCursor(name=name, meta={})
                    db.add(cursor)
                cursor.last_ts = until
                cursor.updated_at = now
                row = provider_row(db, provider)
                link_vendor(db, provider, row)
                row.activity_at = now
                row.activity_error = ""
                row.activity_truncated = not complete
                db.commit()
            except DomainError:
                db.rollback()
                return {"provider": key, "skipped": True, "reason": "paused"}
    return {"provider": key, "records": len(entries), "complete": complete}


def purge_stale(db, now: datetime | None = None) -> int:
    """Remove scope results not refreshed within KEEP_DAYS, so identities from
    a provider that is no longer read do not linger."""
    cutoff = (now or utc_now_naive()) - timedelta(days=KEEP_DAYS)
    removed = (
        db.query(CloudPostureScope)
        .filter(CloudPostureScope.checked_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    return int(removed or 0)


def due(db, key: str, now: datetime) -> bool:
    """Whether a provider's last refresh attempt is a full interval old."""
    row = db.get(CloudProvider, key)
    interval = timedelta(seconds=settings.cloud_posture_poll_seconds)
    return row is None or row.attempted_at is None or now - row.attempted_at >= interval


def refresh_all(db) -> list[dict]:
    """Refresh each provider whose interval has passed. The schedule checks
    often, so a newly enabled provider is read soon after start-up."""
    from app.ingest.cloud import KEYS

    out = []
    now = utc_now_naive()
    for key in KEYS:
        try:
            if not due(db, key, now):
                continue
            out.append(refresh(db, key))
        except Exception:
            db.rollback()
            # Exception text can carry vendor responses or credentials.
            log.warning("Cloud posture refresh failed for %s", key)
            out.append({"provider": key, "error": "failed"})
    purge_stale(db)
    return out


def activity_all(db) -> list[dict]:
    from app.ingest.cloud import KEYS

    out = []
    for key in KEYS:
        try:
            out.append(collect_activity(db, key))
        except Exception:
            db.rollback()
            log.warning("Cloud activity read failed for %s", key)
            out.append({"provider": key, "error": "failed"})
    return out
