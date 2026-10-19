"""Event list, facet and export filters through the assembled application.

Events are filtered by system, actor, free text, control (by reference or ID,
within the chosen framework) and clause. The homepage ticker shows the newest
arrivals with a bounded, redacted summary. Requests come from a user who can
read events.
"""

import csv
import io
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from app.core.datetime_utils import utc_now_naive
from app.mapping.rules import rule_rationale
from app.db.models import (
    ControlClauseLink,
    ControlItem,
    Event,
    Framework,
    FrameworkClause,
    Mapping,
)

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import keen, unsafe  # noqa: F401

FW = "fw-filters"


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
            "Text filters apply to summary, system, actor, action, outcome, external_id",
        ),
        (
            "actor:near:x",
            "Text filter operators are contains, not_contains, starts_with, ends_with, equals, not_equals, regex, not_regex",
        ),
        ("actor:contains:   ", "A text filter needs a value"),
        (
            "actor:contains:" + "x" * 513,
            "A text filter value must be under 512 characters",
        ),
        (
            "actor:regex:(a+)+b",
            "Invalid regular expression: Nested repetition can stall ingestion",
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
    ],
)
def test_unusable_text_conditions_are_refused(events, condition, detail):
    for path in ("/v1/events", "/v1/events/facets", "/v1/events/export"):
        response = events.reader.get(path, params={"framework": FW, "f": condition})
        assert response.status_code == 400, (path, response.text)
        assert response.json()["detail"] == detail, path


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
    from sqlalchemy import text

    from app.services.event_filters import parse_event_filters, scan_budget

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
    from psycopg2 import errors
    from sqlalchemy.exc import OperationalError

    from app.services.errors import DomainError
    from app.services.event_filters import parse_event_filters, run_scan

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
    from sqlalchemy import text

    from app.services import event_filters

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
        'attachment; filename="events_source-ci-jenkins_rule-backup-runs.json"'
    )


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
