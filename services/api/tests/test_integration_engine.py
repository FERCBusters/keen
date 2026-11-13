"""Record normalisation and bounded collection for custom API integrations.

Definitions map upstream JSON to evidence fields with a fixed set of
transforms; collection pages through an API within request, time, size and
origin limits. A recording fetch function stands in for the network.
"""

import ast
import inspect
import re
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

    rows, watermark, pages, _ = collect(
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

    rows, _, _, _ = collect(d, BASE, {}, fetch=upstream)

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

    rows, _, pages, _ = collect(
        Definition(pagination={"mode": "link"}), BASE, {}, fetch=upstream
    )

    assert (len(rows), pages) == (2, 2)
    assert upstream.calls[1]["url"] == BASE + "/v1/checks?cursor=b"


def test_a_page_that_is_not_a_list_of_records_stops_collection():
    with pytest.raises(IntegrationError, match="must select a JSON array"):
        collect(
            Definition(records_path="/items"),
            BASE,
            {},
            fetch=Upstream({"items": {"id": 1}}),
        )


CURSOR = "2025-12-31T00:00:00+00:00"


def future():
    return (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()


DETAIL = {"enrichments": [{"name": "detail", "path": "/checks/{/id}"}]}
LINKED = {
    "fields": {
        "id": {"path": "/id"},
        "timestamp": {"path": "/created_at"},
        "summary": {"path": "/title"},
        "url": {"path": "/link"},
    }
}
RATED = {
    "fields": {
        "id": {"path": "/id"},
        "timestamp": {"path": "/created_at"},
        "summary": {"path": "/title"},
        "severity": {"path": "/level", "transform": "number"},
        "outcome": {"path": "/ok", "transform": "boolean"},
    }
}
MEASURED = {
    "output": "measurement",
    "measure_id": "00000000-0000-0000-0000-000000000001",
    "unit": "%",
    "fields": {
        "id": {"path": "/id"},
        "timestamp": {"path": "/created_at"},
        "summary": {"path": "/title"},
        "value": {"path": "/value"},
        "period_start": {"path": "/from"},
        "period_end": {"path": "/to"},
    },
}
GOOD = {"link": "https://ci.example/1", "level": 3, "ok": True}
GOOD_MEASURE = {"value": 99.5, "from": "2026-01-01", "to": "2026-01-31"}

# One record each refusal applies to, with the definition that reads it. Every
# record-level refusal the engine can make appears here; the contract test
# below fails when a new one does not.
UNUSABLE = [
    ("not-object", {}, "just text", "Each record must be a JSON object"),
    (
        "array",
        {},
        [{"id": 2, "created_at": "2026-01-01T12:00:00Z", "title": "x"}],
        "Each record must be a JSON object",
    ),
    (
        "missing-id",
        {},
        {"created_at": "2026-01-01T12:00:00Z", "title": "x"},
        "Required field id is missing at /id",
    ),
    (
        "empty-id",
        {},
        {"id": "", "created_at": "2026-01-01T12:00:00Z", "title": "x"},
        "Record identifier and summary must not be empty",
    ),
    (
        "empty-summary",
        {},
        {"id": 2, "created_at": "2026-01-01T12:00:00Z", "title": ""},
        "Record identifier and summary must not be empty",
    ),
    (
        "unreadable-time",
        {},
        {"id": 2, "created_at": "yesterday", "title": "x"},
        "invalid timestamp or field value",
    ),
    (
        "naive-time",
        {},
        {"id": 2, "created_at": "2026-01-01T12:00:00", "title": "x"},
        "Timestamp must include a timezone",
    ),
    (
        "future",
        {},
        {"id": 2, "created_at": "FUTURE", "title": "x"},
        "Record timestamp is in the future",
    ),
    (
        "mailto-link",
        LINKED,
        {
            "id": 2,
            "created_at": "2026-01-01T12:00:00Z",
            "title": "x",
            "link": "mailto:a@example.org",
        },
        "Source link must be HTTP or HTTPS",
    ),
    (
        "script-link",
        LINKED,
        {
            "id": 2,
            "created_at": "2026-01-01T12:00:00Z",
            "title": "x",
            "link": "javascript:alert(1)",
        },
        "Source link must be HTTP or HTTPS",
    ),
    (
        "long-link",
        LINKED,
        {
            "id": 2,
            "created_at": "2026-01-01T12:00:00Z",
            "title": "x",
            "link": "https://reports.example/" + "a" * 2025,
        },
        "Source link is too long",
    ),
    (
        "severity-range",
        RATED,
        {
            "id": 2,
            "created_at": "2026-01-01T12:00:00Z",
            "title": "x",
            "level": 11,
            "ok": 1,
        },
        "Severity must be between 0 and 10",
    ),
    (
        "severity-infinite",
        RATED,
        {
            "id": 2,
            "created_at": "2026-01-01T12:00:00Z",
            "title": "x",
            "level": "inf",
            "ok": 1,
        },
        "Numeric fields must be finite",
    ),
    (
        "not-boolean",
        RATED,
        {
            "id": 2,
            "created_at": "2026-01-01T12:00:00Z",
            "title": "x",
            "level": 1,
            "ok": "yes",
        },
        "outcome is not a boolean",
    ),
    (
        "measure-infinite",
        MEASURED,
        {
            "id": 2,
            "created_at": "2026-01-01T12:00:00Z",
            "title": "x",
            "value": "inf",
            "from": "2026-01-01",
            "to": "2026-01-31",
        },
        "Measurement must be finite",
    ),
    (
        "measure-period",
        MEASURED,
        {
            "id": 2,
            "created_at": "2026-01-01T12:00:00Z",
            "title": "x",
            "value": 1,
            "from": "2026-02-01",
            "to": "2026-01-31",
        },
        "Measurement period ends before it starts",
    ),
    (
        "reserved-key",
        DETAIL,
        {"id": 2, "created_at": "2026-01-01T12:00:00Z", "title": "x", "_related": {}},
        "without a reserved _related key",
    ),
    (
        "non-scalar",
        DETAIL,
        {"id": {"nested": 1}, "created_at": "2026-01-01T12:00:00Z", "title": "x"},
        "missing/non-scalar field",
    ),
    (
        "dot-segment",
        DETAIL,
        {"id": "..", "created_at": "2026-01-01T12:00:00Z", "title": "x"},
        r"forms a \. or \.\. path segment",
    ),
]


def good_record(n, options):
    extra = GOOD_MEASURE if options.get("output") == "measurement" else GOOD
    return {
        "id": n,
        "created_at": "2026-01-01T12:00:00Z",
        "title": f"Check {n}",
        **extra,
    }


def unusable_record(record):
    if isinstance(record, dict) and record.get("created_at") == "FUTURE":
        return {**record, "created_at": future()}
    return record


@pytest.mark.parametrize(
    "options,record,reason",
    [case[1:] for case in UNUSABLE],
    ids=[c[0] for c in UNUSABLE],
)
def test_an_unusable_record_is_left_out_and_the_rest_collected(options, record, reason):
    # One upstream record, such as a ticket with an empty title or a link that
    # is not HTTP(S), must not stop the run or hold back the records around it.
    upstream = Upstream(
        [good_record(1, options), unusable_record(record), good_record(3, options)],
        {"status": "ok"},
    )

    rows, watermark, _, skipped = collect(
        Definition(**options), BASE, {}, cursor=CURSOR, fetch=upstream
    )

    assert [row["fields"]["id"] for row in rows] == ["1", "3"]
    assert skipped["count"] == 1
    [only] = skipped["reasons"]
    assert only["count"] == 1 and re.search(reason, only["reason"])
    # The cursor moves only past collected records.
    assert watermark == "2026-01-01T12:00:00+00:00"
    # A refused detail path is never requested.
    assert all("/checks/.." not in call["url"] for call in upstream.calls)


def _record_refusals():
    """Patterns for every refusal raised while one record is prepared."""
    tree = ast.parse(inspect.getsource(engine))
    patterns = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.FunctionDef) and node.name in {"normalize", "prepare"}
        ):
            continue
        for raised in ast.walk(node):
            if not (
                isinstance(raised, ast.Raise)
                and isinstance(raised.exc, ast.Call)
                and getattr(raised.exc.func, "id", "")
                in {"IntegrationError", "UnusableRecord"}
                and raised.exc.args
            ):
                continue
            message = raised.exc.args[0]
            if isinstance(message, ast.Constant):
                patterns.add(re.escape(message.value))
            elif isinstance(message, ast.JoinedStr):
                patterns.add(
                    "".join(
                        re.escape(part.value)
                        if isinstance(part, ast.Constant)
                        else ".+"
                        for part in message.values
                    )
                )
    # These bound the whole run, so they end it rather than skip a record.
    return patterns - {
        re.escape("Detail requests exceeded the request/time budget"),
        re.escape("Run responses exceeded 16 MiB"),
    }


def _uncovered(cases):
    reasons = set()
    for _, options, record, _ in cases:
        _, _, _, skipped = collect(
            Definition(**options),
            BASE,
            {},
            fetch=Upstream([unusable_record(record)], {"status": "ok"}),
        )
        reasons |= {item["reason"] for item in skipped["reasons"]}
    return {
        pattern
        for pattern in _record_refusals()
        if not any(re.fullmatch(pattern, reason) for reason in reasons)
    }


def test_every_record_refusal_is_a_skip_with_a_case():
    # A record-level refusal added to the engine must skip its record and be
    # exercised here; otherwise it could stop runs as one unusable record did.
    assert _record_refusals()
    assert _uncovered(UNUSABLE) == set()
    # The check notices a refusal no case reaches.
    without_links = [case for case in UNUSABLE if not case[0].endswith("link")]
    assert _uncovered(without_links) == {
        re.escape("Source link must be HTTP or HTTPS"),
        re.escape("Source link is too long"),
    }


def test_budget_and_size_limits_still_end_the_run():
    # Limits that bound the whole run are not record problems.
    with pytest.raises(IntegrationError, match="Record limit reached"):
        collect(Definition(max_records=1), BASE, {}, fetch=Upstream(records(1, 2)))


def test_a_future_record_is_collected_once_its_time_arrives(monkeypatch):
    soon = datetime.now(timezone.utc) + timedelta(hours=1)
    upstream = Upstream(
        [
            {"id": 1, "created_at": "2026-01-01T12:00:00Z", "title": "Past"},
            {"id": 2, "created_at": soon.isoformat(), "title": "Later"},
        ]
    )
    rows, watermark, _, skipped = collect(
        Definition(), BASE, {}, cursor=CURSOR, fetch=upstream
    )
    assert [r["fields"]["id"] for r in rows] == ["1"] and skipped["count"] == 1
    assert watermark == "2026-01-01T12:00:00+00:00"

    # The cursor stayed before it, so the next run reads it again.
    real = engine.datetime

    class Later(real):
        @classmethod
        def now(cls, tz=None):
            return real.now(tz) + timedelta(hours=2)

    monkeypatch.setattr(engine, "datetime", Later)
    rows, _, _, skipped = collect(
        Definition(), BASE, {}, cursor=watermark, fetch=upstream
    )
    assert [r["fields"]["id"] for r in rows] == ["1", "2"] and skipped["count"] == 0


def test_the_skipped_summary_is_bounded_and_ordered():
    reasons = {f"reason {n}": n for n in range(1, 9)}
    summary = engine.skipped_summary(reasons)
    assert summary["count"] == sum(range(1, 9))
    assert [r["count"] for r in summary["reasons"]] == [8, 7, 6, 5, 4]


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


def test_a_detail_path_value_utf8_cannot_encode_does_not_stop_the_run():
    # An upstream identifier can decode to a lone surrogate. Its detail request
    # is made with a replacement in the path; the run and its other records go on.
    upstream = Upstream(
        [
            {"id": "a\ud800b", "created_at": "2026-01-01T12:00:00Z", "title": "odd"},
            {"id": "plain", "created_at": "2026-01-01T12:01:00Z", "title": "fine"},
        ]
    )
    d = Definition(
        path="/checks",
        records_path="",
        enrichments=[{"name": "detail", "path": "/checks/{/id}/detail"}],
    )

    rows, _, _, skipped = collect(d, BASE, {}, fetch=upstream)

    assert [row["fields"]["summary"] for row in rows] == ["odd", "fine"]
    assert skipped["count"] == 0
    assert [call["url"] for call in upstream.calls] == [
        BASE + "/checks",
        BASE + "/checks/a%3Fb/detail",
        BASE + "/checks/plain/detail",
    ]


@pytest.mark.parametrize("value", ["..", ".", "../tenant-b", "..\\tenant-b"])
def test_upstream_values_cannot_steer_detail_requests_out_of_the_base_path(value):
    upstream = Upstream([{"id": value, "created_at": "2026-01-01T12:00:00Z"}])
    d = Definition(
        path="/checks",
        records_path="",
        fields={
            "id": {"path": "/id"},
            "timestamp": {"path": "/created_at"},
            "summary": {"path": "/id"},
        },
        enrichments=[{"name": "detail", "path": "/checks/{/id}/detail"}],
    )

    rows, _, _, skipped = collect(
        d, BASE + "/tenant-a", {"kind": "bearer", "secret": "s"}, fetch=upstream
    )

    assert rows == [] and skipped["count"] == 1
    assert "path segment" in skipped["reasons"][0]["reason"]
    assert [call["url"] for call in upstream.calls] == [BASE + "/tenant-a/checks"]


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
