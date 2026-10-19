"""Evidence artefact downloads and PDF previews through the assembled app.

Artefacts are stored in local artefact storage. Downloads carry their stored
checksum; sample exports mask event data when the deployment asks for it;
PDF previews render page one through the bounded renderer and are cached.
"""

import hashlib
import json
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from app.core.config import settings
from app.db.models import Artifact, Event
from app.security.diary_visibility import link_entry_external_id
from app.services import artifacts as artifact_operations
from app.storage.s3 import put_bytes

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import keen  # noqa: F401


def pdf(text="Evidence"):
    """A one-page PDF with correct cross-reference offsets."""
    stream = f"BT /F1 24 Tf 20 100 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


@pytest.fixture
def store(keen, monkeypatch):  # noqa: F811 - fixture imported above
    monkeypatch.setattr(settings, "event_data_masking", "false")
    artifact_operations._PREVIEW_CACHE.clear()

    def artifact(data, content_type, *, source="ci", **event_fields):
        event = Event(
            source=source,
            summary=f"{source} evidence",
            external_id=uuid.uuid4().hex,
            timestamp=datetime(2026, 1, 1),
            **event_fields,
        )
        keen.db.add(event)
        keen.db.flush()
        stored = put_bytes(f"tests/{uuid.uuid4()}", data, content_type)
        row = Artifact(
            event_id=event.id,
            kind="test",
            storage_uri=stored.uri,
            sha256=stored.sha256,
            content_type=content_type,
            size_bytes=stored.size_bytes,
            captured_by="tests",
        )
        keen.db.add(row)
        keen.db.commit()
        return row

    reader = keen.client(keen.user("events.read"))
    yield SimpleNamespace(keen=keen, artifact=artifact, reader=reader)
    artifact_operations._PREVIEW_CACHE.clear()


def download(client, row, **params):
    return client.get(f"/v1/artifacts/{row.id}/download", params=params)


def test_a_download_returns_the_stored_bytes_and_checksum(store):
    body = json.dumps({"build": 7, "result": "passed"}).encode()
    row = store.artifact(body, "application/json")

    response = download(store.reader, row)

    assert response.status_code == 200, response.text
    assert response.content == body
    assert response.headers["x-artifact-sha256"] == hashlib.sha256(body).hexdigest()
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["content-type"].startswith("application/json")


def test_downloads_need_events_read_and_an_existing_object(store, tmp_path):
    row = store.artifact(b"{}", "application/json")
    outsider = store.keen.client(store.keen.user("isms.read"))

    assert download(outsider, row).status_code == 403
    assert download(store.keen.client(), row).status_code == 401
    bad = store.reader.get("/v1/artifacts/not-a-uuid/download")
    assert (bad.status_code, bad.json()["detail"]) == (
        400,
        "artifact_id must be a UUID",
    )
    missing = store.reader.get(f"/v1/artifacts/{uuid.uuid4()}/download")
    assert (missing.status_code, missing.json()["detail"]) == (
        404,
        "Artifact not found",
    )

    for stored in tmp_path.glob("tests/*"):
        stored.unlink()
    lost = download(store.reader, row)
    assert (lost.status_code, lost.json()["detail"]) == (
        502,
        "Failed to fetch artifact from storage",
    )


@pytest.mark.parametrize("mode", ["samples", "true"])
def test_sample_exports_mask_event_data_and_say_so(store, monkeypatch, mode):
    body = json.dumps({"owner": "alice@example.test", "host": "10.1.2.3"}).encode()
    row = store.artifact(body, "application/json")
    monkeypatch.setattr(settings, "event_data_masking", mode)

    masked = download(store.reader, row, sample_export="true")

    assert masked.status_code == 200, masked.text
    assert b"alice@example.test" not in masked.content
    assert b"10.1.2.3" not in masked.content
    assert masked.headers["x-keen-event-data-masked"] == "1"
    assert masked.headers["x-artifact-original-sha256"] == row.sha256
    assert (
        masked.headers["x-artifact-sha256"]
        == hashlib.sha256(masked.content).hexdigest()
    )
    # An ordinary download keeps the original evidence.
    assert download(store.reader, row).content == body


def test_sample_exports_leave_binary_and_unmasked_deployments_alone(store, monkeypatch):
    binary = store.artifact(b"\x89PNG alice@example.test", "image/png")
    text = store.artifact(b"alice@example.test", "text/plain")

    monkeypatch.setattr(settings, "event_data_masking", "samples")
    image = download(store.reader, binary, sample_export="true")
    assert image.content == b"\x89PNG alice@example.test"
    assert "x-keen-event-data-masked" not in image.headers

    monkeypatch.setattr(settings, "event_data_masking", "false")
    plain = download(store.reader, text, sample_export="true")
    assert plain.content == b"alice@example.test"


def test_masking_is_bounded_by_the_artefact_size(store, monkeypatch):
    row = store.artifact(b'"' + b"x" * (20 * 1024 * 1024) + b'"', "application/json")
    monkeypatch.setattr(settings, "event_data_masking", "samples")

    refused = download(store.reader, row, sample_export="true")

    assert (refused.status_code, refused.json()["detail"]) == (
        413,
        "Artifact exceeds the 20 MiB masking limit",
    )


def test_diary_artefacts_follow_the_record_they_describe(store):
    risk_id = uuid.uuid4()
    row = store.artifact(
        b"{}",
        "application/json",
        source="diary",
        system="isms",
        actor="system",
    )
    event = store.keen.db.get(Event, row.event_id)
    event.external_id = link_entry_external_id("risk", risk_id)
    store.keen.db.commit()
    risk_reader = store.keen.client(store.keen.user("events.read", "risk.read"))

    hidden = download(store.reader, row)
    assert (hidden.status_code, hidden.json()["detail"]) == (404, "Artifact not found")
    assert store.reader.get(f"/v1/artifacts/{row.id}/preview").status_code == 404
    assert download(risk_reader, row).status_code == 200


def test_a_pdf_preview_renders_page_one_once(store, monkeypatch):
    row = store.artifact(pdf(), "application/pdf")
    path = f"/v1/artifacts/{row.id}/preview"

    first = store.reader.get(path)

    assert first.status_code == 200, first.text
    assert first.headers["content-type"] == "image/png"
    assert first.content.startswith(b"\x89PNG\r\n\x1a\n")

    def unexpected(command):
        raise AssertionError("rendered twice")

    monkeypatch.setattr(artifact_operations, "_run_preview", unexpected)
    again = store.reader.get(path)
    assert again.content == first.content


@pytest.mark.parametrize(
    "data,content_type,status,detail",
    [
        (b"{}", "application/json", 415, "Preview is only supported for PDFs"),
        (
            b"%PDF-1.4 not really a document",
            "application/pdf",
            500,
            "Failed to render PDF preview",
        ),
        (
            b"%PDF" + b"0" * (20 * 1024 * 1024),
            "application/pdf",
            413,
            "PDF preview exceeds 20 MiB",
        ),
    ],
    ids=["not-pdf", "corrupt", "oversized"],
)
def test_unusable_pdfs_are_refused(store, data, content_type, status, detail):
    row = store.artifact(data, content_type)

    response = store.reader.get(f"/v1/artifacts/{row.id}/preview")

    assert response.status_code == status
    assert response.json()["detail"].startswith(detail)


def test_busy_renderers_ask_the_caller_to_retry(store):
    row = store.artifact(pdf(), "application/pdf")
    slots = artifact_operations._PREVIEW_SLOTS
    taken = [slots.acquire(blocking=False) for _ in range(2)]
    try:
        busy = store.reader.get(f"/v1/artifacts/{row.id}/preview")
    finally:
        for acquired in taken:
            if acquired:
                slots.release()

    assert all(taken)
    assert (busy.status_code, busy.headers["retry-after"]) == (429, "5")
    assert busy.json()["detail"] == "Preview renderer busy; retry shortly"


def test_renderer_failures_are_reported_without_detail(store, monkeypatch):
    import subprocess

    row = store.artifact(pdf(), "application/pdf")
    path = f"/v1/artifacts/{row.id}/preview"
    for error, status, detail in (
        (
            FileNotFoundError("pdftoppm"),
            500,
            "pdftoppm is not installed in the API image",
        ),
        (
            subprocess.TimeoutExpired("pdftoppm", 15),
            422,
            "PDF preview exceeded the rendering time limit",
        ),
    ):

        def fail(command, error=error):
            raise error

        monkeypatch.setattr(artifact_operations, "_run_preview", fail)
        response = store.reader.get(path)
        assert (response.status_code, response.json()["detail"]) == (status, detail)

    monkeypatch.setattr(artifact_operations, "_run_preview", lambda command: None)
    silent = store.reader.get(path)
    assert (silent.status_code, silent.json()["detail"]) == (
        500,
        "Preview renderer did not produce output",
    )
