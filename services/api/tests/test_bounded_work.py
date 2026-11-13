"""Outer bounds on the work one request, record, header or credential can cause.

Each section names the property it enforces across the codebase:

- recursive walks over untrusted structures stop at MAX_DEPTH, so one collected
  record cannot exhaust the interpreter's recursion limit and stop its run;
- parsers whose time grows faster than their input read bounded input only;
- an agent's requests never wait for one another's row locks, and enrolment
  takes no lock until its credential matches;
- unverified webhook deliveries spend only their own allowance;
- untrusted text reaches chat services as plain text;
- scheduled collections expire while a slow one holds the worker;
- session and second-factor cookies are host-only where the browser allows.
"""

import ast
import json
import secrets
import textwrap
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import app.worker.tasks  # noqa: F401 - registers the worker tasks
import pytest
from app.agents import credentials
from app.agents.credentials import digest, locked_agent
from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.db.models import (
    AgentEnrollmentProfile,
    Event,
    IsmsEffectivenessMeasure,
    KeenAgent,
)
from app.ingest import github, rss
from app.ingest.log_pipeline import try_parse_json_line
from app.integrations import engine
from app.integrations.engine import next_link
from app.integrations.runtime import scrub
from app.integrations.schema import MAX_DECODINGS, has_dot_segment
from app.outbound import question_webhooks as qw
from app.security import auth, mfa, request_limits
from app.security.json_shape import MAX_DEPTH, NESTED_TOO_DEEPLY, json_depth
from app.security.redaction import (
    REDACTED,
    mask_event_data_bytes,
    mask_event_data_obj,
    redact_bytes,
    redact_obj,
    storable,
)
from app.services import agent_enrollment
from app.services.errors import DomainError
from app.worker import celery_app as worker
from app.worker.celery_app import SCHEDULED_COLLECTION_EXPIRY, TASK_SECONDS, celery_app
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.responses import Response

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)
from tests.timing import measured

APP = Path(__file__).resolve().parents[1] / "app"


def nested(depth, leaf):
    """``leaf`` inside ``depth`` arrays."""
    value = leaf
    for _ in range(depth):
        value = [value]
    return value


def depth_of(value):
    found = 0
    while isinstance(value, (list, dict)):
        found += 1
        value = value[0] if isinstance(value, list) else next(iter(value.values()))
    return found, value


# --- Recursive walks over untrusted structures ---------------------------------------


def test_walkers_stop_at_the_bound_and_keep_shallow_structures_whole():
    shallow = {"token": "synthetic-secret", "items": [{"note": "a\x00b"}]}
    assert redact_obj(shallow) == {
        "token": REDACTED,
        "items": [{"note": "a\x00b"}],
    }
    assert storable(shallow)["items"] == [{"note": "a�b"}]
    assert mask_event_data_obj({"from": "10.0.0.1"}) != {"from": "10.0.0.1"}
    assert scrub({"a": ["synthetic-secret"]}, ("synthetic-secret",)) == {
        "a": ["[REDACTED]"]
    }

    # Far deeper than the interpreter's recursion limit, as json.loads allows
    # well past it: every walker returns, and nothing below the bound is kept.
    deep = nested(5000, "synthetic-secret")
    for walk in (
        redact_obj,
        mask_event_data_obj,
        storable,
        lambda value: scrub(value, ("synthetic-secret",)),
    ):
        depth, leaf = depth_of(walk(deep))
        assert (depth, leaf) == (MAX_DEPTH, NESTED_TOO_DEEPLY)
    # The bound is exact: a structure as deep as it is kept whole.
    assert depth_of(redact_obj(nested(MAX_DEPTH, "x"))) == (MAX_DEPTH, "x")


def test_deep_documents_stay_text_where_evidence_keeps_text():
    line = "[" * 3000 + "]" * 3000
    assert try_parse_json_line(line) is None
    shallow = nested(MAX_DEPTH, 1)
    assert try_parse_json_line(json.dumps(shallow)) == shallow
    assert try_parse_json_line(json.dumps(nested(MAX_DEPTH + 1, 1))) is None

    # Artifacts keep their whole content: a document deeper than the walkers
    # go is redacted and masked as text, whole.
    document = '{"password": "synthetic-secret", "x": ' + line + "}"
    redacted, status = redact_bytes(document.encode(), "application/json")
    assert (status, b"synthetic-secret" in redacted) == ("redacted", False)
    assert line.encode() in redacted
    masked, status = mask_event_data_bytes(
        ('{"ip": "10.0.0.1", "x": ' + line + "}").encode(), "application/json"
    )
    assert (status, line.encode() in masked) == ("redacted", True)


def test_json_shape_counts_values_and_depth_without_recursion():
    assert json_depth(nested(100_000, 1), 200_000) == 100_000
    assert json_depth(nested(65, 1)) is None
    assert json_depth({"a": [1, 2, 3]}, max_values=5) == 2
    assert json_depth({"a": [1, 2, 3]}, max_values=4) is None


def _self_recursive(tree, prefix=""):
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            calls = {
                sub.func.id
                for sub in ast.walk(node)
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
            }
            if node.name in calls:
                found.add(prefix + node.name)
    return found


# Every self-recursive function and why its depth is bounded. A new one fails
# the inventory below until it is reviewed: a recursive walk over untrusted
# data must stop at MAX_DEPTH (or a smaller fixed bound), or take its input
# only from administrators or from structures already bounded.
REVIEWED_RECURSION = {
    "agents/otlp.py:value": "OTLP AnyValue nesting stops at depth 8",
    "api/mcp/shaping.py:compact": "stops at shaping.DEPTH_LIMIT (4)",
    "agents/ueu/custody.py:_walk": "UEU envelopes are parsed no deeper than 32",
    "agents/ueu/jcs.py:_write": "stops at jcs.MAX_DEPTH",
    "core/cache.py:_normalise_part": "cache keys built by the application",
    "ingest/imap_support/mime.py:parse": "MIME nesting limit",
    "ingest/imap_support/mime.py:visit": "MIME nesting limit",
    "integrations/runtime.py:walk": "stops at MAX_DEPTH",
    "mapping/fields.py:visit": "paths stop at eight keys",
    "mapping/rules.py:_add_label_value": "stops at MAX_DEPTH",
    "mapping/rules.py:_extract_label_values_from_obj": "stops at MAX_DEPTH",
    "security/redaction.py:walk": "stops at MAX_DEPTH",
    "security/regex.py:_one_character": "patterns written in Keen's code",
    "security/regex.py:check": (
        "supplied patterns are at most 512 characters; code patterns are Keen's own"
    ),
    "services/audits/scope.py:selectable_clause_descendants": "framework tree",
    "services/clauses.py:sort_children": "framework tree",
    "services/entity_changelog.py:_clean_scalar": "values Keen stored",
    "services/integrations.py:walk": "administrator definitions",
    "services/managed_configurations.py:walk": "administrator configuration",
}


def test_every_recursive_function_has_a_reviewed_depth_bound():
    found = set()
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP).as_posix()
        found |= _self_recursive(ast.parse(path.read_text()), rel + ":")
    assert found == set(REVIEWED_RECURSION)

    # The inventory finds the walk that once stopped a collection run.
    unbounded = textwrap.dedent(
        """
        def redact_obj(obj):
            if isinstance(obj, list):
                return [redact_obj(x) for x in obj]
            return obj
        """
    )
    assert _self_recursive(ast.parse(unbounded)) == {"redact_obj"}


# --- Parsers whose time grows faster than their input ---------------------------------


def test_feed_dates_are_parsed_only_at_a_dates_length():
    assert rss._parse_dt("Tue, 03 Nov 2026 10:00:00 +0000") == datetime(2026, 11, 3, 10)
    assert rss._parse_dt("2026-11-03T10:00:00Z") == datetime(2026, 11, 3, 10)
    # dateutil's time grows with the square of its input; a feed's date text
    # is refused at a length no date needs, before any parsing.
    with measured() as timing:
        assert rss._parse_dt("1." * 80_000) is None
        costliest = rss._parse_dt(("1." * rss.MAX_DATE_CHARS)[: rss.MAX_DATE_CHARS])
    assert costliest is None or costliest.year
    assert timing.seconds < 0.1


def test_dateutil_parses_only_through_the_bounded_feed_helper():
    users = set()
    for path in APP.rglob("*.py"):
        source = path.read_text()
        if "dateutil" in source:
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [getattr(node, "module", None) or ""] + [
                        alias.name for alias in node.names
                    ]
                    if any(name.startswith("dateutil") for name in names):
                        users.add(path.relative_to(APP).as_posix())
    assert users == {"ingest/rss.py"}


def test_link_headers_are_read_in_linear_time():
    header = '<https://api.example/x?page=2>; rel="next", <https://api.example/x?page=9>; rel="last"'
    assert next_link({"Link": header}) == "https://api.example/x?page=2"
    assert next_link({"link": '<https://api.example/x?page=9>; rel="last"'}) is None
    assert next_link({}) is None
    with measured() as timing:
        assert next_link({"Link": "<" * 65_536}) is None
    assert timing.seconds < 0.1


# A tenth of a custom API run's 110-second time budget.
RUN_SHARE_SECONDS = 11


def test_detail_paths_are_filled_in_linear_time():
    fill = engine._DETAIL_FIELD.sub
    assert fill("7", "/checks/{/id}/runs/{/run}") == "/checks/7/runs/7"
    # Every "{" an administrator leaves unclosed starts an attempt, which ends
    # at the next brace; a pattern reading on to the end took 16 s for this.
    with measured() as timing:
        assert fill("7", "{" * 65_536) == "{" * 65_536
    assert timing.seconds < 0.1
    # The longest path a saved definition can hold.
    longest = "{" * request_limits.MAX_BODY
    with measured() as timing:
        fill("7", longest)
    assert timing.seconds < RUN_SHARE_SECONDS


def encoded_path(levels, tail):
    """A path whose last segment decodes to ``tail`` after ``levels`` decodings."""
    return "/checks/%" + "25" * (levels - 1) + tail


def test_dot_segments_are_found_within_a_bounded_number_of_decodings():
    for levels in (1, 2, MAX_DECODINGS):
        assert has_dot_segment(encoded_path(levels, "2e")) is True
        assert has_dot_segment(encoded_path(levels, "41")) is False
    assert has_dot_segment("/checks/a%25252Fb") is False
    # Encoded more deeply than any server decodes: refused unread.
    assert has_dot_segment(encoded_path(MAX_DECODINGS + 1, "41")) is True
    # Each decoding of a value encoded within itself removes two characters;
    # decoding it until it stopped changing took 4 s for this record value.
    value = "/checks/" + quote("%" + "25" * 32_768, safe="")
    with measured() as timing:
        assert has_dot_segment(value) is True
    assert timing.seconds < 0.1
    # As long as the responses a run may read.
    longest = "/checks/" + quote("%" + "25" * (8 * 1024 * 1024), safe="")
    with measured() as timing:
        has_dot_segment(longest)
    assert timing.seconds < RUN_SHARE_SECONDS


def test_github_paging_links_stay_under_the_api_base_path(monkeypatch):
    monkeypatch.setattr(settings, "github_base_url", "https://ghe.example/api/v3")
    base = "https://ghe.example/api/v3"
    assert github._within_api(base + "/orgs/acme/audit-log?page=2&after=Y2Q%3D") is True
    for escaped in (
        base + "/../x",
        base + "/%2e%2e/%2e%2e/x",
        base + "/..%2f..%2fx",
        base + "/%252e%252e/x",
        "https://other.example/api/v3/x",
    ):
        assert github._within_api(escaped) is False, escaped


# --- Agent locks ----------------------------------------------------------------------


def _profile(db, key="synthetic-bootstrap"):
    profile = AgentEnrollmentProfile(
        id=str(uuid.uuid4()),
        name="Bounded waits",
        key_hash=digest(key),
        state="active",
        token_days=90,
        enrollment_count=0,
        allowed_cidrs=[],
        labels={},
    )
    db.add(profile)
    db.flush()
    return profile


def _agent(db, profile=None):
    agent = KeenAgent(
        id=str(uuid.uuid4()),
        name="host-" + uuid.uuid4().hex[:8],
        credential_type="token",
        token_hash=digest("unused"),
        enrollment_profile_id=profile.id if profile else None,
        enabled=True,
        health={},
        expires_at=utc_now_naive() + timedelta(days=1),
    )
    db.add(agent)
    db.flush()
    return agent


@pytest.fixture
def postgres(contract_postgres_engine):
    return contract_postgres_engine


def test_an_agents_concurrent_request_is_refused_without_waiting(postgres):
    with Session(postgres) as db:
        profile = _profile(db)
        first_id, sibling_id = _agent(db, profile).id, _agent(db, profile).id
        db.commit()
    with Session(postgres) as first, Session(postgres) as second:
        locked_agent(first, first_id)
        with measured(time.monotonic) as timing, pytest.raises(HTTPException) as busy:
            locked_agent(second, first_id)
        assert timing.seconds < 1
        assert (busy.value.status_code, busy.value.headers) == (
            429,
            {"Retry-After": "5"},
        )
        second.rollback()
        # The profile's other agents proceed while the first request runs.
        locked_agent(second, sibling_id)
        second.rollback()
        first.rollback()


def test_a_profile_held_elsewhere_is_waited_for_only_briefly(postgres, monkeypatch):
    monkeypatch.setattr(credentials, "LOCK_WAIT_SECONDS", 1)
    with Session(postgres) as db:
        profile = _profile(db)
        profile_id, agent_id = profile.id, _agent(db, profile).id
        db.commit()
    with Session(postgres) as admin, Session(postgres) as request:
        admin.execute(
            text("SELECT id FROM agent_enrollment_profiles WHERE id = :id FOR UPDATE"),
            {"id": profile_id},
        )
        with measured(time.monotonic) as timing, pytest.raises(HTTPException) as busy:
            locked_agent(request, agent_id)
        assert busy.value.status_code == 429
        assert 0.5 < timing.seconds < 5
        request.rollback()
        admin.rollback()
        # Other database failures are not mistaken for a busy agent.
        with pytest.raises(Exception) as other:
            with credentials.bounded_lock_wait(request):
                request.execute(text("SELECT 1/0"))
        assert not isinstance(other.value, HTTPException)


def test_enrolment_takes_no_lock_until_its_credential_matches(postgres):
    with Session(postgres) as db:
        profile_id = _profile(db).id
        coded = _agent(db)
        coded.credential_type = "key"
        coded.enrollment_code_hash = digest("synthetic-code")
        coded.enrollment_code_expires_at = utc_now_naive() + timedelta(hours=1)
        coded_id = coded.id
        db.commit()
    body = type("Body", (), {"name": "web-01", "nonce": "n" * 43, "public_key": "k"})
    code_body = type("Body", (), {"name": None, "nonce": None, "public_key": "k"})
    context = agent_enrollment.ChangeContext(client_ip="192.0.2.1")
    with Session(postgres) as holder, Session(postgres) as request:
        holder.execute(
            text("SELECT id FROM agent_enrollment_profiles WHERE id = :id FOR UPDATE"),
            {"id": profile_id},
        )
        holder.execute(
            text("SELECT id FROM keen_agents WHERE id = :id FOR UPDATE"),
            {"id": coded_id},
        )
        request.execute(text("SET lock_timeout = '200ms'"))
        for attempt in (
            lambda: agent_enrollment.enroll_with_profile(
                request, context, "wrong-bootstrap", profile_id, body
            ),
            lambda: agent_enrollment.enroll_with_code(
                request, context, "wrong-code", coded_id, code_body
            ),
            lambda: agent_enrollment.enroll_with_code(
                request, context, "wrong-code", str(uuid.uuid4()), code_body
            ),
        ):
            with pytest.raises(DomainError) as refused:
                attempt()
            assert refused.value.status_code == 401
            request.rollback()
        holder.rollback()


# --- Webhook and metric allowances -----------------------------------------------------


def test_the_metric_service_bounds_a_submission_it_is_handed(monkeypatch):
    from app.security.signed_webhooks import sign
    from app.services import webhooks

    monkeypatch.setattr(webhooks, "address_window_allow", lambda *a, **k: (True, 0))
    now = time.time()
    body = b"x" * (webhooks.MAX_METRIC_SUBMISSION + 1)
    timestamp = str(int(now))
    signature = sign("synthetic-secret", timestamp, body)
    with pytest.raises(DomainError) as refused:
        webhooks.submit_signed_metric(
            db=None,
            redis=None,
            metric_key="m",
            timestamps=[timestamp],
            signatures=[signature],
            body=body,
            context=agent_enrollment.ChangeContext(),
            now=now,
        )
    assert refused.value.status_code == 413


# --- Untrusted text in chat messages ---------------------------------------------------


def test_chat_messages_carry_untrusted_text_as_text():
    hostile = "Open: <https://attacker.example|View in Keen> <!channel> [x](https://a.example) & <users/all>"
    payload = {
        "type": "keen.question.created",
        "event": {"summary": hostile, "source": "<!here>"},
        "thread": {"created_by_username": "<@U1>"},
        "post": {"body": hostile},
        "controls": [{"ref": "<b>"}],
        "links": {"event": "https://keen.example/event.html?id=1&framework=A"},
    }
    slack = qw.slack_payload(payload)["text"]
    assert "<https://attacker" not in slack and "<!channel>" not in slack
    assert "&lt;!channel&gt;" in slack and "&amp; " in slack
    # Keen's own link is still a link.
    assert "<https://keen.example/event.html?id=1&framework=A|View in Keen>" in slack
    chat = qw.google_chat_payload(payload)["text"]
    assert "<" not in chat.replace("Open: https://keen.example", "")
    assert "<users/all>" not in chat
    teams = qw.teams_payload(payload)
    for field in (teams["text"], teams["title"]):
        assert "[x](https://a.example)" not in field and "<" not in field
    assert (
        teams["text"]
        == "New question on event\nEvent: Open: ‹https://attacker.example|View in Keen› ‹!channel› ［x］(https://a.example) & ‹users/all›\nSource: ‹!here›\nAsked by: ‹@U1›\n\nOpen: ‹https://attacker.example|View in Keen› ‹!channel› ［x］(https://a.example) & ‹users/all›\nControls: ‹b›\nOpen: https://keen.example/event.html?id=1&framework=A\n\n[View in Keen](https://keen.example/event.html?id=1&framework=A)"
    )


# --- Scheduled collections ---------------------------------------------------------------


def test_every_scheduled_collection_expires_while_it_waits():
    routes = celery_app.conf.task_routes
    collections = [
        entry
        for entry in celery_app.conf.beat_schedule.values()
        if routes.get(entry["task"], {}).get("queue") == "ingest-http"
    ]
    assert (
        len(collections)
        >= len([r for r in routes.values() if r.get("queue") == "ingest-http"]) - 2
    )  # per-connection and custom API runs are queued on demand
    for entry in collections:
        assert entry["options"]["expires"] == SCHEDULED_COLLECTION_EXPIRY, entry


def _runs_per_hour(schedule):
    if isinstance(schedule, (int, float)):
        return 3600 / schedule
    assert schedule.hour == set(range(24)), schedule
    return len(schedule.minute)


def test_each_source_runs_within_its_own_budget_below_its_interval():
    assert set(worker.SOURCE_RUN_SECONDS) == set(worker.HTTP_INGEST_SOURCES)
    beat = {
        entry["task"]: entry["schedule"]
        for entry in celery_app.conf.beat_schedule.values()
    }
    for source, budget in worker.SOURCE_RUN_SECONDS.items():
        name = f"app.worker.tasks.ingest_{source}_all_task"
        task = celery_app.tasks[name]
        assert task.soft_time_limit == min(budget, settings.collection_run_max_seconds)
        assert task.time_limit == task.soft_time_limit + worker.RUN_GRACE_SECONDS
        # A run ends before the next one is due, on average across the hour.
        assert task.soft_time_limit < 3600 / _runs_per_hour(beat[name]), source


def test_every_worker_task_ends_within_an_outer_bound():
    for name, task in celery_app.tasks.items():
        if name.startswith("celery."):
            continue
        soft = task.soft_time_limit or celery_app.conf.task_soft_time_limit
        hard = task.time_limit or celery_app.conf.task_time_limit
        assert 0 < soft < hard <= TASK_SECONDS + 300, name


def test_run_budgets_follow_their_settings_under_the_general_cap(monkeypatch):
    monkeypatch.setattr(settings, "collection_run_max_seconds", 3300)
    assert worker.run_limits(7200) == {
        "soft_time_limit": 3300,
        "time_limit": 3300 + worker.RUN_GRACE_SECONDS,
    }
    assert worker.run_limits(600)["soft_time_limit"] == 600
    for name in ("KEEN_RSS_RUN_SECONDS", "KEEN_LOKI_RUN_SECONDS"):
        field = name.removeprefix("KEEN_").lower()
        assert field in type(settings).model_fields, name
    with pytest.raises(ValueError) as refused:
        type(settings)(rss_run_seconds=60)
    assert [(e["type"], e["loc"], e["msg"]) for e in refused.value.errors()] == [
        (
            "greater_than_equal",
            ("rss_run_seconds",),
            "Input should be greater than or equal to 300",
        )
    ]


# --- Cookies -----------------------------------------------------------------------------


def test_host_only_secure_cookies_take_the_host_prefix(monkeypatch):
    monkeypatch.setattr(settings, "session_cookie_name", "keen_session")
    monkeypatch.setattr(settings, "cookie_domain", "")
    monkeypatch.setattr(settings, "cookie_secure", True)
    assert auth.cookie_name() == "__Host-keen_session"
    response = Response()
    auth.clear_session_cookie(response)
    cleared = response.headers["set-cookie"]
    assert cleared.startswith("__Host-keen_session=") and "secure" in cleared.lower()
    monkeypatch.setattr(settings, "cookie_domain", "example.org")
    assert auth.cookie_name() == "keen_session"
    monkeypatch.setattr(settings, "cookie_domain", "")
    monkeypatch.setattr(settings, "cookie_secure", False)
    assert auth.cookie_name() == "keen_session"
    monkeypatch.setattr(settings, "cookie_secure", True)
    monkeypatch.setattr(settings, "session_cookie_name", "__Secure-keen")
    assert auth.cookie_name() == "__Secure-keen"

    monkeypatch.setattr(settings, "public_base_url", "https://keen.example")
    assert mfa.cookie_name() == "__Host-keen_mfa_pending"
    response = Response()
    mfa.clear_cookie(response)
    assert "secure" in response.headers["set-cookie"].lower()
    monkeypatch.setattr(settings, "public_base_url", "http://localhost")
    assert mfa.cookie_name() == "keen_mfa_pending"


def test_sign_in_sets_and_sign_out_clears_the_prefixed_session(keen):  # noqa: F811
    client = keen.client(keen.user())
    assert client.cookies.get("__Host-keen_session")
    assert client.get("/v1/auth/check").status_code == 204
    signed_out = client.post("/v1/auth/logout", headers=unsafe(client))
    cleared = [
        line
        for line in signed_out.headers.get_list("set-cookie")
        if line.startswith("__Host-keen_session=")
    ]
    assert cleared and "secure" in cleared[0].lower() and "domain" not in cleared[0]


# --- Exports and ISMS metadata -----------------------------------------------------------


def test_exports_named_from_any_filter_keep_a_valid_header(keen):  # noqa: F811
    reader = keen.client(keen.user("events.read"))
    response = reader.get("/v1/events/export", params={"source": "Ж"})
    assert response.status_code == 200, response.text
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;") and "filename*=UTF-8''" in disposition


def test_collected_source_names_reach_only_event_readers(keen):  # noqa: F811
    keen.db.add(
        Event(
            timestamp=utc_now_naive(),
            source="synthetic-private-source",
            summary="x",
            raw_pointer={},
            normalized_payload={},
            external_id="bounded-" + secrets.token_hex(4),
        )
    )
    keen.db.add(
        IsmsEffectivenessMeasure(
            framework_slug="keen",
            summary="Measured",
            metric_key="m-" + secrets.token_hex(4),
            source_type="ci",
        )
    )
    keen.db.commit()

    def sources(*codes):
        client = keen.client(keen.user(*codes))
        meta = client.get("/v1/isms/meta", params={"section": "effectiveness"})
        assert meta.status_code == 200, meta.text
        return meta.json()["effectiveness_metric_source_types"]

    assert sources("isms.read") == ["ci", "other"]
    assert "synthetic-private-source" in sources("isms.read", "events.read")


@pytest.mark.parametrize("permission", ["isms.manage", "risk.manage"])
def test_saving_a_measure_does_not_say_whether_a_guessed_source_is_collected(
    keen,  # noqa: F811
    permission,
):
    # The measure form lists collected sources to event readers only. A save
    # checks a source against the names the saver's form offers, so someone
    # who manages measures without reading events gets one answer for a
    # collected source and for one that does not exist, on every save route.
    keen.db.add(
        Event(
            timestamp=utc_now_naive(),
            source="synthetic-private-source",
            summary="x",
            raw_pointer={},
            normalized_payload={},
            external_id="bounded-" + secrets.token_hex(4),
        )
    )
    named = IsmsEffectivenessMeasure(
        framework_slug="keen",
        summary="Measured",
        metric_key="m-" + secrets.token_hex(4),
        source_type="ci",
    )
    keen.db.add(named)
    keen.db.commit()
    manager = keen.client(keen.user(permission))
    refused = (
        400,
        "Invalid metric source type; choose an existing Keen source or other",
    )

    def save(method, path, **body):
        answer = manager.request(
            method,
            path,
            params={"framework": "keen"},
            json=body,
            headers=unsafe(manager),
        )
        return answer.status_code, answer.json().get("detail")

    measures = "/v1/isms/effectiveness-measures"
    metrics = f"{measures}/{named.id}/metrics"
    for guess in ("synthetic-private-source", "Synthetic_Private-Source", "no-such"):
        new = {"summary": "Probe", "metric_key": "probe-" + secrets.token_hex(4)}
        assert save("POST", measures, **new, source_type=guess) == refused, guess
        # The answer is the same with a second problem in the request.
        assert (
            save("POST", measures, **new, source_type=guess, clauses=["4.1"]) == refused
        )
        assert save("PATCH", f"{measures}/{named.id}", source_type=guess) == refused
        assert save("POST", metrics, metric_value=1, source_type=guess) == refused
    assert keen.db.query(IsmsEffectivenessMeasure).count() == 1

    # A source a measure already names is offered to them, and saved.
    assert save("POST", metrics, metric_value=1, source_type="CI")[0] == 200
    # A result that names no source takes the measure's as stored, though no
    # event carries "ci": the entry does not say what is collected either.
    inherited = manager.post(
        metrics,
        params={"framework": "keen"},
        json={"metric_value": 2},
        headers=unsafe(manager),
    )
    assert (inherited.status_code, inherited.json()["source_type"]) == (200, "ci")
    assert save("PATCH", f"{measures}/{named.id}", source_type="other")[0] == 200

    # An event reader's form lists the collected source, and their save takes it.
    reader = keen.client(keen.user(permission, "events.read"))
    saved = reader.post(
        measures,
        params={"framework": "keen"},
        json={
            "summary": "Collected",
            "metric_key": "collected-" + secrets.token_hex(4),
            "source_type": "Synthetic_Private-Source",
        },
        headers=unsafe(reader),
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["source_type"] == "synthetic-private-source"
