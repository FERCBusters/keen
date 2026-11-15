"""Reading vendor event-log APIs: bounded responses and fixed failures.

An in-memory transport stands in for the vendor. Nothing a response holds may
reach a failure message, and a record a page cannot use is left out and
counted while the records around it are kept.
"""

from datetime import datetime

import httpx2 as httpx
import pytest
from app.ingest import event_api as api

URL = "https://events.example/api"
MARKER = "upstream-body-marker"


def read(handler, **request):
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return api.read_json(client, "GET", URL, product="Vendor", **request)


def test_an_object_answered_with_200_is_returned():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"items": [1]})

    assert read(handler, params={"page": 2}) == {"items": [1]}
    assert seen[0].url.params["page"] == "2"


@pytest.mark.parametrize(
    "status,message",
    [
        (
            429,
            "Vendor rate limit reached; the next run continues from the cursor",
        ),
        (
            401,
            "Vendor API HTTP 401; check the credentials, their permissions and the endpoint",
        ),
        (
            302,
            "Vendor API HTTP 302; check the credentials, their permissions and the endpoint",
        ),
        (
            500,
            "Vendor API HTTP 500; check the credentials, their permissions and the endpoint",
        ),
    ],
)
def test_a_refusal_reports_its_status_and_nothing_the_response_held(status, message):
    def handler(request):
        return httpx.Response(
            status, text=MARKER, headers={"Location": "https://elsewhere.example/"}
        )

    with pytest.raises(api.EventApiError) as refused:
        read(handler)

    assert str(refused.value) == message
    assert refused.value.status == status


@pytest.mark.parametrize(
    "response,message",
    [
        (httpx.Response(200, text="{" + MARKER), "Vendor returned invalid JSON"),
        (httpx.Response(200, content=b"\xff\xfe"), "Vendor returned invalid JSON"),
        # Nested deeply enough to exhaust the parser's recursion.
        (httpx.Response(200, text="[" * 100_000), "Vendor returned invalid JSON"),
        (
            httpx.Response(200, json=[MARKER]),
            "Vendor returned an invalid JSON object",
        ),
        (
            httpx.Response(200, content=b" " * (api.MAX_JSON_BYTES + 1)),
            "Vendor response exceeds the 8 MiB limit",
        ),
    ],
    ids=["malformed", "not-utf8", "too-deep", "not-an-object", "too-large"],
)
def test_an_unusable_body_fails_with_a_fixed_message(response, message):
    with pytest.raises(api.EventApiError) as refused:
        read(lambda request: response)

    assert str(refused.value) == message
    assert refused.value.status is None


def test_a_body_at_the_limit_is_read():
    body = b'{"a":"' + b"x" * (api.MAX_JSON_BYTES - 8) + b'"}'
    assert len(body) == api.MAX_JSON_BYTES

    assert len(read(lambda request: httpx.Response(200, content=body))["a"]) == (
        api.MAX_JSON_BYTES - 8
    )


def test_a_network_failure_names_no_destination():
    def handler(request):
        raise httpx.ConnectError("cannot reach " + str(request.url))

    with pytest.raises(api.EventApiError) as refused:
        read(handler)

    assert str(refused.value) == "Vendor network or TLS failure; cursor retained"


def test_a_page_keeps_its_records_and_counts_what_is_not_one():
    deep = {"a": 1}
    for _ in range(80):
        deep = {"n": deep}
    rows = [{"id": "a\x00b"}, "text", None, deep, 7]

    kept, invalid = api.records(rows, 5, "Vendor")

    assert invalid == 3
    # What PostgreSQL cannot hold is replaced, and deep nesting is cut short.
    assert kept[0] == {"id": "a\ufffdb"}
    assert "[nested too deeply]" in str(kept[1]) and "'a': 1" not in str(kept[1])


@pytest.mark.parametrize(
    "rows,message",
    [
        ({"0": {}}, "Vendor returned an invalid page of events"),
        (None, "Vendor returned an invalid page of events"),
        ([{}, {}, {}], "Vendor returned more events than a page holds"),
    ],
)
def test_a_page_that_is_no_list_or_holds_too_much_is_refused(rows, message):
    with pytest.raises(api.EventApiError, match="^" + message + "$"):
        api.records(rows, 2, "Vendor")


def test_a_paging_value_is_bounded_printable_text():
    assert api.paging_token(None, "Vendor") is None
    assert api.paging_token("", "Vendor") is None
    assert api.paging_token("aGVsbG8=+/-_.~", "Vendor") == "aGVsbG8=+/-_.~"
    assert api.paging_token("x" * 4096, "Vendor") == "x" * 4096
    for value in ("x" * 4097, "a b", "a\nb", "café", 7, ["a"], True):
        with pytest.raises(
            api.EventApiError, match="^Vendor returned an invalid paging value$"
        ):
            api.paging_token(value, "Vendor")


def test_times_are_read_as_naive_utc_or_not_at_all():
    assert api.timestamp("2026-03-01T10:00:00Z") == datetime(2026, 3, 1, 10)
    assert api.timestamp("2026-03-01T10:00:00.1234567Z") == datetime(
        2026, 3, 1, 10, 0, 0, 123456
    )
    assert api.timestamp("2026-03-01T12:30:00+02:30") == datetime(2026, 3, 1, 10)
    assert api.timestamp("2026-03-01T10:00:00") == datetime(2026, 3, 1, 10)
    for value in (
        None,
        "",
        "yesterday",
        1710267184,
        "2026-03-01T10:00:00Z" + " " * 60,
        # Converting to UTC would leave the calendar.
        "0001-01-01T00:00:00+10:00",
    ):
        assert api.timestamp(value) is None, value


def test_upstream_codes_and_text_are_used_only_when_well_formed():
    assert api.label("credentials_failed") == "credentials_failed"
    assert api.label("select-sso-provider") == "select-sso-provider"
    for value in ("", "a b", "x" * 65, "<script>", None, 5, {"a": 1}):
        assert api.label(value) == "unknown"
    assert api.label(None, "other") == "other"
    assert api.text("jane@example.org") == "jane@example.org"
    for value in ("", "   ", None, 5, ["a"]):
        assert api.text(value) is None
    assert api.member({"user": {"email": "a"}}, "user") == {"email": "a"}
    for record in ({}, {"user": None}, {"user": "text"}, {"user": ["a"]}):
        assert api.member(record, "user") == {}


@pytest.mark.parametrize(
    "value",
    [
        "",
        None,
        "http://93.184.216.34/api",
        "https://user:secret@93.184.216.34/api",
        "https://93.184.216.34/api?token=1",
        "https://93.184.216.34/api#part",
        "https://127.0.0.1/api",
        "https://169.254.169.254/latest",
        "https://[unbalanced/api",
        "ftp://93.184.216.34/",
    ],
)
def test_an_endpoint_collection_may_not_reach_is_refused(value):
    assert api.safe_base(value) is None


def test_a_safe_endpoint_loses_its_trailing_slash_and_spaces():
    assert api.safe_base(" https://93.184.216.34/api/ ") == "https://93.184.216.34/api"
