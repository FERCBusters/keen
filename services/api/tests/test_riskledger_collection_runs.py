"""Risk Ledger collection runs from the scheduled entry point.

A connection's API key reaches the Risk Ledger alpha API through the shared
collection client; the run imports suppliers, risks and the controls they name,
stores each record as a snapshot event and records a cursor. An in-memory
transport stands in for Risk Ledger. Runs use the contract database because
imports hold a PostgreSQL advisory lock.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from app.core.config import settings
from app.db.models import (
    Artifact,
    ControlItem,
    Event,
    IngestionCursor,
    IsmsVendor,
    Risk,
    RiskControlLink,
)
from app.ingest import riskledger as rl
from app.ingest.connections import Connection, connection_scope, namespace
from sqlalchemy import text

ORG, SUP, RID, CTL, DOM, LEVEL, OTHER = [
    f"00000000-0000-4000-8000-00000000000{i}" for i in range(1, 8)
]
KEY = "synthetic-riskledger-key"


class RiskLedger:
    """The documented alpha API reads, with page offsets and totals."""

    def __init__(self):
        self.requests = []
        self.failures = {}
        self.organisation = {"id": ORG, "name": "Customer"}
        self.suppliers = [
            {
                "id": SUP,
                "name": "Payroll supplier",
                "website": "https://payroll.example",
                "framework": {"level": {"id": LEVEL}, "addOnDomains": []},
                "complianceByDomain": [{"domainID": DOM, "percentageCompliance": 80}],
            }
        ]
        self.risks = [
            {
                "id": RID,
                "name": "Payroll outage",
                "description": "Single payroll provider",
                "supplierID": SUP,
                "associatedType": "control",
                "associatedID": CTL,
                "likelihood": "medium",
                "impact": "high",
            }
        ]
        self.domains = {DOM: {"id": DOM, "letter": "B", "name": "Continuity"}}
        self.controls = {
            CTL: {
                "id": CTL,
                "domainID": DOM,
                "number": 4,
                "question": "Is there a continuity plan?",
            }
        }

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path.removeprefix("/alpha")
        if path in self.failures:
            return httpx.Response(self.failures[path], text="upstream detail")
        if path == "/organisations/me":
            return httpx.Response(200, json=self.organisation)
        if path in ("/suppliers", "/risks"):
            kind = path[1:]
            page = (
                json.loads(request.content)["page"]
                if kind == "suppliers"
                else {k: int(v) for k, v in request.url.params.items()}
            )
            rows = getattr(self, kind)
            start, limit = page["offset"], page["limit"]
            return httpx.Response(
                200,
                json={
                    kind: rows[start : start + limit],
                    "page": {"offset": start, "limit": limit, "total": len(rows)},
                },
            )
        for prefix, records in (
            ("/framework/domains/", self.domains),
            ("/framework/controls/", self.controls),
        ):
            if path.startswith(prefix) and path[len(prefix) :] in records:
                return httpx.Response(200, json=records[path[len(prefix) :]])
        return httpx.Response(404, json={"message": "Not found"})


@pytest.fixture
def ledger(contract_db, monkeypatch, tmp_path):
    if contract_db.get_bind().dialect.name != "postgresql":
        pytest.skip("Risk Ledger imports hold a PostgreSQL advisory lock")
    for name, value in {
        "demo_mode": False,
        "riskledger_enabled": True,
        "event_data_masking": "false",
        "artifact_storage_backend": "local",
        "artifact_local_dir": str(tmp_path),
    }.items():
        monkeypatch.setattr(settings, name, value)
    api = RiskLedger()
    clients = []

    def client(**kwargs):
        clients.append(kwargs)
        return httpx.Client(transport=httpx.MockTransport(api), **kwargs)

    monkeypatch.setattr(rl, "ingestion_client", client)
    inputs = {"organizations": [{"org": "*", "label": "Customer"}]}
    connection = Connection(
        "db:riskledger", "riskledger", "Risk Ledger", {}, {"api_key": KEY}, inputs
    )

    def run():
        with patch("app.ingest.connections.connections", return_value=[connection]):
            return rl.ingest_riskledger_all(contract_db)

    def cursor():
        with connection_scope(connection):
            name = namespace("riskledger:" + ORG)
        return contract_db.query(IngestionCursor).filter_by(name=name).one_or_none()

    return SimpleNamespace(
        db=contract_db,
        api=api,
        clients=clients,
        inputs=inputs,
        run=run,
        cursor=cursor,
    )


def test_a_run_imports_suppliers_risks_and_the_controls_they_name(ledger):
    (result,) = ledger.run()

    assert {
        k: result[k] for k in ("organization_id", "examined", "created_events")
    } == {
        "organization_id": ORG,
        "examined": 2,
        "created_events": 4,
    }
    assert (result["controls_discovered"], result["domains_discovered"]) == (1, 1)
    assert result["connection_id"] == "db:riskledger"
    (options,) = ledger.clients
    assert options["headers"]["Authorization"] == "Bearer " + KEY
    assert options["follow_redirects"] is False
    requests = [(r.method, r.url.path) for r in ledger.api.requests]
    assert requests == [
        ("GET", "/alpha/organisations/me"),
        ("POST", "/alpha/suppliers"),
        ("GET", f"/alpha/framework/domains/{DOM}"),
        ("GET", "/alpha/risks"),
        ("GET", f"/alpha/framework/controls/{CTL}"),
    ]
    vendor = ledger.db.query(IsmsVendor).one()
    assert (vendor.name, vendor.website) == (
        "Payroll supplier",
        "https://payroll.example",
    )
    risk = ledger.db.query(Risk).one()
    assert risk.asset.vendor_id == vendor.id
    (link,) = ledger.db.query(RiskControlLink).filter_by(risk_id=risk.id).all()
    control = ledger.db.get(ControlItem, link.control_item_id)
    assert (control.framework_slug, control.ref) == (rl.FRAMEWORK, CTL)
    assert control.title == "B 4 — Is there a continuity plan?"
    events = ledger.db.query(Event).filter_by(source="riskledger").all()
    assert sorted(e.action for e in events) == [
        "control.snapshot",
        "domain.snapshot",
        "risk.snapshot",
        "supplier.snapshot",
    ]
    assert {e.system for e in events} == {"Customer"}
    assert (
        ledger.db.query(Artifact)
        .filter(Artifact.event_id.in_([e.id for e in events]))
        .count()
        == 4
    )
    assert ledger.cursor().meta["created_events"] == 4

    # An unchanged account yields no new snapshots.
    (again,) = ledger.run()
    assert (again["created_events"], ledger.db.query(Event).count()) == (0, 4)


def test_a_failure_part_way_keeps_completed_snapshots_and_reports_safely(ledger):
    ledger.api.failures["/risks"] = 500

    (result,) = ledger.run()

    assert result["error"] == (
        "Risk Ledger HTTP 500; check API access or rate limits. Retry on a later run."
    )
    assert result["partial"] is True and "upstream detail" not in json.dumps(result)
    # Snapshots committed before the failure stay; the cursor waits.
    actions = {e.action for e in ledger.db.query(Event).filter_by(source="riskledger")}
    assert actions == {"supplier.snapshot", "domain.snapshot"}
    assert ledger.db.query(Risk).count() == 0
    assert ledger.cursor() is None


def test_one_import_runs_at_a_time(ledger):
    engine = ledger.db.get_bind().engine
    with engine.connect() as other:
        assert other.execute(text("SELECT pg_try_advisory_lock(1262830937)")).scalar()
        try:
            (result,) = ledger.run()
        finally:
            other.execute(text("SELECT pg_advisory_unlock(1262830937)"))
            other.commit()

    assert result["reason"] == "Another Risk Ledger import is in progress"
    assert ledger.api.requests == []


def test_an_account_without_an_organisation_selection_is_skipped(ledger):
    ledger.inputs["organizations"] = []

    (result,) = ledger.run()

    assert result["skipped"] is True and result["reason"].startswith(
        "Add a Risk Ledger organization collection"
    )
    assert ledger.api.requests == []


def test_invalid_settings_stop_the_run_with_their_reason(ledger):
    ledger.inputs["page_size"] = 500

    (result,) = ledger.run()

    assert result["error"] == "page_size must be between 1 and 100"
    assert ledger.api.requests == []


def test_suppliers_outside_the_chosen_levels_and_their_risks_are_left_out(ledger):
    ledger.inputs["framework_level_ids"] = [OTHER]

    (result,) = ledger.run()

    assert (result["examined"], result["created_events"]) == (0, 0)
    assert ledger.db.query(Risk).count() == ledger.db.query(IsmsVendor).count() == 0
    assert [r.url.path for r in ledger.api.requests] == [
        "/alpha/organisations/me",
        "/alpha/suppliers",
        "/alpha/risks",
    ]


def test_masking_applies_before_records_are_stored(ledger, monkeypatch):
    monkeypatch.setattr(settings, "event_data_masking", "true")
    ledger.api.risks[0]["riskOwner"] = "owner@payroll.example"

    ledger.run()

    stored = ledger.db.query(Event).filter_by(action="risk.snapshot").one()
    assert "owner@payroll.example" not in json.dumps(stored.normalized_payload)
    risk = ledger.db.query(Risk).one()
    assert "owner@payroll.example" not in risk.note


def answering(*responses):
    """A Risk Ledger client whose transport returns the given responses in turn."""
    replies = list(responses)

    def handler(request):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    return rl.Client(httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize(
    "reply,message",
    [
        (httpx.Response(200, content=b"[1, 2]"), "invalid JSON object"),
        (httpx.Response(200, content=b"{not json"), "network, TLS or JSON failure"),
        (httpx.Response(200, content=b"\xff\xfe"), "network, TLS or JSON failure"),
        (httpx.ConnectError("refused"), "network, TLS or JSON failure"),
        (
            httpx.Response(200, content=b'{"a": "' + b"x" * (8 * 1024 * 1024) + b'"}'),
            "exceeded the 8 MiB limit",
        ),
    ],
    ids=["array", "malformed", "undecodable", "unreachable", "oversized"],
)
def test_unusable_responses_raise_operator_safe_errors(reply, message):
    with pytest.raises(rl.RiskLedgerError, match=message):
        answering(reply).request("/organisations/me")


def page(kind, rows, offset=0, total=None, limit=1):
    return httpx.Response(
        200,
        json={
            kind: rows,
            "page": {
                "offset": offset,
                "limit": limit,
                "total": len(rows) if total is None else total,
            },
        },
    )


@pytest.mark.parametrize(
    "reply,message",
    [
        (httpx.Response(200, json={"risks": {}, "page": {}}), "invalid pagination"),
        (page("risks", [{"id": RID}, {"id": SUP}]), "invalid pagination"),
        (httpx.Response(200, json={"risks": []}), "invalid pagination"),
        (page("risks", [], total=-1), "inconsistent page offset/total"),
        (page("risks", [], total=True), "inconsistent page offset/total"),
        (page("risks", [{"id": RID}], offset=5), "inconsistent page offset/total"),
        (page("risks", [], total=3), "stopped before the reported total"),
        (page("risks", ["not a record"]), "invalid record"),
        (page("risks", [{"id": "not-a-uuid"}]), "valid UUID identifier"),
    ],
    ids=[
        "rows-object",
        "too-many-rows",
        "no-page",
        "negative-total",
        "boolean-total",
        "wrong-offset",
        "early-end",
        "non-object-row",
        "bad-identifier",
    ],
)
def test_inconsistent_pages_stop_collection(reply, message):
    with pytest.raises(rl.RiskLedgerError, match=message):
        list(answering(reply).pages("risks", {"page_size": 1}))


@pytest.mark.parametrize(
    "cfg,message",
    [
        ([], "must be an object"),
        ({"organizations": {"org": "*"}}, "one Risk Ledger organisation"),
        ({"organizations": [{"label": "No org"}]}, "Set org"),
        ({"organizations": [{"org": "*", "label": "x" * 129}]}, "at most 128"),
        ({"organizations": [{"org": "*", "label": 7}]}, "at most 128"),
        ({"control_ids": CTL}, "list of up to 500"),
        ({"domain_ids": [DOM] * 501}, "list of up to 500"),
        ({"control_ids": [CTL, CTL]}, "distinct UUIDs"),
        ({"control_ids": [7]}, "valid UUID identifier"),
    ],
)
def test_settings_are_checked_before_collection(cfg, message):
    with pytest.raises(rl.RiskLedgerError, match=message):
        rl.validate_config(cfg)


def test_a_full_valid_configuration_is_accepted_unchanged():
    cfg = {
        "organizations": [{"org": ORG, "label": "Customer"}],
        "control_ids": [CTL],
        "domain_ids": [DOM],
        "framework_level_ids": [LEVEL],
        "import_suppliers": False,
        "page_size": 100,
        "max_pages": 1000,
        "max_run_seconds": 1800,
    }
    assert rl.validate_config(cfg) is cfg


@pytest.mark.parametrize(
    "website,kept",
    [
        ("https://payroll.example/about", True),
        ("http://payroll.example", True),
        ("https://user:pass@payroll.example", False),
        ("ftp://payroll.example", False),
        ("http://[::1", False),
        ("", False),
    ],
)
def test_only_plain_web_addresses_are_kept_for_suppliers(website, kept):
    assert rl._safe_website(website) == (website if kept else "")


def test_a_run_stops_when_its_time_budget_ends_mid_response(monkeypatch):
    # Budget set at 0, request starts at 1, the body arrives after the deadline.
    clock = iter([0, 1, 400])
    monkeypatch.setattr(rl.time, "monotonic", lambda: next(clock))
    client = answering(httpx.Response(200, json={"id": ORG}))

    with pytest.raises(rl.RiskLedgerError, match="run time limit reached"):
        client.request("/organisations/me")


def test_named_domains_and_controls_are_imported_without_suppliers(ledger):
    ledger.inputs.update(
        import_suppliers=False, import_risks=False, domain_ids=[DOM], control_ids=[CTL]
    )

    (result,) = ledger.run()

    assert (result["domains_discovered"], result["controls_discovered"]) == (1, 1)
    assert [r.url.path for r in ledger.api.requests] == [
        "/alpha/organisations/me",
        f"/alpha/framework/domains/{DOM}",
        f"/alpha/framework/controls/{CTL}",
    ]
    assert ledger.db.query(IsmsVendor).count() == 0
