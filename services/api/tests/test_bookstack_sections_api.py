"""BookStack policy-section snapshots through the assembled application.

ISMS managers capture a BookStack page as retained evidence of a policy section
for one framework control or clause. Keen reads the page with the configured
BookStack credentials, stores the snapshot with its checksum, lists it and
serves it back. An in-memory HTTP transport stands in for BookStack.
"""

import hashlib
import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
from app.core.config import settings
from app.db.models import (
    BookStackSectionEvidence,
    ControlItem,
    Framework,
    FrameworkClause,
    IsmsDocument,
)
from app.ingest import bookstack

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)

BOOKSTACK = "https://93.184.216.34"
PATH = "/v1/isms/bookstack-sections"
PAGE = {
    "id": 7,
    "name": "Access control policy",
    "revision_count": 4,
    "updated_at": "2026-09-01T10:00:00.000000Z",
    "html": "<h2 id='bkmrk-access-review'>Access review</h2><p>Quarterly.</p>",
}


@pytest.fixture
def books(keen, monkeypatch, tmp_path):  # noqa: F811 - fixture imported above
    for name, value in {
        "bookstack_enabled": True,
        "bookstack_base_url": BOOKSTACK + "/",
        "bookstack_token_id": "synthetic-id",
        "bookstack_token_secret": "synthetic-secret",
        "event_data_masking": "false",
    }.items():
        monkeypatch.setattr(settings, name, value)
    requests = []
    upstream = SimpleNamespace(reply=lambda request: httpx.Response(200, json=PAGE))

    def handler(request):
        requests.append(request)
        return upstream.reply(request)

    def client(**kwargs):
        kwargs.pop("verify", None)
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(bookstack, "ingestion_client", client)
    framework = Framework(slug="fw-books", name="Books framework")
    control = ControlItem(framework_slug="fw-books", ref="A.5.15", type="control")
    clause = FrameworkClause(framework_slug="fw-books", ref="5.2", title="Policy")
    document = IsmsDocument(title="Access control policy")
    keen.db.add_all([framework, control, clause, document])
    keen.db.commit()
    manager = keen.client(keen.user("isms.manage"))

    def capture(status=201, client=manager, **fields):
        body = {
            "document_id": str(document.id),
            "page_id": 7,
            "target_control_id": str(control.id),
            **fields,
        }
        response = client.post(PATH, json=body, headers=unsafe(client))
        assert response.status_code == status, response.text
        return response.json()

    def stored():
        return sorted(tmp_path.glob("bookstack-policy/**/*.json"))

    return SimpleNamespace(
        keen=keen,
        upstream=upstream,
        requests=requests,
        control=control,
        clause=clause,
        document=document,
        manager=manager,
        reader=keen.client(keen.user("isms.read")),
        capture=capture,
        stored=stored,
    )


def test_a_captured_page_is_stored_listed_and_served_with_its_checksum(books):
    section = books.capture(anchor="#bkmrk-access-review")

    (request,) = books.requests
    assert (request.method, request.url.path) == ("GET", "/api/pages/7")
    assert request.headers["authorization"] == "Token synthetic-id:synthetic-secret"
    assert {
        key: section[key]
        for key in (
            "document_id",
            "document_title",
            "target_control_id",
            "target_clause_id",
            "target_framework",
            "target_ref",
            "page_id",
            "page_title",
            "anchor",
            "permalink",
            "revision_count",
            "page_updated_at",
            "archived",
        )
    } == {
        "document_id": str(books.document.id),
        "document_title": "Access control policy",
        "target_control_id": str(books.control.id),
        "target_clause_id": None,
        "target_framework": "fw-books",
        "target_ref": "A.5.15",
        "page_id": 7,
        "page_title": "Access control policy",
        "anchor": "bkmrk-access-review",
        "permalink": BOOKSTACK + "/link/7#bkmrk-access-review",
        "revision_count": 4,
        "page_updated_at": "2026-09-01T10:00:00.000000Z",
        "archived": False,
    }

    download = books.reader.get(f"{PATH}/{section['id']}/snapshot")

    assert download.status_code == 200, download.text
    digest = hashlib.sha256(download.content).hexdigest()
    assert digest == section["sha256"] == download.headers["x-artifact-sha256"]
    assert download.headers["content-disposition"] == (
        f'attachment; filename="bookstack-page-7-{section["id"]}.json"'
    )
    snapshot = json.loads(download.content)
    assert (snapshot["page_id"], snapshot["anchor"], snapshot["permalink"]) == (
        7,
        "bkmrk-access-review",
        section["permalink"],
    )
    assert snapshot["bookstack_page"]["html"] == PAGE["html"]
    (stored,) = books.stored()
    assert stored.read_bytes() == download.content
    assert books.reader.get(PATH).json() == {"items": [section]}


def test_sections_for_a_clause_are_listed_newest_first_and_by_document(books):
    first = books.capture()
    books.upstream.reply = lambda request: httpx.Response(
        200, json={**PAGE, "name": None, "revision_count": "4", "updated_at": None}
    )
    second = books.capture(
        target_control_id=None, target_clause_id=str(books.clause.id)
    )

    assert (second["target_framework"], second["target_ref"]) == ("fw-books", "5.2")
    assert (second["target_control_id"], second["anchor"]) == (None, "")
    assert second["permalink"] == BOOKSTACK + "/link/7"
    # Missing or malformed page details fall back to defaults.
    assert (second["page_title"], second["revision_count"]) == ("Page", None)
    assert second["page_updated_at"] == ""
    listed = books.reader.get(PATH).json()["items"]
    assert [item["id"] for item in listed] == [second["id"], first["id"]]
    other = IsmsDocument(title="Another policy")
    books.keen.db.add(other)
    books.keen.db.commit()
    for document_id, expected in (
        (books.document.id, [second["id"], first["id"]]),
        (other.id, []),
    ):
        filtered = books.reader.get(PATH, params={"document_id": str(document_id)})
        assert [item["id"] for item in filtered.json()["items"]] == expected


def test_archiving_marks_a_snapshot_and_can_be_undone(books):
    section = books.capture()
    url = f"{PATH}/{section['id']}"

    def archive(client, value, headers=None):
        return client.patch(
            url,
            params={"archived": value},
            headers=unsafe(client) if headers is None else headers,
        )

    archived = archive(books.manager, "true")
    assert archived.status_code == 200, archived.text
    assert archived.json() == {**section, "archived": True}
    # An archived snapshot stays listed and downloadable as evidence.
    assert books.reader.get(PATH).json()["items"][0]["archived"] is True
    assert books.reader.get(url + "/snapshot").status_code == 200
    assert archive(books.manager, "false").json()["archived"] is False

    assert archive(books.reader, "true").status_code == 403
    assert archive(books.manager, "true", headers={}).status_code == 403
    missing = books.manager.patch(
        f"{PATH}/{uuid.uuid4()}",
        params={"archived": "true"},
        headers=unsafe(books.manager),
    )
    assert (missing.status_code, missing.json()["detail"]) == (
        404,
        "Policy section snapshot not found",
    )
    assert (
        books.keen.db.get(BookStackSectionEvidence, uuid.UUID(section["id"])).archived
        is False
    )


NOWHERE = "00000000-0000-0000-0000-000000000000"
ONE_TARGET = "Select exactly one framework control or clause"


@pytest.mark.parametrize(
    "fields,detail",
    [
        (
            lambda b: {"document_id": NOWHERE},
            "Select an ISMS policy or process document",
        ),
        (lambda b: {"target_clause_id": str(b.clause.id)}, ONE_TARGET),
        (lambda b: {"target_control_id": None}, ONE_TARGET),
        (lambda b: {"target_control_id": NOWHERE}, "Unknown framework target"),
        (
            lambda b: {"target_control_id": None, "target_clause_id": NOWHERE},
            "Unknown framework target",
        ),
        (
            lambda b: {"anchor": "access review"},
            "Use a BookStack section anchor such as bkmrk-access-review",
        ),
    ],
    ids=[
        "unknown-document",
        "two-targets",
        "no-target",
        "unknown-control",
        "unknown-clause",
        "anchor",
    ],
)
def test_invalid_captures_are_refused_before_bookstack_is_read(books, fields, detail):
    refused = books.capture(status=400, **fields(books))

    assert refused["detail"] == detail
    assert books.requests == []
    assert books.keen.db.query(BookStackSectionEvidence).count() == 0
    assert books.stored() == []


def test_captures_wait_while_bookstack_is_disabled(books, monkeypatch):
    monkeypatch.setattr(settings, "bookstack_enabled", False)

    refused = books.capture(status=409)

    assert refused["detail"] == "BookStack ingestion is disabled"
    assert books.requests == [] and books.stored() == []


UNREADABLE = "Could not read this page from the configured BookStack instance"


@pytest.mark.parametrize(
    "reply,status,detail",
    [
        (lambda request: httpx.Response(500, json={"error": "down"}), 502, UNREADABLE),
        (
            lambda request: httpx.Response(404, json={"error": "missing"}),
            502,
            UNREADABLE,
        ),
        (lambda request: httpx.Response(200, content=b"<html>"), 502, UNREADABLE),
        (
            lambda request: httpx.Response(200, json={**PAGE, "id": 8}),
            502,
            "BookStack returned an unexpected page",
        ),
        (
            lambda request: httpx.Response(200, json=[PAGE]),
            502,
            "BookStack returned an unexpected page",
        ),
        (
            lambda request: httpx.Response(200, content=b" " * 5_000_001),
            413,
            "BookStack page snapshot exceeds 5 MB",
        ),
    ],
    ids=[
        "server-error",
        "not-found",
        "not-json",
        "other-page",
        "not-object",
        "oversized",
    ],
)
def test_unusable_bookstack_replies_store_nothing(books, reply, status, detail):
    books.upstream.reply = reply

    refused = books.capture(status=status)

    assert refused["detail"] == detail
    assert len(books.requests) == 1
    assert books.keen.db.query(BookStackSectionEvidence).count() == 0
    assert books.stored() == []


def test_missing_bookstack_credentials_stop_the_capture(books, monkeypatch):
    monkeypatch.setattr(settings, "bookstack_token_secret", " ")

    refused = books.capture(status=502)

    assert refused["detail"] == UNREADABLE
    assert books.requests == [] and books.stored() == []


def test_a_lost_snapshot_object_is_reported_as_unavailable(books):
    section = books.capture()
    (stored,) = books.stored()
    stored.unlink()

    response = books.reader.get(f"{PATH}/{section['id']}/snapshot")

    assert (response.status_code, response.json()["detail"]) == (
        502,
        "Policy snapshot storage unavailable",
    )


def test_section_routes_keep_their_permission_boundaries(books):
    section = books.capture()
    outsider = books.keen.client(books.keen.user("events.read", "audits.read"))
    anonymous = books.keen.client()

    assert outsider.get(PATH).status_code == 403
    assert anonymous.get(PATH).status_code == 401
    refused = books.capture(status=403, client=books.reader)
    assert refused["detail"] == "isms.manage permission required"
    unprotected = books.manager.post(
        PATH,
        json={
            "document_id": str(books.document.id),
            "page_id": 7,
            "target_control_id": str(books.control.id),
        },
    )
    assert unprotected.status_code == 403 and "CSRF" in unprotected.text
    assert len(books.requests) == 1  # only the first, permitted capture

    snapshot = f"{PATH}/{section['id']}/snapshot"
    # Audit readers reach a snapshot only once it is sampled into an audit.
    denied = outsider.get(snapshot)
    assert (denied.status_code, denied.json()["detail"]) == (
        403,
        "ISMS or sampled-audit read permission required",
    )
    assert anonymous.get(snapshot).status_code == 401
    assert books.reader.get(f"{PATH}/{uuid.uuid4()}/snapshot").status_code == 404
