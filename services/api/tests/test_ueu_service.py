"""UEU receipt checks that need no database: batch manifests, chain placement,
summaries and severities, record checks, custody redaction and display."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from app.agents.otlp import UeuRecord, UeuResource
from app.agents.ueu import custody, jcs
from app.agents.ueu.catalogue import keen
from app.services import ueu_evidence as ueu
from app.services.ueu_evidence import Rejected

from tests.test_ueu_profile import EVIDENCE_ID, HASH, check, observation

PRODUCER = "0192a6c0-0000-7000-8000-0000000000aa"
KEY_ID = PRODUCER + ":0123456789abcdef"
SIGNATURE = "A" * 86


def manifest(**change) -> str:
    doc = {
        "context": ueu.BATCH_CONTEXT,
        "evidence": [],
        "issued_at": "2026-10-01T12:00:00.000Z",
        "key_id": KEY_ID,
        "producer": PRODUCER,
        **change,
    }
    return jcs.canonical(doc).decode()


def refused(call) -> str:
    with pytest.raises(Rejected) as caught:
        call()
    return caught.value.detail


def test_manifests_must_be_canonical_and_name_the_producer():
    assert "not canonical" in refused(lambda: ueu._manifest("{ }", PRODUCER, []))
    for text in ("[]", manifest(producer="someone-else"), manifest(key_id=5)):
        assert "malformed" in refused(
            lambda text=text: ueu._manifest(text, PRODUCER, [])
        )
    assert ueu._manifest(manifest(), PRODUCER, [])["key_id"] == KEY_ID


def test_signatures_must_be_complete_and_name_the_manifests_key():
    def batch(**attributes):
        resource = UeuResource({"ueu.producer.instance_id": PRODUCER, **attributes})
        return lambda: ueu.verify_batch(None, resource, [])

    assert "incomplete" in refused(batch(**{"ueu.batch.manifest": manifest()}))
    assert "incomplete" in refused(
        batch(
            **{
                "ueu.batch.manifest": manifest(),
                "ueu.batch.signature": "short",
                "ueu.batch.key_id": KEY_ID,
            }
        )
    )
    assert "differ" in refused(
        batch(
            **{
                "ueu.batch.manifest": manifest(),
                "ueu.batch.signature": SIGNATURE,
                "ueu.batch.key_id": PRODUCER + ":fedcba9876543210",
            }
        )
    )


def test_an_earlier_sequence_is_out_of_order_and_leaves_the_chain():
    agent = SimpleNamespace(ueu_last_sequence=5, ueu_last_hash=HASH)
    env = SimpleNamespace(sequence=3, previous_hash=None, evidence_hash="x")
    assert ueu.advance(agent, env) == "out_of_order"
    assert agent.ueu_last_sequence == 5


def test_summaries_and_severities_read_the_envelope():
    doc = observation(enabled=True, ports=["22", "443"])
    assert ueu._value(doc, "facts.enabled") == "true"
    assert ueu._value(doc, "facts.ports") == "22, 443"
    assert ueu._value(doc, "facts.missing") == "unknown"
    alert = {
        "event_type": "security.alert.generated",
        "event": {"outcome": "info"},
        "attributes": {"level": 12},
    }
    assert ueu.severity(alert) == 8
    alert["attributes"]["level"] = True
    assert ueu.severity(alert) == 4


def test_records_must_match_their_envelopes_and_the_agent():
    body = jcs.canonical(observation()).decode()
    agent = SimpleNamespace(credential_type="token", id="agent-1")

    def resource(*records, producer="agent-1"):
        attributes = {"ueu.producer.instance_id": producer} if producer else {}
        return UeuResource(attributes, list(records))

    good = UeuRecord(body, EVIDENCE_ID, "endpoint.firewall.observed")
    assert [e.evidence_id for e in ueu._validated(resource(good), agent)] == [
        EVIDENCE_ID
    ]
    assert "missing" in refused(lambda: ueu._validated(resource(producer=None), agent))
    keyed = SimpleNamespace(credential_type="key", id="agent-2")
    assert "another agent" in refused(lambda: ueu._validated(resource(), keyed))
    renamed = UeuRecord(body, EVIDENCE_ID, "endpoint.antivirus.observed")
    assert "contradict" in refused(lambda: ueu._validated(resource(renamed), agent))
    assert "repeated" in refused(lambda: ueu._validated(resource(good, good), agent))


def test_custody_redacts_sensitive_names_and_keeps_withheld_payloads_absent():
    doc = observation()
    del doc["observation"]
    doc["attributes"] = {"api_token": "abc", "password": ["one", "two"]}
    redacted, changes = custody.redact(doc, None)
    assert redacted["attributes"] == {
        "api_token": custody.REDACTED,
        "password": custody.REDACTED,
    }
    assert {c["path"] for c in changes} == {
        "attributes.api_token",
        "attributes.password",
    }
    plain = observation()
    custody.withhold_payload(plain, changes, "above_evidence_mode_ceiling")
    assert plain["evidence"] == {"payload_included": False}


def test_display_redacts_with_the_catalogue_when_it_is_known():
    assert ueu.evidence_record(SimpleNamespace(get=lambda *a: None), "x") is None
    plain = {"token": "secret-value", "note": "ok"}
    assert ueu.display_payload(plain)["token"] != "secret-value"
    for ref in ({"id": "acme", "version": "1"}, "keen@2026.10"):
        doc = observation()
        doc["catalogue"] = ref
        doc["observation"]["facts"]["session_secret"] = "abc"
        shown = ueu.display_payload({"ueu": doc, "password": "x"})
        assert shown["ueu"]["observation"]["facts"]["session_secret"] != "abc"
        assert shown["password"] != "x"
    known = check(observation()).spec
    assert known is keen().event_types["endpoint.firewall.observed"]
    assert json.dumps(ueu.display_payload({"ueu": observation()}))
