"""Automatic diary entries for ISMS, risk and interested-party framework links.

Saving an ISMS record, a risk or an interested party with framework links
maintains one diary event per record. The entry lists the linked control and
clause references and links to the record by UUID. Its automatic mappings
follow the live link state, so control evidence counts include each
association.

Entries carry no record titles; app/security/diary_visibility.py decides who
can read them. Diary events are immune to evidence retention.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.datetime_utils import utc_now_naive
from app.db.models import (
    Artifact,
    ControlItem,
    Event,
    EventIncident,
    EventQuestionThread,
    FrameworkClause,
    InterestedPartyControlLink,
    IsmsEntityClauseLink,
    IsmsEntityControlLink,
    Mapping,
    RiskControlLink,
    User,
)
from app.ingest.common import normalise_ingested_fields, store_event_with_artifact
from app.ingest.connections import outside_connection
from app.security.diary_visibility import (
    DIARY_SOURCE,
    LINK_ENTRY_SYSTEM,
    link_entry_external_id,
)
from app.services.control_refs import CLAUSE_ROW_TYPE
from app.services.errors import DomainError
from app.services.evidence_retention import is_referenced, queue_object
from app.services.ingestion_pause import purge_pause_status
from app.storage.s3 import put_bytes

MANUAL_RATIONALE = "ISMS entity link"
_CAPTURED_BY = "keen:isms"
# The administrator diary editor (services/admin.py) records its revisions
# with this marker. Only those revisions hand the entry text to administrators.
_ADMIN_CAPTURED_BY = "keen:diary"
_ACTION = "isms_link"
_MAX_LISTED_REFS = 100
_MAX_DETAIL_TEXT = 4096

_LABELS = {
    "swot": "SWOT",
    "objective": "Objective",
    "document": "Document",
    "org_node": "Org chart node",
    "asset": "Asset",
    "meeting": "Meeting",
    "effectiveness_measure": "Effectiveness measure",
    "risk": "Risk",
    "interested_party": "Interested party",
}
# Record pages take only the record UUID. Organisation chart nodes have no
# page of their own, so their entries open the organisation chart.
_RECORD_PAGES = {
    "swot": "/swot.html?id={id}",
    "objective": "/isms-objective.html?id={id}",
    "document": "/isms-document.html?id={id}",
    "asset": "/isms-asset.html?id={id}",
    "meeting": "/isms-meeting.html?id={id}",
    "effectiveness_measure": "/isms-effectiveness-measure.html?id={id}",
    "risk": "/risk.html?id={id}",
    "interested_party": "/interested_party.html?id={id}",
}
# Risks and interested parties keep their control links in tables of their own.
_DIRECT_LINKS = {
    "risk": (RiskControlLink, RiskControlLink.risk_id),
    "interested_party": (
        InterestedPartyControlLink,
        InterestedPartyControlLink.interested_party_id,
    ),
}
_SECTION_PAGES = {"org_node": "/isms.html?tab=org"}

_PURGE_PAUSED = (
    "Framework link changes are paused while an evidence purge runs and for "
    "five minutes afterwards; retry later."
)


def _record_type(entity_type: str) -> str:
    if entity_type not in _LABELS:
        raise ValueError(f"Unsupported linked record type: {entity_type}")
    return entity_type


def _record_url(entity_type: str, entity_id: uuid.UUID) -> str | None:
    page = _RECORD_PAGES.get(entity_type)
    if page:
        return page.format(id=entity_id)
    return _SECTION_PAGES.get(entity_type)


def _ref_text(refs: list[str]) -> str:
    unique = sorted({ref for ref in refs if ref})
    value = ", ".join(unique[:_MAX_LISTED_REFS])
    if len(unique) > _MAX_LISTED_REFS:
        value = f"{value} (+{len(unique) - _MAX_LISTED_REFS} more)"
    return value[:_MAX_DETAIL_TEXT]


def _link_state(
    db: Session, entity_type: str, entity_id: uuid.UUID
) -> tuple[set[uuid.UUID], dict[str, list[str]]]:
    """Return the catalogue item IDs to map and the references per framework."""

    refs: dict[str, list[str]] = {}
    if entity_type in _DIRECT_LINKS:
        link, owner = _DIRECT_LINKS[entity_type]
        rows = (
            db.query(ControlItem.id, ControlItem.framework_slug, ControlItem.ref)
            .join(link, link.control_item_id == ControlItem.id)
            .filter(owner == entity_id)
            .all()
        )
        for _item_id, framework, ref in rows:
            refs.setdefault(framework, []).append(ref or "")
        return {row.id for row in rows}, refs

    controls = (
        db.query(ControlItem.id, ControlItem.framework_slug, ControlItem.ref)
        .join(
            IsmsEntityControlLink,
            IsmsEntityControlLink.control_item_id == ControlItem.id,
        )
        .filter(
            IsmsEntityControlLink.entity_type == entity_type,
            IsmsEntityControlLink.entity_id == entity_id,
        )
        .all()
    )
    clauses = (
        db.query(FrameworkClause.framework_slug, FrameworkClause.ref)
        .join(
            IsmsEntityClauseLink, IsmsEntityClauseLink.clause_id == FrameworkClause.id
        )
        .filter(
            IsmsEntityClauseLink.entity_type == entity_type,
            IsmsEntityClauseLink.entity_id == entity_id,
        )
        .all()
    )
    item_ids = {row.id for row in controls}
    for _item_id, framework, ref in controls:
        refs.setdefault(framework, []).append(ref or "")
    clause_refs: dict[str, set[str]] = {}
    for framework, ref in clauses:
        refs.setdefault(framework, []).append(ref or "")
        if ref:
            clause_refs.setdefault(framework, set()).add(ref)
    # A clause maps to its clause-kind catalogue item, so clause links join the
    # same evidence counts as control links. A framework without that item
    # still lists the clause in the entry text.
    for framework, wanted in sorted(clause_refs.items()):
        item_ids.update(
            item_id
            for (item_id,) in db.query(ControlItem.id).filter(
                ControlItem.framework_slug == framework,
                ControlItem.type == CLAUSE_ROW_TYPE,
                ControlItem.ref.in_(sorted(wanted)),
            )
        )
    return item_ids, refs


def _entry_payload(
    entity_type: str, entity_id: uuid.UUID, refs: dict[str, list[str]]
) -> dict[str, Any]:
    label = _LABELS[entity_type]
    payload: dict[str, Any] = {
        "summary": f"{label} linked to framework controls",
        "details": {
            "linked": {
                framework: _ref_text(values)
                for framework, values in sorted(refs.items())
            }
        },
    }
    url = _record_url(entity_type, entity_id)
    if url:
        payload["links"] = [{"url": url, "text": label}]
    return payload


def _payload_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _find_event(db: Session, external_id: str) -> Event | None:
    return (
        db.query(Event)
        .filter(Event.source == DIARY_SOURCE, Event.external_id == external_id)
        .one_or_none()
    )


def _admin_edited(db: Session, event_id: uuid.UUID) -> bool:
    return (
        db.query(Artifact.id)
        .filter(
            Artifact.event_id == event_id,
            Artifact.kind == "diary_entry_edit",
            Artifact.captured_by == _ADMIN_CAPTURED_BY,
        )
        .first()
        is not None
    )


def _automatic_mappings(db: Session, event_id: uuid.UUID) -> dict[uuid.UUID, uuid.UUID]:
    """Return this module's mappings on an entry, keyed by catalogue item."""

    return {
        control_item_id: mapping_id
        for mapping_id, control_item_id in db.query(
            Mapping.id, Mapping.control_item_id
        ).filter(
            Mapping.event_id == event_id,
            Mapping.method == "manual",
            Mapping.rationale == MANUAL_RATIONALE,
        )
    }


def _require_link_writes_allowed(db: Session) -> None:
    # Every evidence writer pauses during a manual evidence purge and its
    # cooldown (app/db/session.py rejects the flush). Report it in terms of the
    # record being saved before any entry is written.
    if purge_pause_status(db)["paused"]:
        raise DomainError(503, _PURGE_PAUSED, headers={"Retry-After": "60"})


def _apply_mappings(
    db: Session,
    event_id: uuid.UUID,
    current: dict[uuid.UUID, uuid.UUID],
    desired: set[uuid.UUID],
    actor: str | None,
) -> None:
    """Add and withdraw automatic mappings; unchanged mappings keep their author."""

    withdrawn = [current[item_id] for item_id in current.keys() - desired]
    if withdrawn:
        db.query(Mapping).filter(Mapping.id.in_(withdrawn)).delete(
            synchronize_session=False
        )
    wanted = desired - current.keys()
    if wanted:
        # Rule and administrator mappings already count this event for a
        # control; an automatic mapping would duplicate them.
        mapped = {
            item_id
            for (item_id,) in db.query(Mapping.control_item_id).filter(
                Mapping.event_id == event_id,
                Mapping.control_item_id.in_(wanted),
            )
        }
        for item_id in sorted(wanted - mapped, key=str):
            db.add(
                Mapping(
                    event_id=event_id,
                    control_item_id=item_id,
                    confidence=1.0,
                    method="manual",
                    rationale=MANUAL_RATIONALE,
                    mapped_by=actor or "unknown",
                )
            )
    db.flush()


def _store_revision(
    db: Session,
    event: Event,
    data: bytes,
    redaction_status: str,
    edited_by: str | None,
) -> None:
    """Record an automatic revision of the entry as a history artifact."""

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    key = (
        f"diary/{event.timestamp.date().isoformat()}/{event.id}/"
        f"edit-{stamp}-{uuid.uuid4().hex}.json"
    )
    stored = put_bytes(key=key, data=data, content_type="application/json")
    db.add(
        Artifact(
            event_id=event.id,
            kind="diary_entry_edit",
            storage_uri=stored.uri,
            sha256=stored.sha256,
            content_type="application/json",
            size_bytes=stored.size_bytes,
            captured_by=_CAPTURED_BY,
            redaction_status=redaction_status,
            meta={"edited_by": edited_by or "unknown"},
        )
    )
    db.flush()


def _create_entry(
    db: Session,
    entity_type: str,
    entity_id: uuid.UUID,
    refs: dict[str, list[str]],
    actor: str | None,
) -> uuid.UUID:
    timestamp = utc_now_naive()
    payload = _entry_payload(entity_type, entity_id, refs)
    payload["author"] = actor
    result = store_event_with_artifact(
        db,
        timestamp=timestamp,
        source=DIARY_SOURCE,
        system=LINK_ENTRY_SYSTEM,
        actor=actor,
        action=_ACTION,
        outcome="info",
        severity=2,
        summary=f"diary: {payload['summary']}",
        raw_pointer={"diary": {"author": actor}},
        normalized_payload=payload,
        external_id=link_entry_external_id(entity_type, entity_id),
        artifact_kind="diary_entry",
        artifact_bytes=_payload_bytes(payload),
        artifact_content_type="application/json",
        artifact_key=(
            f"diary/{timestamp.date().isoformat()}/{entity_type}-{entity_id}.json"
        ),
        captured_by=_CAPTURED_BY,
        commit=False,
    )
    return uuid.UUID(str(result["event_id"]))


def _revise_entry(
    db: Session,
    event: Event,
    entity_type: str,
    entity_id: uuid.UUID,
    refs: dict[str, list[str]],
    actor: str | None,
) -> bool:
    """Rewrite the entry text for the current links; return whether it changed.

    The text passes through the same redaction and masking as ingestion, so
    stored values compare like for like and masked values stay masked.
    """

    current = event.normalized_payload or {}
    fresh = _entry_payload(entity_type, entity_id, refs)
    desired = {**current, **fresh}
    if "links" not in fresh:
        desired.pop("links", None)
    revision = {**fresh, "author": current.get("author") or event.actor}
    fields = normalise_ingested_fields(
        system=event.system,
        actor=event.actor,
        action=event.action,
        outcome=event.outcome,
        summary=f"diary: {desired['summary']}",
        raw_pointer={},
        normalized_payload=desired,
        artifact_bytes=_payload_bytes(revision),
        artifact_content_type="application/json",
    )
    stored = fields.normalized_payload
    if all(
        (current.get(key) or None) == (stored.get(key) or None)
        for key in ("summary", "details", "links")
    ):
        return False
    _require_link_writes_allowed(db)
    event.normalized_payload = stored
    event.summary = fields.summary
    _store_revision(db, event, fields.artifact_bytes, fields.redaction_status, actor)
    return True


def sync_entity_link_diary(
    db: Session,
    entity_type: str,
    entity_id: uuid.UUID,
    user: User | None = None,
    *,
    actor: str | None = None,
) -> None:
    """Create or update the automatic diary entry for a record's links.

    Runs inside the caller's transaction; the caller owns the commit. A save
    whose links are unchanged writes nothing. Once an administrator has edited
    the entry, its text stays as edited and only the mappings follow the links.
    A collector that writes links names itself as ``actor`` in place of a user.
    """

    entity_type = _record_type(entity_type)
    entity_id = uuid.UUID(str(entity_id))
    actor = getattr(user, "username", None) or actor or None
    # Entries belong to Keen, so a collection run that changes links writes
    # them as a save in Keen would.
    with outside_connection():
        _sync(db, entity_type, entity_id, actor)


def _sync(db: Session, entity_type: str, entity_id: uuid.UUID, actor: str | None):
    desired, refs = _link_state(db, entity_type, entity_id)
    event = _find_event(db, link_entry_external_id(entity_type, entity_id))
    if event is None:
        if not desired and not refs:
            return
        _require_link_writes_allowed(db)
        event_id = _create_entry(db, entity_type, entity_id, refs, actor)
        _apply_mappings(db, event_id, _automatic_mappings(db, event_id), desired, actor)
        return

    if not _admin_edited(db, event.id):
        _revise_entry(db, event, entity_type, entity_id, refs, actor)
    current = _automatic_mappings(db, event.id)
    if current.keys() != desired:
        _require_link_writes_allowed(db)
        _apply_mappings(db, event.id, current, desired, actor)


def remove_entity_link_diary(
    db: Session, entity_type: str, entity_id: uuid.UUID
) -> None:
    """Withdraw the automatic diary entry of a deleted record.

    The automatic mappings always go. The entry itself stays when an
    administrator has edited it, a question thread or incident record refers
    to it, or an audit hold or another record references it.
    """

    entity_type = _record_type(entity_type)
    entity_id = uuid.UUID(str(entity_id))
    with outside_connection():
        _remove(db, entity_type, entity_id)


def _remove(db: Session, entity_type: str, entity_id: uuid.UUID) -> None:
    event = _find_event(db, link_entry_external_id(entity_type, entity_id))
    if event is None:
        return
    db.query(Mapping).filter(
        Mapping.event_id == event.id,
        Mapping.method == "manual",
        Mapping.rationale == MANUAL_RATIONALE,
    ).delete(synchronize_session=False)
    db.flush()
    db.expire(event, ["mappings"])
    discussed = (
        db.query(EventQuestionThread.id)
        .filter(EventQuestionThread.event_id == event.id)
        .first()
        is not None
        or db.query(EventIncident.id).filter(EventIncident.event_id == event.id).first()
        is not None
    )
    if discussed or _admin_edited(db, event.id) or is_referenced(db, event.id):
        return
    for (storage_uri,) in db.query(Artifact.storage_uri).filter(
        Artifact.event_id == event.id
    ):
        queue_object(db, storage_uri)
    db.delete(event)
    db.flush()
