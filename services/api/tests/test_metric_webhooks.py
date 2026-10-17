"""Effectiveness metric submissions through the assembled application.

The webhook authenticates with the provider secret from the webhook policy,
so each test uses a real policy file and secret rather than a stub.
"""

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from app.core.config import settings
from app.db.models import (
    EntityChangelog,
    Event,
    IsmsEffectivenessMeasure,
    IsmsEffectivenessMetricEntry,
    SourceIngestionState,
)

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)

SECRET = "synthetic-metrics-secret"
HEADER = "X-Keen-Metrics-Secret"


@pytest.fixture
def metrics(keen, monkeypatch, tmp_path):  # noqa: F811 - fixture imported above
    policy = tmp_path / "webhooks.yml"
    policy.write_text(
        "providers:\n"
        "  isms_metrics:\n"
        f"    secret_header: {HEADER}\n"
        "    secret_env: KEEN_TEST_METRICS_SECRET\n"
    )
    monkeypatch.setattr(settings, "webhooks_path", str(policy))
    monkeypatch.setattr(settings, "webhooks_enabled", True)
    monkeypatch.setenv("KEEN_TEST_METRICS_SECRET", SECRET)
    db = keen.db
    measure = IsmsEffectivenessMeasure(
        framework_slug=settings.default_framework_slug,
        metric_key="CPU-Usage",
        target_unit="percent",
    )
    elsewhere = IsmsEffectivenessMeasure(
        framework_slug="another-framework", metric_key="disk-usage"
    )
    evidence = Event(
        source="cloud-logs",
        summary="Monthly report",
        timestamp=datetime(2026, 5, 31),
        external_id=uuid.uuid4().hex,
        raw_pointer={},
        normalized_payload={},
    )
    db.add_all([measure, elsewhere, evidence])
    db.commit()
    client = keen.client()

    def post(key="cpu-usage", payload=None, *, secret=SECRET, content=None):
        headers = {} if secret is None else {HEADER: secret}
        if content is not None:
            return client.post(
                f"/v1/webhooks/isms/effectiveness-metrics/{key}",
                content=content,
                headers=headers,
            )
        return client.post(
            f"/v1/webhooks/isms/effectiveness-metrics/{key}",
            json={"value": 42} if payload is None else payload,
            headers=headers,
        )

    def entries():
        db.expire_all()
        return db.query(IsmsEffectivenessMetricEntry).all()

    return SimpleNamespace(
        keen=keen,
        db=db,
        measure=measure,
        elsewhere=elsewhere,
        evidence=evidence,
        post=post,
        entries=entries,
    )


def test_a_metric_is_recorded_against_its_measure(metrics):
    response = metrics.post(
        "cpu-usage",
        {
            "value": "42.5",
            "period_start": "2026-05-01",
            "period_end": "2026-05-31T23:59:59Z",
            "recorded_at": "2026-06-01T02:03:04Z",
            "source_type": "Cloud_Logs",
            "source_title": "May report",
            "source_url": "https://grafana.example/d/abc",
            "source_reference": "RPT-5",
            "event_id": str(metrics.evidence.id),
            "notes": "Within target",
        },
    )

    assert response.status_code == 200, response.text
    out = response.json()["metric_entry"]
    (row,) = metrics.entries()
    assert out["id"] == str(row.id) and out["measure_id"] == str(metrics.measure.id)
    assert (row.metric_value, row.metric_unit) == (42.5, "percent")
    # The source name is matched to the collected source regardless of case
    # or separator.
    assert row.source_type == "cloud-logs"
    assert (row.period_start.isoformat(), row.period_end.isoformat()) == (
        "2026-05-01",
        "2026-05-31",
    )
    assert row.recorded_at == datetime(2026, 6, 1, 2, 3, 4)
    assert (row.source_title, row.source_reference, row.notes) == (
        "May report",
        "RPT-5",
        "Within target",
    )
    assert row.source_url == "https://grafana.example/d/abc"
    assert row.source_event_id == metrics.evidence.id
    (history,) = metrics.db.query(EntityChangelog).filter_by(
        entity_type="isms_effectiveness_metric", entity_id=row.id
    )
    assert history.action == "created"


def test_measures_are_found_by_key_or_id_within_their_framework(metrics):
    by_id = metrics.post(str(metrics.measure.id))
    assert by_id.status_code == 200, by_id.text
    assert metrics.post("CPU-USAGE").status_code == 200

    for key in ("disk-usage", str(metrics.elsewhere.id), "unknown-metric"):
        missing = metrics.post(key)
        assert missing.status_code == 404, key
        assert missing.json()["detail"] == "Effectiveness measure not found"
    # The other framework's measure is reachable only by naming that framework.
    other = metrics.post("disk-usage", {"value": 1, "framework": "another-framework"})
    assert other.status_code == 200, other.text
    assert len(metrics.entries()) == 3


@pytest.mark.parametrize(
    "secret",
    [None, "", "wrong-secret", SECRET + " "],
    ids=["none", "blank", "wrong", "padded"],
)
def test_metrics_need_the_provider_secret(metrics, secret):
    refused = metrics.post(secret=secret)

    assert refused.status_code == 403
    assert refused.json()["detail"] == "Invalid webhook secret"
    assert metrics.entries() == []


def test_the_webhook_fails_closed_without_a_configured_secret(metrics, monkeypatch):
    monkeypatch.delenv("KEEN_TEST_METRICS_SECRET")

    assert metrics.post().status_code == 403
    assert metrics.entries() == []


def test_metrics_wait_while_webhooks_are_disabled_or_the_feature_is_off(
    metrics, monkeypatch
):
    monkeypatch.setattr(settings, "webhooks_enabled", False)
    disabled = metrics.post()
    assert disabled.status_code == 503
    assert disabled.json()["detail"] == "Webhook ingestion is disabled"
    # A disabled deployment answers before any secret is checked.
    assert metrics.post(secret=None).json() == disabled.json()

    monkeypatch.setattr(settings, "webhooks_enabled", True)
    admin = metrics.keen.client(metrics.keen.user(role="admin"))
    switched = admin.put(
        "/v1/admin/features",
        json={"version": 0, "changes": {"effectiveness": False}},
        headers=unsafe(admin),
    )
    assert switched.status_code == 200, switched.text
    off = metrics.post()
    assert off.status_code == 404
    assert off.json()["detail"] == "This feature is disabled by an administrator."
    assert metrics.entries() == []


def test_a_paused_source_is_revealed_only_after_the_secret_is_checked(metrics):
    metrics.db.add(SourceIngestionState(source="webhooks", paused=True))
    metrics.db.commit()

    wrong = metrics.post(secret="wrong-secret")
    paused = metrics.post()

    assert wrong.status_code == 403
    assert wrong.json()["detail"] == "Invalid webhook secret"
    assert paused.status_code == 503
    assert paused.json()["detail"] == (
        "Source ingestion is paused by an administrator; retry later."
    )
    assert paused.headers["retry-after"] == "60"
    assert metrics.entries() == []


@pytest.mark.parametrize(
    "payload,detail",
    [
        ({"value": "NaN"}, "metric_value must be a finite number"),
        ({"value": "inf"}, "metric_value must be a finite number"),
        ({"value": "lots"}, "metric_value must be a finite number"),
        (
            {"notes": "no measurement"},
            "metric_value/value or qualitative_value/text is required",
        ),
        (
            {"value": 1, "period_start": "2026-05-31", "period_end": "2026-05-01"},
            "period_end must be after period_start",
        ),
        ({"value": 1, "period_start": "May"}, "Invalid date value: May"),
        ({"value": 1, "recorded_at": "yesterday"}, "Invalid datetime value: yesterday"),
        ({"value": 1, "event_id": "not-a-uuid"}, "Invalid UUID value: not-a-uuid"),
        ({"value": 1, "event_id": str(uuid.uuid4())}, "Unknown source_event_id"),
        (
            {"value": 1, "source_type": "made-up-source"},
            "Invalid source_type; choose an existing KEEN source or other",
        ),
        (
            {"value": 1, "source_url": "javascript:alert(1)"},
            "Use an HTTP or HTTPS source URL",
        ),
        (
            {"value": 1, "source_url": "https://user:password@grafana.example/d"},
            "Use an HTTP or HTTPS source URL",
        ),
        ({"value": 1, "notes": "x" * 12001}, "Payload text field is too long"),
        ({"value": 1, "source_title": "x" * 257}, "Payload text field is too long"),
        (["not", "an", "object"], "Expected JSON object payload"),
    ],
    ids=[
        "nan",
        "infinity",
        "words",
        "no-measurement",
        "period-reversed",
        "bad-date",
        "bad-recorded-at",
        "bad-event-id",
        "unknown-event",
        "unknown-source",
        "script-url",
        "credentials-in-url",
        "long-notes",
        "long-title",
        "not-an-object",
    ],
)
def test_invalid_submissions_record_nothing(metrics, payload, detail):
    refused = metrics.post("cpu-usage", payload)

    assert refused.status_code == 400, refused.text
    assert refused.json()["detail"] == detail
    assert metrics.entries() == []


def test_a_body_that_is_not_json_records_nothing(metrics):
    refused = metrics.post(content=b"value=42")

    assert refused.status_code == 400
    assert refused.json()["detail"] == "Expected JSON payload"
    assert metrics.entries() == []


def test_qualitative_metrics_and_other_sources_are_accepted(metrics):
    response = metrics.post("cpu-usage", {"status": "Green", "source_type": "other"})

    assert response.status_code == 200, response.text
    (row,) = metrics.entries()
    assert (row.metric_value, row.qualitative_value, row.source_type) == (
        None,
        "Green",
        "other",
    )
    assert row.raw_payload == {"status": "Green", "source_type": "other"}
