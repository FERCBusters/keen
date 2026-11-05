"""Artefact sample exports with nothing to mask, and PDF previews that fail
before rendering: a malformed identifier, lost stored bytes and a failure
preparing the renderer's working files.

Requests come from a user who can read events, through the assembled app.
"""

import hashlib
import json
from types import SimpleNamespace

from app.core.config import settings
from app.services import artifacts as artifact_operations

# Imported fixtures: the assembled application and its local artefact store.
from tests.test_artifact_downloads_api import download, pdf, store  # noqa: F401
from tests.test_assembled_authority_regressions import keen  # noqa: F401


def test_a_sample_export_with_nothing_to_mask_is_the_original(store, monkeypatch):  # noqa: F811
    body = json.dumps({"build": 7, "result": "passed"}).encode()
    row = store.artifact(body, "application/json")
    monkeypatch.setattr(settings, "event_data_masking", "samples")

    sample = download(store.reader, row, sample_export="true")

    assert sample.status_code == 200, sample.text
    assert sample.content == body
    assert sample.headers["x-artifact-sha256"] == hashlib.sha256(body).hexdigest()
    # Nothing was masked, so the export claims no masking and no second checksum.
    assert "x-keen-event-data-masked" not in sample.headers
    assert "x-artifact-original-sha256" not in sample.headers


def test_a_preview_needs_an_artefact_identifier(store):  # noqa: F811
    refused = store.reader.get("/v1/artifacts/not-a-uuid/preview")

    assert (refused.status_code, refused.json()["detail"]) == (
        400,
        "artifact_id must be a UUID",
    )


def test_a_preview_of_lost_stored_bytes_reports_the_storage_failure(
    store,  # noqa: F811 - fixture imported above
    tmp_path,
    monkeypatch,
):
    row = store.artifact(pdf(), "application/pdf")
    for stored in tmp_path.glob("tests/*"):
        stored.unlink()

    def unexpected(command):
        raise AssertionError("the renderer ran without its input")

    monkeypatch.setattr(artifact_operations, "_run_preview", unexpected)
    lost = store.reader.get(f"/v1/artifacts/{row.id}/preview")

    assert (lost.status_code, lost.json()["detail"]) == (
        502,
        "Failed to fetch artifact from storage",
    )


def test_a_failure_preparing_the_preview_is_reported_without_detail(
    store,  # noqa: F811 - fixture imported above
    monkeypatch,
):
    row = store.artifact(pdf(), "application/pdf")

    def full_disk(*args, **kwargs):
        raise OSError(28, "No space left on device: /tmp/keen_pdfprev_secret")

    monkeypatch.setattr(
        artifact_operations, "tempfile", SimpleNamespace(TemporaryDirectory=full_disk)
    )
    failed = store.reader.get(f"/v1/artifacts/{row.id}/preview")

    assert (failed.status_code, failed.json()) == (
        500,
        {"detail": "Failed to build preview"},
    )
    assert "keen_pdfprev" not in failed.text
    assert artifact_operations._PREVIEW_CACHE == {}
