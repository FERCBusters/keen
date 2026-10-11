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
