"""KEEN's UEU profile checked directly: canonical JSON, catalogues and envelope
validation. Each rejected case changes one part of an otherwise valid
envelope or catalogue."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from app.agents.ueu import catalogue as catalogues
from app.agents.ueu import envelope, jcs
from app.agents.ueu.catalogue import CatalogueError, FieldSpec
from app.agents.ueu.envelope import EnvelopeError

NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
EVIDENCE_ID = "0192a6c0-0000-7000-8000-000000000001"
HASH = "sha256:" + "a" * 64
PSEUDONYM = "hmac-sha256:" + "b" * 64


def stamp(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def observation(**facts) -> dict:
    return {
        "ueu_spec_version": "0.1-keen",
        "evidence_id": EVIDENCE_ID,
        "event_type": "endpoint.firewall.observed",
        "catalogue": {"id": "keen", "version": "2026.10"},
        "event_time": stamp(NOW),
        "evidence_time": stamp(NOW),
        "source": {
            "application_id": "keen-agent",
            "application_version": "0.2.0",
            "component": "ufw",
            "instance_id": "agent-1",
        },
        "asset": {"asset_type": "server", "platform": "linux", "identifier": "a1"},
        "event": {
            "action": "observe",
            "object_type": "firewall",
            "outcome": "observed",
            "reason_code": "observed",
        },
        "privacy": {
            "profile": "keen-default-1",
            "personal_data_included": False,
            "transformations": [],
        },
        "mappings": [],
        "integrity": {
            "canonicalisation": "RFC8785",
            "hash_algorithm": "SHA-256",
            "stream": "agent-1",
            "sequence": 1,
            "previous_evidence_hash": None,
        },
        "observation": {
            "method": "native_query",
            "scope": "host",
            "valid_for_seconds": 86400,
            "facts": facts or {"enabled": True},
        },
        "evidence": {"payload_included": False},
    }


def log_line(payload="sshd: session opened") -> dict:
    env = observation()
    del env["observation"]
    env["event_type"] = "log.line.collected"
    env["event"].update(object_type="log_line", outcome="info")
    env["attributes"] = {"source": "auth.log", "parser": "syslog", "extra": "x"}
    env["privacy"]["personal_data_included"] = True
    env["evidence"] = {
        "payload_included": True,
        "payload": payload,
        "payload_hash": "sha256:" + hashlib.sha256(payload.encode()).hexdigest(),
        "collection_mode": "all_lines",
        "supporting_record_reference": "journal:1",
    }
    return env


def check(env: dict) -> envelope.Envelope:
    return envelope.validate(jcs.canonical(env).decode(), now=NOW)


def refused(env: dict) -> str:
    with pytest.raises(EnvelopeError) as caught:
        check(env)
    return caught.value.code


def test_valid_observations_and_events_are_accepted():
    found = check(observation())
    assert found.schema_validation == "valid"
    assert found.catalogue == "keen@2026.10" and found.sequence == 1
    assert found.evidence_hash.startswith("sha256:")
    line = log_line()
    line["context"] = {
        "trace_id": "4bf92f3577b34da6",
        "related_evidence_ids": [EVIDENCE_ID],
    }
    line["mappings"] = [{"control": "A.8.15"}]
    line["privacy"]["transformations"] = ["removed:password:secret"]
    line["integrity"].update(sequence=2, previous_evidence_hash=HASH)
    assert check(line).previous_hash == HASH
    public = observation()
    public["event_type"] = "endpoint.screen_lock.observed"
    public["event"]["object_type"] = "screen_lock"
    public["observation"]["facts"] = {"password_required": True}
    public["subject"] = {"identifier_type": "public", "identifier": PSEUDONYM}
    assert check(public).spec.subject == "optional"


def test_unknown_catalogues_get_structural_checks_only():
    env = observation(anything="goes", count=3)
    env["catalogue"] = {"id": "acme", "version": "1"}
    assert check(env).schema_validation == "catalogue_unknown"
    line = log_line()
    line["catalogue"] = {"id": "acme", "version": "1"}
    line["attributes"] = {"free": ["a", "b"]}
    assert check(line).spec is None
    line["attributes"] = {"nested": {"a": 1}}
    assert refused(line) == "UEU_SCHEMA_VALIDATION_FAILED"


def test_oversized_and_non_canonical_texts_are_refused():
    with pytest.raises(EnvelopeError) as caught:
        envelope.validate("x" * (envelope.MAX_ENVELOPE_BYTES + 1))
    assert caught.value.code == "UEU_SCHEMA_VALIDATION_FAILED"
    # Within the character limit but over it in UTF-8 bytes.
    wide = jcs.canonical(["é" * 40000]).decode()
    with pytest.raises(EnvelopeError):
        envelope.validate(wide)
    with pytest.raises(EnvelopeError) as caught:
        envelope.validate(json.dumps(observation(), indent=1))
    assert caught.value.code == "UEU_CANONICALISATION_FAILED"
    with pytest.raises(EnvelopeError) as caught:
        envelope.validate("[]")
    assert "must be an object" in caught.value.detail


def change(path: str, value):
    """Set (or with ``...`` delete) one member of a valid observation."""

    def apply(env):
        *parents, last = path.split(".")
        target = env
        for part in parents:
            target = target[part]
        if value is ...:
            del target[last]
        else:
            target[last] = copy.deepcopy(value)
        return env

    return apply


SCHEMA = "UEU_SCHEMA_VALIDATION_FAILED"
LATER = stamp(NOW + timedelta(days=1))


@pytest.mark.parametrize(
    "edit, code",
    [
        (change("mappings", ...), SCHEMA),
        (change("extra", 1), SCHEMA),
        (change("ueu_spec_version", "0.1"), "UEU_SPEC_VERSION_UNSUPPORTED"),
        (change("evidence_id", "not-an-id"), SCHEMA),
        (change("evidence_id", 7), SCHEMA),
        (change("event_type", "Endpoint"), SCHEMA),
        (change("catalogue", "keen"), SCHEMA),
        (change("catalogue.extra", "x"), SCHEMA),
        (change("catalogue.id", "Keen"), SCHEMA),
        (change("catalogue.version", "-1"), SCHEMA),
        (change("event_time", "2026-13-45T00:00:00.000Z"), SCHEMA),
        (change("event_time", "1999-12-31T00:00:00.000Z"), SCHEMA),
        (change("event_time", LATER), SCHEMA),
        (change("evidence_time", LATER), SCHEMA),
        (change("source.application_id", "has space"), SCHEMA),
        (change("source.component", "c" * 257), SCHEMA),
        (change("source.instance_id", ""), SCHEMA),
        (change("asset.platform", "p" * 33), SCHEMA),
        (change("event.object_type", "Firewall"), SCHEMA),
        (change("event.reason_code", None), SCHEMA),
        (change("event.outcome", "maybe"), SCHEMA),
        (change("subject", {"identifier_type": "email", "identifier": "a"}), SCHEMA),
        (change("subject", {"identifier_type": "public", "identifier": "a"}), SCHEMA),
        (change("context", {"username": "alice"}), SCHEMA),
        (change("context", {"related_evidence_ids": "x"}), SCHEMA),
        (change("context", {"related_evidence_ids": [EVIDENCE_ID] * 17}), SCHEMA),
        (change("context", {"related_evidence_ids": ["x"]}), SCHEMA),
        (change("context", {"trace_id": "has spaces"}), SCHEMA),
        (change("privacy.profile", "bad profile"), SCHEMA),
        (change("privacy.personal_data_included", "no"), SCHEMA),
        (change("privacy.transformations", "none"), SCHEMA),
        (change("privacy.transformations", ["subject-pseudonymised"] * 65), SCHEMA),
        (change("privacy.transformations", ["removed:alice@example.org"]), SCHEMA),
        (change("mappings", {"control": "A.5.1"}), SCHEMA),
        (change("mappings", [{"k": "v"}] * 33), SCHEMA),
        (change("mappings", ["A.5.1"]), SCHEMA),
        (change("mappings", [{f"k{i}": "v" for i in range(9)}]), SCHEMA),
        (change("mappings", [{"Bad Name": "v"}]), SCHEMA),
        (change("mappings", [{"control": 5}]), SCHEMA),
        (change("integrity.canonicalisation", "JCS"), SCHEMA),
        (change("integrity.hash_algorithm", "SHA-1"), SCHEMA),
        (change("integrity.stream", "has space"), SCHEMA),
        (change("integrity.sequence", True), SCHEMA),
        (change("integrity.sequence", 0), SCHEMA),
        (change("integrity.sequence", "1"), SCHEMA),
        (change("integrity.previous_evidence_hash", "sha256:short"), SCHEMA),
        (change("integrity.previous_evidence_hash", HASH), SCHEMA),
        (change("integrity.sequence", 2), SCHEMA),
        (change("event_type", "endpoint.unknown.observed"), "UEU_EVENT_TYPE_UNKNOWN"),
        (change("event.object_type", "antivirus"), SCHEMA),
        (change("observation", ...), SCHEMA),
        (change("attributes", {"a": "b"}), SCHEMA),
        (change("observation.method", "by hand"), SCHEMA),
        (change("observation.valid_for_seconds", 0), SCHEMA),
        (change("event.outcome", "success"), SCHEMA),
        (change("observation.facts", {"enabled": "yes"}), SCHEMA),
        (change("observation.facts", {"Bad Name": True}), SCHEMA),
        (
            change("subject", {"identifier_type": "pairwise", "identifier": PSEUDONYM}),
            "UEU_PROHIBITED_FIELD_DETECTED",
        ),
        (change("evidence", {}), SCHEMA),
        (change("evidence.payload_included", "no"), SCHEMA),
        (change("evidence.payload", "line"), SCHEMA),
        (change("evidence.collection_mode", "everything"), SCHEMA),
        (change("evidence.supporting_record_reference", "r" * 513), SCHEMA),
        (change("evidence.payload_hash", "md5:abc"), SCHEMA),
        (change("privacy.personal_data_included", True), "UEU_PRIVACY_POLICY_FAILED"),
    ],
)
def test_one_wrong_member_refuses_the_envelope(edit, code):
    assert refused(edit(observation())) == code


def test_payload_rules_follow_the_catalogue():
    line = log_line()
    line["evidence"]["payload_hash"] = HASH
    assert refused(line) == SCHEMA
    line = log_line()
    line["evidence"] = {"payload_included": False}
    line["privacy"]["personal_data_included"] = False
    assert refused(line) == SCHEMA
    line = log_line()
    line["attributes"]["count"] = 3
    assert refused(line) == SCHEMA
    payload = "probe"
    env = observation()
    env["evidence"] = {
        "payload_included": True,
        "payload": payload,
        "payload_hash": "sha256:" + hashlib.sha256(payload.encode()).hexdigest(),
    }
    assert refused(env) == "UEU_PROHIBITED_FIELD_DETECTED"
    # A record of the event class may not arrive as an observation.
    line = log_line()
    line["observation"] = observation()["observation"]
    del line["attributes"]
    assert refused(line) == SCHEMA


def test_a_required_subject_must_be_present(monkeypatch):
    raw = {
        "catalogue": {"id": "acme", "version": "1", "description": "d" * 2000},
        "event_types": {
            "acme.login.recorded": {
                "class": "event",
                "object_type": "account",
                "subject": "required",
                "payload": "optional",
                "attributes": {"method": {"type": "string"}},
            }
        },
    }
    acme = catalogues.parse_catalogue(json.dumps(raw).encode())
    assert len(acme.description) == 1024 and acme.ref == "acme@1"
    monkeypatch.setattr(
        envelope.catalogues,
        "lookup",
        lambda ident, version: acme if ident == "acme" else None,
    )
    env = observation()
    del env["observation"]
    env.update(event_type="acme.login.recorded", attributes={"method": "sso"})
    env["catalogue"] = {"id": "acme", "version": "1"}
    env["event"].update(object_type="account", outcome="success")
    assert refused(env) == SCHEMA
    env["subject"] = {"identifier_type": "pairwise", "identifier": PSEUDONYM}
    assert check(env).spec.name == "acme.login.recorded"


# --- catalogues ------------------------------------------------------------------


def catalogue_doc(**event) -> dict:
    spec = {
        "class": "observation",
        "object_type": "widget",
        "subject": "optional",
        "payload": "forbidden",
        "facts": {"enabled": {"type": "boolean"}},
    }
    spec.update(event)
    return {
        "catalogue": {"id": "acme", "version": "1"},
        "event_types": {"acme.widget.observed": spec},
    }


@pytest.mark.parametrize(
    "doc",
    [
        ["not", "an", "object"],
        {**catalogue_doc(), "extra": 1},
        {**catalogue_doc(), "catalogue": {"id": "Acme", "version": "1"}},
        {**catalogue_doc(), "catalogue": {"id": "acme", "version": 1}},
        {**catalogue_doc(), "event_types": {"Bad": {}}},
        {**catalogue_doc(), "event_types": {"acme.widget.observed": "x"}},
        {**catalogue_doc(), "event_types": {}},
        catalogue_doc(colour="red"),
        catalogue_doc(**{"class": "measurement"}),
        catalogue_doc(subject="sometimes"),
        catalogue_doc(prohibited="password"),
        catalogue_doc(prohibited=[1]),
        catalogue_doc(summary="s" * 257),
        catalogue_doc(object_type="Widget"),
        catalogue_doc(additional_facts="anything"),
        catalogue_doc(facts=["enabled"]),
        catalogue_doc(facts={"Bad Name": {"type": "boolean"}}),
        catalogue_doc(facts={"enabled": "boolean"}),
        catalogue_doc(facts={"enabled": {"type": "boolean", "default": True}}),
        catalogue_doc(facts={"enabled": {"type": "float"}}),
        catalogue_doc(
            facts={"enabled": {"type": "boolean", "classification": "secret"}}
        ),
    ],
)
def test_malformed_catalogues_are_refused(doc):
    with pytest.raises(CatalogueError):
        catalogues.parse_catalogue(json.dumps(doc).encode())


def test_oversized_and_duplicate_catalogues_are_refused(tmp_path):
    with pytest.raises(CatalogueError):
        catalogues.parse_catalogue(b" " * (catalogues.MAX_CATALOGUE_BYTES + 1))
    keen = catalogues.keen().content
    (tmp_path / "copy.json").write_bytes(keen)
    with pytest.raises(CatalogueError):
        catalogues._load.__wrapped__(str(tmp_path))


@pytest.mark.parametrize(
    "kind, value, ok",
    [
        ("boolean", True, True),
        ("boolean", 1, False),
        ("integer", 2**53, False),
        ("integer", False, False),
        ("string", "x" * 1025, False),
        ("timestamp", "2026-10-01T12:00:00Z", True),
        ("timestamp", "2026-10-01T12:00:00.123+10:00", True),
        ("timestamp", 5, False),
        ("timestamp", "2026-10-01T12:00:00Z" + " " * 50, False),
        ("timestamp", "yesterday", False),
        ("timestamp", "2026-02-30T12:00:00Z", False),
        ("string_list", ["a", "b"], True),
        ("string_list", ["a"] * 65, False),
        ("string_list", ["a" * 257], False),
        ("string_list", [1], False),
        ("float", 1.5, False),
    ],
)
def test_typed_values(kind, value, ok):
    assert catalogues.typed_ok(kind, value) is ok


def test_field_checks_name_the_failure():
    specs = {"enabled": FieldSpec("boolean")}
    check_fields = catalogues.check_fields
    assert check_fields([], specs, "", frozenset()) == SCHEMA
    many = {f"f{i}": True for i in range(catalogues.MAX_FIELDS + 1)}
    assert check_fields(many, None, "", frozenset()) == SCHEMA
    assert (
        check_fields({"password": "x"}, specs, "", frozenset({"password"}))
        == "UEU_PROHIBITED_FIELD_DETECTED"
    )
    assert check_fields({"extra": "x"}, specs, "reject", frozenset()) == SCHEMA
    assert check_fields({"extra": "x"}, specs, "scalar_string", frozenset()) is None
    assert check_fields({"extra": 1}, specs, "scalar_string", frozenset()) == SCHEMA


# --- canonical JSON --------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        b"\xff\xfe",
        "[" * 100000 + "]" * 100000,
        '{"a": 1}',
        "1.5",
        "NaN",
        "not json",
        '{"a":1,"a":2}',
    ],
)
def test_strict_parsing_refuses(data):
    with pytest.raises(jcs.CanonicalError):
        jcs.parse_canonical(data) if isinstance(data, str) else jcs.parse(data)


@pytest.mark.parametrize(
    "value",
    [
        2**53,
        1.5,
        {1: "a"},
        {"\ud800": 1},
        "\ud800",
        json.loads("[" * 34 + "]" * 34),
        object(),
    ],
)
def test_values_outside_the_profile_have_no_canonical_form(value):
    with pytest.raises(jcs.CanonicalError):
        jcs.canonical(value)


def test_canonical_form_of_profile_values():
    assert (
        jcs.canonical((True, False, None, -3, "é"))
        == '[true,false,null,-3,"é"]'.encode()
    )
    assert jcs.parse(b'{"b":1,"a":[2]}') == {"b": 1, "a": [2]}
    with pytest.raises(jcs.CanonicalError):
        jcs.parse_canonical("\ud800")
