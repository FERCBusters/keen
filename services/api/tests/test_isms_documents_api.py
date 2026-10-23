"""ISMS documents, folders, comments and files through the assembled application.

Readers with isms.read browse documents, revisions, comments and files, and
isms.manage covers every change. A text save names the content version its
editor opened, so a stale save is refused rather than overwriting another
editor's work. Requests pass through the production middleware, password
sign-in, CSRF and permission resolution.
"""

import hashlib
import uuid
from types import SimpleNamespace

import pytest
from app.db.models import (
    BookStackSectionEvidence,
    ControlItem,
    EntityChangelog,
    Framework,
    IsmsDocument,
    IsmsDocumentComment,
    IsmsDocumentRevision,
)
from sqlalchemy import text

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)

DOCS = "/v1/isms/documents"
FOLDERS = "/v1/isms/document-folders"
READ = "isms.read permission required"
MANAGE = "isms.manage permission required"
STALE = "Document changed since it was opened. Reload before saving."
TAGS = "Use at most 30 tags, each under 65 characters"
DISABLED = "This feature is disabled by an administrator."


def sha(html):
    return hashlib.sha256(html.encode("utf-8")).hexdigest()


@pytest.fixture
def docs(keen):  # noqa: F811 - fixture imported above
    manager_user = keen.user("isms.manage")
    manager = keen.client(manager_user)
    reader = keen.client(keen.user("isms.read"))

    def call(client, method, path, status=200, **kwargs):
        if method != "GET":
            kwargs.setdefault("headers", unsafe(client))
        response = client.request(method, path, **kwargs)
        assert response.status_code == status, response.text
        return response.json()

    def create(**body):
        return call(manager, "POST", DOCS, json={"title": "Access policy", **body})

    def folder(name, parent_id=None):
        return call(
            manager, "POST", FOLDERS, json={"name": name, "parent_id": parent_id}
        )

    def upload(document_id, filename, data, content_type, status=200):
        return call(
            manager,
            "POST",
            f"{DOCS}/{document_id}/file",
            status,
            files={"file": (filename, data, content_type)},
        )

    def row(document_id):
        keen.db.expire_all()
        return keen.db.get(IsmsDocument, uuid.UUID(document_id))

    def changelog(document_id):
        keen.db.expire_all()
        rows = (
            keen.db.query(EntityChangelog)
            .filter_by(entity_id=uuid.UUID(document_id))
            .order_by(EntityChangelog.changed_at)
        )
        return [entry.action for entry in rows]

    def queued():
        rows = keen.db.execute(
            text("SELECT storage_uri FROM evidence_object_cleanup ORDER BY id")
        )
        return list(rows.scalars())

    return SimpleNamespace(
        keen=keen,
        manager=manager,
        manager_user=manager_user,
        reader=reader,
        call=call,
        create=create,
        folder=folder,
        upload=upload,
        row=row,
        changelog=changelog,
        queued=queued,
    )


# --- Folders ------------------------------------------------------------------


def test_folders_form_a_named_tree_that_refuses_cycles(docs):
    policies = docs.folder("  Policies  ")
    hr = docs.folder("HR", policies["id"])
    assert policies == {"id": policies["id"], "name": "Policies", "parent_id": None}
    assert hr == {"id": hr["id"], "name": "HR", "parent_id": policies["id"]}
    tree = docs.call(docs.reader, "GET", FOLDERS)
    assert tree == [hr, policies]

    # A folder cannot sit inside itself or inside one of its own descendants.
    for parent in (policies["id"], hr["id"]):
        refused = docs.call(
            docs.manager,
            "PATCH",
            f"{FOLDERS}/{policies['id']}",
            400,
            json={"name": "Policies", "parent_id": parent},
        )
        assert refused["detail"] == "Parent relationship would contain a cycle"
    unknown = str(uuid.uuid4())
    for method, path in (("POST", FOLDERS), ("PATCH", f"{FOLDERS}/{hr['id']}")):
        refused = docs.call(
            docs.manager, method, path, 400, json={"name": "HR", "parent_id": unknown}
        )
        assert refused["detail"] == "Unknown document folder"
    for method, path in (("POST", FOLDERS), ("PATCH", f"{FOLDERS}/{hr['id']}")):
        refused = docs.call(docs.manager, method, path, 400, json={"name": "   "})
        assert refused["detail"] == "Folder name is required"
    missing = docs.call(
        docs.manager, "PATCH", f"{FOLDERS}/{unknown}", 404, json={"name": "HR"}
    )
    assert missing["detail"] == "Folder not found"
    assert docs.call(docs.reader, "GET", FOLDERS) == tree

    moved = docs.call(
        docs.manager,
        "PATCH",
        f"{FOLDERS}/{hr['id']}",
        json={"name": " People ", "parent_id": None},
    )
    assert moved == {"id": hr["id"], "name": "People", "parent_id": None}
    docs.call(
        docs.manager,
        "PATCH",
        f"{FOLDERS}/{policies['id']}",
        json={"name": "Policies", "parent_id": hr["id"]},
    )
    assert docs.call(docs.reader, "GET", FOLDERS) == [
        {"id": hr["id"], "name": "People", "parent_id": None},
        {"id": policies["id"], "name": "Policies", "parent_id": hr["id"]},
    ]


def test_a_folder_is_deleted_only_once_it_is_empty(docs):
    root = docs.folder("Policies")
    child = docs.folder("HR", root["id"])
    document = docs.create(folder_id=child["id"])

    # The root holds a folder and the child holds a document.
    for folder_id in (root["id"], child["id"]):
        refused = docs.call(docs.manager, "DELETE", f"{FOLDERS}/{folder_id}", 409)
        assert refused["detail"] == (
            "Move documents and child folders before deleting this folder"
        )
    docs.call(
        docs.manager, "PATCH", f"{DOCS}/{document['id']}", json={"folder_id": None}
    )
    for folder_id in (child["id"], root["id"]):
        assert docs.call(docs.manager, "DELETE", f"{FOLDERS}/{folder_id}") == {
            "ok": True
        }

    assert docs.call(docs.reader, "GET", FOLDERS) == []
    missing = docs.call(docs.manager, "DELETE", f"{FOLDERS}/{root['id']}", 404)
    assert missing["detail"] == "Folder not found"
    assert docs.call(docs.reader, "GET", f"{DOCS}/{document['id']}")["title"] == (
        "Access policy"
    )


# --- Access ---------------------------------------------------------------------


def test_reading_needs_isms_read_and_every_change_needs_isms_manage(docs):
    document = docs.create(content_html="<p>Text</p>")
    folder = docs.folder("Policies")
    item = f"{DOCS}/{document['id']}"
    outsider = docs.keen.client(docs.keen.user("events.read"))
    for path in (
        FOLDERS,
        DOCS,
        item,
        item + "/revisions",
        item + "/revisions/1",
        item + "/comments",
    ):
        assert docs.reader.get(path).status_code == 200, path
        refused = outsider.get(path)
        assert (refused.status_code, refused.json()["detail"]) == (403, READ), path

    changes = [
        ("POST", FOLDERS, {"json": {"name": "Records"}}),
        ("PATCH", f"{FOLDERS}/{folder['id']}", {"json": {"name": "Records"}}),
        ("DELETE", f"{FOLDERS}/{folder['id']}", {}),
        ("POST", DOCS, {"json": {"title": "Backup standard"}}),
        ("PATCH", item, {"json": {"title": "Renamed"}}),
        ("DELETE", item, {}),
        ("POST", item + "/comments", {"json": {"body": "Looks good"}}),
        ("POST", item + "/file", {"files": {"file": ("a.txt", b"a", "text/plain")}}),
    ]
    for method, path, kwargs in changes:
        refused = docs.reader.request(
            method, path, headers=unsafe(docs.reader), **kwargs
        )
        assert (refused.status_code, refused.json()["detail"]) == (403, MANAGE), path

    assert docs.call(docs.reader, "GET", item) == document
    assert docs.call(docs.reader, "GET", FOLDERS) == [folder]
    assert docs.call(docs.reader, "GET", item + "/comments") == []
    assert docs.changelog(document["id"]) == ["created"]


def test_switching_documents_off_hides_documents_and_folders(docs):
    admin = docs.keen.client(docs.keen.user(role="admin"))

    def switch(**changes):
        version = admin.get("/v1/admin/features").json()["version"]
        response = admin.put(
            "/v1/admin/features",
            json={"version": version, "changes": changes},
            headers=unsafe(admin),
        )
        assert response.status_code == 200, response.text

    document = docs.create()
    item = f"{DOCS}/{document['id']}"
    switch(documents=False)

    for path in (FOLDERS, DOCS, item, item + "/comments", item + "/file"):
        refused = docs.reader.get(path)
        assert (refused.status_code, refused.json()["detail"]) == (404, DISABLED)
    refused = docs.manager.post(
        DOCS, json={"title": "Backup standard"}, headers=unsafe(docs.manager)
    )
    assert (refused.status_code, refused.json()["detail"]) == (404, DISABLED)

    switch(documents=True)
    assert docs.call(docs.reader, "GET", DOCS)["items"] == [document]


# --- Document records -----------------------------------------------------------


def test_a_document_keeps_its_cleaned_record_fields(docs):
    folder = docs.folder("Policies")
    created = docs.create(
        title="  Access policy  ",
        document_type=" Procedure ",
        description="  Who may sign in  ",
        external_url="https://wiki.example/access",
        folder_id=folder["id"],
        tags=[" access ", "access", "", "   ", "identity"],
    )

    assert {key: created[key] for key in RECORD_FIELDS} == {
        "title": "Access policy",
        "document_type": "procedure",
        "description": "Who may sign in",
        "external_url": "https://wiki.example/access",
        "folder_id": folder["id"],
        "tags": ["access", "identity"],
        "content_html": "",
        "content_version": 0,
        "has_file": False,
        "filename": None,
        "display": "Access policy",
    }
    assert docs.call(docs.reader, "GET", f"{DOCS}/{created['id']}") == created
    # A record without text has no revisions.
    assert docs.call(docs.reader, "GET", f"{DOCS}/{created['id']}/revisions") == []
    # Policy is the type when none is chosen.
    assert docs.create(title="Plain")["document_type"] == "policy"
    assert docs.create(title="Blank", document_type="  ")["document_type"] == "policy"
    assert docs.create(title="No link", external_url="  ")["external_url"] is None
    assert docs.changelog(created["id"]) == ["created"]


RECORD_FIELDS = (
    "title",
    "document_type",
    "description",
    "external_url",
    "folder_id",
    "tags",
    "content_html",
    "content_version",
    "has_file",
    "filename",
    "display",
)


INVALID = [
    ("no-title", {"title": "   "}, "title is required"),
    ("unknown-type", {"document_type": "memo"}, "Invalid document_type"),
    ("unknown-folder", {"folder_id": str(uuid.uuid4())}, "Unknown document folder"),
    ("too-many-tags", {"tags": [f"tag-{i}" for i in range(31)]}, TAGS),
    ("long-tag", {"tags": ["x" * 65]}, TAGS),
]


@pytest.mark.parametrize(
    "change,detail",
    [(change, detail) for _, change, detail in INVALID],
    ids=[name for name, _, _ in INVALID],
)
def test_invalid_records_are_refused_on_create_and_update(docs, change, detail):
    original = docs.create(tags=["kept"])

    created = docs.call(
        docs.manager, "POST", DOCS, 400, json={"title": "New", **change}
    )
    updated = docs.call(
        docs.manager, "PATCH", f"{DOCS}/{original['id']}", 400, json=change
    )

    assert (created["detail"], updated["detail"]) == (detail, detail)
    assert docs.call(docs.reader, "GET", DOCS)["items"] == [original]
    assert docs.changelog(original["id"]) == ["created"]


def test_thirty_tags_of_sixty_four_characters_are_accepted(docs):
    tags = [f"{i:02d}".ljust(64, "x") for i in range(30)]

    assert docs.create(tags=tags)["tags"] == tags


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "https://user:pw@wiki.example/", "ftp://wiki.example/a"],
    ids=["script", "credentials", "ftp"],
)
def test_a_link_must_be_a_plain_web_address(docs, url):
    refused = docs.manager.post(
        DOCS,
        json={"title": "Linked", "external_url": url},
        headers=unsafe(docs.manager),
    )

    assert refused.status_code == 422
    assert url not in refused.text
    assert docs.call(docs.reader, "GET", DOCS)["total"] == 0


def test_an_update_changes_only_the_fields_it_names(docs):
    folder = docs.folder("Policies")
    original = docs.create(
        description="Who may sign in",
        external_url="https://wiki.example/access",
        folder_id=folder["id"],
        tags=["access"],
        content_html="<p>Text</p>",
    )
    path = f"{DOCS}/{original['id']}"

    described = docs.call(
        docs.manager, "PATCH", path, json={"description": "Who may sign in, and how"}
    )
    cleared = docs.call(
        docs.manager,
        "PATCH",
        path,
        json={"external_url": "", "folder_id": None, "tags": []},
    )

    assert described == {
        **original,
        "description": "Who may sign in, and how",
        "updated_at": described["updated_at"],
    }
    assert cleared == {
        **described,
        "external_url": None,
        "folder_id": None,
        "tags": [],
        "updated_at": cleared["updated_at"],
    }
    assert docs.changelog(original["id"]) == ["created", "updated", "updated"]


def test_text_saves_name_the_version_the_editor_opened(docs):
    created = docs.create(content_html="<p>One</p>")
    path = f"{DOCS}/{created['id']}"
    assert (created["content_html"], created["content_version"]) == ("<p>One</p>", 1)

    # A save without the opened version, or with an older one, is refused
    # whole: the title in the same request stays as it was.
    for body in (
        {"content_html": "<p>Two</p>"},
        {"content_html": "<p>Two</p>", "expected_content_version": 0, "title": "New"},
    ):
        refused = docs.call(docs.manager, "PATCH", path, 409, json=body)
        assert refused["detail"] == STALE
    assert docs.call(docs.reader, "GET", path) == created

    # Text that sanitises to the saved text adds no version.
    same = docs.call(
        docs.manager,
        "PATCH",
        path,
        json={"content_html": "<p>One</p>\n", "expected_content_version": 1},
    )
    assert (same["content_html"], same["content_version"]) == ("<p>One</p>", 1)

    saved = docs.call(
        docs.manager,
        "PATCH",
        path,
        json={
            "content_html": '<p onclick="steal()">Two</p><img src=x onerror=alert(1)>',
            "expected_content_version": 1,
        },
    )
    assert (saved["content_html"], saved["content_version"]) == ("<p>Two</p>", 2)
    stale = docs.call(
        docs.manager,
        "PATCH",
        path,
        409,
        json={"content_html": "<p>Three</p>", "expected_content_version": 1},
    )
    assert stale["detail"] == STALE

    editor = str(docs.manager_user.id)
    revisions = docs.call(docs.reader, "GET", path + "/revisions")
    assert [
        (r["version"], r["sha256"], r["created_by_user_id"]) for r in revisions
    ] == [
        (2, sha("<p>Two</p>"), editor),
        (1, sha("<p>One</p>"), editor),
    ]
    first = docs.call(docs.reader, "GET", path + "/revisions/1")
    assert (first["version"], first["content_html"], first["sha256"]) == (
        1,
        "<p>One</p>",
        sha("<p>One</p>"),
    )
    missing = docs.call(docs.reader, "GET", path + "/revisions/3", 404)
    assert missing["detail"] == "Revision not found"
    assert docs.call(docs.reader, "GET", path)["content_html"] == "<p>Two</p>"
    # The unchanged save recorded nothing.
    assert docs.changelog(created["id"]) == ["created", "updated"]


def test_the_list_searches_titles_descriptions_and_links_in_pages(docs):
    access = docs.create(title="Access policy", description="Who may sign in")
    docs.create(title="Backup standard", external_url="https://wiki.example/backups")
    docs.create(title="Clean desk", description="Paper records")

    def titles(**params):
        body = docs.call(docs.reader, "GET", DOCS, params=params)
        return body["total"], [item["title"] for item in body["items"]]

    # The most recently updated document comes first.
    assert titles() == (3, ["Clean desk", "Backup standard", "Access policy"])
    assert titles(q="SIGN IN") == (1, ["Access policy"])
    assert titles(q="wiki.example/backups") == (1, ["Backup standard"])
    assert titles(q="  desk  ") == (1, ["Clean desk"])
    assert titles(q="   ") == titles()
    assert titles(q="Retention") == (0, [])

    docs.call(
        docs.manager, "PATCH", f"{DOCS}/{access['id']}", json={"description": "MFA"}
    )
    assert titles() == (3, ["Access policy", "Clean desk", "Backup standard"])
    page = docs.call(docs.reader, "GET", DOCS, params={"limit": 1, "offset": 1})
    assert (page["total"], page["limit"], page["offset"]) == (3, 1, 1)
    assert [item["title"] for item in page["items"]] == ["Clean desk"]
    for limit, used in ((0, 200), (-3, 1), (5000, 1000)):
        body = docs.call(docs.reader, "GET", DOCS, params={"limit": limit})
        assert body["limit"] == used
    assert docs.call(docs.reader, "GET", DOCS, params={"offset": -2})["offset"] == 0


# --- Comments -------------------------------------------------------------------


def test_comments_are_listed_oldest_first_with_their_authors(docs):
    document = docs.create()
    path = f"{DOCS}/{document['id']}/comments"

    first = docs.call(
        docs.manager, "POST", path, json={"body": "  Please review section 2  "}
    )
    second = docs.call(docs.manager, "POST", path, json={"body": "Reviewed"})
    blank = docs.call(docs.manager, "POST", path, 400, json={"body": "   "})

    assert first["body"] == "Please review section 2"
    assert blank["detail"] == "Comment cannot be empty"
    author = {
        "id": str(docs.manager_user.id),
        "username": docs.manager_user.username,
        "email": None,
    }
    assert docs.call(docs.reader, "GET", path) == [
        {
            "id": first["id"],
            "body": "Please review section 2",
            "author": author,
            "created_at": first["created_at"],
        },
        {
            "id": second["id"],
            "body": "Reviewed",
            "author": author,
            "created_at": second["created_at"],
        },
    ]


def test_unknown_documents_have_no_comments_or_revisions(docs):
    unknown = f"{DOCS}/{uuid.uuid4()}"

    for method, path, kwargs in (
        ("GET", unknown + "/comments", {}),
        ("POST", unknown + "/comments", {"json": {"body": "Hello"}}),
        ("GET", unknown + "/revisions", {}),
        ("GET", unknown, {}),
        ("PATCH", unknown, {"json": {"title": "Renamed"}}),
        ("DELETE", unknown, {}),
        ("GET", unknown + "/file", {}),
    ):
        refused = docs.call(docs.manager, method, path, 404, **kwargs)
        assert refused["detail"] == "Document not found", (method, path)
    malformed = docs.call(docs.manager, "GET", f"{DOCS}/policy-1", 400)
    assert malformed["detail"] == "Document id must be a UUID"
    assert docs.call(docs.manager, "GET", unknown + "/revisions/1", 404)["detail"] == (
        "Revision not found"
    )


# --- Files ----------------------------------------------------------------------


def test_an_uploaded_file_downloads_unchanged(docs):
    document = docs.create()
    path = f"{DOCS}/{document['id']}/file"
    missing = docs.call(docs.reader, "GET", path, 404)
    assert missing["detail"] == "No file uploaded"
    pdf = b"%PDF-1.4\n% synthetic policy\n"

    uploaded = docs.upload(
        document["id"], "../../Access Policy.PDF", pdf, "application/pdf"
    )

    record = uploaded["document"]
    assert uploaded["ok"] is True
    assert {key: record[key] for key in FILE_FIELDS} == {
        "has_file": True,
        "filename": "Access Policy.PDF",
        "content_type": "application/pdf",
        "size_bytes": len(pdf),
    }
    assert record["uploaded_by"]["id"] == str(docs.manager_user.id)
    stored = docs.row(document["id"])
    assert stored.sha256 == hashlib.sha256(pdf).hexdigest()
    # The stored name is Keen's own, under the document, keeping a safe extension.
    prefix = f"local://local/isms-documents/{document['id']}/"
    assert stored.storage_uri.startswith(prefix)
    assert stored.storage_uri.endswith(".pdf")
    assert "Access" not in stored.storage_uri
    assert docs.queued() == []

    download = docs.reader.get(path)
    assert download.status_code == 200
    assert download.content == pdf
    assert download.headers["content-type"] == "application/pdf"
    assert download.headers["content-disposition"] == (
        'attachment; filename="Access Policy.PDF"; '
        "filename*=UTF-8''Access%20Policy.PDF"
    )
    assert docs.changelog(document["id"]) == ["created", "updated"]


FILE_FIELDS = ("has_file", "filename", "content_type", "size_bytes")


def test_a_replacement_file_retires_the_previous_one(docs):
    document = docs.create()
    path = f"{DOCS}/{document['id']}/file"
    docs.upload(document["id"], "policy.txt", b"first", "text/plain")
    first = docs.row(document["id"]).storage_uri

    docs.upload(document["id"], "policy.md", b"second", "text/markdown")
    empty = docs.upload(document["id"], "empty.txt", b"", "text/plain", status=400)

    assert empty["detail"] == "Uploaded file is empty"
    assert docs.reader.get(path).content == b"second"
    current = docs.row(document["id"])
    assert (current.filename, current.size_bytes) == ("policy.md", 6)
    assert current.storage_uri != first
    assert docs.queued() == [first]


# --- Deletion -------------------------------------------------------------------


def test_deleting_a_document_retires_its_file_and_history(docs):
    document = docs.create(content_html="<p>Text</p>")
    item = f"{DOCS}/{document['id']}"
    docs.call(docs.manager, "POST", item + "/comments", json={"body": "Noted"})
    docs.upload(document["id"], "policy.txt", b"policy", "text/plain")
    uri = docs.row(document["id"]).storage_uri

    assert docs.call(docs.manager, "DELETE", item) == {"ok": True}

    assert docs.call(docs.reader, "GET", item, 404)["detail"] == "Document not found"
    assert docs.queued() == [uri]
    assert docs.changelog(document["id"]) == ["created", "updated", "deleted"]
    document_id = uuid.UUID(document["id"])
    for model in (IsmsDocumentRevision, IsmsDocumentComment):
        assert docs.keen.db.query(model).filter_by(document_id=document_id).all() == []


def test_captured_bookstack_evidence_keeps_its_document(docs):
    document = docs.create(title="Held policy")
    control = ControlItem(framework_slug="fw-docs", type="control", ref="5.1")
    docs.keen.db.add_all([Framework(slug="fw-docs"), control])
    docs.keen.db.flush()
    docs.keen.db.add(
        BookStackSectionEvidence(
            document_id=uuid.UUID(document["id"]),
            target_control_id=control.id,
            page_id=7,
            permalink="https://books.example/books/policies/page/access",
            page_title="Access",
            storage_uri="local://local/bookstack/7.json",
            sha256="0" * 64,
        )
    )
    docs.keen.db.commit()

    refused = docs.call(docs.manager, "DELETE", f"{DOCS}/{document['id']}", 409)

    assert refused["detail"] == (
        "This document has captured BookStack policy evidence and cannot be deleted"
    )
    assert docs.call(docs.reader, "GET", f"{DOCS}/{document['id']}") == document
    assert docs.queued() == []
    assert docs.changelog(document["id"]) == ["created"]
