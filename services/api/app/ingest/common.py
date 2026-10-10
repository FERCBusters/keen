from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.db.models import Event, Artifact, ControlItem, Mapping
from app.mapping.rules import load_rules, evaluate_by_framework
from app.core.config import settings
from app.storage.s3 import put_bytes
from app.security.redaction import (
    combine_redaction_status,
    mask_event_data_bytes,
    mask_event_data_obj,
    mask_event_data_str,
    redact_bytes,
    redact_obj,
    redact_str,
)


def is_safe_url(url: str) -> bool:
    from app.ingest.http import destination
    try:
        destination(url)
        return True
    except (ValueError, OSError):
        return False


def fingerprint(parts: list[str], payload: bytes) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"|")
    h.update(payload)
    return h.hexdigest()[:32]


def ensure_controls(
    db: Session, framework_slug: str, refs: list[str]
) -> dict[str, ControlItem]:
    if not refs:
        return {}
    from app.db.models import Framework
    if not db.query(Framework.id).filter_by(slug=framework_slug).first():
        return {}  # A stale rule must not resurrect a removed catalogue.
    found = (
        db.query(ControlItem)
        .filter(ControlItem.framework_slug == framework_slug, ControlItem.ref.in_(refs))
        .all()
    )
    by_ref = {c.ref: c for c in found}
    for ref in refs:
        if ref not in by_ref:
            ci = ControlItem(
                framework_slug=framework_slug, type="annex_control", ref=ref, title=None
            )
            db.add(ci)
            db.flush()
            by_ref[ref] = ci
    return by_ref


def apply_rules(db: Session, ev: Event) -> int:
    # Coordinate rule evaluation with connection removal, until this transaction commits.
    if db.get_bind().dialect.name == 'postgresql':
        from sqlalchemy import text
        db.execute(text('SELECT pg_advisory_xact_lock_shared(1262830926)'))
    rules = load_rules(settings.rules_path, db=db)
    event_dict = {
        "source": ev.source,
        "connection_id": ev.connection_id,
        "system": ev.system,
        "actor": ev.actor,
        "action": ev.action,
        "outcome": ev.outcome,
        "severity": ev.severity,
        "summary": ev.summary,
        "raw_pointer": ev.raw_pointer,
        "normalized_payload": ev.normalized_payload,
    }
    matched = evaluate_by_framework(event_dict, rules, details=True)
    if not matched:
        return 0

    # If rules are re-applied to an event that already has mappings, skip any
    # mapping pairs that already exist. This makes remapping idempotent and
    # prevents unique-constraint errors on (event_id, control_item_id).
    existing_control_ids = {
        cid
        for (cid,) in (
            db.query(Mapping.control_item_id).filter(Mapping.event_id == ev.id).all()
        )
    }

    created = 0
    for framework_slug, entries in matched.items():
        controls = ensure_controls(db, framework_slug=framework_slug, refs=[item["ref"] for item in entries])

        for item in entries:
            ci = controls.get(item["ref"])
            if not ci:
                continue
            if ci.id in existing_control_ids:
                continue
            mp = Mapping(
                event_id=ev.id,
                control_item_id=ci.id,
                confidence=item["confidence"],
                method="rule",
                rationale=item["rationale"],
                mapped_by="system",
            )
            db.add(mp)
            created += 1

    # Flush once (faster, and any unexpected constraint errors will surface here).
    if created:
        db.flush()
    return created


def store_event_with_artifact(
    db: Session,
    *,
    timestamp: datetime,
    source: str,
    system: Optional[str],
    actor: Optional[str],
    action: Optional[str],
    outcome: Optional[str],
    severity: Optional[int],
    summary: str,
    raw_pointer: dict[str, Any],
    normalized_payload: dict[str, Any],
    external_id: str,
    artifact_kind: str,
    artifact_bytes: bytes,
    artifact_content_type: str,
    artifact_key: str,
    captured_by: str,
    commit: bool = True,
) -> dict[str, Any]:
    from app.services.ingestion_pause import require_purge_receiving
    require_purge_receiving(db)
    from app.ingest.connections import namespace, identity, artifact_key as scoped_artifact_key
    external_id = namespace(external_id)
    artifact_key = scoped_artifact_key(artifact_key)
    # --- hardening: redact secrets BEFORE persisting anything ---
    system, actor, action, outcome, summary = (
        redact_str(value) for value in (system, actor, action, outcome, summary)
    )
    summary = summary or ""
    raw_pointer = redact_obj(raw_pointer) or {}
    normalized_payload = redact_obj(normalized_payload) or {}

    artifact_bytes, redaction_status = redact_bytes(
        artifact_bytes, artifact_content_type
    )

    # Optional privacy masking at ingestion time.  The "samples" mode is
    # intentionally not applied here; it is applied dynamically during evidence
    # PDF/ZIP export so the main Keen UI retains the original event data.
    if (settings.event_data_masking or "false") == "true":
        system = mask_event_data_str(system)
        actor = mask_event_data_str(actor)
        action = mask_event_data_str(action)
        outcome = mask_event_data_str(outcome)
        summary = mask_event_data_str(summary) or ""
        raw_pointer = mask_event_data_obj(raw_pointer) or {}
        normalized_payload = mask_event_data_obj(normalized_payload) or {}
        artifact_bytes, masking_status = mask_event_data_bytes(
            artifact_bytes, artifact_content_type
        )
        redaction_status = combine_redaction_status(redaction_status, masking_status)

    # Fast de-dupe check (avoids triggering a rollback that would also undo
    # ingestion cursor updates that may be pending in the same session).
    existing = (
        db.query(Event.id)
        .filter(Event.source == source, Event.external_id == external_id)
        .one_or_none()
    )
    if existing:
        return {"ok": True, "deduped": True, "event_id": str(existing[0])}

    ev = Event(
        timestamp=timestamp,
        source=source,
        system=system,
        actor=actor,
        action=action,
        outcome=outcome,
        severity=severity,
        summary=summary,
        raw_pointer=raw_pointer,
        normalized_payload=normalized_payload,
        external_id=external_id,
        **identity(),
    )
    try:
        # Use a SAVEPOINT so an integrity error here doesn't force a full
        # transaction rollback (which would also undo cursor changes).
        with db.begin_nested():
            db.add(ev)
            db.flush()
    except IntegrityError:
        # Race: another worker inserted the same event.
        existing_id = (
            db.query(Event.id)
            .filter(Event.source == source, Event.external_id == external_id)
            .scalar()
        )
        if existing_id is None:
            raise
        return {
            "ok": True,
            "deduped": True,
            "event_id": str(existing_id),
        }

    stored = put_bytes(
        key=artifact_key, data=artifact_bytes, content_type=artifact_content_type
    )
    art = Artifact(
        event_id=ev.id,
        kind=artifact_kind,
        storage_uri=stored.uri,
        sha256=stored.sha256,
        content_type=artifact_content_type,
        size_bytes=stored.size_bytes,
        captured_by=captured_by,
        redaction_status=redaction_status,
    )
    db.add(art)
    db.flush()

    created_mappings = apply_rules(db, ev)
    if commit:
        db.commit()
    return {
        "ok": True,
        "event_id": str(ev.id),
        "artifact_uri": stored.uri,
        "sha256": stored.sha256,
        "mappings": created_mappings,
    }
