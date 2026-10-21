"""Webhook secrets, replay windows and payload limits without external services."""

from unittest.mock import Mock

import fakeredis
import pytest
from app.core.config import settings
from app.ingest import webhooks


@pytest.fixture
def policy(monkeypatch):
    monkeypatch.setattr(
        webhooks,
        "load_webhook_policies",
        lambda path: {
            "providers": {
                "monitor": {
                    "secret_header": "X-Monitor-Secret",
                    "secret_env": "KEEN_TEST_WEBHOOK_SECRET",
                }
            }
        },
    )
    monkeypatch.setenv("KEEN_TEST_WEBHOOK_SECRET", "synthetic-secret")


@pytest.mark.parametrize(
    "headers,expected",
    [
        ({}, False),
        ({"X-Monitor-Secret": "wrong"}, False),
        ({"x-monitor-secret": "synthetic-secret"}, True),
        ({"X-MONITOR-SECRET": "synthetic-secret"}, True),
        ({"other": "synthetic-secret"}, False),
        ({"X-Monitor-Secret": ""}, False),
    ],
)
def test_secret_header_validation(policy, headers, expected):
    assert webhooks.verify_secret("monitor", headers) is expected


def test_unconfigured_and_missing_secret_fail_closed(policy, monkeypatch):
    assert not webhooks.verify_secret("unknown", {})
    monkeypatch.delenv("KEEN_TEST_WEBHOOK_SECRET")
    assert not webhooks.verify_secret(
        "monitor", {"X-Monitor-Secret": "synthetic-secret"}
    )
    monkeypatch.setenv("KEEN_WEBHOOKS_REQUIRE_SECRET", "false")
    assert not webhooks.verify_secret("unknown", {})
    assert not webhooks.verify_secret("monitor", {})


@pytest.mark.parametrize(
    "age,rejected", [(0, False), (899, False), (900, False), (901, True), (-1, True)]
)
def test_replay_timestamp_boundaries(monkeypatch, age, rejected):
    redis = fakeredis.FakeRedis()
    monkeypatch.setattr(webhooks, "get_valkey", lambda: redis)
    monkeypatch.setattr("time.time", lambda: 2000)
    assert (
        webhooks._check_replay_protection(
            "monitor", "alert", b"body", {"x-webhook-timestamp": str(2000 - age)}
        )
        is rejected
    )


def test_replay_deduplication_is_provider_and_type_scoped(monkeypatch):
    redis = fakeredis.FakeRedis()
    monkeypatch.setattr(webhooks, "get_valkey", lambda: redis)
    assert not webhooks._check_replay_protection("a", "alert", b"body", {})
    assert webhooks._check_replay_protection("a", "alert", b"body", {})
    assert not webhooks._check_replay_protection("b", "alert", b"body", {})
    assert not webhooks._check_replay_protection("a", "other", b"body", {})
    assert all(0 < redis.ttl(key) <= 960 for key in redis.keys())


def test_oversized_or_replayed_payload_never_reaches_storage(monkeypatch):
    store = Mock()
    monkeypatch.setattr(webhooks, "store_event_with_artifact", store)
    with pytest.raises(ValueError, match="too large"):
        webhooks.ingest_webhook(None, "a", "alert", b"x" * (10 * 1024 * 1024 + 1), {})
    monkeypatch.setattr(webhooks, "_check_replay_protection", lambda *args: True)
    with pytest.raises(ValueError, match="replay"):
        webhooks.ingest_webhook(None, "a", "alert", b"{}", {})
    store.assert_not_called()


def test_blank_secrets_never_match_and_non_ascii_secrets_compare_safely():
    # Fail closed on either side missing; a non-ASCII shared secret compares
    # as bytes, so the constant-time comparison completes.
    assert webhooks._secret_matches("", "expected") is False
    assert webhooks._secret_matches("got", "") is False
    assert webhooks._secret_matches(None, None) is False
    assert webhooks._secret_matches("pässword", "pässword") is True
    assert webhooks._secret_matches("pässword", "password") is False


def test_a_malformed_timestamp_header_is_ignored_for_replay_checks(monkeypatch):
    # A sender that puts no number in the timestamp header still gets the
    # fingerprint-based replay window: the first delivery passes, a repeat fails.
    redis = fakeredis.FakeRedis()
    monkeypatch.setattr(webhooks, "get_valkey", lambda: redis)
    headers = {"x-timestamp": "yesterday"}
    assert webhooks._check_replay_protection("a", "alert", b"body", headers) is False
    assert webhooks._check_replay_protection("a", "alert", b"body", headers) is True


def test_a_failed_store_releases_the_replay_claim_even_without_valkey(monkeypatch):
    redis = fakeredis.FakeRedis()
    monkeypatch.setattr(webhooks, "get_valkey", lambda: redis)
    monkeypatch.setattr(
        webhooks, "load_webhook_policies", lambda path: {"providers": {}}
    )

    def failing_store(db, **kwargs):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(webhooks, "store_event_with_artifact", failing_store)
    with pytest.raises(RuntimeError, match="storage unavailable"):
        webhooks.ingest_webhook(None, "a", "alert", b"{}", {})
    # The claim is gone, so the sender's retry is accepted by the replay check.
    assert redis.keys() == []
    assert webhooks._check_replay_protection("a", "alert", b"{}", {}) is False

    def broken_valkey():
        raise ConnectionError("valkey down")

    monkeypatch.setattr(webhooks, "get_valkey", broken_valkey)
    # Releasing without a store is best effort and raises nothing.
    webhooks._release_replay_claim("a", "alert", b"{}")


def test_stored_webhooks_summarise_hints_and_keep_raw_bodies(monkeypatch):
    # Summary hints flatten line breaks so a hostile payload cannot forge log
    # lines; a body that is no JSON is kept raw; the secret header is never
    # recorded with the event.
    monkeypatch.setattr(
        webhooks,
        "load_webhook_policies",
        lambda path: {
            "providers": {
                "monitor": {
                    "secret_header": "X-Monitor-Secret",
                    "summary_hints": ["alert", "host", "missing"],
                }
            }
        },
    )
    stored = []

    def store(db, **kwargs):
        stored.append(kwargs)
        return {"event_id": "ev-1", "deduped": False}

    monkeypatch.setattr(webhooks, "store_event_with_artifact", store)
    headers = {
        "X-Monitor-Secret": "synthetic-secret",
        "Content-Type": "application/json",
        "X-Forwarded-For": "203.0.113.9",
    }
    body = b'{"alert": "disk\\nfull\\rnow", "host": "", "other": 1}'
    result = webhooks._store_webhook_event(
        None, provider="monitor", event_type="alert", body=body, headers=headers
    )
    assert result == {"event_id": "ev-1", "deduped": False}
    (event,) = stored
    assert event["summary"] == "webhook:monitor/alert alert=disk full now host="
    assert event["source"] == "webhook:monitor"
    assert event["raw_pointer"]["webhook"]["headers"] == {
        "Content-Type": "application/json"
    }
    assert "synthetic-secret" not in str(event["raw_pointer"])
    assert event["normalized_payload"]["other"] == 1

    stored.clear()
    webhooks._store_webhook_event(
        None, provider="monitor", event_type="alert", body=b"not json \xff", headers={}
    )
    (raw,) = stored
    assert raw["summary"] == "webhook:monitor/alert"
    assert raw["normalized_payload"] == {"raw": "not json �"}
    assert raw["artifact_bytes"] == b"not json \xff"

    stored.clear()
    webhooks._store_webhook_event(
        None,
        provider="monitor",
        event_type="alert",
        body=b'["list", "body"]',
        headers={},
    )
    (listed,) = stored
    assert listed["summary"] == "webhook:monitor/alert"
    assert listed["normalized_payload"] == {"payload": ["list", "body"]}
