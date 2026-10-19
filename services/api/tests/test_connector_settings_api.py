"""Source settings edited in the web editor are refused when they cannot run.

Each refused document leaves the shipped or saved settings in place. Documents
pass the assembled application's administrator, CSRF and body checks; the
shipped files start empty.
"""

import pytest
from app.core.config import settings
from app.db.models import ManagedConfiguration

# Imported fixtures: the assembled application and empty shipped settings.
from tests.test_assembled_authority_regressions import keen  # noqa: F401
from tests.test_evidence_definitions_api import defs  # noqa: F401

RISKLEDGER_ORG = "00000000-0000-4000-8000-000000000001"

REFUSED = [
    ("rss", {"feeds": {"url": "https://feeds.example/a"}}, "feeds must be a list"),
    (
        "rss",
        {"feeds": [{"url": "http://feeds.example/a"}]},
        "RSS feeds require an HTTPS URL",
    ),
    (
        "rss",
        {"feeds": [{"url": "https://feeds.example/a", "verify_tls": False}]},
        "TLS verification cannot be disabled",
    ),
    (
        "rss",
        {"feeds": [{"url": "https://feeds.example/a", "headers": {"X-Key": "1"}}]},
        "RSS credentials and custom headers require administrator review",
    ),
    (
        "rss",
        {
            "feeds": [
                {"url": "https://feeds.example/a"},
                {"url": "https://feeds.example/a"},
            ]
        },
        "Every rss entry needs a distinct url",
    ),
    ("webhooks", {"providers": ["synthetic"]}, "providers must be an object"),
    (
        "webhooks",
        {"providers": {"bad name": {"secret_header": "X-Signature"}}},
        "Webhook provider name must use letters, numbers, underscores or hyphens",
    ),
    (
        "gitea",
        {"organizations": [{"org": "../ops"}]},
        "Enter a valid Gitea org account name",
    ),
    ("forgejo", {"users": "alice"}, "users must be a list of up to 500 accounts"),
    (
        "gitlab",
        {"groups": [{"group": "ops/web"}, {"group": "OPS/web"}]},
        "Duplicate GitLab groups",
    ),
    ("loki", {"queries": [{"name": "auth"}]}, "Each Loki query needs a name and LogQL"),
    (
        "loki",
        {"queries": [{"name": "auth", "logql": '{job="auth"}', "event": "auth"}]},
        "Event fields must be an object",
    ),
    (
        "loki",
        {
            "queries": [
                {"name": "auth", "logql": '{job="auth"}', "event": {"source": "github"}}
            ]
        },
        "The event source must match its adapter",
    ),
    (
        "cloudwatch_logs",
        {"queries": [{"name": "trail"}]},
        "Enter the CloudWatch log group",
    ),
    (
        "jenkins",
        {"jobs": [{"name": "folder/deploy"}]},
        "Jenkins job names must be simple top-level names",
    ),
    (
        "jenkins",
        {"jobs": [{"name": "deploy", "api_token": "synthetic"}]},
        "Move credential jobs.api_token into a server environment variable",
    ),
    ("bookstack", {"selected_pages": "all"}, "BookStack selected pages must be a list"),
    (
        "bookstack",
        {"selected_pages": [{"id": 4}, {"id": 4}]},
        "BookStack pages need distinct positive IDs",
    ),
    (
        "bookstack",
        {"selected_pages": [{"id": 4, "book_id": -1}]},
        "BookStack book ID must be a positive integer",
    ),
    (
        "bookstack",
        {"page_mappings": [{"match": "policies"}]},
        "BookStack page match must be an object",
    ),
    (
        "bookstack",
        {"page_mappings": [{"match": {"slug_regex": "(unclosed"}, "map_to": ["A.1"]}]},
        "slug_regex: Invalid pattern",
    ),
    (
        "github",
        {"repos": [{"owner": "acme", "repo": "app/extra"}]},
        "Give a valid GitHub organisation or owner/repository name",
    ),
    (
        "github",
        {"feeds": [{"url": "https://reader:pw@github.com/acme.atom"}]},
        "Feed URLs must be HTTPS without embedded credentials",
    ),
    (
        "github",
        {"feeds": [{"url": "https://github.com/acme.private.atom?token=synthetic"}]},
        "Feed URLs containing credentials cannot be linked",
    ),
    (
        "taiga",
        {"projects": [{"id": "web"}]},
        "Taiga project ID must be a positive integer",
    ),
    (
        "google_workspace",
        {"streams": [{"name": "logins"}]},
        "Choose a Google Workspace application for this stream",
    ),
    (
        "redmine",
        {"projects": [{"project": "../infra"}]},
        "Use a Redmine project ID, identifier or *",
    ),
    (
        "riskledger",
        {"organizations": [{"org": "customer"}]},
        "Risk Ledger requires a valid UUID identifier",
    ),
]


@pytest.mark.parametrize(
    "name,document,detail",
    REFUSED,
    ids=[f"{name}-{i}" for i, (name, _, _) in enumerate(REFUSED)],
)
def test_settings_that_cannot_run_are_refused(defs, name, document, detail):  # noqa: F811
    refused = defs.put_document(name, document, 0)

    assert refused.status_code == 400, refused.text
    assert refused.json()["detail"].startswith(detail)
    assert defs.db.get(ManagedConfiguration, name) is None


@pytest.mark.parametrize(
    "name,document",
    [
        ("gitea", {"organizations": [{"org": "ops"}], "users": [{"user": "alice"}]}),
        ("gitlab", {"groups": [{"group": "ops/web"}], "users": [{"user": "alice"}]}),
        (
            "riskledger",
            {"organizations": [{"org": RISKLEDGER_ORG, "label": "Customer"}]},
        ),
        ("redmine", {"projects": [{"project": "infra"}]}),
        ("taiga", {"projects": [{"id": 12}]}),
        ("bookstack", {"selected_pages": [{"id": 4, "book_id": 2}]}),
    ],
)
def test_runnable_settings_are_saved_with_a_revision(defs, name, document):  # noqa: F811
    saved = defs.put_document(name, document, 0)

    assert saved.status_code == 200, saved.text
    assert saved.json() == {"version": 1, "document": document}
    assert defs.document(name)["document"] == document


def test_unknown_sources_and_demo_feeds_are_refused(defs, monkeypatch):  # noqa: F811
    unknown = defs.put_document("ftp", {"servers": []}, 0)
    assert (unknown.status_code, unknown.json()["detail"]) == (404, "Unknown connector")
    assert defs.admin.get("/v1/admin/managed-configurations/ftp").status_code == 404

    monkeypatch.setattr(settings, "demo_mode", True)
    from datetime import datetime, timezone

    monkeypatch.setattr(
        settings, "demo_expires_at", datetime(2999, 1, 1, tzinfo=timezone.utc)
    )
    demo = defs.put_document("rss", {"feeds": [{"url": "https://feeds.example/a"}]}, 0)
    assert demo.status_code == 403


def test_feeds_with_credentials_in_yaml_stay_out_of_the_web_editor(defs):  # noqa: F811
    defs.shipped(
        "rss",
        {"feeds": [{"url": "https://feeds.example/a", "auth": {"username": "reader"}}]},
    )

    response = defs.admin.get("/v1/admin/managed-configurations/rss")

    assert response.status_code == 409
    assert response.json()["detail"].startswith(
        "This RSS configuration contains headers or credentials"
    )
