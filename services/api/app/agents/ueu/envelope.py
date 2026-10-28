"""Validation of UEU envelopes in KEEN's profile (``0.1-keen``).

The envelope arrives as the exact canonical bytes the producer hashed. KEEN
checks the bytes are canonical, then checks structure, then checks the typed
facts or attributes against the catalogue the envelope names. Every failure
carries a UEU error code; nothing is repaired or guessed.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.agents.ueu import catalogue as catalogues
from app.agents.ueu.jcs import CanonicalError, parse_canonical

SPEC_VERSION = "0.1-keen"
MAX_ENVELOPE_BYTES = 65536
MAX_PAYLOAD_BYTES = 60000
OUTCOMES = {"observed", "unknown", "success", "failure", "info"}
MODES = ("events", "events_with_lines", "all_lines")
EVIDENCE_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
HASH = re.compile(r"sha256:[0-9a-f]{64}")
PSEUDONYM = re.compile(r"hmac-sha256:[0-9a-f]{64}")
TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z")
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/+-]{0,63}")
CONTEXT_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/+=-]{0,127}")
# The fixed vocabulary of privacy transformations producers record. Values in
# this shape name a field and a reason; they never hold the field's value.
TRANSFORMATION = re.compile(
    r"subject-pseudonymised|removed:[a-z][a-z0-9_.]{0,63}:[a-z_]{1,32}"
)
TOP = {
    "ueu_spec_version",
    "evidence_id",
    "event_type",
    "catalogue",
    "event_time",
    "evidence_time",
    "source",
    "asset",
    "event",
    "evidence",
    "privacy",
    "mappings",
    "integrity",
}
OPTIONAL = {"subject", "observation", "attributes", "context"}
# Correlation identifiers; event detail belongs in facts or attributes.
CONTEXT = {
    "transaction_id",
    "trace_id",
    "span_id",
    "correlation_id",
    "session_id",
    "workflow",
    "parent_evidence_id",
    "related_evidence_ids",
}


class EnvelopeError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(code + ": " + detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Envelope:
    document: dict[str, Any]
    canonical: bytes
    evidence_hash: str
    evidence_id: str
    event_type: str
    event_time: datetime
    sequence: int
    stream: str
    previous_hash: str | None
    catalogue: str
    spec: catalogues.EventType | None

    @property
    def schema_validation(self) -> str:
        return "valid" if self.spec is not None else "catalogue_unknown"


def _fail(detail, code="UEU_SCHEMA_VALIDATION_FAILED"):
    raise EnvelopeError(code, detail)


def _object(value, name, required, optional=frozenset()):
    if not isinstance(value, dict):
        _fail(name + " must be an object")
    keys = set(value)
    if not set(required) <= keys or keys - set(required) - set(optional):
        _fail(name + " has missing or unexpected members")
    return value


def _text(value, name, limit, pattern=None):
    if not isinstance(value, str) or len(value.encode("utf-8")) > limit:
        _fail(name + " must be a string of at most %d bytes" % limit)
    if pattern is not None and not pattern.fullmatch(value):
        _fail(name + " has an invalid form")
    return value


def _time(value, name):
    _text(value, name, 24, TIME)
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        _fail(name + " is not a valid time")


def _integer(value, name, low, high):
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not low <= value <= high
    ):
        _fail(name + " must be an integer between %d and %d" % (low, high))
    return value


def validate(body: str, *, now: datetime | None = None) -> Envelope:
    """Validate one envelope's text, which must be its canonical form."""
    if len(body) > MAX_ENVELOPE_BYTES:
        _fail("envelope exceeds 64 KiB")
    try:
        doc, raw = parse_canonical(body)
    except CanonicalError as exc:
        raise EnvelopeError("UEU_CANONICALISATION_FAILED", str(exc)) from None
    if len(raw) > MAX_ENVELOPE_BYTES:
        _fail("envelope exceeds 64 KiB")
    _object(doc, "envelope", TOP, OPTIONAL)
    if doc["ueu_spec_version"] != SPEC_VERSION:
        _fail("unsupported profile", "UEU_SPEC_VERSION_UNSUPPORTED")
    evidence_id = _text(doc["evidence_id"], "evidence_id", 36, EVIDENCE_ID)
    event_type = _text(doc["event_type"], "event_type", 128, catalogues.EVENT_TYPE)
    ref = _object(doc["catalogue"], "catalogue", {"id", "version"})
    _text(ref["id"], "catalogue.id", 32, catalogues.CATALOGUE_ID)
    _text(ref["version"], "catalogue.version", 32, catalogues.CATALOGUE_VERSION)
    now = now or datetime.now(timezone.utc)
    event_time = _time(doc["event_time"], "event_time")
    evidence_time = _time(doc["evidence_time"], "evidence_time")
    if event_time.year < 2000 or event_time > now + timedelta(minutes=5):
        _fail("event_time must be between 2000 and now + 5 minutes")
    if evidence_time > now + timedelta(minutes=5):
        _fail("evidence_time is in the future")
    source = _object(
        doc["source"],
        "source",
        {"application_id", "application_version", "component", "instance_id"},
    )
    _text(source["application_id"], "source.application_id", 64, TOKEN)
    _text(source["application_version"], "source.application_version", 64)
    _text(source["component"], "source.component", 256)
    _text(source["instance_id"], "source.instance_id", 64, TOKEN)
    asset = _object(doc["asset"], "asset", {"asset_type", "platform", "identifier"})
    _text(asset["asset_type"], "asset.asset_type", 32)
    _text(asset["platform"], "asset.platform", 32)
    _text(asset["identifier"], "asset.identifier", 64)
    event = _object(
        doc["event"], "event", {"action", "object_type", "outcome", "reason_code"}
    )
    _text(event["action"], "event.action", 256)
    _text(event["object_type"], "event.object_type", 64, catalogues.FIELD_NAME)
    _text(event["reason_code"], "event.reason_code", 256)
    if event["outcome"] not in OUTCOMES:
        _fail("event.outcome is not a profile outcome")
    if "subject" in doc:
        subject = _object(doc["subject"], "subject", {"identifier_type", "identifier"})
        if subject["identifier_type"] not in ("pairwise", "public"):
            _fail("subject.identifier_type must be pairwise or public")
        _text(subject["identifier"], "subject.identifier", 80, PSEUDONYM)
    if "context" in doc:
        context = _object(doc["context"], "context", set(), CONTEXT)
        for key, value in context.items():
            if key == "related_evidence_ids":
                if not isinstance(value, list) or len(value) > 16:
                    _fail("context.related_evidence_ids must list at most 16")
                for item in value:
                    _text(item, "context.related_evidence_ids[]", 36, EVIDENCE_ID)
            else:
                _text(value, "context." + key, 128, CONTEXT_VALUE)
    privacy = _object(
        doc["privacy"],
        "privacy",
        {"profile", "personal_data_included", "transformations"},
    )
    _text(privacy["profile"], "privacy.profile", 64, TOKEN)
    if not isinstance(privacy["personal_data_included"], bool):
        _fail("privacy.personal_data_included must be a boolean")
    changes = privacy["transformations"]
    if not isinstance(changes, list) or len(changes) > 64:
        _fail("privacy.transformations must be a list of at most 64")
    for change in changes:
        _text(change, "privacy.transformations[]", 160, TRANSFORMATION)
    mappings = doc["mappings"]
    if not isinstance(mappings, list) or len(mappings) > 32:
        _fail("mappings must be a list of at most 32")
    for mapping in mappings:
        if not isinstance(mapping, dict) or len(mapping) > 8:
            _fail("mappings[] must be small objects")
        for key, value in mapping.items():
            _text(key, "mappings[] name", 64, catalogues.FIELD_NAME)
            _text(value, "mappings[] value", 256)
    integrity = _object(
        doc["integrity"],
        "integrity",
        {
            "canonicalisation",
            "hash_algorithm",
            "stream",
            "sequence",
            "previous_evidence_hash",
        },
    )
    if (
        integrity["canonicalisation"] != "RFC8785"
        or integrity["hash_algorithm"] != "SHA-256"
    ):
        _fail("integrity must name RFC8785 and SHA-256")
    stream = _text(integrity["stream"], "integrity.stream", 64, TOKEN)
    sequence = _integer(integrity["sequence"], "integrity.sequence", 1, 2**53 - 1)
    previous = integrity["previous_evidence_hash"]
    if previous is not None:
        _text(previous, "integrity.previous_evidence_hash", 71, HASH)
    if (sequence == 1) != (previous is None):
        _fail("only a stream's first record has no previous hash")

    spec = None
    known = catalogues.lookup(ref["id"], ref["version"])
    if known is not None:
        spec = known.event_types.get(event_type)
        if spec is None:
            _fail(event_type + " is not in its catalogue", "UEU_EVENT_TYPE_UNKNOWN")
        if event["object_type"] != spec.object_type:
            _fail("event.object_type does not match the catalogue")
    observed = "observation" in doc
    if observed == ("attributes" in doc):
        _fail("an envelope carries either an observation or attributes")
    if spec is not None and observed != (spec.cls == "observation"):
        _fail("the envelope's class does not match the catalogue")
    if observed:
        observation = _object(
            doc["observation"],
            "observation",
            {"method", "scope", "valid_for_seconds", "facts"},
        )
        _text(observation["method"], "observation.method", 64, TOKEN)
        _text(observation["scope"], "observation.scope", 256)
        _integer(
            observation["valid_for_seconds"],
            "observation.valid_for_seconds",
            1,
            31536000,
        )
        if event["outcome"] != "observed":
            _fail("observations are measured states (outcome observed)")
        error = catalogues.check_fields(
            observation["facts"],
            spec.facts if spec else None,
            spec.additional_facts if spec else "",
            spec.prohibited if spec else frozenset(),
        )
    else:
        error = catalogues.check_fields(
            doc["attributes"],
            spec.attributes if spec else None,
            spec.additional_attributes if spec else "",
            spec.prohibited if spec else frozenset(),
        )
    if error:
        _fail("facts or attributes break the catalogue", error)
    if spec is not None:
        if spec.subject == "forbidden" and "subject" in doc:
            _fail("subject is forbidden for this type", "UEU_PROHIBITED_FIELD_DETECTED")
        if spec.subject == "required" and "subject" not in doc:
            _fail("subject is required for this type")
    evidence = _object(
        doc["evidence"],
        "evidence",
        {"payload_included"},
        {"payload", "payload_hash", "supporting_record_reference", "collection_mode"},
    )
    included = evidence["payload_included"]
    if not isinstance(included, bool) or included != ("payload" in evidence):
        _fail("evidence.payload must be present exactly when included")
    if "collection_mode" in evidence and evidence["collection_mode"] not in MODES:
        _fail("evidence.collection_mode is not a profile mode")
    if "supporting_record_reference" in evidence:
        _text(
            evidence["supporting_record_reference"],
            "evidence.supporting_record_reference",
            512,
        )
    if "payload_hash" in evidence:
        _text(evidence["payload_hash"], "evidence.payload_hash", 71, HASH)
    if included:
        payload = _text(evidence["payload"], "evidence.payload", MAX_PAYLOAD_BYTES)
        digest = "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
        if evidence.get("payload_hash") != digest:
            _fail("evidence.payload_hash does not match the payload")
    if privacy["personal_data_included"] and not included:
        # Only an included source record may carry personal data; structured
        # fields carry pseudonyms.
        _fail("personal data is outside the profile", "UEU_PRIVACY_POLICY_FAILED")
    if spec is not None:
        if spec.payload == "forbidden" and included:
            _fail("payload is forbidden for this type", "UEU_PROHIBITED_FIELD_DETECTED")
        if spec.payload == "required" and not included:
            _fail("payload is required for this type")
    return Envelope(
        document=doc,
        canonical=raw,
        evidence_hash="sha256:" + hashlib.sha256(raw).hexdigest(),
        evidence_id=evidence_id,
        event_type=event_type,
        event_time=event_time,
        sequence=sequence,
        stream=stream,
        previous_hash=previous,
        catalogue=ref["id"] + "@" + ref["version"],
        spec=spec,
    )
