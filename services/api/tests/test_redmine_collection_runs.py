"""Redmine collection runs from the scheduled entry point.

A connection supplies the Redmine base URL and API key; each run reads the
selected projects' issues and journals, stores them as evidence events and
moves each project's cursor. An in-memory transport stands in for Redmine.
Runs use the contract database because they hold a PostgreSQL advisory lock.
"""

from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from app.core.config import settings
from app.db.models import Event, IngestionCursor
from app.ingest import redmine
from app.ingest.connections import Connection, connection_scope, namespace
from sqlalchemy import text

BASE = "https://93.184.216.34/redmine"
KEY = "synthetic-redmine-key"
PROJECT = {"id": 2, "identifier": "infra", "name": "Infrastructure"}


def issue(issue_id=7, project_id=2, **fields):
    return {
        "id": issue_id,
        "project": {"id": project_id, "name": "Infrastructure"},
        "subject": f"Issue {issue_id}",
        "created_on": "2026-09-01T00:00:00Z",
        "updated_on": "2026-10-01T10:00:00Z",
        "status": {"id": 3, "name": "Resolved", "is_closed": True},
        "journals": [
            {
                "id": issue_id * 10,
                "created_on": "2026-09-02T00:00:00Z",
                "user": {"id": 6, "name": "Operator"},
                "notes": "Patched",
                "details": [],
            }
        ],
        **fields,
    }


class Redmine:
    def __init__(self):
        self.requests = []
        self.projects = {"infra": PROJECT}
        self.issues = {7: issue()}

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path.removeprefix("/redmine/")
        offset = int(request.url.params.get("offset", 0))
        if path == "projects.json":
            rows = list(self.projects.values())[offset : offset + 100]
            total = len(self.projects)
            return httpx.Response(
                200, json={"projects": rows, "offset": offset, "total_count": total}
            )
        if path.startswith("projects/"):
            key = path.removeprefix("projects/").removesuffix(".json")
            project = self.projects.get(key) or next(
                (p for p in self.projects.values() if str(p["id"]) == key), None
            )
            if project is None:
                return httpx.Response(404)
            return httpx.Response(200, json={"project": project})
        if path == "issues.json":
            rows = [{"id": i} for i in sorted(self.issues)][offset : offset + 100]
            return httpx.Response(
                200,
                json={
                    "issues": rows,
                    "offset": offset,
                    "total_count": len(self.issues),
                },
            )
        if path.startswith("issues/"):
            issue_id = int(path.removeprefix("issues/").removesuffix(".json"))
            return httpx.Response(200, json={"issue": self.issues[issue_id]})
        return httpx.Response(404)


@pytest.fixture
def tracker(contract_db, monkeypatch, tmp_path):
    if contract_db.get_bind().dialect.name != "postgresql":
        pytest.skip("Redmine runs hold a PostgreSQL advisory lock")
    for name, value in {
        "demo_mode": False,
        "redmine_enabled": True,
        "event_data_masking": "false",
        "artifact_storage_backend": "local",
        "artifact_local_dir": str(tmp_path),
    }.items():
        monkeypatch.setattr(settings, name, value)
    api = Redmine()
    clients = []

    def client(**kwargs):
        clients.append(kwargs)
        return httpx.Client(transport=httpx.MockTransport(api), **kwargs)

    monkeypatch.setattr(redmine, "ingestion_client", client)
    inputs = {"projects": [{"project": "infra", "label": "Infrastructure"}]}
    state = SimpleNamespace(base_url=BASE, api_key=KEY)

    def connection():
        return Connection(
            "db:redmine",
            "redmine",
            "Redmine",
            {"base_url": state.base_url},
            {"api_key": state.api_key},
            inputs,
        )

    def run():
        with patch("app.ingest.connections.connections", return_value=[connection()]):
            return redmine.ingest_redmine_all(contract_db)

    def cursor(project_id=2):
        import hashlib

        digest = hashlib.sha256(f"{BASE}:{project_id}".encode()).hexdigest()
        with connection_scope(connection()):
            name = namespace("redmine:" + digest)
        return contract_db.query(IngestionCursor).filter_by(name=name).one_or_none()

    def events():
        return (
            contract_db.query(Event)
            .filter_by(source="redmine")
            .order_by(Event.timestamp)
            .all()
        )

    return SimpleNamespace(
        db=contract_db,
        api=api,
        clients=clients,
        inputs=inputs,
        state=state,
        run=run,
        cursor=cursor,
        events=events,
    )


def test_a_run_stores_issues_and_journals_and_later_runs_read_changes(tracker):
    (result,) = tracker.run()

    assert {
        k: result[k] for k in ("project_id", "examined_issues", "created_events")
    } == {
        "project_id": 2,
        "examined_issues": 1,
        "created_events": 2,
    }
    (options,) = tracker.clients
    assert options["headers"]["X-Redmine-API-Key"] == KEY
    assert options["follow_redirects"] is False
    comment, snapshot = sorted(tracker.events(), key=lambda e: e.action)
    assert (comment.action, comment.actor, comment.system) == (
        "issue.comment",
        "Operator",
        "Infrastructure",
    )
    assert comment.raw_pointer["redmine"]["url"] == BASE + "/issues/7#change-70"
    assert (snapshot.action, snapshot.actor) == ("issue.snapshot", None)
    first = [r for r in tracker.api.requests if r.url.path.endswith("/issues.json")]
    # The first run reads every issue in the project, closed ones included.
    assert "updated_on" not in first[0].url.params
    assert first[0].url.params["status_id"] == "*"
    cursor = tracker.cursor()
    assert cursor.last_ts and cursor.meta["last_full_scan"]

    tracker.api.requests.clear()
    tracker.api.issues[7]["journals"].append(
        {"id": 71, "created_on": "2026-10-02T00:00:00Z", "user": {"name": "Reviewer"}}
    )
    (later,) = tracker.run()

    assert later["created_events"] == 1
    listing = next(
        r for r in tracker.api.requests if r.url.path.endswith("/issues.json")
    )
    # Later runs ask for changes since the cursor, less the overlap.
    assert listing.url.params["updated_on"].startswith(">=")
    assert [e.action for e in tracker.events()].count("issue.updated") == 1


def test_a_project_named_twice_is_collected_once(tracker):
    tracker.inputs["projects"] = [{"project": "infra"}, {"project": 2}]

    results = tracker.run()

    assert [r["project_id"] for r in results] == [2]


def test_an_issue_moved_mid_run_keeps_the_project_cursor(tracker):
    tracker.api.issues[7] = issue(project_id=3)

    (result,) = tracker.run()

    assert result == {
        "project_id": 2,
        "error": "Issue moved project during collection; retry required",
        "connection_id": "db:redmine",
        "connection_name": "Redmine",
    }
    assert tracker.cursor() is None and tracker.events() == []


def test_project_discovery_failures_are_reported_per_selection(tracker):
    tracker.inputs["projects"] = [{"project": "retired"}]

    (missing,) = tracker.run()
    assert missing["collection"] == "retired"
    assert missing["error"].startswith("Redmine API HTTP 404")

    tracker.api.projects = {}
    tracker.inputs["projects"] = [{"project": "*"}]
    (none,) = tracker.run()
    assert none["reason"] == "No accessible projects returned by Redmine"


@pytest.mark.parametrize(
    "base_url,error",
    [
        ("https://reader:secret@93.184.216.34/redmine", "Configure a safe HTTPS"),
        ("https://93.184.216.34/redmine?key=1", "Configure a safe HTTPS"),
        ("https://93.184.216.34/redmine#top", "Configure a safe HTTPS"),
        ("http://93.184.216.34/redmine", "Configure a safe HTTPS"),
        ("https://127.0.0.1/redmine", "Configure a safe HTTPS"),
        ("https://[::1/redmine", "Invalid KEEN_REDMINE_BASE_URL"),
    ],
)
def test_unsafe_base_urls_stop_the_run_before_any_request(tracker, base_url, error):
    tracker.state.base_url = base_url

    (result,) = tracker.run()

    assert result["error"].startswith(error)
    assert tracker.api.requests == [] and tracker.clients == []


def test_missing_keys_and_settings_stop_the_run(tracker):
    tracker.state.api_key = " "
    assert tracker.run()[0]["error"] == (
        "Set KEEN_REDMINE_API_KEY in the API/worker environment"
    )

    tracker.state.api_key = KEY
    tracker.inputs["projects"] = [{"project": "../escape"}]
    assert tracker.run()[0]["error"] == (
        "Invalid Redmine configuration; check projects and shared settings"
    )

    tracker.inputs["projects"] = []
    assert tracker.run()[0]["reason"].startswith("Add a Redmine project collection")
    assert tracker.api.requests == []


def test_one_run_per_redmine_instance_at_a_time(tracker):
    import hashlib

    key = int.from_bytes(
        hashlib.sha256(("redmine:" + BASE).encode()).digest()[:8], "big", signed=True
    )
    with tracker.db.get_bind().engine.connect() as other:
        assert other.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
        ).scalar()
        try:
            (result,) = tracker.run()
        finally:
            other.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            other.commit()

    assert result["reason"] == "Another Redmine run is in progress"
    assert tracker.api.requests == []


@pytest.mark.parametrize(
    "cfg,message",
    [
        ({"projects": {"project": "*"}}, "list of up to 500"),
        ({"projects": ["infra"]}, "must be an object"),
        ({"projects": [{"project": "0"}]}, "must be positive"),
        ({"projects": [{"project": "infra", "label": "x" * 129}]}, "at most 128"),
        ({"initial_lookback_days": -1}, "initial_lookback_days"),
        ({"overlap_seconds": 59}, "overlap_seconds"),
        ({"full_scan_hours": True}, "full_scan_hours"),
    ],
)
def test_settings_are_checked_before_collection(cfg, message):
    with pytest.raises(redmine.RedmineError, match=message):
        redmine.validate_config(cfg)


@pytest.mark.parametrize(
    "reply,message",
    [
        (httpx.Response(200, json=[1]), "invalid JSON object"),
        (httpx.ConnectError("refused"), "network/TLS failure"),
        (
            httpx.Response(200, json={"issues": [1], "offset": 0, "total_count": 1}),
            "invalid collection",
        ),
        (
            httpx.Response(200, json={"issues": [], "offset": 5, "total_count": 0}),
            "inconsistent",
        ),
        (
            httpx.Response(200, json={"issues": [], "offset": 0, "total_count": 3}),
            "incomplete page",
        ),
        (
            httpx.Response(
                200, json={"issues": [{"id": "7"}], "offset": 0, "total_count": 1}
            ),
            "invalid numeric ID",
        ),
    ],
    ids=["array", "unreachable", "rows", "offset", "incomplete", "string-id"],
)
def test_unusable_listings_stop_collection(reply, message):
    def handler(request):
        if isinstance(reply, Exception):
            raise reply
        return reply

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(redmine.RedmineError, match=message):
            list(redmine._pages(client, BASE, "issues"))


@pytest.mark.parametrize(
    "detail,message,kept",
    [
        ({"issue": issue(issue_id=8)}, "inconsistent issue detail", []),
        ({"issue": "missing"}, "inconsistent issue detail", []),
        ({"issue": issue(updated_on="yesterday")}, "invalid timestamp", []),
        # The issue snapshot is stored before its journals are read.
        ({"issue": issue(journals={"id": 1})}, "invalid journals", ["issue.snapshot"]),
        ({"issue": issue(journals=["note"])}, "invalid journals", ["issue.snapshot"]),
    ],
    ids=["other-issue", "not-an-object", "time", "journals-object", "journal-text"],
)
def test_inconsistent_issue_details_keep_the_project_cursor(
    tracker, detail, message, kept
):
    original = Redmine.__call__

    def reply(api, request):
        if request.url.path.endswith("/issues/7.json"):
            return httpx.Response(200, json=detail)
        return original(api, request)

    with patch.object(Redmine, "__call__", reply):
        (result,) = tracker.run()

    assert result["project_id"] == 2 and message in result["error"]
    # The next run reads the project again.
    assert tracker.cursor() is None
    assert [e.action for e in tracker.events()] == kept


def test_custom_fields_and_journal_changes_become_searchable_fields(tracker):
    tracker.api.issues[7] = issue(
        custom_fields=[
            {"id": 4, "name": "Risk", "value": "High"},
            {"id": 5, "name": "Systems", "value": ["api", "db"]},
            {"id": "6", "value": "ignored"},
            "ignored",
        ],
        journals=[
            {
                "id": 70,
                "created_on": "2026-09-02T00:00:00Z",
                "notes": "",
                "private_notes": True,
                "details": [
                    {
                        "property": "cf",
                        "name": "4",
                        "old_value": "Low",
                        "new_value": "High",
                    },
                    {"property": "attr", "name": "status id; drop", "new_value": "x"},
                    {"property": "relation", "name": "relates", "new_value": "9"},
                    "ignored",
                ],
            }
        ],
    )

    tracker.run()

    snapshot, change = sorted(
        tracker.events(), key=lambda e: e.action != "issue.snapshot"
    )
    fields = snapshot.normalized_payload["fields"]
    assert (fields["issue.custom_field.4"], fields["issue.custom_field.5"]) == (
        "High",
        '["api", "db"]',
    )
    assert not any(key.startswith("issue.custom_field.6") for key in fields)
    changed = change.normalized_payload["fields"]
    assert change.action == "issue.updated"
    assert change.summary == "Redmine #7: Issue 7: fields updated"
    assert changed["journal.private_notes"] is True
    assert {k: v for k, v in changed.items() if k.startswith("change.")} == {
        "change.cf.4.changed": True,
        "change.cf.4.old_value": "Low",
        "change.cf.4.new_value": "High",
    }


def test_a_first_run_can_start_from_a_lookback_window(tracker):
    tracker.inputs["initial_lookback_days"] = 30

    tracker.run()

    listing = next(
        r for r in tracker.api.requests if r.url.path.endswith("/issues.json")
    )
    assert listing.url.params["updated_on"].startswith(">=")
    assert tracker.cursor().meta["history_since"]
