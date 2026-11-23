"""JSON from outside Keen is decoded whole only when it holds few enough values.

A decoded document costs many times its text, most for many small values such
as empty arrays, and redacting or masking it copies it again. A webhook
delivery, an artifact being redacted at intake and a masked download each
decode a whole document in a shared process, bounded before this only in bytes.
Each now decodes at most MAX_DECODED_VALUES values and handles a larger text as
text, as it handles text that is not JSON. These tests take the costliest shape
at the largest size each path accepts and measure what it holds.
"""

import json
import tracemalloc

import pytest
from app.ingest import webhooks
from app.ingest.common import normalise_ingested_fields
from app.services.artifacts import MASKING_LIMIT_BYTES
from mosp.security.json_shape import MAX_DECODED_VALUES, decodes_within, json_depth
from mosp.security.redaction import mask_event_data_bytes, redact_bytes

WEBHOOK_BYTES = webhooks._MAX_WEBHOOK_PAYLOAD_BYTES


def empty_arrays(size: int) -> bytes:
    """The costliest JSON shape to decode, as close to ``size`` bytes as fits."""
    return ("[" + ",".join(["[]"] * ((size - 2) // 3)) + "]").encode()


def peak(work):
    tracemalloc.start()
    try:
        result = work()
        return result, tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


@pytest.mark.parametrize(
    "text",
    [
        "1",
        "[]",
        "{}",
        '{"a": [1, 2, {"b": null}], "c": "d"}',
        '[[], {}, [[]], {"x": {}}]',
        '{"k": "a, b, [c], {d}"}',
    ],
)
def test_the_count_never_admits_more_values_than_json_depth_counts(text):
    values = 1
    while json_depth(json.loads(text), max_values=values) is None:
        values += 1
    # A text holding more values than the limit is never admitted.
    assert not decodes_within(text, values - 1)
    assert not decodes_within(text.encode(), values - 1)


def test_the_largest_admitted_document_decodes_within_a_bounded_size():
    """Empty arrays cost the most per byte; the most of them the bound admits
    decode to a few megabytes."""
    count = (MAX_DECODED_VALUES - 2) // 2
    largest = empty_arrays(3 * count + 2)
    assert decodes_within(largest)
    assert not decodes_within(largest[:-1] + b",[]]")
    decoded, used = peak(lambda: json.loads(largest))
    assert len(decoded) == count
    assert used < 16 * 1024 * 1024


@pytest.mark.parametrize("transform", [mask_event_data_bytes, redact_bytes])
def test_an_artifact_at_the_masking_limit_costs_about_its_own_size(transform):
    body = empty_arrays(MASKING_LIMIT_BYTES)
    (out, status), used = peak(lambda: transform(body, "application/json"))
    assert status == "clean" and out == body
    # Decoded, this shape costs about fifty times its size.
    assert used < 3 * len(body)


def test_maskable_values_past_the_bound_are_still_masked_as_text():
    body = json.dumps(["user@example.com from 10.1.2.3"] * MAX_DECODED_VALUES).encode()
    assert not decodes_within(body)
    masked, status = mask_event_data_bytes(body, "application/json")
    assert status == "redacted"
    assert b"10.1.2.3" not in masked and b"user@example.com" not in masked
    secret = json.dumps([{"password": "hunter2"}] * MAX_DECODED_VALUES).encode()
    redacted, status = redact_bytes(secret, "application/json")
    assert status == "redacted" and b"hunter2" not in redacted


def test_within_the_bound_documents_are_still_redacted_as_structures():
    body = json.dumps({"password": "hunter2", "note": "x"}).encode()
    redacted, status = redact_bytes(body, "application/json")
    assert status == "redacted"
    assert json.loads(redacted) == {"note": "x", "password": "REDACTED"}


def test_a_webhook_delivery_at_its_size_limit_is_kept_as_text(monkeypatch):
    monkeypatch.setattr(webhooks, "load_webhook_policies", lambda path: {})
    stored = {}

    def store(db, **fields):
        stored.update(fields)
        # Normalisation is the costly part of storing a delivery.
        normalise_ingested_fields(
            **{
                key: fields[key]
                for key in (
                    "system",
                    "actor",
                    "action",
                    "outcome",
                    "summary",
                    "external_id",
                    "raw_pointer",
                    "normalized_payload",
                    "artifact_bytes",
                    "artifact_content_type",
                )
            }
        )
        return {"event_id": "ev-1"}

    monkeypatch.setattr(webhooks, "store_event_with_artifact", store)
    body = empty_arrays(WEBHOOK_BYTES)
    _, used = peak(
        lambda: webhooks._store_webhook_event(
            None, provider="monitor", event_type="alert", body=body, headers={}
        )
    )
    assert stored["normalized_payload"] == {"raw": body.decode()}
    assert used < 8 * len(body)
