"""Verification, chaining and storage of UEU evidence from agents.

Each OTLP resource in a request is one producer batch: its records' bodies
are canonical UEU envelopes and its attributes may carry a signed manifest
listing every envelope's hash. KEEN checks the envelopes, then the manifest,
then each record's place in the producer's chain, and stores what it accepts
with a provenance row recording the result of each check.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import timezone

from app.agents.ueu import catalogue as catalogues
from app.agents.ueu.custody import redact, withhold_payload
from app.agents.ueu.envelope import Envelope, EnvelopeError, validate
from app.agents.ueu.jcs import CanonicalError, canonical, parse_canonical
from app.core.config import settings
from app.db.models import AgentKey, UeuBatch, UeuEvidence
from app.ingest.common import store_event_with_artifact
from app.security.agent_signatures import SignatureError, verify_detached
from app.security.redaction import redact_obj
from app.services.errors import DomainError

BATCH_CONTEXT = "keen-ueu-batch-v1"
MODE_RANK = {"events": 1, "events_with_lines": 2, "all_lines": 3}
LINES_ONLY = {"log.line.collected"}
PLATFORMS = {"linux", "darwin", "windows"}


def effective_mode(env) -> int:
    """The evidence mode an envelope actually needs, as a rank.

    The declared collection_mode is the agent's own label; the content decides
    too, so an agent cannot send lines under its ceiling by omitting the label.
    """
    evidence = env.document["evidence"]
    rank = MODE_RANK[evidence.get("collection_mode", "events")]
    if evidence.get("payload_included") or "payload" in evidence:
        rank = max(rank, MODE_RANK["events_with_lines"])
    if env.event_type in LINES_ONLY:
        rank = max(rank, MODE_RANK["all_lines"])
    return rank


COVERAGE = "evidence.collection.completed"
_TEMPLATE = re.compile(r"\{([a-z][a-z0-9_.]{0,80})\}")
_KEY_ID = re.compile(r"[0-9a-f-]{36}:[0-9a-f]{16}")
_SIGNATURE = re.compile(r"[A-Za-z0-9_-]{86}")


class Rejected(DomainError):
    def __init__(self, code, detail):
        super().__init__(400, code + ": " + detail)


@dataclass
class Signed:
    verification: str
    key_id: str | None
    manifest: str | None
    signature: str | None


def _manifest(text, producer, entries):
    """Parse a batch manifest and check it lists exactly these envelopes."""
    try:
        doc, _ = parse_canonical(text)
    except CanonicalError:
        raise Rejected("UEU_EVIDENCE_VERIFICATION_FAILED", "manifest is not canonical")
    if (
        not isinstance(doc, dict)
        or set(doc) != {"context", "evidence", "issued_at", "key_id", "producer"}
        or doc["context"] != BATCH_CONTEXT
        or doc["producer"] != producer
        or not isinstance(doc["key_id"], str)
        or not _KEY_ID.fullmatch(doc["key_id"])
        or doc["key_id"].split(":", 1)[0] != producer
    ):
        raise Rejected("UEU_EVIDENCE_VERIFICATION_FAILED", "manifest is malformed")
    listed = doc["evidence"]
    if not isinstance(listed, list) or listed != [
        {"evidence_hash": h, "evidence_id": i} for i, h in entries
    ]:
        raise Rejected(
            "UEU_EVIDENCE_VERIFICATION_FAILED",
            "manifest does not list exactly the batch's envelopes",
        )
    return doc


def verify_batch(db, resource, envelopes) -> Signed:
    """Check a resource's manifest signature.

    A batch without a manifest is accepted as unsigned, and one signed by a
    key KEEN does not hold is accepted as unknown_key, the way a version
    control system shows commits signed by unknown keys. A signature that
    fails with a known key means the batch changed after signing, so the
    whole request is rejected.
    """
    attrs = resource.attributes
    manifest = attrs.get("ueu.batch.manifest")
    signature = attrs.get("ueu.batch.signature")
    key_id = attrs.get("ueu.batch.key_id")
    if manifest is None and signature is None and key_id is None:
        return Signed("unsigned", None, None, None)
    if not all(isinstance(v, str) for v in (manifest, signature, key_id)) or (
        not _SIGNATURE.fullmatch(signature)
    ):
        raise Rejected("UEU_EVIDENCE_VERIFICATION_FAILED", "incomplete signature")
    producer = attrs.get("ueu.producer.instance_id")
    doc = _manifest(
        manifest, producer, [(e.evidence_id, e.evidence_hash) for e in envelopes]
    )
    if doc["key_id"] != key_id:
        raise Rejected("UEU_EVIDENCE_VERIFICATION_FAILED", "key identifiers differ")
    key = db.get(AgentKey, key_id)
    if key is None or key.agent_id != producer:
        return Signed("unknown_key", key_id, manifest, signature)
    try:
        verify_detached(key.public_key, manifest.encode(), signature)
    except SignatureError:
        raise Rejected("UEU_EVIDENCE_VERIFICATION_FAILED", "batch signature invalid")
    state = "revoked_key" if key.state == "revoked" else "verified"
    return Signed(state, key_id, manifest, signature)


def advance(agent, env: Envelope) -> str:
    """Place an envelope in the agent's chain and move the chain forward."""
    last, last_hash = agent.ueu_last_sequence, agent.ueu_last_hash
    seq = env.sequence
    if last is None:
        status = "started" if seq == 1 else "gap"
    elif seq == last + 1:
        status = "continuous" if env.previous_hash == last_hash else "broken"
    elif seq == 1:
        status = "restarted"
    elif seq > last + 1:
        status = "gap"
    else:
        return "out_of_order"
    agent.ueu_last_sequence, agent.ueu_last_hash = seq, env.evidence_hash
    return status


def _value(doc, path):
    aliases = {
        "component": ("source", "component"),
        "scope": ("observation", "scope"),
        "reason_code": ("event", "reason_code"),
        "outcome": ("event", "outcome"),
        "action": ("event", "action"),
    }
    parts = aliases.get(path) or tuple(path.split("."))
    if parts[0] == "facts":
        parts = ("observation",) + parts
    value = doc
    for part in parts:
        if not isinstance(value, dict) or part not in value:
            return "unknown"
        value = value[part]
    if isinstance(value, list):
        return ", ".join(str(v) for v in value[:16])
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def summary(doc, spec) -> str:
    """Fill the catalogue's summary template from the stored envelope."""
    if spec is None or not spec.summary:
        text = doc["event_type"] + ": " + doc["event"]["reason_code"]
    else:
        text = _TEMPLATE.sub(lambda m: _value(doc, m[1]), spec.summary)
    return text[:4096]


def severity(doc) -> int:
    if doc["event_type"] == "security.alert.generated":
        level = doc.get("attributes", {}).get("level")
        if isinstance(level, int) and not isinstance(level, bool):
            return max(0, min(10, level * 2 // 3))
    return 6 if doc["event"]["outcome"] == "failure" else 4


def _validated(resource, agent):
    envelopes = []
    producer = resource.attributes.get("ueu.producer.instance_id")
    if not isinstance(producer, str):
        raise Rejected("UEU_SCHEMA_VALIDATION_FAILED", "producer instance missing")
    if agent.credential_type == "key" and producer != agent.id:
        raise Rejected("UEU_SCHEMA_VALIDATION_FAILED", "producer is another agent")
    for record in resource.records:
        try:
            env = validate(record.body)
        except EnvelopeError as exc:
            raise Rejected(exc.code, exc.detail) from None
        doc = env.document
        if (
            record.evidence_id != env.evidence_id
            or (record.event_name and record.event_name != env.event_type)
            or doc["source"]["instance_id"] != producer
        ):
            raise Rejected(
                "UEU_SCHEMA_VALIDATION_FAILED", "record attributes contradict the body"
            )
        if agent.credential_type == "key" and env.stream != agent.id:
            raise Rejected("UEU_SCHEMA_VALIDATION_FAILED", "stream is another agent's")
        envelopes.append(env)
    ids = [e.evidence_id for e in envelopes]
    if len(set(ids)) != len(ids):
        raise Rejected("UEU_SCHEMA_VALIDATION_FAILED", "evidence repeated in a batch")
    return envelopes


def persist(db, agent, resources, received_at):
    """Store a request's UEU resources. Caller holds the agent row lock.

    Returns (stored, duplicates, withheld). Everything is checked before
    anything is written, so a rejected request leaves no partial batch.
    """
    checked = []
    for resource in resources:
        envelopes = _validated(resource, agent)
        checked.append((envelopes, verify_batch(db, resource, envelopes)))
    stored = duplicates = withheld = 0
    ceiling = MODE_RANK[agent.evidence_mode_ceiling]
    for envelopes, signed in checked:
        batch = None
        for env in sorted(envelopes, key=lambda e: e.sequence):
            old = (
                db.query(UeuEvidence)
                .filter_by(agent_id=agent.id, evidence_id=env.evidence_id)
                .one_or_none()
            )
            if old is not None:
                if old.evidence_hash != env.evidence_hash:
                    raise DomainError(
                        409, "Evidence identifier reused with other content"
                    )
                duplicates += 1
                continue
            chain = advance(agent, env) if env.stream == agent.id else "untracked"
            doc, changes = redact(env.document, env.spec)
            if effective_mode(env) > ceiling:
                if env.event_type in LINES_ONLY:
                    # The organisation does not collect whole log lines from
                    # this agent; the chain still records that it was sent.
                    withheld += 1
                    continue
                withhold_payload(doc, changes, "above_evidence_mode_ceiling")
            if batch is None:
                batch = UeuBatch(
                    id=uuid.uuid4(),
                    agent_id=agent.id,
                    key_id=signed.key_id,
                    manifest=signed.manifest,
                    signature=signed.signature,
                    verification=signed.verification,
                    record_count=len(envelopes),
                    received_at=received_at,
                )
                db.add(batch)
                db.flush()
            store(db, agent, env, doc, changes, signed, chain, batch, received_at)
            stored += 1
            platform = env.document["asset"]["platform"]
            if (
                agent.credential_type == "key"
                and env.stream == agent.id
                and platform in PLATFORMS
                and agent.health.get("platform") != platform
            ):
                # The platform the agent's own key reports, for display.
                agent.health = {**agent.health, "platform": platform}
    return stored, duplicates, withheld


def store(db, agent, env, doc, changes, signed, chain, batch, received_at):
    custody = {
        "original_evidence_hash": env.evidence_hash,
        "changes": changes,
        # Optional ingestion masking replaces addresses in stored text after
        # this record is made, so the stored envelope may differ without a
        # listed change.
        "event_data_masking": (settings.event_data_masking or "false") == "true",
    }
    artifact = env.canonical if not changes else canonical(doc)
    subject = doc.get("subject", {}).get("identifier")
    result = store_event_with_artifact(
        db,
        timestamp=env.event_time.astimezone(timezone.utc).replace(tzinfo=None),
        source="keen-agent",
        system=agent.name,
        actor=subject,
        action=env.event_type,
        outcome=doc["event"]["outcome"],
        severity=severity(doc),
        summary=summary(doc, env.spec),
        raw_pointer={
            "agent": {
                "id": agent.id,
                "enrollment_profile_id": agent.enrollment_profile_id,
                "enrollment_labels": agent.enrollment_labels,
                "received_at": received_at.isoformat() + "Z",
            },
            "ueu": {
                "evidence_id": env.evidence_id,
                "evidence_hash": env.evidence_hash,
                "verification": signed.verification,
                "key_id": signed.key_id,
                "batch_id": str(batch.id),
                "sequence": env.sequence,
                "chain_status": chain,
                "schema_validation": env.schema_validation,
                "catalogue": env.catalogue,
                "custody_changes": len(changes),
            },
        },
        normalized_payload={"ueu": doc},
        external_id=agent.id + ":" + env.evidence_id,
        artifact_kind="ueu_envelope",
        artifact_bytes=artifact,
        artifact_content_type="application/json",
        artifact_key=f"agents/{agent.id}/ueu/{env.evidence_id}.json",
        captured_by="keen-agent:" + agent.id,
        commit=False,
        payload_redaction="redacted" if changes else "clean",
    )
    event_id = uuid.UUID(result["event_id"])
    if result.get("deduped"):
        raise DomainError(409, "Evidence identifier reused with other content")
    db.add(
        UeuEvidence(
            event_id=event_id,
            agent_id=agent.id,
            evidence_id=env.evidence_id,
            batch_id=batch.id,
            event_type=env.event_type,
            key_id=signed.key_id,
            evidence_hash=env.evidence_hash,
            sequence=env.sequence,
            verification=signed.verification,
            chain_status=chain,
            schema_validation=env.schema_validation,
            catalogue=env.catalogue,
            custody=custody,
        )
    )
    db.flush()
    if (
        agent.agent_type == "workstation"
        and agent.credential_type == "key"
        and agent.person_id is not None
        and env.spec is not None
        and (env.spec.cls == "observation" or env.event_type == COVERAGE)
    ):
        from app.services.workstation_posture import record_observation

        record_observation(db, agent, event_id)


def catalogue_grant():
    keen = catalogues.keen()
    return {"id": keen.id, "version": keen.version}


def evidence_record(db, event_id):
    """Provenance for one event, or None when it is not UEU evidence."""
    row = db.get(UeuEvidence, event_id)
    if row is None:
        return None
    batch = db.get(UeuBatch, row.batch_id) if row.batch_id else None
    return {
        "evidence_id": row.evidence_id,
        "event_type": row.event_type,
        "evidence_hash": row.evidence_hash,
        "sequence": row.sequence,
        "verification": row.verification,
        "chain_status": row.chain_status,
        "schema_validation": row.schema_validation,
        "catalogue": row.catalogue,
        "key_id": row.key_id,
        "custody": row.custody,
        "batch": {
            "id": str(batch.id),
            "received_at": batch.received_at,
            "record_count": batch.record_count,
            "manifest": json.loads(batch.manifest) if batch.manifest else None,
        }
        if batch
        else None,
    }


def display_payload(payload):
    """Redact a stored envelope for display with its catalogue's types.

    Stored envelopes were redacted on receipt; this repeats it so changes to
    KEEN's sensitive-key settings apply to evidence already held.
    """
    doc = (payload or {}).get("ueu")
    if not isinstance(doc, dict):
        return redact_obj(payload)
    spec = None
    ref = doc.get("catalogue")
    if isinstance(ref, dict):
        known = catalogues.lookup(str(ref.get("id")), str(ref.get("version")))
        if known is not None:
            spec = known.event_types.get(doc.get("event_type"))
    redacted, _ = redact(doc, spec)
    return {
        **redact_obj({k: v for k, v in payload.items() if k != "ueu"}),
        "ueu": redacted,
    }
