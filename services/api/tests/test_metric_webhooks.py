"""Effectiveness metric submissions through the assembled application.

A reporting job signs each submission with its measure's own secret: an
HMAC-SHA256 over the timestamp, a full stop and the exact body. Each test signs
its requests as a sender would, with secrets issued and stored as Keen stores
them, rather than stubbing the check.
"""

import json
import os
import time
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from app.core.config import settings
from app.db.models import (
    EntityChangelog,
    Event,
    IsmsEffectivenessMeasure,
    IsmsEffectivenessMeasureSecret,
    IsmsEffectivenessMetricEntry,
    Quiz,
    SourceIngestionState,
)
from app.integrations.transport import encrypt
from app.security import signed_webhooks
from cryptography.fernet import Fernet

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)

SECRET = "keen_ms_synthetic-cpu-secret"
OTHER_SECRET = "keen_ms_synthetic-disk-secret"
INVALID = "Invalid webhook signature"


def signed(secret, body, timestamp=None):
    timestamp = str(int(time.time())) if timestamp is None else timestamp
    return {
        "Content-Type": "application/json",
        "X-Keen-Timestamp": timestamp,
        "X-Keen-Signature": signed_webhooks.sign(secret, timestamp, body),
    }


@pytest.fixture
def metrics(keen, monkeypatch):  # noqa: F811 - fixture imported above
    monkeypatch.setattr(settings, "webhooks_enabled", True)
    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", Fernet.generate_key().decode())
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
    db.flush()
    db.add_all(
        [
            IsmsEffectivenessMeasureSecret(
                measure_id=measure.id, secret_encrypted=encrypt(SECRET)
            ),
            IsmsEffectivenessMeasureSecret(
                measure_id=elsewhere.id, secret_encrypted=encrypt(OTHER_SECRET)
            ),
        ]
    )
    db.commit()
    client = keen.client()

    def post(key="cpu-usage", payload=None, *, secret=SECRET, content=None, **kw):
        body = (
            content
            if content is not None
            else json.dumps({"value": 42} if payload is None else payload).encode()
        )
        headers = kw.get("headers") or signed(secret, body, kw.get("timestamp"))
        return client.post(
            f"/v1/webhooks/isms/effectiveness-metrics/{key}",
            content=body,
            headers=headers,
        )

    def entries():
        db.expire_all()
        return db.query(IsmsEffectivenessMetricEntry).all()

    return SimpleNamespace(
        keen=keen,
        db=db,
        client=client,
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
    by_id = metrics.post(str(metrics.measure.id), {"value": 1})
    assert by_id.status_code == 200, by_id.text
    assert metrics.post("CPU-USAGE", {"value": 2}).status_code == 200

    # The other framework's measure verifies with its own secret, and is
    # reached only by naming that framework.
    for key in ("disk-usage", str(metrics.elsewhere.id)):
        missing = metrics.post(key, secret=OTHER_SECRET)
        assert missing.status_code == 404, key
        assert missing.json()["detail"] == "Effectiveness measure not found"
    other = metrics.post(
        "disk-usage",
        {"value": 1, "framework": "another-framework"},
        secret=OTHER_SECRET,
    )
    assert other.status_code == 200, other.text
    assert len(metrics.entries()) == 3


def _now():
    return int(time.time())


@pytest.mark.parametrize(
    "headers,detail",
    [
        ({}, "Sign the submission: send one X-Keen-Timestamp header and one"),
        (
            {"X-Keen-Timestamp": "TS"},
            "Sign the submission: send one X-Keen-Timestamp header and one",
        ),
        (
            {"X-Keen-Timestamp": "1.5", "X-Keen-Signature": "SIG"},
            "X-Keen-Timestamp must be the current Unix time in whole seconds",
        ),
        (
            {"X-Keen-Timestamp": "STALE", "X-Keen-Signature": "SIG"},
            "X-Keen-Timestamp is more than five minutes from Keen's clock",
        ),
        (
            {"X-Keen-Timestamp": "FUTURE", "X-Keen-Signature": "SIG"},
            "X-Keen-Timestamp is more than five minutes from Keen's clock",
        ),
        (
            {"X-Keen-Timestamp": "TS", "X-Keen-Signature": "sha256=abc"},
            "X-Keen-Signature must be v1= followed by the hexadecimal HMAC-SHA256",
        ),
        ({"X-Keen-Timestamp": "TS", "X-Keen-Signature": "WRONG"}, INVALID),
        ({"X-Keen-Timestamp": "TS", "X-Keen-Signature": "OTHER"}, INVALID),
        ({"X-Keen-Timestamp": "TS", "X-Keen-Signature": "TAMPERED"}, INVALID),
        ({"X-Keen-Timestamp": "TS", "X-Keen-Signature": "SHIFTED"}, INVALID),
    ],
    ids=[
        "unsigned",
        "no-signature",
        "fractional-time",
        "stale",
        "future",
        "other-scheme",
        "wrong-secret",
        "other-measures-secret",
        "tampered-body",
        "other-timestamp",
    ],
)
def test_unsigned_or_mis_signed_submissions_record_nothing(metrics, headers, detail):
    body = json.dumps({"value": 42}).encode()
    now = _now()
    stamp = {"TS": str(now), "STALE": str(now - 301), "FUTURE": str(now + 301)}
    signatures = {
        "SIG": signed_webhooks.sign(SECRET, str(now), body),
        "WRONG": signed_webhooks.sign("keen_ms_guess", str(now), body),
        "OTHER": signed_webhooks.sign(OTHER_SECRET, str(now), body),
        "TAMPERED": signed_webhooks.sign(SECRET, str(now), b'{"value": 1}'),
        "SHIFTED": signed_webhooks.sign(SECRET, str(now - 1), body),
    }
    sent = {
        name: stamp.get(value, signatures.get(value, value))
        for name, value in headers.items()
    }

    refused = metrics.post(content=body, headers=sent or {"Accept": "*/*"})

    assert refused.status_code == 403
    assert refused.json()["detail"].startswith(detail)
    assert metrics.entries() == []


def test_a_signature_older_than_the_window_is_refused_even_when_valid(metrics):
    stale = str(_now() - signed_webhooks.TOLERANCE_SECONDS - 5)

    refused = metrics.post(timestamp=stale)

    assert refused.status_code == 403
    assert "five minutes" in refused.json()["detail"]
    assert metrics.entries() == []


def test_duplicate_signature_headers_are_refused(metrics):
    body = json.dumps({"value": 42}).encode()
    headers = signed(SECRET, body)
    raw = [(k.encode(), v.encode()) for k, v in headers.items()]
    raw.append((b"X-Keen-Signature", headers["X-Keen-Signature"].encode()))

    refused = metrics.client.post(
        "/v1/webhooks/isms/effectiveness-metrics/cpu-usage", content=body, headers=raw
    )

    assert refused.status_code == 403
    assert refused.json()["detail"].startswith("Sign the submission")
    assert metrics.entries() == []


def test_unknown_unsecured_and_unreadable_measures_are_refused_alike(
    metrics, monkeypatch
):
    bare = IsmsEffectivenessMeasure(
        framework_slug=settings.default_framework_slug, metric_key="no-secret"
    )
    metrics.db.add(bare)
    metrics.db.commit()
    wrong = metrics.post(secret="keen_ms_guess")

    unknown = metrics.post("unknown-metric")
    unknown_id = metrics.post(str(uuid.uuid4()))
    unsecured = metrics.post("no-secret")
    blank = metrics.post("%20")
    # Secrets saved under another integration key cannot be read.
    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", Fernet.generate_key().decode())
    unreadable = metrics.post()

    for response in (unknown, unknown_id, unsecured, blank, unreadable):
        assert (response.status_code, response.json()) == (
            wrong.status_code,
            wrong.json(),
        )
    assert wrong.json() == {"detail": INVALID}
    assert metrics.entries() == []


def test_a_replayed_submission_is_recorded_once(metrics):
    body = json.dumps({"value": 42}).encode()
    headers = signed(SECRET, body)

    first = metrics.post(content=body, headers=headers)
    replay = metrics.post(content=body, headers=headers)

    assert first.status_code == 200, first.text
    assert replay.status_code == 409
    assert replay.json()["detail"] == "This submission was already received"
    assert len(metrics.entries()) == 1
    # The same request sent to the measure's other address is the same
    # submission; signing the measurement again makes a new one.
    refused = metrics.post(str(metrics.measure.id), content=body, headers=headers)
    assert (refused.status_code, refused.json()) == (
        409,
        {"detail": "This submission was already received"},
    )
    resigned = metrics.post(content=body, timestamp=headers["X-Keen-Timestamp"] + "0")
    assert (resigned.status_code, resigned.json()) == (
        403,
        {
            "detail": "X-Keen-Timestamp is more than five minutes from Keen's clock; sign each submission as it is sent and keep the sender's clock synchronised"
        },
    )
    later = str(int(headers["X-Keen-Timestamp"]) + 1)
    assert metrics.post(content=body, timestamp=later).status_code == 200
    assert len(metrics.entries()) == 2


def test_a_submission_that_is_not_recorded_can_be_sent_again(metrics, monkeypatch):
    body = json.dumps({"value": 42}).encode()
    headers = signed(SECRET, body)
    admin = metrics.keen.client(metrics.keen.user(role="admin"))
    switched = admin.put(
        "/v1/admin/features",
        json={"version": 0, "changes": {"effectiveness": False}},
        headers=unsafe(admin),
    )
    assert switched.status_code == 200, switched.text

    off = metrics.post(content=body, headers=headers)
    assert (off.status_code, off.json()) == (
        404,
        {"detail": "This feature is disabled by an administrator."},
    )

    restored = admin.put(
        "/v1/admin/features",
        json={"version": 1, "changes": {"effectiveness": True}},
        headers=unsafe(admin),
    )
    assert restored.status_code == 200, restored.text
    retried = metrics.post(content=body, headers=headers)
    assert retried.status_code == 200, retried.text
    assert len(metrics.entries()) == 1


def test_submissions_wait_when_replays_cannot_be_checked(metrics, monkeypatch):
    def unavailable(*args):
        raise ConnectionError("store down")

    monkeypatch.setattr(signed_webhooks, "claim", unavailable)

    refused = metrics.post()

    assert (refused.status_code, refused.json()) == (
        503,
        {"detail": "Submissions cannot be checked for replay right now; retry later."},
    )
    assert refused.headers["retry-after"] == "60"
    assert metrics.entries() == []


def test_oversized_submissions_are_refused_before_verification(metrics):
    body = json.dumps({"value": 1, "notes": "x" * (256 * 1024)}).encode()

    # The body limit applies as the request arrives, so an unsigned sender
    # never has more than a submission's worth of body read for it.
    refused = metrics.post(content=body)

    assert refused.status_code == 413
    assert refused.json()["detail"] == "Request body too large"
    assert metrics.entries() == []


def test_metrics_wait_while_webhooks_are_disabled_or_the_feature_is_off(
    metrics, monkeypatch
):
    monkeypatch.setattr(settings, "webhooks_enabled", False)
    disabled = metrics.post()
    assert disabled.status_code == 503
    assert disabled.json()["detail"] == "Webhook ingestion is disabled"
    # A disabled deployment answers before any signature is checked.
    assert metrics.post(headers={"Accept": "*/*"}).json() == disabled.json()

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


def test_a_paused_source_is_revealed_only_after_the_signature_is_checked(metrics):
    metrics.db.add(SourceIngestionState(source="webhooks", paused=True))
    metrics.db.commit()
    body = json.dumps({"value": 42}).encode()
    headers = signed(SECRET, body)

    wrong = metrics.post(secret="keen_ms_guess")
    paused = metrics.post(content=body, headers=headers)

    assert wrong.status_code == 403
    assert wrong.json()["detail"] == INVALID
    assert paused.status_code == 503
    assert paused.json()["detail"] == (
        "Source ingestion is paused by an administrator; retry later."
    )
    assert paused.headers["retry-after"] == "60"
    assert metrics.entries() == []
    # Pausing claims nothing, so the same signed request goes in on resume.
    state = metrics.db.query(SourceIngestionState).filter_by(source="webhooks").one()
    state.paused = False
    metrics.db.commit()
    assert metrics.post(content=body, headers=headers).status_code == 200


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
            "Invalid source_type; choose an existing Keen source or other",
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
    body = json.dumps(payload).encode()
    headers = signed(SECRET, body)

    refused = metrics.post(content=body, headers=headers)

    assert refused.status_code == 400, refused.text
    assert refused.json()["detail"] == detail
    assert metrics.entries() == []
    # A refused submission claims nothing: resending it is refused the same way.
    assert metrics.post(content=body, headers=headers).json()["detail"] == detail


@pytest.mark.parametrize(
    "content", [b"value=42", b"\xff\xfe", b"[" * 5000], ids=["form", "bytes", "deep"]
)
def test_a_body_that_is_not_json_records_nothing(metrics, content):
    refused = metrics.post(content=content)

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


def test_a_submission_without_a_source_inherits_the_measure_expected_source(metrics):
    metrics.measure.source_type = "cloud-logs"
    metrics.db.commit()
    response = metrics.post("cpu-usage", {"value": 3})
    assert response.status_code == 200, response.text
    assert metrics.entries()[-1].source_type == "cloud-logs"
    # A named source still wins, and a blank one means the measure's.
    assert (
        metrics.post("cpu-usage", {"value": 4, "source_type": "other"}).status_code
        == 200
    )
    assert metrics.entries()[-1].source_type == "other"
    assert metrics.post("cpu-usage", {"value": 5, "source_type": ""}).status_code == 200
    assert metrics.entries()[-1].source_type == "cloud-logs"
    # An expected source that no longer collects evidence falls back to other.
    metrics.db.delete(metrics.evidence)
    metrics.db.commit()
    assert metrics.post("cpu-usage", {"value": 6}).status_code == 200
    assert metrics.entries()[-1].source_type == "other"


# --- Submission secrets ---------------------------------------------------------


def _secret_path(measure):
    return (
        f"/v1/isms/effectiveness-measures/{measure.id}/submission-secret"
        f"?framework={measure.framework_slug}"
    )


def test_a_manager_issues_rotates_and_revokes_a_measure_secret(metrics):
    manager = metrics.keen.client(metrics.keen.user("isms.manage"))
    path = _secret_path(metrics.measure)

    issued = manager.post(path, headers=unsafe(manager))
    assert issued.status_code == 200, issued.text
    first = issued.json()["secret"]
    assert first.startswith("keen_ms_") and len(first) >= 40
    assert issued.json()["measure"]["submission_secret_issued_at"]
    # The secret is shown once: reads, the stored row and history never carry it.
    read = manager.get(
        f"/v1/isms/effectiveness-measures/{metrics.measure.id}"
        f"?framework={metrics.measure.framework_slug}"
    )
    assert read.status_code == 200 and first not in read.text
    assert read.json()["submission_secret_issued_at"]
    metrics.db.expire_all()
    stored = metrics.db.get(IsmsEffectivenessMeasureSecret, metrics.measure.id)
    assert first not in stored.secret_encrypted
    history = metrics.db.query(EntityChangelog).filter_by(entity_id=metrics.measure.id)
    assert history.count() and all(
        first not in json.dumps([h.before_state, h.after_state, h.changes], default=str)
        for h in history
    )
    # The earlier secret stops signing at once; the issued one signs.
    assert metrics.post().json() == {"detail": INVALID}
    assert metrics.post(secret=first).status_code == 200

    rotated = manager.post(path, headers=unsafe(manager)).json()["secret"]
    assert rotated != first
    assert metrics.post(secret=first).json() == {"detail": INVALID}
    assert metrics.post(secret=rotated).status_code == 200

    revoked = manager.delete(path, headers=unsafe(manager))
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["submission_secret_issued_at"] is None
    assert metrics.post(secret=rotated).json() == {"detail": INVALID}
    # Revoking again changes nothing.
    assert manager.delete(path, headers=unsafe(manager)).status_code == 200
    assert len(metrics.entries()) == 2


def test_only_measure_managers_handle_submission_secrets(metrics):
    path = _secret_path(metrics.measure)
    reader = metrics.keen.client(metrics.keen.user("isms.read"))
    manager = metrics.keen.client(metrics.keen.user("isms.manage"))
    anonymous = metrics.keen.client()

    for method in ("post", "delete"):
        refused = getattr(anonymous, method)(path)
        assert (refused.status_code, refused.json()) == (
            401,
            {"detail": "Not authenticated"},
        )
        refused = getattr(reader, method)(path, headers=unsafe(reader))
        assert (refused.status_code, refused.json()) == (
            403,
            {"detail": "isms.manage permission required"},
        )
        # Cookie-authenticated changes still need the CSRF token.
        refused = getattr(manager, method)(path)
        assert (refused.status_code, refused.json()) == (
            403,
            {"detail": "CSRF token missing or invalid"},
        )
    assert metrics.post().status_code == 200


def test_quiz_and_unknown_measures_take_no_secret(metrics):
    manager = metrics.keen.client(metrics.keen.user("isms.manage"))
    metrics.db.add(
        Quiz(
            title="Phishing",
            framework_slug=metrics.measure.framework_slug,
            measure_id=metrics.measure.id,
        )
    )
    metrics.db.commit()

    quiz = manager.post(_secret_path(metrics.measure), headers=unsafe(manager))
    assert (quiz.status_code, quiz.json()) == (
        409,
        {"detail": "This measure belongs to a quiz; change it from the quiz"},
    )
    other_framework = manager.post(
        f"/v1/isms/effectiveness-measures/{metrics.elsewhere.id}/submission-secret",
        headers=unsafe(manager),
    )
    assert (other_framework.status_code, other_framework.json()) == (
        404,
        {"detail": "Effectiveness measure not found"},
    )
    missing = manager.post(
        f"/v1/isms/effectiveness-measures/{uuid.uuid4()}/submission-secret",
        headers=unsafe(manager),
    )
    assert (missing.status_code, missing.json()) == (
        404,
        {"detail": "Effectiveness measure not found"},
    )


def test_a_secret_needs_the_integration_key(metrics, monkeypatch):
    manager = metrics.keen.client(metrics.keen.user("isms.manage"))
    key = os.environ["KEEN_INTEGRATION_SECRET_KEY"]
    monkeypatch.delenv("KEEN_INTEGRATION_SECRET_KEY")

    refused = manager.post(_secret_path(metrics.measure), headers=unsafe(manager))

    assert refused.status_code == 503
    assert "KEEN_INTEGRATION_SECRET_KEY" in refused.json()["detail"]
    # Without the key no secret can be read, so submissions are refused too.
    assert metrics.post().json() == {"detail": INVALID}
    # The measure keeps its existing secret.
    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", key)
    assert metrics.post().status_code == 200


def test_deleting_a_measure_removes_its_secret(metrics):
    manager = metrics.keen.client(metrics.keen.user("isms.manage"))
    measure_id = metrics.measure.id

    deleted = manager.delete(
        f"/v1/isms/effectiveness-measures/{metrics.measure.id}"
        f"?framework={metrics.measure.framework_slug}",
        headers=unsafe(manager),
    )

    assert (deleted.status_code, deleted.json()) == (200, {"ok": True})
    metrics.db.expire_all()
    assert metrics.db.get(IsmsEffectivenessMeasureSecret, measure_id) is None


def test_a_measure_receives_its_first_secret(metrics):
    manager = metrics.keen.client(metrics.keen.user("isms.manage"))
    fresh = IsmsEffectivenessMeasure(
        framework_slug=settings.default_framework_slug, metric_key="fresh-measure"
    )
    metrics.db.add(fresh)
    metrics.db.commit()

    issued = manager.post(_secret_path(fresh), headers=unsafe(manager))

    assert issued.status_code == 200, issued.text
    assert (
        metrics.post("fresh-measure", secret=issued.json()["secret"]).status_code == 200
    )


def test_releasing_a_claim_tolerates_an_unavailable_store():
    class Unavailable:
        def delete(self, key):
            raise ConnectionError("store down")

    # The claim lapses with the signature's window instead.
    assert signed_webhooks.release(Unavailable(), "scope", "v1=" + "0" * 64) is None
