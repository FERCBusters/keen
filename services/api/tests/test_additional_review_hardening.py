"""Upload resource bounds and safe object names, using synthetic fixtures."""

import asyncio
import io
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from app.services import questions
from app.services.audits import reports
from app.services.context import ChangeContext
from app.services.errors import DomainError
from app.services.isms import documents
from starlette.datastructures import UploadFile

# Real sessions and permissions are exercised by the shared assembled fixture.
from tests.test_security_sampling_masking import sampling_client as sampling_client


def test_oversized_report_does_not_write_storage_or_metadata(
    sampling_client, monkeypatch
):
    client, factory, ids = sampling_client
    monkeypatch.setattr(reports, "MAX_REPORT_BYTES", 8)
    store = Mock()
    monkeypatch.setattr(reports, "put_file", store)
    response = client.post(
        f"/v1/audits/{ids.audit}/report",
        files={"file": ("report.txt", b"synthetic oversized report", "text/plain")},
    )
    assert response.status_code == 413
    store.assert_not_called()
    from app.db.models import Audit

    with factory() as db:
        assert db.get(Audit, ids.audit).final_report_storage_uri is None


def test_later_oversized_question_attachment_prevents_all_storage_writes(monkeypatch):
    monkeypatch.setattr(questions, "MAX_ATTACHMENT_BYTES", 16)
    uploads = [
        UploadFile(io.BytesIO(b"%PDF-1.7\n"), filename="ok.pdf"),
        UploadFile(io.BytesIO(b"%PDF-1.7\n" + b"x" * 40), filename="large.pdf"),
    ]
    from app.api.uploads import upload_inputs

    store = Mock()
    monkeypatch.setattr(questions, "put_bytes", store)
    db = Mock()
    with pytest.raises(DomainError) as error:
        asyncio.run(
            questions._store_attachments(
                db,
                event_id=uuid.uuid4(),
                thread_id=uuid.uuid4(),
                post_id=uuid.uuid4(),
                files=upload_inputs(uploads),
                captured_by="synthetic",
            )
        )
    assert error.value.status_code == 413
    assert uploads[1].file.tell() == 17
    store.assert_not_called()
    db.add.assert_not_called()


@pytest.mark.parametrize(
    "filename,extension",
    [("report.PDF", ".pdf"), ("report.a b", ""), ("report." + "x" * 11, "")],
)
@pytest.mark.parametrize("kind", ["audit", "isms"])
def test_upload_object_extensions_are_safe(monkeypatch, kind, filename, extension):
    row = SimpleNamespace(
        id=uuid.uuid4(), storage_uri=None, final_report_storage_uri=None
    )
    user = SimpleNamespace(id=uuid.uuid4())
    store = Mock(
        return_value=SimpleNamespace(
            uri="local://local/fixture", sha256="0" * 64, size_bytes=7
        )
    )
    db = Mock()
    if kind == "audit":
        monkeypatch.setattr(reports, "editable_audit_or_404", lambda *a, **k: row)
        monkeypatch.setattr(reports, "require_mutable_audit", lambda *args: None)
        monkeypatch.setattr(reports, "report_dict", lambda *args: {})
        monkeypatch.setattr(reports, "put_file", store)
        reports.save_final_report(
            audit_id=str(row.id),
            db=db,
            user=user,
            filename=filename,
            content_type="application/pdf",
            content=io.BytesIO(b"fixture"),
        )
    else:
        monkeypatch.setattr(documents, "_by_id_or_404", lambda *a, **k: row)
        monkeypatch.setattr(documents, "_document_out", lambda *args: {})
        monkeypatch.setattr(documents, "_record", lambda *args: None)
        monkeypatch.setattr(documents, "put_file", store)
        documents.upload_document_file(
            document_id=str(row.id),
            content=io.BytesIO(b"fixture"),
            filename=filename,
            content_type="application/pdf",
            framework="fixture",
            user=user,
            db=db,
            context=ChangeContext(method="POST", path="/synthetic"),
        )
    key = store.call_args.args[0]
    tail = key.rsplit("/", 1)[-1]
    assert tail == str(uuid.UUID(tail[:36])) + extension


def test_report_at_limit_is_stored_and_remains_downloadable(
    sampling_client, monkeypatch
):
    client, factory, ids = sampling_client
    monkeypatch.setattr(reports, "MAX_REPORT_BYTES", 8)
    uploaded = client.post(
        f"/v1/audits/{ids.audit}/report",
        files={"file": ("report.TXT", b"12345678", "text/plain")},
    )
    assert uploaded.status_code == 200
    download = client.get(f"/v1/audits/{ids.audit}/report")
    assert download.status_code == 200
    assert download.content == b"12345678"
    assert "attachment;" in download.headers["Content-Disposition"]
