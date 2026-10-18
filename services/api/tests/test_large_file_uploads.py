"""ISMS document files and audit final reports up to 100 MiB.

Those two uploads have their own body limit above the general 40 MiB, and
their operations measure the file itself against the same 100 MiB. The file
goes to storage from the spooled request in bounded chunks, so a large upload
never sits in memory whole, and the handlers run off the event loop.
"""

import asyncio
import hashlib
import inspect
import io
import os

import pytest
from app.api.routes import audits as audit_routes
from app.api.routes.isms import documents as document_routes
from app.db.models import Framework
from app.security.request_limits import (
    MAX_BODY,
    MAX_FILE_UPLOAD,
    RequestBodyLimitMiddleware,
    body_limit,
)
from app.services.audits import reports
from app.services.isms import documents
from app.services.uploads import upload_size
from app.storage.s3 import put_file as reports_put_file

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)

MIB = 1024 * 1024
UPLOADS = ["/v1/isms/documents/7f1d/file", "/v1/audits/7f1d/report"]
NEIGHBOURS = [
    "/v1/isms/documents",
    "/v1/isms/documents/7f1d",
    "/v1/isms/documents/7f1d/files",
    "/v1/isms/documents/7f1d/file/extra",
    "/v1/isms/documents//file",
    "/v1/audits/7f1d/report/extra",
    "/v1/audits/report",
    "/v1/audits/7f1d/evidence",
    "/v1/questions/7f1d/posts",
    "/api/v1/isms/documents/7f1d/file",
]


def test_only_the_two_file_uploads_get_the_larger_limit():
    assert MAX_FILE_UPLOAD == 100 * MIB
    assert documents.MAX_DOCUMENT_BYTES == reports.MAX_REPORT_BYTES == MAX_FILE_UPLOAD
    for path in UPLOADS:
        # The file itself plus a little room for the multipart framing.
        assert MAX_FILE_UPLOAD < body_limit(path) <= MAX_FILE_UPLOAD + MIB, path
    for path in NEIGHBOURS:
        assert body_limit(path) == MAX_BODY == 40 * MIB, path


async def declared(path, length):
    """Send one byte under a declared Content-Length; return the status."""
    sent = []
    messages = iter([{"type": "http.request", "body": b"x", "more_body": False}])

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    async def endpoint(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    scope = {
        "type": "http",
        "path": path,
        "headers": [(b"content-length", str(length).encode())],
    }
    await RequestBodyLimitMiddleware(endpoint)(scope, receive, send)
    return sent[0]["status"]


@pytest.mark.parametrize("path", UPLOADS)
def test_upload_routes_accept_declared_sizes_up_to_their_limit(path):
    limit = body_limit(path)

    assert asyncio.run(declared(path, 41 * MIB)) == 200
    assert asyncio.run(declared(path, limit)) == 200
    assert asyncio.run(declared(path, limit + 1)) == 413


def test_other_routes_keep_the_general_limit():
    assert asyncio.run(declared("/v1/isms/documents", MAX_BODY)) == 200
    assert asyncio.run(declared("/v1/isms/documents", MAX_BODY + 1)) == 413


def test_an_upload_is_measured_whole_and_left_at_its_start():
    content = io.BytesIO(b"0123456789")
    content.seek(4)  # wherever the request parser left it

    assert upload_size(content) == 10
    assert content.tell() == 0


def test_upload_handlers_run_off_the_event_loop():
    # FastAPI runs synchronous handlers in its threadpool, so a storage write
    # of up to 100 MiB never blocks other requests on the worker.
    for handler in (
        document_routes.upload_document_file,
        audit_routes.upload_final_report,
    ):
        assert not inspect.iscoroutinefunction(handler), handler.__name__


# --- Through the assembled application ---------------------------------------------

BIG = 41 * MIB  # over the general request limit


def test_a_document_file_over_forty_mib_is_stored_and_downloads_unchanged(keen):  # noqa: F811
    client = keen.client(keen.user("isms.manage"))
    document = client.post(
        "/v1/isms/documents", json={"title": "Records policy"}, headers=unsafe(client)
    ).json()
    data = os.urandom(BIG)

    uploaded = client.post(
        f"/v1/isms/documents/{document['id']}/file",
        files={"file": ("records.pdf", data, "application/pdf")},
        headers=unsafe(client),
    )

    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["document"]["size_bytes"] == BIG
    download = client.get(f"/v1/isms/documents/{document['id']}/file")
    assert download.status_code == 200
    assert hashlib.sha256(download.content).digest() == hashlib.sha256(data).digest()
    # Every other route keeps the general limit.
    refused = client.post(
        "/v1/isms/documents",
        content=b" " * BIG,
        headers={**unsafe(client), "Content-Type": "application/json"},
    )
    assert (refused.status_code, refused.json()["detail"]) == (
        413,
        "Request body too large",
    )


def test_an_audit_final_report_over_forty_mib_is_stored(keen):  # noqa: F811
    keen.db.add(Framework(slug="fw-large"))
    keen.db.commit()
    client = keen.client(keen.user("audits.manage"))
    audit = client.post(
        "/v1/audits",
        json={"title": "Annual review", "framework_slug": "fw-large"},
        headers=unsafe(client),
    ).json()
    data = os.urandom(BIG)

    uploaded = client.post(
        f"/v1/audits/{audit['id']}/report",
        files={"file": ("report.pdf", data, "application/pdf")},
        headers=unsafe(client),
    )

    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["final_report"]["size_bytes"] == BIG
    download = client.get(f"/v1/audits/{audit['id']}/report")
    assert hashlib.sha256(download.content).digest() == hashlib.sha256(data).digest()


def test_a_document_file_over_the_limit_is_refused_before_storage(keen, monkeypatch):  # noqa: F811
    monkeypatch.setattr(documents, "MAX_DOCUMENT_BYTES", 8)
    client = keen.client(keen.user("isms.manage"))
    document = client.post(
        "/v1/isms/documents", json={"title": "Records policy"}, headers=unsafe(client)
    ).json()
    path = f"/v1/isms/documents/{document['id']}/file"

    refused = client.post(
        path,
        files={"file": ("records.txt", b"123456789", "text/plain")},
        headers=unsafe(client),
    )
    at_limit = client.post(
        path,
        files={"file": ("records.txt", b"12345678", "text/plain")},
        headers=unsafe(client),
    )

    assert (refused.status_code, refused.json()["detail"]) == (
        413,
        "ISMS document is too large",
    )
    assert at_limit.status_code == 200, at_limit.text
    assert client.get(path).content == b"12345678"


def test_a_final_report_over_the_limit_or_empty_is_refused_before_storage(
    keen,  # noqa: F811 - fixture imported above
    monkeypatch,
):
    monkeypatch.setattr(reports, "MAX_REPORT_BYTES", 8)
    stored = []
    monkeypatch.setattr(
        reports,
        "put_file",
        lambda key, content, content_type: (
            stored.append(content.read())
            or reports_put_file(key, content, content_type)
        ),
    )
    keen.db.add(Framework(slug="fw-small"))
    keen.db.commit()
    client = keen.client(keen.user("audits.manage"))
    audit = client.post(
        "/v1/audits",
        json={"title": "Annual review", "framework_slug": "fw-small"},
        headers=unsafe(client),
    ).json()
    path = f"/v1/audits/{audit['id']}/report"

    def upload(data):
        return client.post(
            path,
            files={"file": ("report.txt", data, "text/plain")},
            headers=unsafe(client),
        )

    refused, empty = upload(b"123456789"), upload(b"")
    assert (refused.status_code, refused.json()["detail"]) == (
        413,
        "Final report is too large",
    )
    assert (empty.status_code, empty.json()["detail"]) == (
        400,
        "Uploaded file is empty",
    )
    assert stored == []
    at_limit = upload(b"12345678")
    assert at_limit.status_code == 200, at_limit.text
    assert at_limit.json()["final_report"]["size_bytes"] == 8
    assert stored == [b"12345678"]
    assert client.get(path).content == b"12345678"
