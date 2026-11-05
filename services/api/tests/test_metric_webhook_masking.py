"""Metric submissions follow the installation's event data masking.

With masking on for all event data, addresses are masked in the extracted
fields and in the stored copy of the payload; without it they are kept.
"""

from app.core.config import settings
from app.security.redaction import MASKED_EMAIL, MASKED_IP

from tests.test_assembled_authority_regressions import keen as keen
from tests.test_metric_webhooks import metrics as metrics

PAYLOAD = {
    "value": 5,
    "notes": "Checked by ada@example.org from 192.0.2.4",
    "collector": {"host": "198.51.100.9", "contact": ["ops@example.org"]},
}


def test_masking_all_event_data_masks_the_stored_payload(metrics, monkeypatch):
    monkeypatch.setattr(settings, "event_data_masking", "true")
    response = metrics.post("cpu-usage", PAYLOAD)
    assert response.status_code == 200, response.text
    (row,) = metrics.entries()
    assert row.notes == f"Checked by {MASKED_EMAIL} from {MASKED_IP}"
    assert row.raw_payload == {
        "value": 5,
        "notes": f"Checked by {MASKED_EMAIL} from {MASKED_IP}",
        "collector": {"host": MASKED_IP, "contact": [MASKED_EMAIL]},
    }
    assert "ada@example.org" not in response.text


def test_masking_samples_only_keeps_metric_payloads_as_sent(metrics, monkeypatch):
    monkeypatch.setattr(settings, "event_data_masking", "samples")
    response = metrics.post("cpu-usage", PAYLOAD)
    assert response.status_code == 200, response.text
    (row,) = metrics.entries()
    assert row.notes == PAYLOAD["notes"]
    assert row.raw_payload == PAYLOAD
