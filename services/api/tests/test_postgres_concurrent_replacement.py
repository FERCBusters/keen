"""Overlapping changes to one document or audit queue behind its row lock.

A second text save, a second file upload, or a deletion that overlaps an
upload waits for the first change to commit and then works from what it
saved. A stale text save is refused with the usual conflict, and every stored
file that no record references any more is queued for deletion. The changes
run on separate PostgreSQL connections, so these tests skip on SQLite.
"""

import io
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from app.core.config import settings
from app.db.models import Audit, IsmsDocument, User
from app.services import evidence_retention
from app.services.audits import operations as audit_operations
from app.services.audits import reports
from app.services.context import ChangeContext
from app.services.errors import DomainError
from app.services.isms import documents
from app.services.isms.schemas import DocumentPayload
from sqlalchemy import text
from sqlalchemy.orm import Session

CONTEXT = ChangeContext(method="POST", path="/v1/isms/documents")
STALE = "Document changed since it was opened. Reload before saving."


@pytest.fixture
def race(contract_postgres_engine, monkeypatch, tmp_path):
    engine = contract_postgres_engine
    if engine is None:
        pytest.skip("Row locks need separate PostgreSQL connections")
    monkeypatch.setattr(settings, "artifact_storage_backend", "local")
    monkeypatch.setattr(settings, "artifact_local_dir", str(tmp_path))
    with Session(engine) as db:
        user = User(
            username="editor-" + uuid.uuid4().hex[:10],
            password_hash="unused",
            role="admin",
            is_active=True,
        )
        db.add(user)
        db.commit()
        user_id = user.id

    def session():
        return Session(engine)

    def run(first, second, *, pause_at):
        """Hold the first change at pause_at while the second one starts.

        The first change continues once the second waits on a lock, or once
        the second has finished because nothing made it wait. Each change
        returns "done" or the status and detail it was refused with.
        """
        module, name = pause_at
        original = getattr(module, name)
        holder = {}
        paused, release = threading.Event(), threading.Event()

        def hold(*args, **kwargs):
            if threading.get_ident() == holder.get("thread"):
                paused.set()
                assert release.wait(10)
            return original(*args, **kwargs)

        def call(change, is_first):
            with Session(engine) as db:
                db.execute(text("SET lock_timeout = '10s'"))
                if is_first:
                    holder["thread"] = threading.get_ident()
                else:
                    holder["pid"] = db.execute(text("SELECT pg_backend_pid()")).scalar()
                try:
                    change(db, db.get(User, user_id))
                except DomainError as refused:
                    return refused.status_code, refused.detail
                return "done"

        def waiting():
            with engine.connect() as connection:
                state = connection.execute(
                    text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :p"),
                    {"p": holder.get("pid")},
                ).scalar()
            return state == "Lock"

        monkeypatch.setattr(module, name, hold)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                one = pool.submit(call, first, True)
                assert paused.wait(10)
                two = pool.submit(call, second, False)
                deadline = time.monotonic() + 10
                while not two.done() and not waiting():
                    assert time.monotonic() < deadline
                    time.sleep(0.02)
                release.set()
                return one.result(20), two.result(20)
        finally:
            release.set()
            monkeypatch.setattr(module, name, original)

    def queued(prefix):
        with Session(engine) as db:
            rows = db.execute(
                text(
                    "SELECT storage_uri FROM evidence_object_cleanup WHERE storage_uri LIKE :p"
                ),
                {"p": f"local://local/{prefix}/%"},
            )
            return set(rows.scalars())

    def stored(prefix):
        folder = tmp_path / prefix
        return {
            f"local://local/{prefix}/{path.name}"
            for path in (folder.iterdir() if folder.is_dir() else [])
        }

    return SimpleNamespace(
        user_id=user_id, session=session, run=run, queued=queued, stored=stored
    )


# --- ISMS documents ---------------------------------------------------------------


def new_document(race, **fields):
    with race.session() as db:
        created = documents.create_document(
            payload=DocumentPayload(title="Access policy", **fields),
            context=CONTEXT,
            framework="iso27001",
            user=db.get(User, race.user_id),
            db=db,
        )
    return created["id"]


def save_text(document_id, html):
    def change(db, user):
        documents.update_document(
            document_id=document_id,
            payload=DocumentPayload(content_html=html, expected_content_version=1),
            context=CONTEXT,
            framework="iso27001",
            user=user,
            db=db,
        )

    return change


def upload(document_id, data):
    def change(db, user):
        documents.upload_document_file(
            document_id=document_id,
            content=io.BytesIO(data),
            filename="policy.txt",
            content_type="text/plain",
            framework="iso27001",
            user=user,
            db=db,
            context=CONTEXT,
        )

    return change


def delete(document_id):
    def change(db, user):
        documents.delete_document(
            document_id=document_id,
            context=CONTEXT,
            framework="iso27001",
            user=user,
            db=db,
        )

    return change


def document(race, document_id):
    with race.session() as db:
        return db.get(IsmsDocument, uuid.UUID(document_id))


def test_a_text_save_overlapping_another_is_refused_as_stale(race):
    document_id = new_document(race, content_html="<p>One</p>")

    outcome = race.run(
        save_text(document_id, "<p>First</p>"),
        save_text(document_id, "<p>Second</p>"),
        pause_at=(documents, "sanitize_rich_text_html"),
    )

    assert outcome == ("done", (409, STALE))
    saved = document(race, document_id)
    assert (saved.content_html, saved.content_version) == ("<p>First</p>", 2)


def test_overlapping_uploads_retire_every_replaced_file(race):
    document_id = new_document(race)
    with race.session() as db:
        upload(document_id, b"original")(db, db.get(User, race.user_id))
    prefix = f"isms-documents/{document_id}"

    outcome = race.run(
        upload(document_id, b"first"),
        upload(document_id, b"second"),
        pause_at=(documents, "put_file"),
    )

    assert outcome == ("done", "done")
    current = document(race, document_id).storage_uri
    files = race.stored(prefix)
    assert len(files) == 3
    assert race.queued(prefix) == files - {current}
    with open(
        settings.artifact_local_dir + current.removeprefix("local://local"), "rb"
    ) as f:
        assert f.read() == b"second"


@pytest.mark.parametrize("first", ["deletion", "upload"])
def test_an_upload_overlapping_a_deletion_leaves_no_file_behind(race, first):
    document_id = new_document(race)
    with race.session() as db:
        upload(document_id, b"original")(db, db.get(User, race.user_id))
    prefix = f"isms-documents/{document_id}"

    if first == "deletion":
        outcome = race.run(
            delete(document_id),
            upload(document_id, b"replacement"),
            pause_at=(documents, "_record"),
        )
        assert outcome == ("done", (404, "Document not found"))
    else:
        outcome = race.run(
            upload(document_id, b"replacement"),
            delete(document_id),
            pause_at=(documents, "put_file"),
        )
        assert outcome == ("done", "done")

    assert document(race, document_id) is None
    files = race.stored(prefix)
    assert files and race.queued(prefix) == files


# --- Audit final reports ------------------------------------------------------------


def new_audit(race):
    with race.session() as db:
        audit = Audit(
            title="Access review",
            framework_slug="iso27001",
            status="in_progress",
            created_by_user_id=race.user_id,
        )
        db.add(audit)
        db.commit()
        audit_id = str(audit.id)
        report(audit_id, b"original")(db, db.get(User, race.user_id))
    return audit_id


def report(audit_id, data):
    def change(db, user):
        reports.save_final_report(
            audit_id=audit_id,
            db=db,
            user=user,
            filename="report.txt",
            content_type="text/plain",
            content=io.BytesIO(data),
        )

    return change


def delete_audit(audit_id):
    def change(db, user):
        audit_operations.delete_audit(audit_id=audit_id, db=db, user=user)

    return change


def test_overlapping_final_reports_retire_every_replaced_report(race):
    audit_id = new_audit(race)
    prefix = f"audit-reports/{audit_id}"

    outcome = race.run(
        report(audit_id, b"first"),
        report(audit_id, b"second"),
        pause_at=(reports, "put_file"),
    )

    assert outcome == ("done", "done")
    with race.session() as db:
        current = db.get(Audit, uuid.UUID(audit_id)).final_report_storage_uri
    files = race.stored(prefix)
    assert len(files) == 3
    assert race.queued(prefix) == files - {current}


@pytest.mark.parametrize("first", ["deletion", "report"])
def test_a_final_report_overlapping_a_deletion_leaves_no_report_behind(race, first):
    audit_id = new_audit(race)
    prefix = f"audit-reports/{audit_id}"

    if first == "deletion":
        outcome = race.run(
            delete_audit(audit_id),
            report(audit_id, b"replacement"),
            pause_at=(evidence_retention, "queue_object"),
        )
        assert outcome == ("done", (404, "Audit not found"))
    else:
        outcome = race.run(
            report(audit_id, b"replacement"),
            delete_audit(audit_id),
            pause_at=(reports, "put_file"),
        )
        assert outcome == ("done", "done")

    with race.session() as db:
        assert db.get(Audit, uuid.UUID(audit_id)) is None
    files = race.stored(prefix)
    assert files and race.queued(prefix) == files
