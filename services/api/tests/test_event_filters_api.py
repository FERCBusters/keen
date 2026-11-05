"""Event list, facet and export filters through the assembled application.

Events are filtered by system, actor, free text, control (by reference or ID,
within the chosen framework) and clause. The homepage ticker shows the newest
arrivals with a bounded, redacted summary. Requests come from a user who can
read events.
"""

import csv
import io
import json
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from app.core.datetime_utils import utc_now_naive
from app.db.models import (
    ControlClauseLink,
    ControlItem,
    Event,
    Framework,
    FrameworkClause,
    Mapping,
)
from app.mapping.rules import rule_rationale
from app.security.regex import compile_pattern, validate_database_pattern
from app.services import events as event_operations
from psycopg2 import errors
from sqlalchemy import event as sql_event
from sqlalchemy import text

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import keen, unsafe  # noqa: F401

FW = "fw-filters"
# Every view that applies the Events page filters, and a window covering the
# fixture's events.
FILTERED_VIEWS = (
    "/v1/events",
    "/v1/events/facets",
    "/v1/events/export",
    "/v1/charts/event_volume_timeseries",
    "/v1/graph/control_source",
    "/v1/graph/clause_source",
)
WINDOW = {"start_date": "2026-02-01", "end_date": "2026-03-31"}
BACKREFERENCE = (
    "Invalid regular expression: Backreferences are unsupported, and the database "
    "reads a backslash followed by a digit as one; write a character code as a "
    "hexadecimal escape such as \\x41"
)
LOOKAROUND = "Invalid regular expression: Lookahead and lookbehind are unsupported"


@pytest.fixture
def events(keen):  # noqa: F811 - fixture imported above
    db = keen.db
    db.add_all([Framework(slug=FW), Framework(slug="fw-elsewhere")])
    access = ControlItem(framework_slug=FW, ref="A.5.15", type="annex_control")
    backup = ControlItem(framework_slug=FW, ref="A.8.13", type="annex_control")
    foreign = ControlItem(
        framework_slug="fw-elsewhere", ref="A.5.15", type="annex_control"
    )
    clause = FrameworkClause(framework_slug=FW, ref="9.2", title="Internal audit")
    db.add_all([access, backup, foreign, clause])
    db.flush()
    db.add(
        ControlClauseLink(
            control_item_id=backup.id, clause_id=clause.id, applicability="applicable"
        )
    )

    def event(summary, *controls, rule=None, **fields):
        row = Event(
            source=fields.pop("source", "ci"),
            summary=summary,
            external_id=fields.pop("external_id", uuid.uuid4().hex),
            timestamp=fields.pop("timestamp", datetime(2026, 3, 1)),
            **fields,
        )
        db.add(row)
        db.flush()
        for control in controls:
            db.add(
                Mapping(
                    event_id=row.id,
                    control_item_id=control.id,
                    method="rule",
                    mapped_by="system",
                    rationale=rule_rationale(rule or "legacy", control.framework_slug),
                )
            )
        return row

    rows = {
        "login": event(
            "Admin login reviewed",
            access,
            system="Okta-Prod",
            actor="Alice Smith",
            rule="login-review",
            external_id="okta-4411",
        ),
        "backup": event(
            "Nightly backup & restore",
            backup,
            system="backup-01",
            actor="cron",
            rule="backup-runs",
            source="jenkins",
            connection_id="conn-1",
            connection_name="Build feed",
        ),
        "foreign": event(
            "Access reviewed elsewhere", foreign, system="okta-test", actor="alice"
        ),
        "plain": event("Unmapped heartbeat", system="monitor", actor="robot"),
        "budget": event("R&D budget approved", system="finance", actor="cfo"),
        "users": event("userXname rotated", system="iam", actor="robot"),
        "blank": event("Config reloaded", system=None, actor=None),
    }
    # An auditor's rationale that quotes a rule is not the rule's work.
    quoted = event("Access reviewed by hand", system="iam", actor="dana")
    db.add(
        Mapping(
            event_id=quoted.id,
            control_item_id=access.id,
            method="manual",
            mapped_by="dana",
            rationale=rule_rationale("login-review", FW),
        )
    )
    rows["quoted"] = quoted
    db.commit()
    reader = keen.client(keen.user("events.read"))

    def listed(**params):
        response = reader.get("/v1/events", params={"framework": FW, **params})
        assert response.status_code == 200, response.text
        return sorted(item["summary"] for item in response.json()["items"])

    return SimpleNamespace(
        db=db,
        keen=keen,
        reader=reader,
        rows=rows,
        listed=listed,
        access=access,
        foreign=foreign,
        clause=clause,
    )


@pytest.mark.parametrize(
    "params,expected",
    [
        ({"system": "okta"}, ["Access reviewed elsewhere", "Admin login reviewed"]),
        ({"actor": "ALICE"}, ["Access reviewed elsewhere", "Admin login reviewed"]),
        ({"q": "backup"}, ["Nightly backup & restore"]),
        # Search operators are read as ordinary text.
        ({"q": "backup & restore"}, ["Nightly backup & restore"]),
        ({"q": "!heartbeat|"}, ["Unmapped heartbeat"]),
        # Punctuation is part of a substring, and LIKE wildcards match literally.
        ({"q": "R&D"}, ["R&D budget approved"]),
        ({"q": "user_name"}, []),
        ({"q": "100%"}, []),
        ({"control": "A.5.15"}, ["Access reviewed by hand", "Admin login reviewed"]),
        ({"control": "A.9.99"}, []),
        ({"clause": "9.2"}, ["Nightly backup & restore"]),
        (
            {"unmapped": "true"},
            [
                "Access reviewed elsewhere",
                "Config reloaded",
                "R&D budget approved",
                "Unmapped heartbeat",
                "userXname rotated",
            ],
        ),
    ],
)
def test_list_filters_select_the_matching_events(events, params, expected):
    assert events.listed(**params) == expected


def test_control_ids_from_another_framework_match_nothing(events):
    assert events.listed(control=str(events.foreign.id)) == []
    assert events.listed(control=str(events.access.id)) == [
        "Access reviewed by hand",
        "Admin login reviewed",
    ]
    assert events.listed(clause=str(events.clause.id)) == ["Nightly backup & restore"]


def test_facets_apply_the_same_filters(events):
    response = events.reader.get(
        "/v1/events/facets",
        params={"framework": FW, "system": "okta", "actor": "alice", "q": "reviewed"},
    )

    assert response.status_code == 200, response.text
    sources = {item["source"]: item["count"] for item in response.json()["sources"]}
    assert sources == {"ci": 2}
    for control in ("A.9.99", str(events.foreign.id)):
        unknown = events.reader.get(
            "/v1/events/facets", params={"framework": FW, "control": control}
        ).json()
        assert sum(item["count"] for item in unknown["sources"]) == 0, control
    known = events.reader.get(
        "/v1/events/facets", params={"framework": FW, "control": str(events.access.id)}
    ).json()
    assert {item["source"]: item["count"] for item in known["sources"]} == {"ci": 2}


def test_exports_for_an_unknown_control_are_empty(events):
    as_csv = events.reader.get(
        "/v1/events/export", params={"framework": FW, "control": "A.9.99"}
    )
    assert as_csv.status_code == 200
    (header,) = list(csv.reader(io.StringIO(as_csv.text)))
    assert header[:3] == ["id", "timestamp", "source"]

    as_json = events.reader.get(
        "/v1/events/export",
        params={"framework": FW, "control": "A.9.99", "format": "json"},
    )
    assert as_json.json() == {"total": 0, "returned": 0, "items": []}

    filtered = events.reader.get(
        "/v1/events/export",
        params={"framework": FW, "system": "okta", "actor": "alice", "format": "json"},
    ).json()
    assert filtered["total"] == 2


@pytest.mark.parametrize(
    "params,expected",
    [
        # Each list filter matches any of its values.
        (
            {"source": "ci,jenkins"},
            [
                "Access reviewed by hand",
                "Access reviewed elsewhere",
                "Admin login reviewed",
                "Config reloaded",
                "Nightly backup & restore",
                "R&D budget approved",
                "Unmapped heartbeat",
                "userXname rotated",
            ],
        ),
        ({"source": "jenkins, nothing"}, ["Nightly backup & restore"]),
        (
            {"control": "A.5.15,A.8.13"},
            [
                "Access reviewed by hand",
                "Admin login reviewed",
                "Nightly backup & restore",
            ],
        ),
        ({"control": "A.9.99,A.8.13"}, ["Nightly backup & restore"]),
        ({"clause": "9.2,9.9"}, ["Nightly backup & restore"]),
        # The rule that made the mapping, for this framework only.
        ({"rule": "login-review"}, ["Admin login reviewed"]),
        (
            {"rule": "login-review,backup-runs"},
            ["Admin login reviewed", "Nightly backup & restore"],
        ),
        ({"rule": "no-such-rule"}, []),
        # Text conditions apply one operator to one field, ignoring case.
        (
            {"f": "actor:contains:ALICE"},
            ["Access reviewed elsewhere", "Admin login reviewed"],
        ),
        # An event with no actor does not contain the text.
        (
            {"f": "actor:not_contains:alice"},
            [
                "Access reviewed by hand",
                "Config reloaded",
                "Nightly backup & restore",
                "R&D budget approved",
                "Unmapped heartbeat",
                "userXname rotated",
            ],
        ),
        (
            {"f": "system:starts_with:okta"},
            ["Access reviewed elsewhere", "Admin login reviewed"],
        ),
        ({"f": "system:starts_with:prod"}, []),
        ({"f": "system:ends_with:-prod"}, ["Admin login reviewed"]),
        ({"f": "summary:equals:unmapped HEARTBEAT"}, ["Unmapped heartbeat"]),
        ({"f": "summary:equals:heartbeat"}, []),
        ({"f": "external_id:regex:^OKTA-"}, ["Admin login reviewed"]),
        (
            {"f": "summary:not_equals:Unmapped heartbeat"},
            [
                "Access reviewed by hand",
                "Access reviewed elsewhere",
                "Admin login reviewed",
                "Config reloaded",
                "Nightly backup & restore",
                "R&D budget approved",
                "userXname rotated",
            ],
        ),
        ({"f": "external_id:regex:^okta-\\d+$"}, ["Admin login reviewed"]),
        (
            {"f": "summary:not_regex:review|backup"},
            [
                "Config reloaded",
                "R&D budget approved",
                "Unmapped heartbeat",
                "userXname rotated",
            ],
        ),
        # "any" applies the operator across every text field and the source,
        # so one filter stands in for the former free-text search box.
        (
            {"f": "any:contains:alice"},
            ["Access reviewed elsewhere", "Admin login reviewed"],
        ),
        ({"f": "any:contains:jenkins"}, ["Nightly backup & restore"]),
        # The connection that collected the evidence is a text field too.
        ({"f": "connection:equals:Build feed"}, ["Nightly backup & restore"]),
        (
            {"f": "connection:not_regex:."},
            [
                "Access reviewed by hand",
                "Access reviewed elsewhere",
                "Admin login reviewed",
                "Config reloaded",
                "R&D budget approved",
                "Unmapped heartbeat",
                "userXname rotated",
            ],
        ),
        ({"f": "any:contains:build feed"}, ["Nightly backup & restore"]),
        (
            {"f": "any:starts_with:okta"},
            ["Access reviewed elsewhere", "Admin login reviewed"],
        ),
        ({"f": "any:equals:robot"}, ["Unmapped heartbeat", "userXname rotated"]),
        ({"f": "any:regex:^okta-\\d+$"}, ["Admin login reviewed"]),
        # A negated "any" keeps only events where no field matches, including
        # events whose fields are empty.
        (
            {"f": "any:not_contains:re"},
            ["R&D budget approved", "Unmapped heartbeat", "userXname rotated"],
        ),
        (
            {"f": ["any:contains:review", "any:not_contains:okta"]},
            ["Access reviewed by hand"],
        ),
        # A value may contain the separator, and LIKE wildcards match literally.
        ({"f": "summary:contains:backup & restore"}, ["Nightly backup & restore"]),
        ({"f": "summary:contains:user_name"}, []),
        ({"f": "summary:contains:100%"}, []),
        # Conditions combine with each other and with the list filters.
        (
            {"f": ["actor:contains:alice", "system:ends_with:prod"]},
            ["Admin login reviewed"],
        ),
        (
            {"f": "actor:contains:alice", "source": "ci", "control": "A.5.15"},
            ["Admin login reviewed"],
        ),
    ],
)
def test_list_filters_combine_lists_rules_and_text_conditions(events, params, expected):
    assert events.listed(**params) == expected


@pytest.mark.parametrize(
    "condition,detail",
    [
        ("actor:contains", "A text filter is written field:operator:value"),
        (
            "colour:contains:x",
            "Text filters apply to summary, system, actor, action, outcome, external_id, connection, any",
        ),
        (
            "actor:near:x",
            "Text filter operators are contains, not_contains, starts_with, ends_with, equals, not_equals, regex, not_regex",
        ),
        ("actor:contains:   ", "A text filter needs a value"),
        (
            "actor:contains:" + "x" * 513,
            "A text filter value must be at most 512 characters",
        ),
        (
            "actor:regex:(a+)+b",
            "Invalid regular expression: Nested repetition can stall ingestion",
        ),
        (
            "actor:regex:(?x)(a{1, 2}){1, 1000}$",
            "Invalid regular expression: Expanded mode (the x flag) is unsupported; "
            "write the pattern without it",
        ),
        (
            "actor:regex:(a)\\1",
            "Invalid regular expression: Backreferences are unsupported",
        ),
        (
            "actor:regex:[",
            "Invalid regular expression: Invalid pattern: unterminated character set at position 0",
        ),
        # Valid for Python, refused by the database.
        (
            "actor:regex:(?P<name>a)",
            "The database does not support this regular expression: invalid regular expression: invalid embedded option",
        ),
        # Python reads \101 as the octal escape for "A"; once 101 groups
        # precede it, PostgreSQL reads a back-reference, whose matching time
        # grows polynomially with the text.
        ("summary:regex:" + "()" * 98 + "(.*)(.*)(.+)\\101$", BACKREFERENCE),
        ("summary:regex:\\101", BACKREFERENCE),
        ("summary:regex:[\\1]", BACKREFERENCE),
        # PostgreSQL's lookaround constraints take quadratic time.
        ("summary:regex:a(?=.*z)", LOOKAROUND),
        ("summary:not_regex:a(?!b)", LOOKAROUND),
        ("any:regex:(?<=a)b", LOOKAROUND),
        ("any:regex:(?<!a)b", LOOKAROUND),
    ],
)
def test_unusable_text_conditions_are_refused(events, condition, detail):
    for path in FILTERED_VIEWS:
        response = events.reader.get(
            path, params={"framework": FW, "f": condition, **WINDOW}
        )
        assert response.status_code == 400, (path, response.text)
        assert response.json()["detail"] == detail, path


def test_mapping_rule_patterns_keep_the_python_engine_rules():
    # Rule patterns match in Python, which reads these constructs safely.
    assert compile_pattern("\\101").search("A")
    assert compile_pattern("a(?=b)").search("ab")
    # The database check reads the pattern's text, so it also refuses an
    # escaped backslash before a digit; [\\]1 matches a backslash and a digit.
    for pattern in ("\\101", "a(?=b)", "\\\\1"):
        with pytest.raises(ValueError):
            validate_database_pattern(pattern)
    validate_database_pattern("[\\\\]1")
    validate_database_pattern("\\x41\\d+\\.\\(")


def test_every_filtered_view_scans_with_the_budget(events):
    """Each view applies the budget and reports a stopped scan as a 400."""
    if not events.keen.postgres:
        pytest.skip("PostgreSQL statement budgets")
    params = {"framework": FW, "f": "summary:regex:review", **WINDOW}
    for path in FILTERED_VIEWS:
        assert events.reader.get(path, params=params).status_code == 200, path

    dialect = events.db.get_bind().engine.dialect
    budgets = []

    def scan(cursor, statement, parameters, context):
        if "~*" not in statement:
            return False
        cursor.execute("SHOW statement_timeout")
        budgets.append(cursor.fetchone()[0])
        if budgets[-1] != "0":
            # The database stops the scan when its budget runs out.
            raise errors.QueryCanceled("canceling statement due to statement timeout")
        return False

    sql_event.listen(dialect, "do_execute", scan)
    try:
        for n, path in enumerate(FILTERED_VIEWS):
            budgets.clear()
            # A source of its own leaves the view no cached answer.
            response = events.reader.get(path, params={**params, "source": f"s{n}"})
            assert budgets and set(budgets) == {"5s"}, (path, budgets)
            assert response.status_code == 400, (path, response.text)
            assert response.json()["detail"] == (
                "The regular expression search took too long; narrow the date "
                "range or simplify the pattern"
            )
            assert events.db.execute(text("SHOW statement_timeout")).scalar() == "0"
    finally:
        sql_event.remove(dialect, "do_execute", scan)


def test_filter_lists_are_bounded(events):
    too_many = events.reader.get(
        "/v1/events", params={"framework": FW, "f": ["actor:contains:a"] * 11}
    )
    assert (too_many.status_code, too_many.json()["detail"]) == (
        400,
        "At most 10 text filters can be combined",
    )
    sources = events.reader.get(
        "/v1/events",
        params={"framework": FW, "source": ",".join(f"s{i}" for i in range(51))},
    )
    assert (sources.status_code, sources.json()["detail"]) == (
        400,
        "At most 50 source values can be chosen",
    )


def test_a_regular_expression_scan_runs_with_a_time_budget(events):
    from app.services.event_filters import parse_event_filters, scan_budget
    from sqlalchemy import text

    db = events.db
    without = parse_event_filters(framework=FW, conditions=["summary:contains:x"])
    with_regex = parse_event_filters(framework=FW, conditions=["summary:regex:x"])

    scan_budget(db, without)
    assert db.execute(text("SHOW statement_timeout")).scalar() == "0"
    scan_budget(db, with_regex)
    assert db.execute(text("SHOW statement_timeout")).scalar() == "5s"
    # The budget ends with the transaction.
    db.rollback()
    assert db.execute(text("SHOW statement_timeout")).scalar() == "0"


def test_a_cancelled_scan_is_reported_as_the_pattern_taking_too_long(events):
    from app.services.errors import DomainError
    from app.services.event_filters import parse_event_filters, run_scan
    from psycopg2 import errors
    from sqlalchemy.exc import OperationalError

    filters = parse_event_filters(framework=FW, conditions=["summary:regex:x"])
    cancelled = OperationalError(
        "SELECT 1",
        {},
        errors.QueryCanceled("canceling statement due to statement timeout"),
    )

    def scan():
        raise cancelled

    with pytest.raises(DomainError) as refused:
        run_scan(events.db, filters, scan)
    assert (refused.value.status_code, refused.value.detail) == (
        400,
        "The regular expression search took too long; narrow the date range "
        "or simplify the pattern",
    )
    # A scan the database itself cancels under the budget reports the same way.
    from app.services import event_filters
    from sqlalchemy import text

    event_filters.REGEX_SCAN_TIMEOUT_MS = 1
    try:
        event_filters.scan_budget(events.db, filters)
        with pytest.raises(DomainError) as stopped:
            run_scan(
                events.db,
                filters,
                lambda: events.db.execute(text("SELECT pg_sleep(0.2)")).scalar(),
            )
    finally:
        event_filters.REGEX_SCAN_TIMEOUT_MS = 5000
    assert stopped.value.status_code == 400
    # The session is usable again after the cancelled statement.
    assert events.db.execute(text("SELECT 1")).scalar() == 1
    # Other database failures are not the pattern's fault.
    other = OperationalError("SELECT 1", {}, errors.AdminShutdown("terminating"))

    def failing():
        raise other

    with pytest.raises(OperationalError):
        run_scan(events.db, filters, failing)


def test_rule_facets_name_the_rules_behind_the_filtered_events(events):
    def facets(**params):
        response = events.reader.get(
            "/v1/events/facets", params={"framework": FW, **params}
        )
        assert response.status_code == 200, response.text
        return response.json()

    rules = facets()["rules"]
    assert [(r["rule_id"], r["count"]) for r in rules] == [
        ("backup-runs", 1),
        ("login-review", 1),
    ]
    assert rules[0]["targets"] == [] and rules[0]["description"] == ""
    # The rule facet leaves the rule filter out, so other rules stay offered,
    # while the source facet respects it.
    with_rule = facets(rule="login-review")
    assert [r["rule_id"] for r in with_rule["rules"]] == ["backup-runs", "login-review"]
    assert {s["source"]: s["count"] for s in with_rule["sources"]} == {"ci": 1}
    # Other filters narrow the rule facet.
    assert [r["rule_id"] for r in facets(source="jenkins")["rules"]] == ["backup-runs"]
    assert facets(unmapped="true")["rules"] == []


def test_rule_facets_describe_saved_rules(events, monkeypatch):
    from app.services import managed_configurations

    monkeypatch.setattr(
        managed_configurations,
        "_normalized_rules",
        lambda db: [
            {
                "id": "login-review",
                "description": "Administrator sign-ins",
                "enabled": True,
                "map_to": [{"framework": FW, "ref": "A.5.15", "kind": "control"}],
            }
        ],
    )
    rules = events.reader.get("/v1/events/facets", params={"framework": FW}).json()[
        "rules"
    ]
    described = next(r for r in rules if r["rule_id"] == "login-review")
    assert described == {
        "rule_id": "login-review",
        "count": 1,
        "description": "Administrator sign-ins",
        "enabled": True,
        "targets": [f"{FW}:A.5.15"],
    }


def test_exports_apply_list_filters_and_name_them(events):
    response = events.reader.get(
        "/v1/events/export",
        params={
            "framework": FW,
            "source": "ci,jenkins",
            "rule": "backup-runs",
            "format": "json",
        },
    )
    assert response.status_code == 200, response.text
    assert [item["summary"] for item in response.json()["items"]] == [
        "Nightly backup & restore"
    ]
    assert response.headers["content-disposition"] == (
        'attachment; filename="events_source-ci-jenkins_rule-backup-runs.json"; '
        "filename*=UTF-8''events_source-ci-jenkins_rule-backup-runs.json"
    )


def _export_statements(events, record):
    """Record each statement that reads a page of exported events."""

    def page(conn, cursor, statement, parameters, context, executemany):
        if "ORDER BY events.timestamp DESC" in statement:
            record(statement)

    engine = events.db.get_bind().engine
    sql_event.listen(engine, "before_cursor_execute", page)
    return lambda: sql_event.remove(engine, "before_cursor_execute", page)


def test_exports_page_by_time_and_identifier(events, monkeypatch):
    # Every fixture event has the same timestamp, so pages split within it.
    monkeypatch.setattr(event_operations, "EXPORT_PAGE_ROWS", 3)
    pages = []
    stop = _export_statements(events, pages.append)
    try:
        response = events.reader.get(
            "/v1/events/export", params={"framework": FW, "format": "json"}
        )
    finally:
        stop()

    assert response.status_code == 200, response.text
    # Newest first, then by identifier, each event once.
    stored = events.db.query(Event.id, Event.timestamp).all()
    newest_first = sorted(stored, key=lambda row: (-row.timestamp.timestamp(), row.id))
    expected = [str(row.id) for row in newest_first]
    assert [item["id"] for item in response.json()["items"]] == expected
    # Each page starts after the last one's key; none re-reads earlier events.
    assert len(pages) == 3 and not any("OFFSET" in s for s in pages)


@pytest.mark.parametrize("fmt", ["csv", "json", "ndjson"])
def test_an_export_that_reaches_its_time_limit_says_so(events, monkeypatch, fmt):
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(
        event_operations, "time", SimpleNamespace(monotonic=lambda: clock.now)
    )
    monkeypatch.setattr(event_operations, "EXPORT_PAGE_ROWS", 2)
    read_page, apply_budget = (
        event_operations._export_page,
        event_operations.scan_budget,
    )
    budgets = []

    def slow_page(*args):
        found = read_page(*args)
        clock.now += 40
        return found

    def budget(db, filters, *, within_ms=None):
        budgets.append(within_ms)
        apply_budget(db, filters, within_ms=within_ms)

    monkeypatch.setattr(event_operations, "_export_page", slow_page)
    monkeypatch.setattr(event_operations, "scan_budget", budget)

    response = events.reader.get(
        "/v1/events/export", params={"framework": FW, "format": fmt}
    )

    assert response.status_code == 200, response.text
    # The count and each page run within the time the export has left; after
    # two 40-second pages, none is left for a third.
    assert budgets == [60000, 60000, 20000]
    detail = (
        "Export incomplete: 4 of 8 events were exported before the export reached "
        "its time limit; narrow the date range or filters and export again"
    )
    if fmt == "json":
        exported = response.json()
        assert len(exported.pop("items")) == 4
        assert exported == {
            "total": 8,
            "returned": 8,
            "incomplete": True,
            "exported": 4,
            "detail": detail,
        }
    elif fmt == "ndjson":
        lines = [json.loads(line) for line in response.text.splitlines()]
        assert len(lines) == 5 and all("id" in line for line in lines[:4])
        assert lines[-1] == {"incomplete": True, "exported": 4, "detail": detail}
    else:
        rows = list(csv.reader(io.StringIO(response.text)))
        assert rows[0][0] == "id" and len(rows) == 6
        assert rows[-1] == [detail]


def test_a_stopped_export_is_reported(events, monkeypatch):
    """A count stopped by its budget is an error; a page stopped later ends the file."""
    if not events.keen.postgres:
        pytest.skip("PostgreSQL statement budgets")
    monkeypatch.setattr(event_operations, "EXPORT_PAGE_ROWS", 3)
    dialect = events.db.get_bind().engine.dialect
    stop = {"count": True}
    pages = []

    def scan(cursor, statement, parameters, context):
        # The database stops the chosen statement as its budget runs out.
        cancelled = errors.QueryCanceled("canceling statement due to statement timeout")
        if "count(*)" in statement and stop["count"]:
            raise cancelled
        if "ORDER BY events.timestamp DESC" in statement:
            pages.append(statement)
            if len(pages) == 2:
                raise cancelled
        return False

    sql_event.listen(dialect, "do_execute", scan)
    try:
        counted = events.reader.get(
            "/v1/events/export", params={"framework": FW, "format": "json"}
        )
        assert (counted.status_code, counted.json()["detail"]) == (
            400,
            "The search took too long; narrow the date range or filters",
        )
        # The next export counts and reads its first page; its second page is
        # stopped after the response has started.
        stop["count"] = False
        response = events.reader.get(
            "/v1/events/export", params={"framework": FW, "format": "json"}
        )
    finally:
        sql_event.remove(dialect, "do_execute", scan)

    assert response.status_code == 200, response.text
    exported = response.json()
    assert len(exported["items"]) == 3
    assert (exported["incomplete"], exported["exported"]) == (True, 3)
    assert exported["detail"].startswith("Export incomplete: 3 of 8 events")


def test_the_ticker_shows_newer_arrivals_with_bounded_summaries(events):
    long_summary = "Deploy " + "x" * 300 + " password=hunter2"
    newest = events.db.query(Event).filter_by(summary="Unmapped heartbeat").one()
    newest.summary = long_summary
    newest.created_at = utc_now_naive() + timedelta(seconds=5)
    events.db.commit()

    response = events.reader.get("/v1/evidence/latest", params={"limit": 2})

    assert response.status_code == 200, response.text
    first = response.json()["events"][0]
    assert first["id"] == str(newest.id)
    assert len(first["summary"]) == 218 and first["summary"].endswith("…")
    later = events.reader.get(
        "/v1/evidence/latest",
        params={"after": (newest.created_at + timedelta(seconds=1)).isoformat()},
    ).json()
    assert later["events"] == []


def test_incidents_need_their_own_permissions(events):
    event_id = events.rows["login"].id
    create = events.reader.post(
        f"/v1/events/{event_id}/incident",
        json={"title": "Suspicious login"},
        headers=unsafe(events.reader),
    )
    assert (create.status_code, create.json()["detail"]) == (
        403,
        "incident.create permission required",
    )
    remove = events.reader.delete(
        f"/v1/events/{event_id}/incidents/{uuid.uuid4()}", headers=unsafe(events.reader)
    )
    assert (remove.status_code, remove.json()["detail"]) == (
        403,
        "incident.delete permission required",
    )
    deleter = events.keen.client(events.keen.user("events.read", "incident.delete"))
    bad = deleter.delete(
        "/v1/events/not-a-uuid/incidents/also-not", headers=unsafe(deleter)
    )
    assert (bad.status_code, bad.json()["detail"]) == (
        400,
        "event_id and incident_id must be UUIDs",
    )


def test_incident_operations_check_permissions_themselves(events, monkeypatch):
    from app.schemas import IncidentCreatePayload
    from app.services import events as event_operations
    from app.services.context import ChangeContext
    from app.services.errors import DomainError

    # The incident webhook is configured; the reader still lacks permission.
    monkeypatch.setattr(event_operations, "incident_webhook_enabled", lambda: True)
    reader = events.keen.user("events.read")
    context = ChangeContext(actor=reader)
    event_id = str(events.rows["login"].id)

    with pytest.raises(DomainError) as created:
        event_operations.create_event_incident(
            event_id=event_id,
            payload=IncidentCreatePayload(title="Suspicious login"),
            context=context,
            db=events.db,
            event_url="/event.html",
        )
    with pytest.raises(DomainError) as deleted:
        event_operations.delete_event_incident(
            event_id=event_id,
            incident_id=str(uuid.uuid4()),
            context=context,
            db=events.db,
        )

    assert (created.value.status_code, deleted.value.status_code) == (403, 403)
