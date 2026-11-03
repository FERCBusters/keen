"""Downloads are served with a passive media type, whatever an uploader declared.

An upload's recorded media type comes from its uploader. Served unchanged as
text/javascript or text/css, a downloaded file would be same-origin script or
style for any page that loaded it, past a script-src 'self' policy; nosniff and
Content-Disposition: attachment do not stop a <script src> load. Every download
therefore keeps its type only when it is one a browser merely displays or saves.
A contract test refuses any route that sets a computed media type another way.
"""

import ast
import uuid
from pathlib import Path

import pytest
from app.db.models import Artifact, Framework
from app.storage.filenames import PASSIVE_DOWNLOAD_TYPES, attachment_media_type

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)

ROUTES = Path(__file__).resolve().parents[1] / "app" / "api" / "routes"
ACTIVE = [
    "text/javascript",
    "application/javascript",
    "TEXT/JAVASCRIPT; charset=utf-8",
    "text/css",
    "text/html",
    "image/svg+xml",
    "application/xhtml+xml",
    "text/xml",
    "application/wasm",
    "",
    None,
    "nonsense",
]


@pytest.mark.parametrize("declared", ACTIVE)
def test_active_or_unknown_types_are_served_as_octet_stream(declared):
    assert attachment_media_type(declared) == "application/octet-stream"


@pytest.mark.parametrize("declared", sorted(PASSIVE_DOWNLOAD_TYPES))
def test_passive_types_keep_their_type_without_parameters(declared):
    assert attachment_media_type(declared.upper() + "; name=x") == declared


def _offenders(tree, label):
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", getattr(node.func, "attr", None))
        if name not in {"Response", "StreamingResponse", "FileResponse"}:
            continue
        for keyword in node.keywords:
            if keyword.arg != "media_type":
                continue
            value = keyword.value
            constant = isinstance(value, ast.Constant) and isinstance(value.value, str)
            named = isinstance(value, (ast.Name, ast.Attribute)) and (
                getattr(value, "id", getattr(value, "attr", "")).isupper()
            )
            vetted = (
                isinstance(value, ast.Call)
                and getattr(value.func, "id", None) == "attachment_media_type"
            )
            if not (constant or named or vetted):
                found.add(f"{label}:{node.lineno}")
    return found


def test_the_detector_finds_a_computed_media_type():
    bad = "def f(row):\n    return StreamingResponse(b, media_type=row.content_type)\n"
    good = (
        "def f(row):\n"
        "    StreamingResponse(b, media_type=attachment_media_type(row.content_type))\n"
        "    Response(b, media_type='application/json')\n"
        "    Response(b, media_type=keys.SEALED_RESPONSE)\n"
    )
    assert _offenders(ast.parse(bad), "bad") == {"bad:2"}
    assert _offenders(ast.parse(good), "good") == set()


def test_every_route_sets_a_computed_media_type_through_the_vetting_function():
    offenders = set()
    for path in ROUTES.rglob("*.py"):
        offenders |= _offenders(ast.parse(path.read_text()), path.name)
    assert sorted(offenders) == []


SCRIPT = b"alert(document.domain)\n"


def test_an_isms_document_declared_as_script_downloads_as_octet_stream(keen):  # noqa: F811
    client = keen.client(keen.user("isms.manage"))
    document = client.post(
        "/v1/isms/documents", json={"title": "Records policy"}, headers=unsafe(client)
    ).json()
    path = f"/v1/isms/documents/{document['id']}/file"

    for declared, served in (
        ("text/javascript", "application/octet-stream"),
        ("text/css", "application/octet-stream"),
        ("application/pdf", "application/pdf"),
    ):
        uploaded = client.post(
            path, files={"file": ("f.js", SCRIPT, declared)}, headers=unsafe(client)
        )
        assert uploaded.status_code == 200, uploaded.text
        download = client.get(path)
        assert download.status_code == 200
        assert download.headers["content-type"] == served
        assert download.headers["x-content-type-options"] == "nosniff"
        assert download.headers["content-disposition"].startswith("attachment;")
        assert download.content == SCRIPT


def test_an_audit_report_declared_as_script_downloads_as_octet_stream(keen):  # noqa: F811
    keen.db.add(Framework(slug="fw-media"))
    keen.db.commit()
    client = keen.client(keen.user("audits.manage"))
    audit = client.post(
        "/v1/audits",
        json={"title": "Annual review", "framework_slug": "fw-media"},
        headers=unsafe(client),
    ).json()
    uploaded = client.post(
        f"/v1/audits/{audit['id']}/report",
        files={"file": ("report.js", SCRIPT, "application/javascript")},
        headers=unsafe(client),
    )
    assert uploaded.status_code == 200, uploaded.text

    download = client.get(f"/v1/audits/{audit['id']}/report")
    assert download.headers["content-type"] == "application/octet-stream"
    assert download.content == SCRIPT


def test_a_diary_attachment_declared_as_script_downloads_as_octet_stream(keen):  # noqa: F811
    client = keen.client(keen.user(role="admin"))
    created = client.post(
        "/v1/admin/diary",
        data={"summary": "Policy signed"},
        files={"attachments": ("note.js", SCRIPT, "text/javascript")},
        headers=unsafe(client),
    )
    assert created.status_code == 200, created.text
    keen.db.expire_all()
    (artifact,) = (
        keen.db.query(Artifact)
        .filter(
            Artifact.event_id == uuid.UUID(created.json()["event_id"]),
            Artifact.kind != "diary_entry",
        )
        .all()
    )
    assert artifact.content_type == "text/javascript"  # recorded as declared

    download = client.get(f"/v1/artifacts/{artifact.id}/download")
    assert download.status_code == 200, download.text
    assert download.headers["content-type"] == "application/octet-stream"
