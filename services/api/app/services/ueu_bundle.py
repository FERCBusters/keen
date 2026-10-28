"""Verification bundles of stored UEU evidence.

A bundle holds everything an independent reviewer needs to check stored
evidence without access to KEEN: each stored envelope, the signed batch
manifests that list them, the public keys that signed the manifests and the
catalogues the envelopes follow. ``tools/ueu_verify_bundle.py`` checks one.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone

from sqlalchemy import select

from app.agents.ueu import catalogue as catalogues
from app.db.models import AgentKey, Artifact, Event, KeenAgent, UeuBatch, UeuEvidence
from app.services.errors import DomainError
from app.storage.s3 import get_object_stream, parse_s3_uri

FORMAT = "keen-ueu-bundle-1"
MAX_RECORDS = 1000
MAX_ENVELOPE_BYTES = 70000
MAX_TOTAL_BYTES = 64 * 1024 * 1024
README = """KEEN UEU verification bundle

manifest.json        Every record: its evidence hash, batch, chain position and
                     the changes KEEN made before storing it.
envelopes/           Each stored envelope. With no custody changes these are the
                     exact bytes the producer hashed: SHA-256 of the file is the
                     evidence hash.
batches/             Each batch manifest as received and its Ed25519 signature
                     (unpadded base64url over the manifest's bytes).
keys/                The public keys (raw Ed25519, unpadded base64url).
catalogues/          The catalogues the envelopes name.

Check it with:  python3 ueu_verify_bundle.py bundle.zip
"""


def _read(artifact):
    bucket, key = parse_s3_uri(artifact.storage_uri)
    body = get_object_stream(bucket, key)["Body"]
    try:
        data = body.read(MAX_ENVELOPE_BYTES + 1)
    finally:
        body.close()
    if len(data) > MAX_ENVELOPE_BYTES:
        raise DomainError(500, "A stored envelope exceeds its size limit")
    return data


def _file(name):
    return name.replace(":", "_")


def build(db, rows) -> bytes:
    """ZIP the given UeuEvidence rows with their batches, keys and catalogues."""
    if len(rows) > MAX_RECORDS:
        raise DomainError(
            413, f"Choose at most {MAX_RECORDS} records for one verification bundle"
        )
    buffer = io.BytesIO()
    total = 0
    batches, keys, used = {}, {}, set()
    records = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for row in rows:
            artifact = db.scalars(
                select(Artifact)
                .where(
                    Artifact.event_id == row.event_id, Artifact.kind == "ueu_envelope"
                )
                .limit(1)
            ).first()
            if artifact is None:
                continue
            data = _read(artifact)
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise DomainError(
                    413, "Choose fewer records for one verification bundle"
                )
            zf.writestr(f"envelopes/{row.evidence_id}.json", data)
            used.add(row.catalogue)
            if row.batch_id and row.batch_id not in batches:
                batches[row.batch_id] = db.get(UeuBatch, row.batch_id)
            if row.key_id and row.key_id not in keys:
                keys[row.key_id] = db.get(AgentKey, row.key_id)
            records.append(
                {
                    "event_id": str(row.event_id),
                    "agent_id": row.agent_id,
                    "evidence_id": row.evidence_id,
                    "event_type": row.event_type,
                    "evidence_hash": row.evidence_hash,
                    "sequence": row.sequence,
                    "batch_id": str(row.batch_id) if row.batch_id else None,
                    "key_id": row.key_id,
                    "verification": row.verification,
                    "chain_status": row.chain_status,
                    "schema_validation": row.schema_validation,
                    "catalogue": row.catalogue,
                    "custody": row.custody,
                    "file": f"envelopes/{row.evidence_id}.json",
                }
            )
        for ident, batch in batches.items():
            if batch is None:
                continue
            zf.writestr(
                f"batches/{ident}.json",
                json.dumps(
                    {
                        "id": str(ident),
                        "agent_id": batch.agent_id,
                        "key_id": batch.key_id,
                        "manifest": batch.manifest,
                        "signature": batch.signature,
                        "verification": batch.verification,
                        "received_at": batch.received_at.isoformat() + "Z",
                    },
                    indent=2,
                ),
            )
        for ident, key in keys.items():
            if key is None:
                continue
            zf.writestr(
                f"keys/{_file(ident)}.json",
                json.dumps(
                    {
                        "key_id": key.key_id,
                        "agent_id": key.agent_id,
                        "public_key": key.public_key,
                        "algorithm": "Ed25519",
                        "state": key.state,
                        "created_at": key.created_at.isoformat() + "Z",
                        "retired_at": key.retired_at.isoformat() + "Z"
                        if key.retired_at
                        else None,
                    },
                    indent=2,
                ),
            )
        for ref in sorted(used):
            ident, _, version = ref.partition("@")
            if catalogues.lookup(ident, version) is None:
                continue
            for path in catalogues.BUILTIN.glob("*.json"):
                doc = json.loads(path.read_bytes())
                head = doc.get("catalogue", {})
                if head.get("id") == ident and head.get("version") == version:
                    zf.writestr(f"catalogues/{_file(ref)}.json", path.read_bytes())
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": FORMAT,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "records": records,
                },
                indent=2,
            ),
        )
        zf.writestr("README.txt", README)
    return buffer.getvalue()


def event_bundle(*, db, event_id) -> bytes:
    row = db.get(UeuEvidence, event_id)
    if row is None or db.get(Event, event_id) is None:
        raise DomainError(404, "No UEU evidence for this event")
    return build(db, [row])


def agent_bundle(*, db, agent_id, since: datetime, until: datetime) -> bytes:
    if db.get(KeenAgent, agent_id) is None:
        raise DomainError(404, "Agent not found")
    if until <= since:
        raise DomainError(400, "The end of the period must follow its start")
    rows = db.scalars(
        select(UeuEvidence)
        .join(Event, Event.id == UeuEvidence.event_id)
        .where(
            UeuEvidence.agent_id == agent_id,
            Event.timestamp >= since,
            Event.timestamp < until,
        )
        .order_by(UeuEvidence.sequence, UeuEvidence.evidence_id)
        .limit(MAX_RECORDS + 1)
    ).all()
    return build(db, rows)
