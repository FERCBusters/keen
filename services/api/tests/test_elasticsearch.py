"""Elasticsearch polling contracts with local HTTP fixtures and real persistence."""

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import app.services.source_connections as source_connections_operations
import httpx
import pytest
from app.db.models import Artifact, Event, IngestionCursor
from app.ingest import common
from app.ingest import elasticsearch as es
from app.ingest.connections import Connection, connection_scope
from app.mapping.collector import (
    collector_id,
    collector_pointer_filter,
    event_collector,
)
from app.services.errors import DomainError


def input_config(**values):
    return es.validate_input({"name": "Auth", "index_pattern": "logs-*", **values})


def hit(identity="1", **values):
    return {
        "_index": "logs-2026.10",
        "_id": identity,
        "_source": {
            "message": "Login failed",
            "host": {"name": "host"},
            "user": {"name": "alice"},
            "password": "synthetic-secret",
        },
        "fields": {"@timestamp": ["2026-10-11T00:00:00.123456789Z"]},
        "sort": [int(identity)],
        **values,
    }


@pytest.fixture
def storage(monkeypatch):
    objects = {}

    def put_bytes(key, data, content_type):
        objects[key] = data
        return SimpleNamespace(
            uri="local://local/" + key,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
        )

    monkeypatch.setattr(common, "put_bytes", put_bytes)
    monkeypatch.setattr(common, "apply_rules", lambda db, event: 0)
    return objects


def scoped(key="one", **values):
    return connection_scope(
        Connection(
            key,
            "elasticsearch",
            "Logs " + key,
            values.get("configuration", {"base_url": "https://elastic.example"}),
            values.get("credentials", {}),
            {},
        )
    )


def mock_client(pages, calls):
    responses = iter(pages)

    def handle(request):
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url, body))
        if request.method == "DELETE":
            return httpx.Response(200, json={"succeeded": True})
        if request.url.path.endswith("/_pit"):
            return httpx.Response(
                200, json={"id": "pit-initial", "_shards": {"failed": 0}}
            )
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        return httpx.Response(200, json=value)

    return httpx.Client(
        base_url="https://elastic.example/prefix/",
        transport=httpx.MockTransport(handle),
    )


def page(hits, **kwargs):
    return {
        "hits": {"hits": hits},
        "timed_out": False,
        "_shards": {"failed": 0},
        **kwargs,
    }


def test_pages_same_timestamp_redaction_dedup_and_connection_isolation(
    contract_db, storage
):
    for key in ("one", "one", "two"):
        calls = []
        with (
            scoped(key),
            mock_client([page([hit()], pit_id="pit-next"), page([])], calls) as client,
        ):
            result = es.ingest_elasticsearch_once(
                contract_db,
                client,
                input_config(page_size=1, initial_lookback_minutes=1),
                time.monotonic() + 120,
            )
        assert result["caught_up"]
        assert calls[2][2]["search_after"] == [1]
        assert calls[2][2]["pit"]["id"] == "pit-next"
        assert calls[-1][0] == "DELETE" and calls[-1][2] == {"id": "pit-next"}
        assert all(
            str(call[1]).startswith("https://elastic.example/prefix/") for call in calls
        )
    rows = contract_db.query(Event).all()
    assert len(rows) == 2
    assert {row.connection_id for row in rows} == {"one", "two"}
    assert len({row.external_id for row in rows}) == 2
    for row in rows:
        assert row.system == "host" and row.actor == "alice"
        assert "synthetic-secret" not in json.dumps(row.normalized_payload)
        event = {"source": row.source, "raw_pointer": row.raw_pointer}
        assert event_collector(event) == collector_id(
            "elasticsearch", "queries", "Auth"
        )
        assert collector_pointer_filter(event_collector(event)) == {
            "elasticsearch": {"query_name": "Auth"}
        }
    assert contract_db.query(Artifact).count() == 2
    assert all(b"synthetic-secret" not in data for data in storage.values())


@pytest.mark.parametrize(
    "failure",
    [
        page([], timed_out=True),
        page([], _shards={"failed": 1}),
        page([], terminated_early=True),
        {},
        httpx.ReadTimeout("sensitive upstream diagnostic"),
    ],
)
def test_incomplete_window_keeps_cursor_and_closes_pit(contract_db, storage, failure):
    calls = []
    with scoped(), mock_client([failure], calls) as client:
        with pytest.raises((es.ElasticsearchInputError, httpx.ReadTimeout)):
            es.ingest_elasticsearch_once(
                contract_db, client, input_config(), time.monotonic() + 120
            )
    cursor = contract_db.query(IngestionCursor).one()
    assert cursor.last_ts is None
    assert cursor.meta["initial_start"]
    assert calls[-1][0] == "DELETE"


def test_page_limit_commits_hits_but_never_skips_unread_window(contract_db, storage):
    calls = []
    with scoped(), mock_client([page([hit()])], calls) as client:
        with pytest.raises(es.ElasticsearchInputError, match="page limit"):
            es.ingest_elasticsearch_once(
                contract_db,
                client,
                input_config(page_size=1, max_pages=1),
                time.monotonic() + 120,
            )
    assert contract_db.query(Event).count() == 1
    assert contract_db.query(IngestionCursor).one().last_ts is None


def test_repeat_page_is_rejected(contract_db, storage):
    calls = []
    with scoped(), mock_client([page([hit()]), page([hit()])], calls) as client:
        with pytest.raises(es.ElasticsearchInputError, match="did not progress"):
            es.ingest_elasticsearch_once(
                contract_db, client, input_config(page_size=1), time.monotonic() + 120
            )
    assert contract_db.query(IngestionCursor).one().last_ts is None


def test_empty_window_advances_and_next_run_overlaps(contract_db, storage):
    calls = []
    with scoped(), mock_client([page([]), page([])], calls) as client:
        entry = input_config(initial_lookback_minutes=1)
        es.ingest_elasticsearch_once(contract_db, client, entry, time.monotonic() + 120)
        first_end = (
            contract_db.query(IngestionCursor)
            .one()
            .last_ts.replace(tzinfo=timezone.utc)
        )
        es.ingest_elasticsearch_once(contract_db, client, entry, time.monotonic() + 120)
    searches = [body for method, url, body in calls if url.path.endswith("_search")]
    lower = searches[1]["query"]["bool"]["filter"][0]["range"]["@timestamp"]["gte"]
    assert datetime.fromisoformat(lower) == first_end - timedelta(minutes=5)


@pytest.mark.parametrize(
    "values",
    [
        {"index_pattern": "../_security"},
        {"index_pattern": "https://evil.example"},
        {"index_pattern": "logs-*?x=y"},
        {"index_pattern": "logs-*#secret"},
        {"index_pattern": "_all"},
        {"timestamp_field": "*"},
        {"page_size": 1001},
        {"max_pages": 0},
        {"overlap_minutes": 60, "window_minutes": 60},
        {"initial_lookback_minutes": True},
        {"query": "x" * 4097},
    ],
)
def test_input_validation(values):
    with pytest.raises(es.ElasticsearchInputError):
        input_config(**values)


def test_auth_transport_and_no_environment_secret_inheritance(monkeypatch):
    from app.ingest.connections import deployment_settings
    from app.ingest.http import IngestionTransport

    monkeypatch.setattr(deployment_settings, "elasticsearch_api_key", "environment-key")
    with (
        scoped(
            credentials={"password": "password"},
            configuration={"base_url": "https://elastic.example", "username": "user"},
        ),
        es._client() as client,
    ):
        assert isinstance(client._transport, IngestionTransport)
        assert client.follow_redirects is False
        assert isinstance(client.auth, httpx.BasicAuth)
        assert "authorization" not in client.headers
    with scoped(credentials={"api_key": "encoded-key"}), es._client() as client:
        assert client.headers["Authorization"] == "ApiKey encoded-key"
    with scoped(credentials={"api_key": "encoded-key", "password": "secret"}):
        with pytest.raises(es.ElasticsearchInputError):
            es._client()
    with scoped(configuration={"base_url": "http://elastic.example"}):
        with pytest.raises(es.ElasticsearchInputError):
            es._client()


def test_connection_api_validation_and_type_metadata(contract_db, monkeypatch):
    from app.api.routes import source_connections
    from app.ingest.connections import deployment_settings

    monkeypatch.setattr(deployment_settings, "elasticsearch_enabled", True)
    payload = source_connections.ConnectionInput(
        source="elasticsearch",
        name="Logs",
        configuration={"base_url": "https://elastic.example"},
        inputs={"queries": [{"name": "Auth", "index_pattern": "logs-*"}]},
    )
    source_connections_operations.validate(payload)
    from app.core import source_meta

    monkeypatch.setattr(source_meta, "_configured_source_meta", lambda bucket: {})
    types = source_connections.listing(contract_db)["types"]
    entry = next(item for item in types if item["source"] == "elasticsearch")
    assert entry["enabled"] and entry["label"] == "Elasticsearch"
    assert entry["credential_fields"] == ["password", "api_key"]


@pytest.mark.parametrize(
    "bad_hit",
    [
        hit(fields={}),
        hit(fields={"@timestamp": ["not-a-date"]}),
        hit(_source=None),
        hit(
            _source={"host": {"name": "x" * 500}, "message": "Long host"},
            fields={"@timestamp": ["2026-10-11T00:00:00Z"]},
        ),
    ],
)
def test_document_validation_and_database_field_bounds(contract_db, storage, bad_hit):
    calls = []
    with scoped(), mock_client([page([bad_hit])], calls) as client:
        if (
            isinstance(bad_hit.get("_source"), dict)
            and bad_hit["_source"].get("message") == "Long host"
        ):
            es.ingest_elasticsearch_once(
                contract_db,
                client,
                input_config(initial_lookback_minutes=1),
                time.monotonic() + 120,
            )
            assert len(contract_db.query(Event).one().system) == 128
        else:
            with pytest.raises(es.ElasticsearchInputError):
                es.ingest_elasticsearch_once(
                    contract_db, client, input_config(), time.monotonic() + 120
                )
            assert contract_db.query(IngestionCursor).one().last_ts is None
    assert calls[-1][0] == "DELETE"


def test_connection_rejects_http_and_secret_field_in_inputs():
    from app.services.source_connections import ConnectionInput, validate
    from fastapi import HTTPException

    with pytest.raises((HTTPException, DomainError)) as error:
        validate(
            ConnectionInput(
                source="elasticsearch",
                name="Logs",
                configuration={"base_url": "http://elastic.example"},
            )
        )
    assert error.value.status_code == 422
    with pytest.raises((HTTPException, DomainError)):
        validate(
            ConnectionInput(
                source="elasticsearch",
                name="Logs",
                configuration={"base_url": "https://elastic.example"},
                inputs={
                    "queries": [
                        {
                            "name": "Auth",
                            "index_pattern": "logs-*",
                            "password": "wrong-place",
                        }
                    ]
                },
            )
        )


class Elastic:
    """Point-in-time searches per index pattern, with scripted failures."""

    def __init__(self):
        self.requests = []
        self.pages = {}
        self.pit = {}
        self.fail_close = False

    def __call__(self, request):
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, request.url.path, body))
        if request.method == "DELETE":
            if self.fail_close:
                raise httpx.ConnectError("close failed for pit-secret")
            return httpx.Response(200, json={"succeeded": True})
        if request.url.path.endswith("/_pit"):
            index = request.url.path.split("/")[-2]
            value = self.pit.get(
                index, {"id": "pit-" + index, "_shards": {"failed": 0}}
            )
            if isinstance(value, Exception):
                raise value
            return httpx.Response(200, json=value)
        index = body["pit"]["id"].removeprefix("pit-")
        return httpx.Response(200, json=self.pages[index].pop(0))


@pytest.fixture
def elastic(contract_db, storage, monkeypatch):
    from unittest.mock import patch

    from app.ingest.connections import deployment_settings

    if contract_db.get_bind().dialect.name != "postgresql":
        pytest.skip("Connection runs hold a PostgreSQL advisory lock")
    monkeypatch.setattr(deployment_settings, "elasticsearch_enabled", True)
    server = Elastic()
    clients = []

    def client(**kwargs):
        clients.append(kwargs)
        return httpx.Client(transport=httpx.MockTransport(server), **kwargs)

    monkeypatch.setattr(es, "ingestion_client", client)

    def run(*queries, credentials=None):
        connection = Connection(
            "db:logs",
            "elasticsearch",
            "Production logs",
            {"base_url": "https://elastic.example/prefix"},
            credentials or {"api_key": "synthetic-api-key"},
            {"queries": list(queries)},
        )
        with patch("app.ingest.connections.connections", return_value=[connection]):
            return es.ingest_elasticsearch_all(contract_db)

    return SimpleNamespace(db=contract_db, server=server, clients=clients, run=run)


def recent(name, index, **values):
    return {
        "name": name,
        "index_pattern": index,
        "initial_lookback_minutes": 30,
        **values,
    }


def test_a_run_collects_each_query_and_reports_failures_without_upstream_detail(
    elastic,
):
    elastic.server.pages["auth-*"] = [page([hit()])]
    elastic.server.pit["broken-*"] = httpx.ConnectError("10.0.0.5 refused api_key=leak")
    elastic.server.pit["bad-*"] = {"_shards": {"failed": 0}}

    results = elastic.run(
        recent("Auth", "auth-*", query='"login failed"', message_field="log.message"),
        recent("Broken", "broken-*"),
        recent("Bad", "bad-*"),
    )

    assert [
        {key: row.get(key) for key in ("query_name", "created_events", "error")}
        for row in results
    ] == [
        {"query_name": "Auth", "created_events": 1, "error": None},
        {
            "query_name": "Broken",
            "created_events": None,
            "error": "Connection failed; check DNS, connectivity and the upstream TLS certificate",
        },
        {
            "query_name": "Bad",
            "created_events": None,
            "error": "Elasticsearch did not return a valid point-in-time identifier",
        },
    ]
    assert all(row["connection_id"] == "db:logs" for row in results)
    assert results[0]["caught_up"] is True
    (options,) = elastic.clients
    assert options["base_url"] == "https://elastic.example/prefix/"
    assert options["headers"] == {"Authorization": "ApiKey synthetic-api-key"}
    # Query text is matched in the message field with a fixed, bounded syntax.
    search = next(
        body
        for method, path, body in elastic.server.requests
        if path.endswith("_search")
    )
    assert search["query"]["bool"]["filter"][1] == {
        "simple_query_string": {
            "query": '"login failed"',
            "fields": ["log.message"],
            "default_operator": "and",
            "flags": "AND|OR|NOT|PHRASE|PRECEDENCE|WHITESPACE|ESCAPE",
        }
    }
    (event,) = elastic.db.query(Event).all()
    assert (event.connection_id, event.source) == ("db:logs", "elasticsearch")


def test_queries_beyond_the_run_budget_wait_for_the_next_run(elastic, monkeypatch):
    monkeypatch.setattr(es, "RUN_SECONDS", -1)

    results = elastic.run(recent("Auth", "auth-*"))

    assert [(row["query_name"], row["skipped"], row["reason"]) for row in results] == [
        ("Auth", True, "Connection run budget exhausted; run collection again")
    ]
    assert elastic.server.requests == []


def test_an_empty_input_list_contacts_nobody(elastic):
    assert elastic.run() == []
    assert elastic.clients == []


def test_a_failed_point_in_time_close_does_not_fail_the_window(elastic):
    elastic.server.pages["auth-*"] = [page([hit()])]
    elastic.server.fail_close = True

    (result,) = elastic.run(recent("Auth", "auth-*"))

    assert (result["created_events"], result["windows_completed"]) == (1, 1)
    assert elastic.db.query(IngestionCursor).one().last_ts is not None


def test_windows_stop_at_the_collection_deadline(contract_db, storage, monkeypatch):
    calls = []
    clock = iter([0, 0, 50])
    monkeypatch.setattr(es, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    with scoped(), mock_client([page([hit("1"), hit("2")])], calls) as client:
        with pytest.raises(es.ElasticsearchInputError, match="time budget"):
            es.ingest_elasticsearch_once(contract_db, client, input_config(), 10)
    # The deadline is checked before each document; the window is retried.
    assert calls[-1][0] == "DELETE"
    assert contract_db.query(IngestionCursor).one().last_ts is None


def test_a_search_page_with_an_unusable_point_in_time_is_refused(contract_db, storage):
    calls = []
    with scoped(), mock_client([page([], pit_id="")], calls) as client:
        with pytest.raises(es.ElasticsearchInputError, match="invalid point-in-time"):
            es.ingest_elasticsearch_once(
                contract_db, client, input_config(), time.monotonic() + 120
            )


@pytest.mark.parametrize(
    "document,detail",
    [
        ([], "Elasticsearch configuration must be an object"),
        ({"queries": {}}, "Configure up to 50 Elasticsearch inputs per connection"),
        (
            {
                "queries": [
                    {"name": f"q{i}", "index_pattern": "logs-*"} for i in range(51)
                ]
            },
            "Configure up to 50 Elasticsearch inputs per connection",
        ),
        (
            {"queries": [{"name": "Auth", "index_pattern": "a-*"}] * 2},
            "Elasticsearch input names must be distinct",
        ),
        ({"queries": ["Auth"]}, "Elasticsearch inputs must be objects"),
        (
            {"queries": [{"name": "Auth/../x", "index_pattern": "logs-*"}]},
            "Enter an input name using letters, numbers, spaces, dots, hyphens or underscores",
        ),
        (
            {"queries": [{"name": "   ", "index_pattern": "logs-*"}]},
            "Enter an input name using letters, numbers, spaces, dots, hyphens or underscores",
        ),
        (
            {"queries": [{"name": "Auth"}]},
            "Enter an index, alias or data stream pattern",
        ),
    ],
)
def test_configuration_documents_are_checked_as_a_whole(document, detail):
    with pytest.raises(es.ElasticsearchInputError) as error:
        es.validate_config(document)
    assert str(error.value) == detail


@pytest.mark.parametrize(
    "configuration,credentials,detail",
    [
        (
            {"base_url": "https://elastic.example", "username": "user"},
            {},
            "Supply both username and password",
        ),
        (
            {"base_url": "https://elastic.example"},
            {"api_key": "ApiKey with spaces"},
            "Enter the encoded Elasticsearch API key without the Authorization prefix",
        ),
        (
            {"base_url": "https://elastic.example"},
            {"api_key": "k" * 8193},
            "Enter the encoded Elasticsearch API key without the Authorization prefix",
        ),
    ],
)
def test_unusable_credentials_are_refused_before_connecting(
    configuration, credentials, detail
):
    with scoped(configuration=configuration, credentials=credentials):
        with pytest.raises(es.ElasticsearchInputError) as error:
            es._client()
    assert str(error.value) == detail


@pytest.mark.parametrize("value", ["2026-10-11T00:00:00", 5, "x" * 65])
def test_document_dates_need_a_time_zone(value):
    with pytest.raises(es.ElasticsearchInputError, match="single valid date"):
        es._timestamp(value)
