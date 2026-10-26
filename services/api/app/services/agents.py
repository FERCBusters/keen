from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import Field, model_validator
from sqlalchemy.orm import Session

from app.agents.credentials import authenticate_credential, live_only, view
from app.agents.schema import Strict, fingerprint
from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.db.models import AgentPostureObservation, Event, IsmsPerson, KeenAgent
from app.ingest.common import store_event_with_artifact
from app.security.redaction import redact_obj
from app.services.errors import DomainError
from app.services.features import state as feature_state


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


class Rotation(Strict):
    expires_days: int = Field(default=90, ge=1, le=365)


def issue(agent, days):
    token = "ka_" + agent.id + "." + secrets.token_urlsafe(32)
    agent.token_hash = hashlib.sha256(token.encode()).hexdigest()
    agent.previous_token_hash = None
    agent.renewal_nonce_hash = None
    agent.renewal_retry_until = None
    agent.expires_at = utc_now_naive() + timedelta(days=days)
    return token


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
    a = KeenAgent(
        id=str(uuid.uuid4()),
        name=body.name,
        enabled=True,
        health={},
        agent_type=body.agent_type,
        person_id=body.person_id,
        posture_assigned_at=utc_now_naive()
        if body.agent_type == "workstation"
        else None,
    )
    token = issue(a, body.expires_days)
    db.add(a)
    db.commit()
    return {"agent": view(a), "token": token}


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
    token = issue(a, body.expires_days)
    db.commit()
    return {"agent": view(a), "token": token}


def revoke(*, agent_id: str, db: Session):
    live_only()
    a = db.query(KeenAgent).filter_by(id=agent_id).with_for_update().one_or_none()
    if not a:
        raise DomainError(404, "Agent not found")
    a.enabled = False
    a.token_hash = ""
    db.commit()
    return {"ok": True}


def persist_batch(*, db, authorization, events):
    # Per-agent row lock serializes ingestion and credential revocation. One transaction
    # persists the whole batch; a lost acknowledgement is safely retried.
    a = authenticate_credential(authorization, db)
    accepted = []
    for event in events:
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
            payload = event.model_dump(mode="json")
            stored = store_event_with_artifact(
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
                artifact_key=f"agents/{a.id}/{event.id}/{uuid.uuid4()}.json",
                captured_by="keen-agent:" + a.id,
                commit=False,
            )
            if a.agent_type == "workstation" and a.person_id is not None:
                from app.services.workstation_posture import record_observation

                record_observation(db, a, uuid.UUID(stored["event_id"]))
        accepted.append(str(event.id))
    a.last_seen = utc_now_naive()
    a.health = {
        **a.health,
        "last_batch_records": len(events),
        "received_total": a.health.get("received_total", 0) + len(accepted),
    }
    db.commit()
    return {}


def record_heartbeat(*, db, authorization, health):
    a = authenticate_credential(authorization, db)
    a.last_seen = utc_now_naive()
    a.health = {
        **a.health,
        **redact_obj(health.model_dump(mode="json")),
        "health_received_at": utc_now_naive().isoformat(),
    }
    db.commit()
    return {"ok": True}
