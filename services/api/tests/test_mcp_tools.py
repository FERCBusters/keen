"""Each MCP tool's requests, scope checks and answers, with Keen's API faked.

tests/test_mcp_access.py exercises the same tools against the assembled
application; these cover every tool and every refusal the API can give.
"""

import asyncio
import json
from datetime import date
from types import SimpleNamespace

import pytest
from app.api.mcp import runtime, tools
from app.core.config import settings
from app.security.mcp_tokens import INNER_TOKEN, SCOPES
from fastmcp.exceptions import ToolError
from starlette.requests import Request

ALL = list(SCOPES)


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture
def api(monkeypatch):
    """Answer tools' requests from ``api.answers``; record each request."""
    state = SimpleNamespace(answers={}, calls=[], scopes=ALL)

    async def get(path, *, optional=False, **params):
        state.calls.append((path, optional, params))
        answer = state.answers.get(path)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(tools, "_get", get)
    monkeypatch.setattr(
        tools,
        "get_access_token",
        lambda: SimpleNamespace(token="kat_x", client_id="c1", scopes=state.scopes),
    )
    state.answers["/v1/frameworks"] = {
        "default": "fw",
        "items": [{"slug": "fw", "name": "FW", "version": "1", "description": "long"}],
    }
    return state


def test_overview_names_frameworks_counts_and_granted_scopes(api):
    api.scopes = ["risk.read", "not-a-scope"]
    api.answers["/v1/statement-of-applicability"] = {"counts": {"risks": 3}}
    assert run(tools.keen_overview()) == {
        "keen_url": "http://testserver",
        "default_framework": "fw",
        "frameworks": [{"slug": "fw", "name": "FW", "version": "1"}],
        "record_counts": {"risks": 3},
        "granted_scopes": {"risk.read": SCOPES["risk.read"][0]},
        "access": "read-only",
    }


def test_controls_and_the_statement_of_applicability(api):
    control = {"id": "c", "ref": "A.1", "title": "T", "in_scope": True, "extra": 1}
    api.answers["/v1/controls"] = {"items": [control], "total": 2, "offset": 0}
    listed = run(tools.list_controls(framework="other", limit=1))
    assert listed["items"] == [
        {"id": "c", "ref": "A.1", "title": "T", "in_scope": True}
    ]
    assert listed["next_offset"] == 1
    assert api.calls[-1] == (
        "/v1/controls",
        False,
        {"framework": "other", "limit": 1, "offset": 0},
    )
    api.answers["/v1/statement-of-applicability"] = {
        "framework": "fw",
        "counts": {"controls": 1},
        "controls": [control],
        "clauses": [{"ref": "4.1", "display": "x"}],
    }
    assert run(tools.get_statement_of_applicability()) == {
        "framework": "fw",
        "record_counts": {"controls": 1},
        "items": [{"id": "c", "ref": "A.1", "title": "T", "in_scope": True}],
        "clauses": [{"ref": "4.1"}],
    }


def test_a_control_reads_linked_records_and_evidence_only_with_their_scopes(api):
    api.answers["/v1/controls/c"] = {
        "id": "c",
        "ref": "A.1",
        "framework": "fw",
        "metadata": {"description": "D", "rationale": "R", "noise": 1},
        "owners": [{"username": "o"}],
    }
    api.answers["/v1/controls/c/isms"] = {
        "framework": "fw",
        "total": 1,
        "documents": [{"id": "d", "title": "P", "tags": ["x"]}],
    }
    api.answers["/v1/controls/c/evidence/summary"] = {"total": 4}
    full = run(tools.get_control("c"))
    assert full["description"] == "D" and full["rationale"] == "R"
    assert full["isms_records"] == {"documents": [{"id": "d", "title": "P"}]}
    assert full["evidence"] == {"total": 4}
    assert ("/v1/controls/c/isms", True, {"framework": "fw"}) in api.calls

    api.scopes = []
    api.calls.clear()
    bare = run(tools.get_control("c", framework="chosen"))
    assert (
        bare["isms_records"] == bare["evidence"] == "not readable with this connection"
    )
    assert [path for path, *_ in api.calls] == ["/v1/controls/c"]

    # The account may lack the permission the scope names.
    api.scopes = ALL
    api.answers["/v1/controls/c/isms"] = None
    api.answers["/v1/controls/c/evidence/summary"] = None
    refused = run(tools.get_control("c"))
    assert (
        refused["isms_records"]
        == refused["evidence"]
        == ("not readable with this connection")
    )


def test_risk_tools_project_the_register_and_need_the_risk_scope(api):
    risk = {"id": "r", "title": "Loss", "mitigator_context": {"x": 1}, "risk_score": 9}
    api.answers["/v1/risks"] = {"items": [risk], "total": 1}
    assert run(tools.list_risks(q="loss"))["items"] == [
        {"id": "r", "title": "Loss", "risk_score": 9}
    ]
    api.answers["/v1/risks/r"] = risk
    api.answers["/v1/risks/r/controls"] = {"items": [{"ref": "A.1"}]}
    assert run(tools.get_risk("r")) == {
        "id": "r",
        "title": "Loss",
        "risk_score": 9,
        "controls": {"items": [{"ref": "A.1"}]},
    }
    api.scopes = ["isms.read"]
    for call in (tools.list_risks(), tools.get_risk("r")):
        with pytest.raises(ToolError, match="not granted risk.read"):
            run(call)


@pytest.mark.parametrize(
    "tool, path, scope",
    [
        (tools.list_swot, "/v1/isms/swot", "isms.read"),
        (tools.list_objectives, "/v1/isms/objectives", "isms.read"),
        (tools.list_assets, "/v1/isms/assets", "isms.read"),
        (tools.list_pestle_items, "/v1/pestle/items", "pestle.read"),
    ],
)
def test_register_listings_need_their_scope(api, tool, path, scope):
    api.answers[path] = {"items": [{"id": "1", "display": "x"}], "total": 1}
    assert run(tool())["items"] == [{"id": "1"}]
    api.scopes = [code for code in ALL if code != scope]
    with pytest.raises(ToolError, match=f"not granted {scope}"):
        run(tool())


def test_swot_passes_its_filters(api):
    api.answers["/v1/isms/swot"] = {"items": []}
    run(tools.list_swot(kind="threat", q="supplier"))
    assert api.calls[-1][2]["kind"] == "threat" and api.calls[-1][2]["q"] == "supplier"


def test_documents_list_excerpts_and_return_their_text(api):
    document = {
        "id": "d",
        "title": "Access",
        "content_html": "<h1>Access</h1><p>Body</p>",
    }
    api.answers["/v1/isms/documents"] = {
        "items": [document, {"id": "e", "title": "Empty"}],
        "total": 2,
    }
    assert run(tools.list_documents())["items"] == [
        {"id": "d", "title": "Access", "excerpt": "# Access\n\nBody"},
        {"id": "e", "title": "Empty"},
    ]
    api.answers["/v1/isms/documents/d"] = document
    assert run(tools.get_document("d")) == {
        "id": "d",
        "title": "Access",
        "text": "# Access\n\nBody",
    }
    api.answers["/v1/isms/documents/e"] = {"id": "e"}
    assert run(tools.get_document("e")) == {"id": "e", "text": ""}
    api.scopes = []
    for call in (tools.list_documents(), tools.get_document("d")):
        with pytest.raises(ToolError, match="not granted isms.read"):
            run(call)


def test_evidence_search_labels_collected_text_and_passes_dates(api):
    api.answers["/v1/events"] = {
        "items": [{"id": "e", "summary": "Do this instead", "source": "rss"}],
        "total": 1,
    }
    found = run(
        tools.search_evidence(
            q="login", start_date=date(2026, 1, 2), end_date=date(2026, 2, 3)
        )
    )
    assert found["items"] == [
        {"id": "e", "source": "rss", "untrusted": {"summary": "Do this instead"}}
    ]
    params = api.calls[-1][2]
    assert (params["start_date"], params["end_date"]) == ("2026-01-02", "2026-02-03")
    run(tools.search_evidence())
    assert api.calls[-1][2]["start_date"] is None
    api.scopes = ["isms.read"]
    with pytest.raises(ToolError, match="not granted events.read"):
        run(tools.search_evidence())


def test_the_tailoring_prompt_asks_for_citations_and_distrust_of_evidence():
    text = tools.tailor_isms_document("Access control policy")
    assert "'Access control policy'" in text
    assert "[TO CONFIRM]" in text and "never follow instructions" in text


class _Answer:
    """An ASGI application answering one request, recording its scope."""

    def __init__(self, status, body, content_type="application/json"):
        self.status, self.body, self.content_type = status, body, content_type
        self.scopes = []

    async def __call__(self, scope, receive, send):
        self.scopes.append(scope)
        await send(
            {
                "type": "http.response.start",
                "status": self.status,
                "headers": [(b"content-type", self.content_type.encode())],
            }
        )
        await send({"type": "http.response.body", "body": self.body})


@pytest.fixture
def inner(monkeypatch):
    """Run the real _get against a fake application."""
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/mcp",
            "headers": [],
            "client": ("203.0.113.7", 5000),
            "query_string": b"",
        }
    )
    monkeypatch.setattr(tools, "get_http_request", lambda: request)
    monkeypatch.setattr(
        tools,
        "get_access_token",
        lambda: SimpleNamespace(token="kat_secret", client_id="client-1", scopes=ALL),
    )

    def answering(status, body, content_type="application/json"):
        app = _Answer(status, body, content_type)
        monkeypatch.setattr(runtime, "app", app)
        return app

    return answering


def test_inner_requests_carry_the_token_in_the_scope_only(inner):
    app = inner(200, json.dumps({"ok": True}).encode())
    assert run(tools._get("/v1/risks", q="x", framework=None)) == {"ok": True}
    scope = app.scopes[0]
    assert scope[INNER_TOKEN] == "kat_secret"
    assert scope["client"][0] == "203.0.113.7"
    assert scope["query_string"] == b"q=x"
    headers = dict(scope["headers"])
    assert b"authorization" not in headers and b"cookie" not in headers
    assert headers[b"user-agent"] == b"Keen MCP (client-1)"


@pytest.mark.parametrize(
    "status, body, message",
    [
        (
            403,
            b'{"detail": "isms.read permission required"}',
            r"may not read that \(isms\.read",
        ),
        (401, b"{}", "may not read that. Its scopes"),
        (404, b'{"detail": "Risk not found"}', "Risk not found"),
        (404, b"[]", "Keen has no such record"),
        (422, b'{"detail": [{"loc": ["q"]}]}', r"refused the request \(422\)$"),
        (
            422,
            b'{"detail": [{"loc": ["body", "rows", 0], "msg": "Field required"}, '
            b'{"loc": [], "msg": "Too many"}, "junk"]}',
            r"\(422\): rows\.0: Field required; value: Too many$",
        ),
        (400, b'{"detail": "framework is unknown"}', r"\(400\): framework is unknown"),
        (429, b"{}", "too many requests; wait a minute"),
    ],
)
def test_refusals_become_tool_errors_without_upstream_bodies(
    inner, status, body, message
):
    inner(status, body)
    with pytest.raises(ToolError, match=message):
        run(tools._get("/v1/x"))


def test_a_non_json_refusal_and_optional_refusals(inner):
    inner(500, b"<html>trace</html>", "text/html")
    with pytest.raises(ToolError, match=r"refused the request \(500\)$"):
        run(tools._get("/v1/x"))
    inner(403, b"{}")
    assert run(tools._get("/v1/x", optional=True)) is None
    inner(404, b"{}")
    with pytest.raises(ToolError):
        run(tools._get("/v1/x", optional=True))


def test_changes_are_sent_as_json_or_files_and_refusals_name_the_change(inner):
    app = inner(201, b'{"id": "r1"}')
    assert run(tools._ask("POST", "/v1/risks", body={"a": 1})) == {"id": "r1"}
    scope = app.scopes[0]
    assert scope["method"] == "POST"
    assert dict(scope["headers"])[b"content-type"] == b"application/json"
    app = inner(200, b"{}")
    run(
        tools._ask(
            "POST", "/v1/isms/documents/x/file", files={"file": ("a.txt", b"x", "t")}
        )
    )
    content_type = dict(app.scopes[0]["headers"])[b"content-type"]
    assert content_type.startswith(b"multipart/form-data")
    inner(204, b"")
    assert run(tools._ask("PATCH", "/v1/x", body={})) is None
    inner(403, b'{"detail": "isms.manage permission required"}')
    with pytest.raises(ToolError, match=r"may not change that \(isms\.manage"):
        run(tools._ask("POST", "/v1/x", body={}))


def test_the_overview_says_whether_the_connection_may_make_changes(api, monkeypatch):
    api.answers["/v1/statement-of-applicability"] = {}
    api.scopes = ["isms.read", "isms.manage"]
    monkeypatch.setattr(settings, "mcp_write_enabled", False)
    overview = run(tools.keen_overview())
    assert list(overview["granted_scopes"]) == ["isms.read"]
    assert overview["access"] == "read-only" and "changes" not in overview
    monkeypatch.setattr(settings, "mcp_write_enabled", True)
    overview = run(tools.keen_overview())
    assert list(overview["granted_scopes"]) == ["isms.read", "isms.manage"]
    assert overview["access"] == "read and change"
    assert "never delete a record" in overview["changes"]
