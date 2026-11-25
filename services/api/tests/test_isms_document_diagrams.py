"""MIDENA diagrams shown with policy and process documents, through the
assembled application.

ISMS managers find a MIDENA diagram, add it to a document, refresh it to
MIDENA's current version and remove it; ISMS readers see the drawings. Keen
reads MIDENA with the configured API token through the collectors' HTTP
boundary; an in-memory HTTP transport stands in for MIDENA and answers as its
API does. MIDENA's answers are untrusted, so hostile drawings, shapes and
statuses are part of every path.
"""

import hashlib
import uuid
from types import SimpleNamespace

import httpx2 as httpx
import pytest
from app.core.config import Settings, settings
from app.db.models import EntityChangelog, IsmsDocument, IsmsDocumentDiagram
from app.services.isms import diagrams
from pydantic import ValidationError

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)

MIDENA = "https://93.184.216.34"
TOKEN = "midena_at_" + "s" * 43
DIAGRAM = uuid.UUID("7f8d0c4e-5a1b-4c3d-9e2f-0a1b2c3d4e5f")
OTHER = uuid.UUID("1b2c3d4e-5f60-4718-8293-a4b5c6d7e8f9")
SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
    '<rect width="5" height="5" fill="#fff"/><text>Start</text></svg>'
)
HOSTILE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)">'
    "<script>alert(2)</script>"
    '<foreignObject><div xmlns="http://www.w3.org/1999/xhtml">x</div></foreignObject>'
    '<a href="javascript:alert(3)"><rect width="5" height="5"/></a></svg>'
)


def detail(diagram_uuid=DIAGRAM, **fields):
    return {
        "uuid": str(diagram_uuid),
        "name": "Access review",
        "diagram_type": "bpmn",
        "description": "Quarterly review of access",
        "current_version_number": 3,
        "updated_at": "2026-11-01T10:00:00",
        "svg_xml": SVG,
        "bpmn_xml": "<definitions/>",
        "public_token": None,
        **fields,
    }


def listing(*items, total=None):
    return {
        "total": len(items) if total is None else total,
        "limit": 50,
        "offset": 0,
        "items": list(items),
    }


@pytest.fixture
def midena(keen, monkeypatch):  # noqa: F811 - fixture imported above
    monkeypatch.setattr(settings, "midena_base_url", MIDENA)
    monkeypatch.setattr(settings, "midena_api_token", TOKEN)
    requests = []
    clients = []
    upstream = SimpleNamespace(
        diagrams={DIAGRAM: detail(), OTHER: detail(OTHER, name="Onboarding")},
        reply=None,
        during=None,
    )

    def handler(request):
        requests.append(request)
        if upstream.during is not None:
            upstream.during()
        if upstream.reply is not None:
            return upstream.reply(request)
        if request.url.path == "/api/v1/diagrams":
            return httpx.Response(200, json=listing(*upstream.diagrams.values()))
        found = upstream.diagrams.get(uuid.UUID(request.url.path.rsplit("/", 1)[1]))
        if found is None:
            return httpx.Response(404, json={"detail": "Diagram not found"})
        return httpx.Response(200, json=found)

    def client(**kwargs):
        clients.append(kwargs)
        kwargs.pop("verify", None)
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(diagrams, "ingestion_client", client)
    document = IsmsDocument(title="Access control policy")
    keen.db.add(document)
    keen.db.commit()
    manager = keen.client(keen.user("isms.manage"))
    path = f"/v1/isms/documents/{document.id}/diagrams"

    def attach(diagram_uuid=DIAGRAM, status=201, client=manager):
        response = client.post(
            path, json={"diagram_uuid": str(diagram_uuid)}, headers=unsafe(client)
        )
        assert response.status_code == status, response.text
        return response.json()

    return SimpleNamespace(
        keen=keen,
        upstream=upstream,
        requests=requests,
        clients=clients,
        document=document,
        path=path,
        manager=manager,
        reader=keen.client(keen.user("isms.read")),
        attach=attach,
    )


def test_a_manager_finds_and_adds_a_diagram_and_readers_see_its_drawing(midena):
    catalog = midena.manager.get("/v1/isms/midena-diagrams?q=access")
    assert catalog.status_code == 200, catalog.text
    assert [item["uuid"] for item in catalog.json()["items"]] == [
        str(DIAGRAM),
        str(OTHER),
    ]
    (request,) = midena.requests
    assert request.url.path == "/api/v1/diagrams"
    assert dict(request.url.params) == {"q": "access", "limit": "50", "offset": "0"}
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    # MIDENA's cookies are never kept or sent back.
    assert "cookie" not in request.headers
    assert midena.clients[0]["base_url"] == f"{MIDENA}/api"
    assert midena.clients[0]["cookies"] is not None

    added = midena.attach()
    assert {
        key: added[key]
        for key in (
            "diagram_uuid",
            "name",
            "diagram_type",
            "description",
            "version_number",
            "source_updated_at",
            "midena_url",
        )
    } == {
        "diagram_uuid": str(DIAGRAM),
        "name": "Access review",
        "diagram_type": "bpmn",
        "description": "Quarterly review of access",
        "version_number": 3,
        "source_updated_at": "2026-11-01T10:00:00",
        "midena_url": f"{MIDENA}/diagram.html?id={DIAGRAM}",
    }
    assert "svg" not in added
    assert added["captured_by"]["username"].startswith("user-")

    listed = midena.reader.get(midena.path).json()
    assert listed["enabled"] is True and listed["base_url"] == MIDENA
    assert [item["id"] for item in listed["items"]] == [added["id"]]
    drawing = midena.reader.get(f"{midena.path}/{added['id']}").json()
    assert drawing["svg"].startswith("<svg")
    assert drawing["sha256"] == hashlib.sha256(drawing["svg"].encode()).hexdigest()

    # The document records the version it shows, in its own history too.
    document = midena.reader.get(f"/v1/isms/documents/{midena.document.id}").json()
    assert document["diagrams"] == [
        {
            "id": added["id"],
            "diagram_uuid": str(DIAGRAM),
            "name": "Access review",
            "version_number": 3,
            "sha256": drawing["sha256"],
        }
    ]
    history = (
        midena.keen.db.query(EntityChangelog)
        .filter(EntityChangelog.entity_id == midena.document.id)
        .all()
    )
    assert any(
        (row.after_state or {}).get("diagrams") == document["diagrams"]
        for row in history
    )


def test_readers_cannot_search_add_refresh_or_remove(midena):
    added = midena.attach()
    reader = midena.reader
    assert reader.get("/v1/isms/midena-diagrams").status_code == 403
    for method, path in (
        ("POST", midena.path),
        ("POST", f"{midena.path}/{added['id']}/refresh"),
        ("DELETE", f"{midena.path}/{added['id']}"),
    ):
        response = reader.request(
            method, path, json={"diagram_uuid": str(OTHER)}, headers=unsafe(reader)
        )
        assert response.status_code == 403, path
    outsider = midena.keen.client(midena.keen.user())
    assert outsider.get(midena.path).status_code == 403
    assert outsider.get(f"{midena.path}/{added['id']}").status_code == 403


def test_the_drawing_is_rebuilt_before_it_is_stored(midena):
    midena.upstream.diagrams[DIAGRAM] = detail(svg_xml=HOSTILE_SVG)
    added = midena.attach()
    svg = midena.reader.get(f"{midena.path}/{added['id']}").json()["svg"]
    for hostile in ("onload", "script", "foreignObject", "javascript:", "alert"):
        assert hostile not in svg
    assert "<rect" in svg


@pytest.mark.parametrize(
    "drawing, status, detail_text",
    [
        (None, 409, "This diagram has no saved drawing in MIDENA yet"),
        ("   ", 409, "This diagram has no saved drawing in MIDENA yet"),
        ("<html/>", 502, "MIDENA returned a drawing Keen cannot show"),
        ("<svg", 502, "MIDENA returned a drawing Keen cannot show"),
    ],
)
def test_unusable_drawings_are_refused(midena, drawing, status, detail_text):
    midena.upstream.diagrams[DIAGRAM] = detail(svg_xml=drawing)
    response = midena.attach(status=status)
    assert response["detail"].startswith(detail_text)
    assert midena.keen.db.query(IsmsDocumentDiagram).count() == 0


@pytest.mark.parametrize(
    "answer, status, detail_text",
    [
        (httpx.Response(401), 502, "MIDENA refused Keen's API token"),
        (httpx.Response(403), 502, "MIDENA refused Keen's API token"),
        (httpx.Response(404), 404, "MIDENA has no such diagram"),
        (
            httpx.Response(500, text="secret trace"),
            502,
            "MIDENA answered with status 500",
        ),
        (httpx.Response(200, text="not json"), 502, "Could not read from"),
        (httpx.Response(200, json=[1]), 502, "MIDENA returned an unexpected diagram"),
        (
            httpx.Response(200, json=detail(OTHER)),
            502,
            "MIDENA returned an unexpected diagram",
        ),
    ],
)
def test_midena_failures_are_named_without_its_body(
    midena, answer, status, detail_text
):
    midena.upstream.reply = lambda request: answer
    response = midena.attach(status=status)
    assert response["detail"].startswith(detail_text)
    assert "secret trace" not in str(response)


def test_an_unreachable_midena_is_reported(midena):
    def refuse(request):
        raise httpx.ConnectError("no route", request=request)

    midena.upstream.reply = refuse
    response = midena.manager.get("/v1/isms/midena-diagrams")
    assert response.status_code == 502
    assert response.json()["detail"] == (
        "Could not read from the configured MIDENA installation"
    )


def test_the_catalog_keeps_only_usable_entries_clipped_to_size(midena):
    midena.upstream.reply = lambda request: httpx.Response(
        200,
        json={
            "total": True,
            "items": [
                "not an object",
                {"uuid": "not-a-uuid", "name": "x"},
                {
                    "uuid": str(DIAGRAM),
                    "name": "n" * 400,
                    "diagram_type": 7,
                    "folder_name": "",
                    "current_version_number": -1,
                    "updated_at": None,
                },
                {
                    "uuid": str(OTHER),
                    "name": "",
                    "current_version_number": True,
                },
            ],
        },
    )
    page = midena.manager.get("/v1/isms/midena-diagrams?offset=20").json()
    assert page["total"] == 2
    assert page["offset"] == 20
    assert page["items"] == [
        {
            "uuid": str(DIAGRAM),
            "name": "n" * 256,
            "diagram_type": "",
            "folder_name": None,
            "version_number": None,
            "updated_at": "",
        },
        {
            "uuid": str(OTHER),
            "name": "Untitled diagram",
            "diagram_type": "",
            "folder_name": None,
            "version_number": None,
            "updated_at": "",
        },
    ]
    midena.upstream.reply = lambda request: httpx.Response(200, json={"items": {}})
    assert midena.manager.get("/v1/isms/midena-diagrams").status_code == 502
    midena.upstream.reply = lambda request: httpx.Response(200, json=[])
    assert midena.manager.get("/v1/isms/midena-diagrams").status_code == 502
    # The offset is bounded at the route.
    assert (
        midena.manager.get("/v1/isms/midena-diagrams?offset=10001").status_code == 422
    )


def test_a_document_shows_each_diagram_once_and_a_bounded_number(midena, monkeypatch):
    midena.attach()
    assert midena.attach(status=409)["detail"] == (
        "This document already shows that diagram"
    )
    monkeypatch.setattr(diagrams, "MAX_DIAGRAMS_PER_DOCUMENT", 1)
    assert midena.attach(OTHER, status=409)["detail"] == (
        "A document shows at most 1 diagrams"
    )


def test_a_capture_finished_while_midena_answered_is_not_repeated(midena):
    db = midena.keen.db

    def captured_meanwhile():
        midena.upstream.during = None
        db.add(
            IsmsDocumentDiagram(
                document_id=midena.document.id,
                diagram_uuid=DIAGRAM,
                name="Access review",
                diagram_type="bpmn",
                svg=SVG,
                sha256="0" * 64,
            )
        )
        db.commit()

    midena.upstream.during = captured_meanwhile
    assert midena.attach(status=409)["detail"] == (
        "This document already shows that diagram"
    )
    assert db.query(IsmsDocumentDiagram).count() == 1


def test_refreshing_captures_midenas_current_version(midena):
    added = midena.attach()
    refresh = f"{midena.path}/{added['id']}/refresh"
    manager = midena.manager

    # Unchanged in MIDENA: the capture time moves on, the history does not.
    def changes():
        return (
            midena.keen.db.query(EntityChangelog)
            .filter(EntityChangelog.entity_id == midena.document.id)
            .count()
        )

    before = changes()
    same = manager.post(refresh, headers=unsafe(manager))
    assert same.status_code == 200, same.text
    assert same.json()["version_number"] == 3
    assert changes() == before

    newer = SVG.replace("Start", "Begin")
    midena.upstream.diagrams[DIAGRAM] = detail(
        current_version_number=4, svg_xml=newer, name="Access review v4"
    )
    updated = manager.post(refresh, headers=unsafe(manager)).json()
    assert (updated["version_number"], updated["name"]) == (4, "Access review v4")
    assert updated["id"] == added["id"]
    assert changes() == before + 1
    svg = midena.reader.get(f"{midena.path}/{added['id']}").json()["svg"]
    assert "Begin" in svg

    # Gone from MIDENA: the document keeps showing the version it captured.
    del midena.upstream.diagrams[DIAGRAM]
    gone = manager.post(refresh, headers=unsafe(manager))
    assert gone.status_code == 404
    assert "Begin" in midena.reader.get(f"{midena.path}/{added['id']}").json()["svg"]


def test_removing_a_diagram_and_unknown_diagrams(midena):
    added = midena.attach()
    manager = midena.manager
    other_document = IsmsDocument(title="Other")
    midena.keen.db.add(other_document)
    midena.keen.db.commit()
    elsewhere = f"/v1/isms/documents/{other_document.id}/diagrams/{added['id']}"
    # A diagram is reached only through the document that shows it.
    assert midena.reader.get(elsewhere).status_code == 404
    assert manager.delete(elsewhere, headers=unsafe(manager)).status_code == 404
    assert midena.reader.get(f"{midena.path}/not-a-uuid").status_code == 404
    unknown_document = f"/v1/isms/documents/{uuid.uuid4()}/diagrams"
    assert midena.reader.get(unknown_document).status_code == 404

    removed = manager.delete(f"{midena.path}/{added['id']}", headers=unsafe(manager))
    assert removed.status_code == 200
    assert midena.reader.get(midena.path).json()["items"] == []
    document = midena.reader.get(f"/v1/isms/documents/{midena.document.id}").json()
    assert document["diagrams"] == []


def test_deleting_a_document_removes_what_it_showed(midena):
    midena.attach()
    manager = midena.manager
    response = manager.delete(
        f"/v1/isms/documents/{midena.document.id}", headers=unsafe(manager)
    )
    assert response.status_code == 200, response.text
    assert midena.keen.db.query(IsmsDocumentDiagram).count() == 0


def test_without_midena_configured_nothing_reaches_it(midena, monkeypatch):
    added = midena.attach()
    monkeypatch.setattr(settings, "midena_base_url", "")
    monkeypatch.setattr(settings, "midena_api_token", "")
    manager = midena.manager
    calls = len(midena.requests)
    for response in (
        manager.get("/v1/isms/midena-diagrams"),
        manager.post(
            midena.path, json={"diagram_uuid": str(OTHER)}, headers=unsafe(manager)
        ),
        manager.post(f"{midena.path}/{added['id']}/refresh", headers=unsafe(manager)),
    ):
        assert response.status_code == 409
        assert response.json()["detail"] == "MIDENA is not configured for this Keen"
    assert len(midena.requests) == calls
    # What a document already shows stays readable, without a MIDENA link.
    listed = midena.reader.get(midena.path).json()
    assert listed["enabled"] is False and listed["base_url"] is None
    assert listed["items"][0]["midena_url"] is None
    assert (
        manager.delete(
            f"{midena.path}/{added['id']}", headers=unsafe(manager)
        ).status_code
        == 200
    )


def test_the_signed_in_account_learns_whether_midena_is_configured(midena):
    assert midena.reader.get("/v1/me").json()["midena_enabled"] is True


# Configuration.


def test_midena_is_configured_with_both_settings_or_neither(monkeypatch):
    monkeypatch.setenv("KEEN_MIDENA_BASE_URL", "https://midena.example/ ")
    monkeypatch.setenv("KEEN_MIDENA_API_TOKEN", f" {TOKEN} ")
    config = Settings()
    assert config.midena_base_url == "https://midena.example"
    assert config.midena_api_token == TOKEN
    assert config.midena_enabled is True
    monkeypatch.delenv("KEEN_MIDENA_API_TOKEN")
    with pytest.raises(ValidationError, match="together"):
        Settings()
    monkeypatch.delenv("KEEN_MIDENA_BASE_URL")
    assert Settings().midena_enabled is False


@pytest.mark.parametrize(
    "url",
    [
        "http://midena.example",
        "https://",
        "https://user:pass@midena.example",
        "https://midena.example?x=1",
        "https://midena.example#top",
        "https://midena.example/a b",
        "https://midena.example\\evil",
    ],
)
def test_the_midena_address_is_https_without_credentials(monkeypatch, url):
    monkeypatch.setenv("KEEN_MIDENA_BASE_URL", url)
    monkeypatch.setenv("KEEN_MIDENA_API_TOKEN", TOKEN)
    with pytest.raises(ValidationError, match="KEEN_MIDENA_BASE_URL"):
        Settings()


@pytest.mark.parametrize("token", ["two words", "x" * 257, "tab\tinside", "é" * 4])
def test_the_midena_token_is_kept_as_issued(monkeypatch, token):
    monkeypatch.setenv("KEEN_MIDENA_BASE_URL", "https://midena.example")
    monkeypatch.setenv("KEEN_MIDENA_API_TOKEN", token)
    with pytest.raises(ValidationError, match="KEEN_MIDENA_API_TOKEN"):
        Settings()
