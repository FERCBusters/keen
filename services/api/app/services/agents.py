from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import Field, model_validator
from sqlalchemy.orm import Session

from app.agents.credentials import live_only, reauthenticate, view
from app.agents.schema import Strict, fingerprint
from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.db.models import (
    AgentKey,
    AgentPostureObservation,
    Event,
    IsmsPerson,
    KeenAgent,
)
from app.ingest.common import store_event_with_artifact
from app.security.redaction import redact_obj, redact_str
from app.services.errors import DomainError
from app.services.features import state as feature_state
from app.services.ueu_evidence import persist as persist_ueu
from app.services.ueu_subjects import public_subject_key

MODES = Literal["events", "events_with_lines", "all_lines"]
CODE_LIFETIME = timedelta(hours=24)


class Assignment(Strict):
    agent_type: Literal["server", "workstation"] = "server"
    person_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def association(self):
        if (self.agent_type == "workstation") != (self.person_id is not None):
            raise ValueError("Workstations require a person; servers have no person")
        return self


class Registration(Assignment):
    name: str = Field(
        min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]*$"
    )
    expires_days: int = Field(default=90, ge=1, le=365)
    credential_type: Literal["key", "token"] = "key"
    subject_type: Literal["pairwise", "public"] = "pairwise"
    evidence_mode_ceiling: MODES = "events"

    @model_validator(mode="after")
    def collectors_are_servers(self):
        if self.credential_type == "token" and self.agent_type != "server":
            raise ValueError("Workstation posture agents enrol with a key")
        return self


class Rotation(Strict):
    expires_days: int = Field(default=90, ge=1, le=365)
    # Moves a token identity to key enrolment; keys never move back to tokens.
    credential_type: Literal["key"] | None = None


class EvidencePolicy(Strict):
    subject_type: Literal["pairwise", "public"] | None = None
    evidence_mode_ceiling: MODES | None = None


def issue(agent, days):
    token = "ka_" + agent.id + "." + secrets.token_urlsafe(32)
    agent.token_hash = hashlib.sha256(token.encode()).hexdigest()
    agent.previous_token_hash = None
    agent.renewal_nonce_hash = None
    agent.renewal_retry_until = None
    agent.expires_at = utc_now_naive() + timedelta(days=days)
    return token


def issue_code(agent, days):
    """A single-use enrolment code. The agent's key, bound by the code, is
    the credential; an earlier key stays usable until the code is used."""
    code = "kc_" + agent.id + "." + secrets.token_urlsafe(32)
    now = utc_now_naive()
    agent.token_hash = ""
    agent.previous_token_hash = None
    agent.enrollment_code_hash = hashlib.sha256(code.encode()).hexdigest()
    agent.enrollment_code_expires_at = now + CODE_LIFETIME
    agent.validity_days = days
    # An unused identity stays valid long enough to use its code.
    agent.expires_at = max(agent.expires_at or now, now + CODE_LIFETIME)
    return code


def grant_credential(agent, days):
    if agent.credential_type == "key":
        return {"enrollment_code": issue_code(agent, days)}
    return {"token": issue(agent, days)}


def listing(*, db: Session):
    return {
        "demo_mode": settings.demo_mode,
        "agents": [view(a) for a in db.query(KeenAgent).order_by(KeenAgent.name)],
        "people_enabled": feature_state(db).enabled("people"),
        "people": [
            {"id": str(p.id), "name": p.name}
            for p in db.query(IsmsPerson).order_by(IsmsPerson.name)
        ]
        if feature_state(db).enabled("people")
        else [],
    }


def register(*, body: Registration, db: Session):
    live_only()
    validate_assignment(db, body)
    if body.subject_type == "public":
        public_subject_key(db)
    a = KeenAgent(
        id=str(uuid.uuid4()),
        name=body.name,
        enabled=True,
        health={},
        agent_type=body.agent_type,
        person_id=body.person_id,
        credential_type=body.credential_type,
        subject_type=body.subject_type,
        evidence_mode_ceiling=body.evidence_mode_ceiling,
        validity_days=body.expires_days,
        posture_assigned_at=utc_now_naive()
        if body.agent_type == "workstation"
        else None,
    )
    secret = grant_credential(a, body.expires_days)
    db.add(a)
    db.commit()
    return {"agent": view(a), **secret}


def validate_assignment(db, body):
    if body.agent_type == "workstation":
        feature_state(db).require("people")
        if db.get(IsmsPerson, body.person_id) is None:
            raise DomainError(400, "Person not found")


def assign(*, agent_id: str, body: Assignment, db: Session):
    live_only()
    a = db.query(KeenAgent).filter_by(id=agent_id).with_for_update().one_or_none()
    if a is None:
        raise DomainError(404, "Agent not found")
    validate_assignment(db, body)
    if body.agent_type == "workstation" and a.credential_type != "key":
        # Posture is a signed assertion by the device's own key.
        raise DomainError(409, "Workstation posture agents enrol with a key")
    if a.agent_type != body.agent_type or a.person_id != body.person_id:
        # New assignments start with new observations. Historical raw events stay intact.
        db.query(AgentPostureObservation).filter_by(agent_id=a.id).delete()
        a.agent_type, a.person_id = body.agent_type, body.person_id
        a.posture_assigned_at = (
            utc_now_naive() if body.agent_type == "workstation" else None
        )
        a.health = {k: v for k, v in a.health.items() if k != "posture_overflow"}
    db.commit()
    return {"agent": view(a)}


def rotate(*, agent_id: str, body: Rotation, db: Session):
    live_only()
    a = db.query(KeenAgent).filter_by(id=agent_id).with_for_update().one_or_none()
    if not a:
        raise DomainError(404, "Agent not found")
    if not a.enabled:
        raise DomainError(
            409, "Revoked agents cannot be reactivated; register a new identity"
        )
    if body.credential_type == "key" and a.credential_type != "key":
        a.credential_type = "key"
    secret = grant_credential(a, body.expires_days)
    db.commit()
    return {"agent": view(a), **secret}


def set_evidence_policy(*, agent_id: str, body: EvidencePolicy, db: Session):
    """Change what an agent may send. KEEN enforces the ceiling on receipt;
    the agent learns both settings at its next enrolment or key renewal."""
    live_only()
    a = db.query(KeenAgent).filter_by(id=agent_id).with_for_update().one_or_none()
    if a is None:
        raise DomainError(404, "Agent not found")
    if body.subject_type == "public":
        public_subject_key(db)
    if body.subject_type is not None:
        a.subject_type = body.subject_type
    if body.evidence_mode_ceiling is not None:
        a.evidence_mode_ceiling = body.evidence_mode_ceiling
    db.commit()
    return {"agent": view(a)}


def revoke(*, agent_id: str, db: Session):
    live_only()
    a = db.query(KeenAgent).filter_by(id=agent_id).with_for_update().one_or_none()
    if not a:
        raise DomainError(404, "Agent not found")
    a.enabled = False
    a.token_hash = ""
    a.enrollment_code_hash = None
    db.query(AgentKey).filter(
        AgentKey.agent_id == a.id, AgentKey.state != "revoked"
    ).update(
        {AgentKey.state: "revoked", AgentKey.retired_at: utc_now_naive()},
        synchronize_session=False,
    )
    db.commit()
    return {"ok": True}


def stored_record(event) -> dict:
    """Return a plain OTLP log record as Keen stores and displays it."""
    return event.model_dump(mode="json")


def persist_batch(*, db, auth, authorization, batch):
    # The per-agent row lock serialises ingestion, chain updates and credential
    # revocation. One transaction persists the whole request; a lost
    # acknowledgement is safely retried.
    a, _ = reauthenticate(db, auth, authorization)
    if a.credential_type == "key" and batch.events:
        raise DomainError(
            400, "keen-agent identities send UEU evidence only; upgrade the agent"
        )
    received = utc_now_naive()
    stored, duplicates, withheld = persist_ueu(db, a, batch.ueu, received)
    accepted = []
    for event in batch.events:
        external = a.id + ":" + str(event.id)
        digest = fingerprint(event)
        old = (
            db.query(Event)
            .filter_by(source="keen-agent", external_id=external)
            .one_or_none()
        )
        if old:
            if old.raw_pointer.get("agent", {}).get("sha256") != digest:
                raise DomainError(409, "Event UUID reused with different content")
        else:
            payload = stored_record(event)
            store_event_with_artifact(
                db,
                timestamp=event.timestamp.astimezone(timezone.utc).replace(tzinfo=None),
                source="keen-agent",
                system=a.name,
                actor=redact_obj(event.actor),
                action=redact_obj(event.action),
                outcome=redact_obj(event.outcome),
                severity=event.severity,
                summary=redact_obj(event.summary),
                raw_pointer={
                    "agent": {
                        "id": a.id,
                        "enrollment_profile_id": a.enrollment_profile_id,
                        "enrollment_labels": a.enrollment_labels,
                        "event_id": str(event.id),
                        "sha256": digest,
                        "received_at": datetime.now(timezone.utc).isoformat(),
                    }
                },
                normalized_payload=payload,
                external_id=external,
                artifact_kind="agent_record",
                artifact_bytes=json.dumps(payload).encode(),
                artifact_content_type="application/json",
                artifact_key=f"agents/{a.id}/{event.id}.json",
                captured_by="keen-agent:" + a.id,
                commit=False,
            )
        accepted.append(str(event.id))
    a.last_seen = received
    a.health = {
        **a.health,
        "last_batch_records": batch.size,
        "received_total": a.health.get("received_total", 0) + len(accepted) + stored,
        "ueu_duplicates_total": a.health.get("ueu_duplicates_total", 0) + duplicates,
        "ueu_withheld_total": a.health.get("ueu_withheld_total", 0) + withheld,
    }
    db.commit()
    return {}


# Health maps keyed by configured source names. Their keys are names chosen by
# the operator (a source called "auth" is common), so name-based redaction
# would hide them; their values are validated counts, modes or short statuses.
SOURCE_MAPS = (
    "sources",
    "filtered_by_source",
    "not_evidence_by_source",
    "evidence_modes",
)


def health_record(health) -> dict:
    """The health report as stored: free text redacted, counts kept."""
    report = health.model_dump(mode="json")
    out = redact_obj({k: v for k, v in report.items() if k not in SOURCE_MAPS})
    for key in SOURCE_MAPS:
        out[key] = {
            name: redact_str(value) if isinstance(value, str) else value
            for name, value in report.get(key, {}).items()
        }
    return out


def record_heartbeat(*, db, auth, authorization, health):
    a, _ = reauthenticate(db, auth, authorization)
    a.last_seen = utc_now_naive()
    a.health = {
        **a.health,
        **health_record(health),
        "health_received_at": utc_now_naive().isoformat(),
    }
    db.commit()
    return {"ok": True}
