"""Collection settings and evidence definitions through the assembled application.

Administrators decide what Keen collects, under which connection, and how the
matched events map to controls. The tests cover the administrator boundary,
revision checks, the exclusion of credentials from editable documents, and the
single transaction that publishes a collection input with its mapping rule.
Requests pass through the production middleware, password sign-in, CSRF and
the administrator check; upstream BookStack is faked at the HTTP transport.
"""

import json
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
import yaml
from app.core.config import settings
from app.db.models import ControlItem, Event, Framework, SourceConnection
from app.ingest import bookstack
from app.ingest.connections import FIELDS as CONNECTION_FIELDS
from app.integrations.transport import encrypt
from app.services.managed_configurations import CONNECTORS
from app.worker import tasks
from cryptography.fernet import Fernet

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    BASE,
    keen,  # noqa: F401
    unsafe,
)

FW = "fw-definitions"
SECRET = "synthetic-credential-value"
# Every non-administrator permission that touches evidence or its review.
BROAD = ("events.read", "isms.manage", "risk.manage", "audits.admin")


def _rule(rule_id="deploys", **when):
    return {
        "id": rule_id,
        "when": when,
        "map_to": [{"framework": FW, "ref": "A.1"}],
        "confidence": 0.8,
    }


def _collector(adapter, section, key):
    return json.dumps([adapter, section, key], separators=(",", ":"))


@pytest.fixture
def defs(keen, monkeypatch, tmp_path):  # noqa: F811 - fixture imported above
    def shipped(name, document):
        path = tmp_path / f"{name}.yml"
        path.write_text(yaml.safe_dump(document))
        return str(path)

    # Each source starts from an empty shipped document unless a test says otherwise.
    for name, (setting, _, _) in CONNECTORS.items():
        monkeypatch.setattr(settings, setting, shipped(name, {}))
    monkeypatch.setattr(settings, "rules_path", shipped("rules", {"rules": []}))
    monkeypatch.delenv("KEEN_SOURCE_CONNECTIONS_FILE", raising=False)
    queued = []
    monkeypatch.setattr(tasks.backfill_rule_task, "delay", queued.append)

    db = keen.db
    db.add(Framework(slug=FW))
    db.add_all(
        ControlItem(framework_slug=FW, type="annex_control", ref=ref, title=ref)
        for ref in ("A.1", "A.2")
    )
    db.commit()
    admin = keen.client(keen.user(role="admin"))

    def member(*codes):
        return keen.client(keen.user(*codes))

    def document(name):
        response = admin.get(f"/v1/admin/managed-configurations/{name}")
        assert response.status_code == 200, response.text
        return response.json()

    def put_document(name, body, version):
        return admin.put(
            f"/v1/admin/managed-configurations/{name}",
            json={"version": version, "document": body},
            headers=unsafe(admin),
        )

    def definition(
        rule_id="deploys",
        *,
        adapter="jenkins",
        section="jobs",
        entry=None,
        entry_key=None,
        rule=None,
        connector_version=0,
        rules_version=0,
        connection_id=None,
        apply_existing=False,
    ):
        return admin.put(
            f"/v1/admin/evidence-definitions/{rule_id}",
            json={
                "adapter": adapter,
                "section": section,
                "entry": {"name": "deploy"} if entry is None else entry,
                "entry_key": entry_key,
                "rule": _rule(rule_id) if rule is None else rule,
                "connector_version": connector_version,
                "rules_version": rules_version,
                "connection_id": connection_id,
                "apply_existing": apply_existing,
            },
            headers=unsafe(admin),
        )

    def versions():
        listed = admin.get("/v1/admin/managed-configurations").json()["items"]
        rules = admin.get("/v1/admin/mapping-rules").json()["version"]
        return {**{item["name"]: item["version"] for item in listed}, "rules": rules}

    def revisions(name):
        response = admin.get(f"/v1/admin/configuration-revisions/{name}")
        return [item["version"] for item in response.json()["items"]]

    return SimpleNamespace(
        keen=keen,
        db=db,
        tmp_path=tmp_path,
        admin=admin,
        queued=queued,
        shipped=lambda name, body: monkeypatch.setattr(
            settings, CONNECTORS[name][0], shipped(name, body)
        ),
        member=member,
        document=document,
        put_document=put_document,
        definition=definition,
        versions=versions,
        revisions=revisions,
    )


# --- The administrator boundary -------------------------------------------------


ADMIN_ROUTES = [
    ("GET", "/v1/admin/managed-configurations", None),
    ("GET", "/v1/admin/managed-configurations/jenkins", None),
    (
        "PUT",
        "/v1/admin/managed-configurations/jenkins",
        {"version": 0, "document": {"jobs": [{"name": "deploy"}]}},
    ),
    ("GET", "/v1/admin/adapter-settings/jenkins", None),
    (
        "PUT",
        "/v1/admin/adapter-settings/jenkins",
        {"version": 0, "settings": {"poll_minutes": 5}},
    ),
    ("GET", "/v1/admin/evidence-definitions", None),
    (
        "PUT",
        "/v1/admin/evidence-definitions/deploys",
        {
            "adapter": "jenkins",
            "section": "jobs",
            "entry": {"name": "deploy"},
            "rule": _rule(),
            "connector_version": 0,
            "rules_version": 0,
        },
    ),
    ("GET", "/v1/admin/source-catalogue", None),
    ("GET", "/v1/admin/bookstack/catalog", None),
    ("GET", "/v1/admin/configuration-revisions/jenkins", None),
]


@pytest.mark.parametrize(
    "method,path,body", ADMIN_ROUTES, ids=[f"{m} {p}" for m, p, _ in ADMIN_ROUTES]
)
def test_collection_settings_are_for_administrators_only(defs, method, path, body):
    before = defs.versions()
    signed_in, anonymous = defs.member(*BROAD), defs.keen.client()
    for client, headers, status in (
        (signed_in, unsafe(signed_in), 403),
        (anonymous, {"Origin": BASE}, 401),
    ):
        response = client.request(method, path, json=body, headers=headers)
        assert response.status_code == status, response.text
        assert defs.document("jenkins")["document"] == {}
    assert defs.versions() == before


# --- Collection documents -------------------------------------------------------


def test_collection_documents_are_saved_against_the_version_read(defs):
    defs.shipped("jenkins", {"jobs": [{"name": "nightly"}]})
    assert defs.document("jenkins") == {
        "name": "jenkins",
        "version": 0,
        "document": {"jobs": [{"name": "nightly"}]},
        "origin": "shipped YAML",
    }

    jobs = {"jobs": [{"name": "nightly"}, {"name": "deploy"}]}
    saved = defs.put_document("jenkins", jobs, version=0)
    assert saved.json() == {"version": 1, "document": jobs}
    stale = defs.put_document("jenkins", {"jobs": []}, version=0)

    assert stale.status_code == 409
    assert stale.json()["detail"] == (
        "Configuration changed in another session. Reload before saving."
    )
    current = defs.document("jenkins")
    assert (current["version"], current["origin"], current["document"]) == (
        1,
        "database",
        jobs,
    )
    assert defs.revisions("jenkins") == [1]
    unknown = defs.admin.get("/v1/admin/managed-configurations/ftp")
    assert unknown.status_code == 404


@pytest.mark.parametrize(
    "document,path",
    [
        ({"jobs": [{"name": "deploy", "token": SECRET}]}, "jobs.token"),
        (
            {"jobs": [{"name": "deploy", "auth": {"Password": SECRET}}]},
            "jobs.auth.Password",
        ),
        ({"defaults": {"smtp_password": SECRET}}, "defaults.smtp_password"),
        ({"Authorization": SECRET}, "Authorization"),
        ({"jobs": [{"name": "deploy", "client_secret": SECRET}]}, "jobs.client_secret"),
        ({"jobs": [{"name": "deploy", "Api-Token": SECRET}]}, "jobs.Api-Token"),
    ],
    ids=[
        "token",
        "nested-password",
        "password-suffix",
        "authorization",
        "client-secret",
        "hyphenated",
    ],
)
def test_credentials_never_enter_collection_documents(defs, document, path):
    refused = defs.put_document("jenkins", document, version=0)

    assert refused.status_code == 400
    assert refused.json()["detail"] == (
        f"Move credential {path} into a server environment variable"
    )
    assert SECRET not in refused.text
    assert defs.versions()["jenkins"] == 0


# Every credential a source connection can hold, under the name Keen reads it
# by, including the names each built-in source uses today.
CONNECTION_CREDENTIALS = sorted(
    {name for _, secret in CONNECTION_FIELDS.values() for name in secret.split()}
    | {
        "api_token",
        "token_id",
        "token_secret",
        "header_value",
        "cookie",
        "sa_json",
        "aws_access_key_id",
        "aws_secret_access_key",
        "aws_session_token",
    }
)


@pytest.mark.parametrize("name", CONNECTION_CREDENTIALS)
def test_no_connection_credential_field_enters_a_collection_document(defs, name):
    refused = defs.put_document(
        "jenkins", {"defaults": {name: SECRET}, "jobs": []}, version=0
    )

    assert refused.status_code == 400
    assert refused.json()["detail"] == (
        f"Move credential defaults.{name} into a server environment variable"
    )
    assert defs.versions()["jenkins"] == 0


def test_credentials_stay_out_of_definitions_and_adapter_settings(defs):
    entry = defs.definition(entry={"name": "deploy", "api_key": SECRET})
    assert entry.status_code == 400
    assert entry.json()["detail"] == (
        "Move credential jobs.api_key into a server environment variable"
    )
    setting = defs.admin.put(
        "/v1/admin/adapter-settings/jenkins",
        json={"version": 0, "settings": {"secret": SECRET}},
        headers=unsafe(defs.admin),
    )
    assert setting.status_code == 400
    assert SECRET not in entry.text + setting.text
    current = defs.versions()
    assert (current["jenkins"], current["rules"]) == (0, 0)


def test_documents_holding_credentials_are_withheld_from_the_editors(defs):
    defs.shipped(
        "rss",
        {"feeds": [{"url": "https://news.example/feed", "auth": {"password": SECRET}}]},
    )
    defs.shipped(
        "forgejo",
        {
            "feeds": [
                {"url": f"https://git.example/user.rss?token={SECRET}"},
                {"url": "https://git.example/team.rss"},
            ]
        },
    )

    listing = defs.admin.get("/v1/admin/evidence-definitions")
    catalogue = defs.admin.get("/v1/admin/source-catalogue")
    viewed = defs.admin.get("/v1/admin/managed-configurations/rss")

    for response in (listing, catalogue, viewed):
        assert SECRET not in response.text
    warnings = [
        "rss contains sensitive settings that require server administrator review",
        "forgejo has a feed with a credential in its URL; edit it outside the "
        "evidence definition editor",
    ]
    assert listing.json()["warnings"] == warnings
    assert catalogue.json()["warnings"] == warnings
    assert [c["key"] for c in listing.json()["collectors"]] == [
        "https://git.example/team.rss"
    ]
    assert viewed.status_code == 400


def test_rss_documents_with_custom_headers_stay_out_of_the_web_editor(defs):
    defs.shipped(
        "rss",
        {"feeds": [{"url": "https://news.example/feed", "headers": {"X-Feed": "1"}}]},
    )

    refused = defs.admin.get("/v1/admin/managed-configurations/rss")

    assert refused.status_code == 409
    assert refused.json()["detail"].startswith(
        "This RSS configuration contains headers or credentials."
    )


def test_items_with_published_definitions_cannot_be_removed_or_renamed(defs):
    assert defs.definition(entry={"name": "deploy"}).status_code == 200

    for jobs in ([], [{"name": "deploy-renamed"}]):
        refused = defs.put_document("jenkins", {"jobs": jobs}, version=1)
        assert refused.status_code == 409
        assert refused.json()["detail"] == (
            "A collection item has framework mappings. Remove those rules before "
            "deleting or renaming it."
        )
    kept = defs.put_document(
        "jenkins", {"jobs": [{"name": "deploy"}, {"name": "nightly"}]}, version=1
    )
    assert kept.status_code == 200, kept.text


def test_adapter_settings_change_only_source_wide_values(defs):
    defs.shipped("jenkins", {"poll_minutes": 15, "jobs": [{"name": "nightly"}]})

    def put(body, version):
        return defs.admin.put(
            "/v1/admin/adapter-settings/jenkins",
            json={"version": version, "settings": body},
            headers=unsafe(defs.admin),
        )

    assert defs.admin.get("/v1/admin/adapter-settings/jenkins").json() == {
        "version": 0,
        "settings": {"poll_minutes": 15},
    }
    refused = put({"jobs": []}, 0)
    assert refused.status_code == 400
    assert refused.json()["detail"] == "Edit collection items in evidence definitions"

    assert put({"poll_minutes": 30}, 0).json()["version"] == 1
    assert defs.document("jenkins")["document"] == {
        "poll_minutes": 30,
        "jobs": [{"name": "nightly"}],
    }
    assert put({}, 1).json()["document"] == {"jobs": [{"name": "nightly"}]}
    assert put({"poll_minutes": 5}, 1).status_code == 409
    assert defs.admin.get("/v1/admin/adapter-settings/ftp").status_code == 404


# --- Evidence definitions -------------------------------------------------------


def test_a_definition_publishes_its_input_and_rule_together(defs):
    published = defs.definition(rule=_rule(action="deploy"))

    assert published.status_code == 200, published.text
    body = published.json()
    collector = _collector("jenkins", "jobs", "deploy")
    assert (body["rules_version"], body["connector_version"], body["backfill"]) == (
        1,
        1,
        None,
    )
    assert body["rule"]["when"] == {
        "action": "deploy",
        "source": "jenkins",
        "collector": collector,
    }
    assert body["rule"]["map_to"] == [
        {"framework": FW, "ref": "A.1", "kind": "control"}
    ]
    listing = defs.admin.get("/v1/admin/evidence-definitions").json()
    assert listing["collectors"] == [
        {
            "adapter": "jenkins",
            "section": "jobs",
            "key": "deploy",
            "collector": collector,
            "entry": {"name": "deploy"},
            "version": 1,
        }
    ]
    (rule,) = listing["rules"]
    assert (rule["id"], rule["when"]["collector"], listing["rules_version"]) == (
        "deploys",
        collector,
        1,
    )
    assert defs.revisions("jenkins") == [1] and defs.revisions("rules") == [1]


def test_publishing_over_existing_evidence_queues_a_backfill(defs):
    nothing = defs.definition(apply_existing=True)
    assert nothing.json()["backfill"] == {
        "status": "no_previous_evidence",
        "total_estimate": 0,
    }
    defs.db.add(
        Event(
            source="jenkins",
            action="deploy",
            summary="Deploy",
            timestamp=datetime(2026, 3, 1),
            external_id="deploy-1",
            raw_pointer={},
            normalized_payload={},
        )
    )
    defs.db.commit()

    queued = defs.definition(
        "nightlies",
        entry={"name": "nightly"},
        connector_version=1,
        rules_version=1,
        apply_existing=True,
    )

    backfill = queued.json()["backfill"]
    assert backfill["status"] == "queued"
    assert defs.queued == [backfill["id"]]


@pytest.mark.parametrize("stale", ["jenkins", "rules"])
def test_a_stale_definition_changes_neither_document(defs, stale):
    if stale == "jenkins":
        moved = defs.put_document("jenkins", {"jobs": [{"name": "nightly"}]}, version=0)
    else:
        moved = defs.admin.put(
            "/v1/admin/mapping-rules/others",
            json={"version": 0, "rule": _rule("others", source="github")},
            headers=unsafe(defs.admin),
        )
    assert moved.status_code == 200, moved.text

    refused = defs.definition()

    assert refused.status_code == 409
    assert refused.json()["detail"] == (
        f"{stale} changed in another session. Reload before saving."
    )
    current = defs.versions()
    assert (current["jenkins"], current["rules"]) == (
        (1, 0) if stale == "jenkins" else (0, 1)
    )
    assert defs.revisions("jenkins" if stale == "rules" else "rules") == []
    listing = defs.admin.get("/v1/admin/evidence-definitions").json()
    assert "deploy" not in [c["key"] for c in listing["collectors"]]
    assert "deploys" not in [r["id"] for r in listing["rules"]]


REFUSALS = [
    ("id-mismatch", dict(rule=_rule("other")), 400, "Rule ID does not match the URL"),
    (
        "source-mismatch",
        dict(rule=_rule(source="github")),
        400,
        "Rule source must match the selected adapter",
    ),
    (
        "advanced-section",
        dict(section="queries"),
        400,
        "This adapter entry requires the advanced configuration editor",
    ),
    (
        "no-identity",
        dict(entry={"name": "  "}),
        400,
        "Provide a stable name or URL for the collection item",
    ),
    (
        "rename",
        dict(entry={"name": "deploy-v2"}, entry_key="nightly"),
        400,
        "Collection identity cannot be renamed; create a new definition instead",
    ),
    (
        "duplicate",
        dict(entry={"name": "nightly"}),
        409,
        "Collection item already exists; reload and edit it",
    ),
    (
        "removed",
        dict(entry={"name": "gone"}, entry_key="gone"),
        409,
        "Collection item was removed; reload before saving",
    ),
    (
        "other-item",
        dict(rule_id="linked", rule=_rule("linked"), entry={"name": "deploy"}),
        409,
        "Rule is linked to a different collection item",
    ),
]


@pytest.mark.parametrize(
    "case,fields,status,detail", REFUSALS, ids=[case for case, *_ in REFUSALS]
)
def test_definitions_keep_inputs_and_rules_paired(defs, case, fields, status, detail):
    defs.shipped("jenkins", {"jobs": [{"name": "nightly"}]})
    linked = defs.definition(
        "linked", entry={"name": "nightly"}, entry_key="nightly", rule=_rule("linked")
    )
    assert linked.status_code == 200, linked.text
    fields = dict(fields)
    rule_id = fields.pop("rule_id", "deploys")

    refused = defs.definition(rule_id, connector_version=1, rules_version=1, **fields)

    assert refused.status_code == status, refused.text
    assert refused.json()["detail"] == detail
    assert (defs.versions()["jenkins"], defs.versions()["rules"]) == (1, 1)
    assert defs.document("jenkins")["document"] == {"jobs": [{"name": "nightly"}]}


def test_source_wide_definitions_match_the_whole_source(defs):
    saved = defs.definition(
        "all-ci", section="source", entry={}, rule=_rule("all-ci", source="jenkins")
    )

    assert saved.status_code == 200, saved.text
    assert saved.json()["rule"]["when"] == {"source": "jenkins"}
    assert defs.versions()["jenkins"] == 0
    for rule in (
        _rule("item", source="jenkins", collector=_collector("jenkins", "jobs", "x")),
        _rule("item", source="github"),
    ):
        refused = defs.definition(
            "item", section="source", entry={}, rule=rule, rules_version=1
        )
        assert refused.status_code == 400
        assert refused.json()["detail"] == (
            "Source-wide definition must use the selected source"
        )
    hooks = defs.definition(
        "alerts",
        adapter="webhooks",
        section="source",
        entry={},
        rule=_rule("alerts", source="webhook:alerts"),
        rules_version=1,
    )
    assert hooks.status_code == 200, hooks.text
    assert [
        r["id"] for r in defs.admin.get("/v1/admin/mapping-rules").json()["items"]
    ] == [
        "all-ci",
        "alerts",
    ]


@pytest.mark.parametrize(
    "name,accepted",
    [
        ("alerts", True),
        ("pager-duty_2", True),
        ("bad name", False),
        ("x" * 65, False),
        ("../up", False),
    ],
)
def test_webhook_providers_need_simple_names(defs, name, accepted):
    response = defs.definition(
        "hook",
        adapter="webhooks",
        section="providers",
        entry={"secret_header": "X-Hook-Secret", "secret_env": "KEEN_HOOK_SECRET"},
        entry_key=name,
        rule=_rule("hook"),
    )

    if accepted:
        assert response.status_code == 200, response.text
        assert response.json()["rule"]["when"] == {
            "source": f"webhook:{name}",
            "collector": _collector("webhooks", "providers", name),
        }
        assert defs.document("webhooks")["document"] == {
            "providers": {
                name: {
                    "secret_header": "X-Hook-Secret",
                    "secret_env": "KEEN_HOOK_SECRET",
                }
            }
        }
    else:
        assert response.status_code == 400
        assert response.json()["detail"] == (
            "Webhook provider name must use letters, numbers, underscores or hyphens"
        )
        assert defs.versions()["webhooks"] == 0


@pytest.mark.parametrize(
    "url,detail",
    [
        (
            "http://news.example/feed",
            "Feed URLs must be HTTPS without embedded credentials",
        ),
        (
            "https://reader:pw@news.example/feed",
            "Feed URLs must be HTTPS without embedded credentials",
        ),
        (
            "https://news.example/feed?token=abc123",
            "Feed URLs containing credentials cannot be linked to evidence definitions",
        ),
    ],
    ids=["plain-http", "userinfo", "token-query"],
)
def test_feed_definitions_need_plain_https_addresses(defs, url, detail):
    refused = defs.definition(
        "news", adapter="rss", section="feeds", entry={"url": url}, rule=_rule("news")
    )

    assert refused.status_code == 400
    assert refused.json()["detail"] == detail
    assert (defs.versions()["rss"], defs.versions()["rules"]) == (0, 0)


# --- Named connections ----------------------------------------------------------


def test_definitions_for_a_named_connection_stay_with_that_connection(defs):
    production = SourceConnection(
        id="jenkins-prod",
        source="jenkins",
        name="Production CI",
        configuration={"base_url": "https://ci.example"},
        inputs={"jobs": [{"name": "release"}]},
        version=3,
    )
    github = SourceConnection(
        id="github-main", source="github", name="GitHub", version=1
    )
    defs.db.add_all([production, github])
    defs.db.commit()

    saved = defs.definition(connection_id="jenkins-prod", connector_version=3)

    assert saved.status_code == 200, saved.text
    assert saved.json()["connector_version"] == 4
    assert saved.json()["rule"]["when"]["connection_id"] == "jenkins-prod"
    jobs = {"jobs": [{"name": "release"}, {"name": "deploy"}]}
    defs.db.refresh(production)
    assert (production.inputs, production.version) == (jobs, 4)
    # The deployment's own document is untouched.
    assert defs.versions()["jenkins"] == 0
    listed = defs.admin.get("/v1/admin/evidence-definitions").json()["collectors"]
    assert [
        (i["key"], i["connection_id"], i["connection_name"], i["version"])
        for i in listed
    ] == [
        ("release", "jenkins-prod", "Production CI", 4),
        ("deploy", "jenkins-prod", "Production CI", 4),
    ]

    for connection_id, version, status, detail in (
        ("github-main", 1, 400, "Choose a connection belonging to this source"),
        ("jenkins-missing", 1, 400, "Choose a connection belonging to this source"),
        ("env:github", 0, 400, "Connection does not belong to this source"),
        ("jenkins-prod", 3, 409, "Connection changed; reload before saving"),
    ):
        refused = defs.definition(
            "nightlies",
            entry={"name": "nightly"},
            connection_id=connection_id,
            connector_version=version,
            rules_version=1,
        )
        assert refused.status_code == status, connection_id
        assert refused.json()["detail"] == detail
    defs.db.refresh(production)
    assert (production.inputs, production.version) == (jobs, 4)
    assert defs.versions()["rules"] == 1


def test_deployment_managed_connections_take_rules_but_not_input_edits(
    defs, monkeypatch
):
    connections = defs.tmp_path / "connections.yml"
    connections.write_text(
        yaml.safe_dump(
            {
                "connections": [
                    {
                        "id": "ci",
                        "source": "jenkins",
                        "name": "CI from file",
                        "configuration": {"base_url": "https://ci.example"},
                        "inputs": {"jobs": [{"name": "nightly"}]},
                    }
                ]
            }
        )
    )
    before = connections.read_text()
    monkeypatch.setenv("KEEN_SOURCE_CONNECTIONS_FILE", str(connections))

    saved = defs.definition(
        "nightlies", entry={"name": "nightly"}, connection_id="file:ci"
    )

    assert saved.status_code == 200, saved.text
    assert "connector_version" not in saved.json()
    assert saved.json()["rule"]["when"] == {
        "source": "jenkins",
        "connection_id": "file:ci",
        "collector": _collector("jenkins", "jobs", "nightly"),
    }
    edited = defs.definition(
        entry={"name": "deploy"}, connection_id="file:ci", rules_version=1
    )
    assert edited.status_code == 409
    assert (
        edited.json()["detail"] == "Edit this input in the deployment connections file"
    )
    unknown = defs.definition(connection_id="file:other", rules_version=1)
    assert unknown.status_code == 400
    assert unknown.json()["detail"] == "Unknown deployment-managed connection"
    mismatched = defs.definition(
        "nightlies",
        entry={"name": "nightly"},
        connection_id="file:ci",
        rule=_rule("other"),
        rules_version=1,
    )
    assert mismatched.status_code == 400
    assert mismatched.json()["detail"] == "Rule ID does not match the URL"
    assert connections.read_text() == before
    assert (defs.versions()["jenkins"], defs.versions()["rules"]) == (0, 1)


# --- BookStack catalogue --------------------------------------------------------


class BookStack:
    def __init__(self):
        self.requests = []
        self.response = httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": 7,
                        "name": "Access policy",
                        "slug": "access-policy",
                        "book_id": 3,
                        "chapter_id": 9,
                        "html": "<p>page body</p>",
                        "owned_by": {"email": "owner@example.com"},
                    },
                    {"id": 8, "name": "Elsewhere", "slug": "elsewhere", "book_id": 4},
                    {"id": 10, "name": "N" * 300, "slug": "s" * 300, "book_id": 3},
                    {"id": "9", "name": "Not an ID"},
                    "not an object",
                ],
                "total": 150,
            },
        )

    def __call__(self, request):
        self.requests.append(request)
        return self.response


@pytest.fixture
def catalogue(defs, monkeypatch):
    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", Fernet.generate_key().decode())
    for name, value in {
        "bookstack_enabled": True,
        "bookstack_base_url": "https://93.184.216.34",
        "bookstack_token_id": "environment-id",
        "bookstack_token_secret": "environment-secret",
    }.items():
        monkeypatch.setattr(settings, name, value)
    upstream = BookStack()

    def client(**kwargs):
        kwargs.pop("verify", None)
        return httpx.Client(transport=httpx.MockTransport(upstream), **kwargs)

    monkeypatch.setattr(bookstack, "ingestion_client", client)

    def get(**params):
        return defs.admin.get("/v1/admin/bookstack/catalog", params=params)

    return SimpleNamespace(upstream=upstream, get=get)


def test_the_bookstack_catalogue_returns_page_metadata_only(defs, catalogue):
    pages = catalogue.get(kind="pages", book_id=3)

    assert pages.status_code == 200, pages.text
    assert pages.json() == {
        "items": [
            {
                "id": 7,
                "name": "Access policy",
                "slug": "access-policy",
                "book_id": 3,
                "chapter_id": 9,
            },
            # Long upstream names are bounded.
            {
                "id": 10,
                "name": "N" * 250,
                "slug": "s" * 250,
                "book_id": 3,
                "chapter_id": None,
            },
        ],
        "next_offset": 5,
    }
    (sent,) = catalogue.upstream.requests
    assert sent.url.path == "/api/pages"
    assert dict(sent.url.params) == {
        "count": "100",
        "offset": "0",
        "filter[book_id]": "3",
    }
    assert sent.headers["authorization"] == "Token environment-id:environment-secret"
    assert "environment-secret" not in pages.text

    # The last page of the catalogue offers no further offset.
    catalogue.upstream.response = httpx.Response(
        200, json={"data": [{"id": 11, "name": "Last", "book_id": 3}], "total": 6}
    )
    last = catalogue.get(kind="pages", book_id=3, offset=5)
    assert last.json()["next_offset"] is None


def test_the_bookstack_catalogue_uses_the_chosen_connections_credentials(
    defs, catalogue
):
    defs.db.add(
        SourceConnection(
            id="bookstack-team",
            source="bookstack",
            name="Team wiki",
            configuration={"base_url": "https://93.184.216.35"},
            encrypted_credentials=encrypt(
                {"token_id": "team-id", "token_secret": "team-secret"}
            ),
        )
    )
    defs.db.commit()

    team = catalogue.get(connection_id="bookstack-team")
    environment = catalogue.get()
    missing = catalogue.get(connection_id="bookstack-other")

    assert (team.status_code, environment.status_code) == (200, 200)
    assert [
        (str(r.url.copy_with(query=None)), r.headers["authorization"])
        for r in catalogue.upstream.requests
    ] == [
        ("https://93.184.216.35/api/books", "Token team-id:team-secret"),
        ("https://93.184.216.34/api/books", "Token environment-id:environment-secret"),
    ]
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Connection not found"


@pytest.mark.parametrize(
    "params,status,detail",
    [
        ({"kind": "users"}, 400, "Choose books, chapters or pages"),
        ({"kind": "pages"}, 400, "Select a book first"),
        ({"kind": "chapters", "book_id": 0}, 400, "Select a book first"),
        ({"offset": -1}, 400, "Invalid catalogue offset"),
        ({"offset": 100_001}, 400, "Invalid catalogue offset"),
    ],
)
def test_bookstack_catalogue_requests_are_checked_before_contacting_bookstack(
    defs, catalogue, params, status, detail
):
    refused = catalogue.get(**params)

    assert refused.status_code == status
    assert refused.json()["detail"] == detail
    assert catalogue.upstream.requests == []


@pytest.mark.parametrize(
    "response,detail",
    [
        (
            httpx.Response(500, text="stack trace with internal hostnames"),
            "BookStack catalogue is unavailable; check the configured backend URL "
            "and API credentials",
        ),
        (
            httpx.Response(200, json={"data": "not a list"}),
            "BookStack returned an unexpected catalogue response",
        ),
    ],
    ids=["upstream-error", "unexpected-shape"],
)
def test_bookstack_failures_are_reported_without_upstream_detail(
    defs, catalogue, response, detail
):
    catalogue.upstream.response = response

    failed = catalogue.get()

    assert failed.status_code == 502
    assert failed.json()["detail"] == detail
    assert "internal hostnames" not in failed.text


def test_the_bookstack_catalogue_waits_while_bookstack_is_disabled(
    defs, catalogue, monkeypatch
):
    monkeypatch.setattr(settings, "bookstack_enabled", False)

    refused = catalogue.get()

    assert refused.status_code == 409
    assert refused.json()["detail"] == "BookStack ingestion is disabled"
    assert catalogue.upstream.requests == []


# --- Ingester checks on single entries --------------------------------------------


UNINGESTIBLE = [
    (
        "elasticsearch",
        "queries",
        {"name": "audit"},
        "Enter an index, alias or data stream pattern",
    ),
    (
        "riskledger",
        "organizations",
        {"org": "customer"},
        "Risk Ledger requires a valid UUID identifier",
    ),
    (
        "bookstack",
        "selected_pages",
        {"id": "7"},
        "Select a BookStack page from the catalogue",
    ),
    ("loki", "queries", {"name": "auth"}, "Enter a LogQL query"),
    ("taiga", "projects", {"id": -3}, "Taiga project ID must be a positive integer"),
]


@pytest.mark.parametrize(
    "adapter,section,entry,detail",
    UNINGESTIBLE,
    ids=[adapter for adapter, *_ in UNINGESTIBLE],
)
def test_entries_the_ingester_would_reject_are_refused_as_definitions(
    defs, adapter, section, entry, detail
):
    """A definition is refused when its collection entry cannot be ingested.

    The entry check runs before either document is touched, so the collection
    document and the rules keep their versions.
    """
    refused = defs.definition(
        "item", adapter=adapter, section=section, entry=entry, rule=_rule("item")
    )

    assert refused.status_code == 400, refused.text
    assert refused.json()["detail"] == detail
    assert (defs.versions()[adapter], defs.versions()["rules"]) == (0, 0)


INGESTIBLE = [
    ("rss", "feeds", {"url": "https://news.example/feed"}, "https://news.example/feed"),
    ("github", "repos", {"owner": "acme", "repo": "app"}, "acme/app"),
    (
        "loki",
        "queries",
        {"name": "auth", "logql": '{job="auth"}', "event": {"source": "loki"}},
        "auth",
    ),
    (
        "cloudwatch_logs",
        "queries",
        {"name": "trail", "log_group": "/aws/trail"},
        "trail",
    ),
    (
        "google_workspace",
        "streams",
        {"name": "logins", "application": "login"},
        "logins",
    ),
]


@pytest.mark.parametrize(
    "adapter,section,entry,key", INGESTIBLE, ids=[a for a, *_ in INGESTIBLE]
)
def test_entries_the_ingester_accepts_publish_with_their_collection_identity(
    defs, adapter, section, entry, key
):
    """Entries that pass every ingester check publish under a stable identity."""
    saved = defs.definition(
        "item", adapter=adapter, section=section, entry=entry, rule=_rule("item")
    )

    assert saved.status_code == 200, saved.text
    assert saved.json()["rule"]["when"] == {
        "source": adapter,
        "collector": _collector(adapter, section, key),
    }
    assert defs.document(adapter)["document"] == {section: [entry]}
    (listed,) = defs.admin.get("/v1/admin/evidence-definitions").json()["collectors"]
    assert (listed["adapter"], listed["section"], listed["key"]) == (
        adapter,
        section,
        key,
    )


# --- Rule identity, repeated saves and duplicate IDs ----------------------------


@pytest.mark.parametrize(
    "rule",
    [
        {
            "id": "all-ci",
            "when": "jenkins",
            "map_to": [{"framework": FW, "ref": "A.1"}],
        },
        _rule("other", source="jenkins"),
    ],
    ids=["conditions-not-an-object", "id-differs-from-url"],
)
def test_source_wide_definitions_need_a_rule_identity(defs, rule):
    """A source-wide definition needs the URL's rule ID and an object of conditions."""
    refused = defs.definition("all-ci", section="source", entry={}, rule=rule)

    assert refused.status_code == 400, refused.text
    assert refused.json()["detail"] == "Rule identity is required"
    assert defs.versions()["rules"] == 0


def test_saving_a_definition_again_replaces_its_rule_in_place(defs):
    """Editing a definition replaces the existing rule; the rule count stays the same."""
    assert defs.definition(rule=_rule("deploys", action="deploy")).status_code == 200
    source_wide = defs.definition(
        "all-ci",
        section="source",
        entry={},
        rule=_rule("all-ci", source="jenkins"),
        rules_version=1,
    )
    assert source_wide.status_code == 200, source_wide.text

    edited = defs.definition(
        entry={"name": "deploy"},
        entry_key="deploy",
        rule=_rule("deploys", action="rollback"),
        connector_version=1,
        rules_version=2,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["rule"]["when"]["action"] == "rollback"
    rewritten = defs.definition(
        "all-ci",
        section="source",
        entry={},
        rule={**_rule("all-ci", source="jenkins"), "description": "Every CI event"},
        rules_version=3,
    )
    assert rewritten.status_code == 200, rewritten.text

    rules = {
        r["id"]: r for r in defs.admin.get("/v1/admin/mapping-rules").json()["items"]
    }
    assert set(rules) == {"deploys", "all-ci"}
    assert rules["deploys"]["when"]["action"] == "rollback"
    assert rules["all-ci"]["description"] == "Every CI event"
    assert defs.document("jenkins")["document"] == {"jobs": [{"name": "deploy"}]}
    assert (defs.versions()["jenkins"], defs.versions()["rules"]) == (2, 4)


@pytest.fixture
def duplicated_rules(defs, monkeypatch):
    """Shipped YAML whose rules reuse an ID, as a hand-edited file can."""
    rule = {
        "id": "dup",
        "when": {"source": "jenkins"},
        "map_to": [{"framework": FW, "ref": "A.1"}],
    }
    unreadable = {"id": "broken", "when": "jenkins", "map_to": []}
    path = defs.tmp_path / "duplicated-rules.yml"
    path.write_text(yaml.safe_dump({"rules": [rule, rule, unreadable]}))
    monkeypatch.setattr(settings, "rules_path", str(path))
    return rule


def test_rules_collection_cannot_read_are_left_out_of_the_editor(
    defs, duplicated_rules
):
    """A rule the collection parser skips is absent from the rule listing."""
    listed = defs.admin.get("/v1/admin/mapping-rules").json()

    assert [r["id"] for r in listed["items"]] == ["dup", "dup"]
    assert listed["version"] == 0


def test_duplicate_rule_ids_in_yaml_block_every_rule_edit(defs, duplicated_rules):
    """Rules with duplicate IDs are resolved in YAML before any editor save."""
    refusals = [
        (
            defs.definition(
                "all-ci",
                section="source",
                entry={},
                rule=_rule("all-ci", source="jenkins"),
            ),
            "Existing rules have duplicate IDs",
        ),
        (defs.definition(), "Existing rules have duplicate IDs"),
        (
            defs.admin.put(
                "/v1/admin/mapping-rules/deploys",
                json={"version": 0, "rule": _rule("deploys", source="jenkins")},
                headers=unsafe(defs.admin),
            ),
            "Existing rules have duplicate IDs; resolve in YAML before editing",
        ),
        (
            defs.admin.get("/v1/admin/mapping-rules/dup/revisions"),
            "Duplicate rule IDs; resolve before editing",
        ),
    ]
    for response, detail in refusals:
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == detail
    missing = defs.admin.get("/v1/admin/mapping-rules/absent/revisions")
    assert (missing.status_code, missing.json()["detail"]) == (404, "Rule not found")
    assert (defs.versions()["jenkins"], defs.versions()["rules"]) == (0, 0)


# --- Listings skip entries without an identity -----------------------------------


def test_collection_entries_without_an_identity_are_left_out_of_listings(defs):
    """An entry with no stable identity cannot be linked, so listings omit it."""
    defs.shipped("jenkins", {"jobs": [{"label": "unnamed"}, {"name": "nightly"}]})
    defs.db.add(
        SourceConnection(
            id="jenkins-prod",
            source="jenkins",
            name="Production CI",
            inputs={"jobs": [{"label": "unnamed"}, {"name": "release"}]},
            version=1,
        )
    )
    defs.db.commit()

    listed = defs.admin.get("/v1/admin/evidence-definitions").json()["collectors"]

    assert [(c["key"], c.get("connection_id")) for c in listed] == [
        ("nightly", None),
        ("release", "jenkins-prod"),
    ]


def test_source_wide_definitions_for_file_managed_connections_carry_no_collector(
    defs, monkeypatch
):
    """A source-wide rule on a deployment-managed connection names the connection only."""
    connections = defs.tmp_path / "connections.yml"
    connections.write_text(
        yaml.safe_dump(
            {
                "connections": [
                    {
                        "id": "ci",
                        "source": "jenkins",
                        "name": "CI from file",
                        "configuration": {"base_url": "https://ci.example"},
                        "inputs": {"jobs": [{"name": "nightly"}]},
                    }
                ]
            }
        )
    )
    monkeypatch.setenv("KEEN_SOURCE_CONNECTIONS_FILE", str(connections))

    saved = defs.definition(
        "all-ci",
        section="source",
        entry={},
        rule=_rule("all-ci", source="jenkins"),
        connection_id="file:ci",
    )

    assert saved.status_code == 200, saved.text
    assert saved.json()["rule"]["when"] == {
        "source": "jenkins",
        "connection_id": "file:ci",
    }
    assert defs.versions()["rules"] == 1


def test_demo_environments_show_supplier_sources_as_disabled(defs, monkeypatch):
    """Demo mode reports Redmine, Risk Ledger and IMAP as disabled whatever Docker says."""
    from datetime import datetime, timezone

    for name in ("redmine_enabled", "riskledger_enabled", "imap_enabled"):
        monkeypatch.setattr(settings, name, True)
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(
        settings, "demo_expires_at", datetime(2999, 1, 1, tzinfo=timezone.utc)
    )

    catalogue = defs.admin.get("/v1/admin/source-catalogue")

    assert catalogue.status_code == 200, catalogue.text
    enabled = {item["adapter"]: item["enabled"] for item in catalogue.json()["items"]}
    assert (enabled["redmine"], enabled["riskledger"], enabled["imap"]) == (
        False,
        False,
        False,
    )
    assert (enabled["keen-agent"], enabled["api-ingesters"]) == (False, False)


# --- Restoring collection revisions -----------------------------------------------


def _restore(defs, name, revision, version):
    return defs.admin.post(
        f"/v1/admin/configuration-revisions/{name}/{revision}/restore",
        json={"version": version},
        headers=unsafe(defs.admin),
    )


def test_restoring_a_collection_revision_recreates_it_as_a_new_revision(defs):
    """A restored collection document is validated and saved as its own revision."""
    first = {"jobs": [{"name": "nightly"}]}
    assert defs.put_document("jenkins", first, version=0).status_code == 200
    assert defs.put_document("jenkins", {"jobs": []}, version=1).status_code == 200

    restored = _restore(defs, "jenkins", 1, 2)

    assert restored.status_code == 200, restored.text
    assert restored.json() == {"version": 3}
    assert defs.document("jenkins")["document"] == first
    assert defs.revisions("jenkins") == [3, 2, 1]


def test_restoring_a_revision_cannot_orphan_a_published_definition(defs):
    """Restoring an older collection document keeps items that rules depend on."""
    assert defs.put_document("jenkins", {"jobs": []}, version=0).status_code == 200
    assert defs.definition(connector_version=1).status_code == 200

    refused = _restore(defs, "jenkins", 1, 2)

    assert refused.status_code == 409
    assert refused.json()["detail"] == (
        "A collection item has framework mappings. Remove those rules before "
        "deleting or renaming it."
    )
    assert defs.document("jenkins") == {
        "name": "jenkins",
        "version": 2,
        "document": {"jobs": [{"name": "deploy"}]},
        "origin": "database",
    }


# --- Conflicting writes -----------------------------------------------------------


@pytest.fixture
def conflicting_commit(monkeypatch):
    """Make the commit that records a configuration revision collide.

    A concurrent save of the same configuration row produces an integrity error
    at commit time; the request must roll back and report a conflict.
    """
    from app.db.models import ManagedConfigurationRevision
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session

    original = Session.commit

    def commit(self):
        if any(isinstance(row, ManagedConfigurationRevision) for row in self.new):
            raise IntegrityError(
                "INSERT INTO managed_configurations", {}, Exception("duplicate key")
            )
        return original(self)

    monkeypatch.setattr(Session, "commit", commit)


def test_a_colliding_collection_save_rolls_back_and_reports_a_conflict(
    defs, conflicting_commit
):
    refused = defs.put_document("jenkins", {"jobs": [{"name": "deploy"}]}, version=0)

    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == (
        "Configuration changed in another session. Reload before saving."
    )
    assert defs.document("jenkins") == {
        "name": "jenkins",
        "version": 0,
        "document": {},
        "origin": "shipped YAML",
    }
    assert defs.revisions("jenkins") == []


def test_a_colliding_definition_save_rolls_back_both_documents(
    defs, conflicting_commit
):
    refused = defs.definition()

    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == (
        "Configuration changed in another session. Reload before saving."
    )
    assert (defs.versions()["jenkins"], defs.versions()["rules"]) == (0, 0)
    listing = defs.admin.get("/v1/admin/evidence-definitions").json()
    assert (listing["collectors"], listing["rules"]) == ([], [])
    assert defs.queued == []


# --- Removing an evidence connection ----------------------------------------------


def _removal_preview(defs, key):
    return defs.admin.get(f"/v1/admin/evidence-connections/{key}/removal")


@pytest.mark.parametrize(
    "key,detail",
    [
        ("api:absent", "Connection not found"),
        ("collector:absent", "API ingester not found"),
        ("builtin:ftp", "Unknown connection"),
        ("ldap:directory", "Unknown connection"),
    ],
)
def test_removal_previews_name_the_missing_connection_kind(defs, key, detail):
    """Each connection kind reports its own absence; unknown kinds are refused."""
    missing = _removal_preview(defs, key)

    assert missing.status_code == 404, missing.text
    assert missing.json()["detail"] == detail


def test_webhook_removal_previews_cover_every_provider_source(defs):
    """Removing the webhooks connection owns one source per provider."""
    defs.shipped(
        "webhooks",
        {
            "providers": {
                "alerts": {"secret_header": "X-Hook", "secret_env": "KEEN_ALERTS"},
                "pager": {"secret_header": "X-Hook", "secret_env": "KEEN_PAGER"},
            }
        },
    )
    hooks = defs.definition(
        "alerts",
        adapter="webhooks",
        section="source",
        entry={},
        rule=_rule("alerts", source="webhook:alerts"),
    )
    assert hooks.status_code == 200, hooks.text
    others = defs.definition(
        "all-ci",
        section="source",
        entry={},
        rule=_rule("all-ci", source="jenkins"),
        rules_version=1,
    )
    assert others.status_code == 200, others.text

    plan = _removal_preview(defs, "builtin:webhooks")

    assert plan.status_code == 200, plan.text
    body = plan.json()
    assert body["sources"] == ["webhook:alerts", "webhook:pager"]
    assert [d["id"] for d in body["definitions"]] == ["alerts"]
    assert (body["collections"], body["blocked"], body["kind"]) == (2, None, "builtin")


def test_an_api_connection_stays_while_a_moved_collector_runs_its_published_revision(
    defs,
):
    """A collector moved to another connection can still run a revision published here."""
    from app.db.models import (
        IntegrationCollector,
        IntegrationConnection,
        IntegrationRevision,
    )

    defs.db.add_all(
        [
            IntegrationConnection(
                id="old",
                name="Old API",
                base_url="https://old.example",
                auth_kind="none",
            ),
            IntegrationConnection(
                id="new",
                name="New API",
                base_url="https://new.example",
                auth_kind="none",
            ),
        ]
    )
    # Connections exist before the collectors that reference them.
    defs.db.flush()
    defs.db.add_all(
        [
            IntegrationCollector(
                id="checks",
                name="Checks",
                connection_id="new",
                draft={},
                live_revision=1,
            ),
            IntegrationRevision(
                collector_id="checks", revision=1, connection_id="old", definition={}
            ),
            # A collector still on this connection does not block its removal.
            IntegrationCollector(
                id="kept", name="Kept", connection_id="old", draft={}, live_revision=1
            ),
            IntegrationRevision(
                collector_id="kept", revision=1, connection_id="old", definition={}
            ),
        ]
    )
    defs.db.commit()

    plan = _removal_preview(defs, "api:old")

    assert plan.status_code == 200, plan.text
    assert plan.json()["blocked"] == (
        "Another collector still has a published revision using this connection. "
        "Republish it with its new connection first."
    )
    assert plan.json()["collectors"] == [{"id": "kept", "name": "Kept"}]
    removal = defs.admin.post(
        "/v1/admin/evidence-connections/api:old/remove",
        json={"fingerprint": plan.json()["fingerprint"]},
        headers=unsafe(defs.admin),
    )
    assert removal.status_code == 409
    assert removal.json()["detail"] == plan.json()["blocked"]
    defs.db.expire_all()
    assert defs.db.get(IntegrationConnection, "old") is not None


def test_removing_a_shipped_connection_records_first_managed_revisions(
    defs, monkeypatch
):
    """Removing a connection known only from YAML creates its first database rows."""
    defs.shipped("jenkins", {"poll_minutes": 15, "jobs": [{"name": "nightly"}]})
    rules = defs.tmp_path / "shipped-rules.yml"
    rules.write_text(
        yaml.safe_dump(
            {
                "rules": [
                    {
                        "id": "all-ci",
                        "when": {"source": "jenkins"},
                        "map_to": [{"framework": FW, "ref": "A.1"}],
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(settings, "rules_path", str(rules))
    plan = _removal_preview(defs, "builtin:jenkins").json()
    assert [d["id"] for d in plan["definitions"]] == ["all-ci"]

    removed = defs.admin.post(
        "/v1/admin/evidence-connections/builtin:jenkins/remove",
        json={"fingerprint": plan["fingerprint"]},
        headers=unsafe(defs.admin),
    )

    assert removed.status_code == 200, removed.text
    assert removed.json() == {
        "deleted_definitions": 1,
        "deleted_automatic_mappings": 0,
        "retained_evidence": True,
    }
    assert defs.document("jenkins") == {
        "name": "jenkins",
        "version": 1,
        "document": {"poll_minutes": 15, "jobs": []},
        "origin": "database",
    }
    assert defs.admin.get("/v1/admin/mapping-rules").json() == {
        "version": 1,
        "items": [],
    }
    assert defs.revisions("jenkins") == [1] and defs.revisions("rules") == [1]
