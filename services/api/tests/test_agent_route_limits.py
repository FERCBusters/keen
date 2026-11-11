"""Agent route limits and failures: sealed body bounds, encodings, slow bodies,
health reports, the health allowance, keyless responses and failures outside
the OTLP contract.

Agents authenticate with their own credentials through the agent router, as in
test_keen_agent.py and test_ueu_evidence.py. Valkey and the database are
replaced or failed at their boundaries; sealing uses the real HPKE suite.
"""

import asyncio
import json
import logging
import uuid
from types import SimpleNamespace

import fakeredis
import pytest
from app.agents import server_keys
from app.api.routes import agents as api
from app.db.models import AgentServerKey, KeenAgent
from app.security import rate_limit
from sqlalchemy import event as sql_event
from sqlalchemy.exc import OperationalError

# Imported fixtures: the agent router on its database, and its keen-agent harness.
from tests.test_keen_agent import record
from tests.test_keen_agent import setup as setup  # noqa: F401
from tests.test_ueu_evidence import enrolled
from tests.test_ueu_evidence import keen as keen  # noqa: F401


def test_a_failure_outside_the_otlp_route_is_an_ordinary_server_error(
    keen,  # noqa: F811 - fixture imported above
    caplog,
):
    agent, _ = enrolled(keen)
    with keen.db() as db:
        seen_before = db.get(KeenAgent, agent.agent_id).last_seen
    engine = keen.db.kw["bind"]

    def unavailable(conn, cursor, statement, *args):
        if statement.startswith("UPDATE keen_agents"):
            raise OperationalError(statement, {}, Exception("database went away"))

    sql_event.listen(engine, "before_cursor_execute", unavailable)
    try:
        with caplog.at_level(logging.ERROR, logger=api.logger.name):
            failed = agent.heartbeat()
    finally:
        sql_event.remove(engine, "before_cursor_execute", unavailable)

    # The OTLP route answers its own failures in OTLP form; other routes do not.
    assert failed.status_code == 500
    assert failed.text == "Internal Server Error"
    assert not any("OTLP batch" in r.getMessage() for r in caplog.records)
    with keen.db() as db:
        assert db.get(KeenAgent, agent.agent_id).last_seen == seen_before


def test_a_response_sealed_while_keen_has_no_active_key_names_none(
    keen,  # noqa: F811 - fixture imported above
):
    agent, _ = enrolled(keen)
    current = agent.heartbeat()
    assert current.status_code == 200, current.text
    assert current.headers[server_keys.KEY_HEADER] == agent.server["key_id"]

    # As when a reset or rotation lands while a request is being answered.
    with keen.db() as db:
        row = db.query(AgentServerKey).filter_by(purpose="encryption").one()
        row.state = "retired"
        db.commit()
    keyless = agent.heartbeat()

    assert keyless.status_code == 200, keyless.text
    assert json.loads(keyless.content)["ok"] is True
    assert server_keys.KEY_HEADER not in keyless.headers
    assert keyless.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "path,operation,limit,unreadable,refusal",
    [
        (
            "/v1/otlp/logs",
            "otlp-logs",
            api.MAX_BODY,
            {
                "code": 3,
                "message": "Invalid OTLP JSON logs; maximum 64 records and "
                "bounded attributes",
            },
            {"code": 8, "message": "Request exceeds limit"},
        ),
        (
            "/v1/agents/heartbeat",
            "heartbeat",
            api.HEALTH_BODY,
            {"detail": "Invalid health report"},
            {"detail": "Request exceeds limit"},
        ),
    ],
)
def test_an_opened_body_is_bounded_whatever_the_sealing_allowance(
    keen,  # noqa: F811 - fixture imported above
    monkeypatch,
    path,
    operation,
    limit,
    unreadable,
    refusal,
):
    agent, _ = enrolled(keen)
    # Sealing adds exactly SEALED_OVERHEAD today; a wider allowance, as a
    # suite with a larger encapsulation would need, still bounds the plaintext.
    monkeypatch.setattr(
        server_keys, "SEALED_OVERHEAD", server_keys.SEALED_OVERHEAD + 64
    )
    # A body at the limit is opened and read, and refused only as unreadable.
    within = agent.post(path, b" " * limit, operation)
    assert (within.status_code, within.json()) == (400, unreadable)

    refused = agent.post(path, b" " * (limit + 1), operation)

    assert refused.status_code == 413
    assert refused.json() == refusal


def test_unsupported_content_encodings_are_refused_before_the_body_is_read(
    setup,  # noqa: F811 - fixture imported above
):
    client, factory, headers, _, stored, _ = setup
    refused = client.post(
        "/v1/otlp/logs",
        content=json.dumps(record()).encode(),
        headers={
            **headers,
            "Content-Type": "application/json",
            "Content-Encoding": "br",
        },
    )

    assert refused.status_code == 415
    assert refused.json() == {"code": 12, "message": "Unsupported encoding"}
    assert stored == []


def test_a_body_that_stops_arriving_is_refused_at_its_deadline(
    setup,  # noqa: F811 - fixture imported above
    monkeypatch,
):
    _, _, headers, _, stored, app = setup
    deadlines = []

    def timeout(seconds):
        deadlines.append(seconds)
        return asyncio.timeout(0)  # the deadline has passed

    monkeypatch.setattr(api, "asyncio", SimpleNamespace(timeout=timeout))
    answered = []

    async def drive():
        finished = asyncio.Event()
        chunks = [
            {"type": "http.request", "body": b'{"resourceLogs"', "more_body": True}
        ]

        async def receive():
            if chunks:
                return chunks.pop(0)
            await finished.wait()  # the client sends nothing more
            return {"type": "http.disconnect"}

        async def send(message):
            answered.append(message)
            if message["type"] == "http.response.body" and not message.get("more_body"):
                finished.set()

        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "https",
                "path": "/v1/otlp/logs",
                "raw_path": b"/v1/otlp/logs",
                "query_string": b"",
                "root_path": "",
                "headers": [
                    (b"host", b"keen.example"),
                    (b"content-type", b"application/json"),
                    (b"authorization", headers["Authorization"].encode()),
                ],
                "client": ("203.0.113.9", 50000),
                "server": ("keen.example", 443),
                "state": {},
            },
            receive,
            send,
        )

    asyncio.run(drive())

    start, body = answered[0], answered[1]
    assert start["status"] == 408
    assert json.loads(body["body"]) == {"code": 4, "message": "Request body timeout"}
    assert deadlines == [10]
    assert stored == []


def test_a_health_report_that_is_not_a_report_is_refused(
    keen,  # noqa: F811 - fixture imported above
):
    agent, _ = enrolled(keen)
    with keen.db() as db:
        before = db.get(KeenAgent, agent.agent_id).health

    for body in (b"not json", b'{"filtered_by_source": {"": 1}}'):
        refused = agent.post("/v1/agents/heartbeat", body, "heartbeat")
        assert refused.status_code == 400, body
        assert refused.json() == {"detail": "Invalid health report"}

    with keen.db() as db:
        assert db.get(KeenAgent, agent.agent_id).health == before


def test_health_reports_have_a_per_agent_allowance(
    keen,  # noqa: F811 - fixture imported above
    monkeypatch,
):
    store = fakeredis.FakeRedis()
    monkeypatch.setattr(api, "get_valkey", lambda: store)
    monkeypatch.setattr(api, "fixed_window_allow", rate_limit.fixed_window_allow)
    first, _ = enrolled(keen)
    second, _ = enrolled(keen, name="web-02")

    assert [first.heartbeat().status_code for _ in range(6)] == [200] * 6
    limited = first.heartbeat()

    assert limited.status_code == 429
    assert limited.json() == {"detail": "Rate limited"}
    assert 0 < int(limited.headers["retry-after"]) <= 60
    # Another agent behind the same address keeps its own allowance.
    assert second.heartbeat().status_code == 200
    assert uuid.UUID(first.agent_id) != uuid.UUID(second.agent_id)
