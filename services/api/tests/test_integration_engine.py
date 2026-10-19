"""Record normalisation and bounded collection for custom API integrations.

Definitions map upstream JSON to evidence fields with a fixed set of
transforms; collection pages through an API within request, time, size and
origin limits. A recording fetch function stands in for the network.
"""

from datetime import datetime, timedelta, timezone

import pytest
from app.integrations import engine
from app.integrations.engine import collect, normalize
from app.integrations.errors import IntegrationError
from app.integrations.schema import Definition

BASE = "https://api.example.org"


def definition(**fields):
    mapped = {
        "id": {"path": "/id"},
        "timestamp": {"path": "/at"},
        "summary": {"path": "/name"},
    }
    mapped.update(fields)
    return Definition(fields=mapped)


RECORD = {"id": 7, "at": "2026-01-01T12:00:00Z", "name": "Nightly backup"}


@pytest.mark.parametrize(
    "mapping,record,expected",
    [
        ({"path": "/state", "transform": "lower"}, {"state": "PASSED"}, "passed"),
        ({"path": "/state", "transform": "upper"}, {"state": "passed"}, "PASSED"),
        (
            {"path": "/state", "values": {"0": "failure", "1": "success"}},
            {"state": 1},
            "success",
        ),
        ({"value": "backup-server"}, {}, "backup-server"),
        (
            {"path": "/tags", "transform": "json"},
            {"tags": {"b": 2, "a": 1}},
            '{"a": 1, "b": 2}',
        ),
    ],
    ids=["lower", "upper", "lookup", "constant", "json"],
)
def test_text_transforms_shape_the_stored_value(mapping, record, expected):
    fields = normalize({**RECORD, **record}, definition(system=mapping))

    assert fields["system"] == expected


def test_numbers_booleans_and_severities_are_typed():
    d = definition(
        severity={"path": "/severity"},
        outcome={"path": "/ok", "transform": "boolean"},
    )

    fields = normalize({**RECORD, "severity": "7", "ok": "TRUE"}, d)

    assert (fields["severity"], fields["outcome"]) == (7, True)
    assert normalize({**RECORD, "severity": 0, "ok": 0}, d)["outcome"] is False
    with pytest.raises(IntegrationError, match="Severity must be between 0 and 10"):
        normalize({**RECORD, "severity": 11, "ok": 1}, d)
    with pytest.raises(IntegrationError, match="outcome is not a boolean"):
        normalize({**RECORD, "severity": 1, "ok": "yes"}, d)


def test_millisecond_timestamps_and_unix_seconds_are_read_as_utc():
    d = definition(timestamp={"path": "/at", "transform": "unix_ms"})

    fields = normalize({**RECORD, "at": 1767268800000}, d)

    assert fields["timestamp"] == "2026-01-01T12:00:00+00:00"
    assert engine.timestamp(1767268800) == datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    with pytest.raises(IntegrationError, match="include a timezone"):
        engine.timestamp("2026-01-01T12:00:00")


@pytest.mark.parametrize(
    "record,mapping,message",
    [
        ([RECORD], {}, "must be a JSON object"),
        ({**RECORD, "id": ""}, {}, "identifier and summary must not be empty"),
        ({**RECORD, "name": ""}, {}, "identifier and summary must not be empty"),
        ({**RECORD, "id": None}, {}, "Required field id is missing at /id"),
        (
            {**RECORD, "link": "javascript:alert(1)"},
            {"url": {"path": "/link"}},
            "Source link must be HTTP or HTTPS",
        ),
        (
            {**RECORD, "level": "inf"},
            {"severity": {"path": "/level", "transform": "number"}},
            "Numeric fields must be finite",
        ),
    ],
    ids=["array", "empty-id", "empty-summary", "missing-id", "script-link", "infinite"],
)
def test_unusable_records_are_refused(record, mapping, message):
    with pytest.raises(IntegrationError, match=message):
        normalize(record, definition(**mapping))


def test_optional_fields_absent_from_a_record_are_left_out():
    fields = normalize(
        RECORD,
        definition(actor={"path": "/user/name"}, url={"path": "/link"}),
    )

    assert set(fields) == {"id", "timestamp", "summary"}
    assert normalize(
        {**RECORD, "link": "https://ci.example/7"}, definition(url={"path": "/link"})
    )["url"] == ("https://ci.example/7")


class Upstream:
    """Records each request and answers from a list of (payload, headers)."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, url, **options):
        body = options.get("body")
        self.calls.append(
            {
                "url": url,
                "query": dict(options.get("query") or {}),
                "body": dict(body) if body is not None else None,
            }
        )
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return reply if isinstance(reply, tuple) else (reply, {})


def records(*ids, at="2026-01-01T12:00:00Z"):
    return [{"id": i, "created_at": at, "title": f"Check {i}"} for i in ids]


def test_a_short_page_ends_page_based_collection():
    upstream = Upstream(records(1, 2), records(3))
    d = Definition(pagination={"mode": "page", "size": 2})

    rows, watermark, pages = collect(
        d, BASE, {}, cursor="2025-12-31T00:00:00+00:00", fetch=upstream
    )

    assert [r["fields"]["id"] for r in rows] == ["1", "2", "3"]
    assert pages == 2 and [c["query"]["page"] for c in upstream.calls] == [1, 2]
    # The cursor moves to the newest record collected.
    assert watermark == "2026-01-01T12:00:00+00:00"


def test_body_pagination_posts_the_page_number_in_the_body():
    upstream = Upstream(records(1), [])
    d = Definition(
        method="POST",
        body={"filter": "nightly"},
        pagination={"mode": "page", "location": "body", "size": 1},
    )

    collect(d, BASE, {}, fetch=upstream)

    assert [c["body"] for c in upstream.calls] == [
        {"filter": "nightly", "page": 1, "limit": 1},
        {"filter": "nightly", "page": 2, "limit": 1},
    ]
    with pytest.raises(IntegrationError, match="Body pagination requires POST"):
        collect(
            Definition(pagination={"mode": "page", "location": "body"}),
            BASE,
            {},
            fetch=upstream,
        )


def test_the_since_parameter_starts_at_the_cursor_less_the_overlap():
    upstream = Upstream(records(1))
    d = Definition(since_parameter="updated_after", overlap_seconds=600)

    collect(d, BASE, {}, cursor="2026-01-01T12:00:00+00:00", fetch=upstream)

    assert upstream.calls[0]["query"]["updated_after"] == "2026-01-01T11:50:00+00:00"


def test_next_links_on_the_connection_origin_are_followed():
    upstream = Upstream(
        ({"items": records(1), "next": "/v1/checks?page=2"}, {}),
        ({"items": records(2), "next": ""}, {}),
    )
    d = Definition(
        path="/v1/checks",
        query={"status": "all"},
        records_path="/items",
        pagination={"mode": "next_url"},
    )

    rows, _, _ = collect(d, BASE, {}, fetch=upstream)

    assert len(rows) == 2
    # The next URL replaces the original query string.
    assert [(c["url"], c["query"]) for c in upstream.calls] == [
        (BASE + "/v1/checks", {"status": "all"}),
        (BASE + "/v1/checks?page=2", {}),
    ]


def test_link_headers_drive_link_pagination():
    upstream = Upstream(
        (records(1), {"Link": f'<{BASE}/v1/checks?cursor=b>; rel="next"'}),
        (records(2), {}),
    )

    rows, _, pages = collect(
        Definition(pagination={"mode": "link"}), BASE, {}, fetch=upstream
    )

    assert (len(rows), pages) == (2, 2)
    assert upstream.calls[1]["url"] == BASE + "/v1/checks?cursor=b"


@pytest.mark.parametrize(
    "payload,options,message",
    [
        ({"items": {"id": 1}}, {"records_path": "/items"}, "must select a JSON array"),
        (
            records(
                1, at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            ),
            {},
            "Record timestamp is in the future",
        ),
        (
            [{"id": 1, "created_at": "yesterday", "title": "x"}],
            {},
            "invalid timestamp or field value",
        ),
        (
            [
                {
                    "id": 1,
                    "created_at": "2026-01-01T12:00:00Z",
                    "title": "x",
                    "_related": {},
                }
            ],
            {"enrichments": [{"name": "detail", "path": "/checks/{/id}"}]},
            "without a reserved _related key",
        ),
        (
            [{"id": {"nested": 1}, "created_at": "2026-01-01T12:00:00Z", "title": "x"}],
            {"enrichments": [{"name": "detail", "path": "/checks/{/id}"}]},
            "missing/non-scalar field",
        ),
        # A mapping error keeps its own explanation.
        (
            [{"created_at": "2026-01-01T12:00:00Z", "title": "x"}],
            {},
            "Required field id is missing at /id",
        ),
    ],
    ids=[
        "records-object",
        "future",
        "unreadable-time",
        "reserved-key",
        "non-scalar",
        "missing-id",
    ],
)
def test_unusable_pages_stop_collection(payload, options, message):
    with pytest.raises(IntegrationError, match=message):
        collect(Definition(**options), BASE, {}, fetch=Upstream(payload))


def test_requests_are_bounded_per_run_including_detail_requests():
    # 99 records each need a detail request: the first page alone spends the
    # hundred-request budget, so the next page is never requested.
    upstream = Upstream(records(*range(1, 100)))
    d = Definition(
        pagination={"mode": "page", "size": 99},
        enrichments=[{"name": "detail", "path": "/checks/{/id}"}],
    )

    with pytest.raises(IntegrationError, match="exceeded 100 requests"):
        collect(d, BASE, {}, fetch=upstream)
    assert len(upstream.calls) == 100

    many = Upstream(records(*range(1, 150)))
    with pytest.raises(IntegrationError, match="request/time budget"):
        collect(
            Definition(enrichments=[{"name": "detail", "path": "/checks/{/id}"}]),
            BASE,
            {},
            fetch=many,
        )


def test_runs_are_bounded_in_time_and_response_size(monkeypatch):
    clock = iter([0, 0, 200])
    monkeypatch.setattr(engine.time, "monotonic", lambda: next(clock))
    upstream = Upstream(records(1))
    with pytest.raises(IntegrationError, match="time budget"):
        collect(
            Definition(pagination={"mode": "page", "size": 1}), BASE, {}, fetch=upstream
        )

    monkeypatch.setattr(engine.time, "monotonic", lambda: 0)
    large = [
        {"id": i, "created_at": "2026-01-01T12:00:00Z", "title": "x" * 100_000}
        for i in range(170)
    ]
    with pytest.raises(IntegrationError, match="exceeded 16 MiB"):
        collect(Definition(max_records=10000), BASE, {}, fetch=Upstream(large))

    detail = {"blob": "x" * (17 * 1024 * 1024)}
    with pytest.raises(IntegrationError, match="exceeded 16 MiB"):
        collect(
            Definition(enrichments=[{"name": "detail", "path": "/checks/{/id}"}]),
            BASE,
            {},
            fetch=Upstream(records(1), detail),
        )


def test_measurement_values_must_be_finite_numbers():
    d = Definition(
        output="measurement",
        measure_id="00000000-0000-0000-0000-000000000001",
        fields={
            "id": {"path": "/id"},
            "timestamp": {"path": "/at"},
            "summary": {"path": "/name"},
            "value": {"path": "/value"},
            "period_start": {"path": "/from"},
            "period_end": {"path": "/to"},
        },
    )
    sample = {**RECORD, "value": "12.5", "from": "2026-01-01", "to": "2026-01-31"}

    assert normalize(sample, d)["value"] == 12.5
    with pytest.raises(IntegrationError, match="Measurement must be finite"):
        normalize({**sample, "value": "nan"}, d)
