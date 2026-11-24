"""Metric submissions follow the installation's event data masking.

With masking on for all event data, addresses are masked in the extracted
fields and in the stored copy of the payload; without it they are kept.
"""

import pytest
from app.core.config import settings
from mosp.security.redaction import MASKED_EMAIL, MASKED_IP

from tests.test_assembled_authority_regressions import keen as keen
from tests.test_metric_webhooks import metrics as metrics

PAYLOAD = {
    "value": 5,
    "notes": "Checked by ada@example.org from 192.0.2.4",
    "collector": {"host": "198.51.100.9", "contact": ["ops@example.org"]},
}
LINK = "https://ci.example/runs?by=ada@example.org&from=192.0.2.4"


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


@pytest.mark.parametrize(
    "mode, stored",
    [
        ("true", "https://ci.example/runs?by=%5BMASKED_EMAIL%5D&from=%5BMASKED_IP%5D"),
        ("samples", "https://ci.example/runs?by=ada%40example.org&from=192.0.2.4"),
    ],
)
def test_the_source_link_follows_masking(metrics, monkeypatch, mode, stored):
    monkeypatch.setattr(settings, "event_data_masking", mode)
    response = metrics.post("cpu-usage", {"value": 5, "source_url": LINK})
    assert response.status_code == 200, response.text
    (row,) = metrics.entries()
    assert row.source_url == stored


@pytest.mark.parametrize(
    "link, stored",
    [
        # Already percent-encoded where it was linked from.
        (
            "https://ci.example/runs?by=ada%40example.org&from=2001%3Adb8%3A%3A7",
            "https://ci.example/runs?by=%5BMASKED_EMAIL%5D&from=%5BMASKED_IP%5D",
        ),
        # A host given as an address keeps the rest of the link.
        ("https://192.0.2.10/tickets/1", "https://[MASKED_IP]/tickets/1"),
        (
            "https://ci.example/runs/ada@example.org",
            "https://ci.example/runs/[MASKED_EMAIL]",
        ),
    ],
)
def test_a_masked_link_hides_addresses_however_written(
    metrics, monkeypatch, link, stored
):
    monkeypatch.setattr(settings, "event_data_masking", "true")
    response = metrics.post("cpu-usage", {"value": 5, "source_url": link})
    assert response.status_code == 200, response.text
    (row,) = metrics.entries()
    assert row.source_url == stored
    # With an address percent-encoded too.
    assert "ada" not in str(row.raw_payload)
    assert "192.0.2.10" not in str(row.raw_payload)


def test_a_masked_link_still_fits_its_column(metrics, monkeypatch):
    monkeypatch.setattr(settings, "event_data_masking", "true")
    link = "https://ci.example/?" + "&".join(["a=b@c.io"] * 220)
    assert len(link) <= 2048
    response = metrics.post("cpu-usage", {"value": 5, "source_url": link})
    assert response.status_code == 200, response.text
    (row,) = metrics.entries()
    assert len(row.source_url) == 2048 and "c.io" not in row.source_url
