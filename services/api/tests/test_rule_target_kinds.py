"""Evidence rules attach events only to the control or clause they target.

Framework clauses are also kept in the control catalogue, as rows of type
"clause", so a clause and a control can share a reference. A rule target
records which of the two it names; older targets without a kind name the
control when both exist. Every mapping path is exercised: collector ingestion,
the log collectors, rule backfills, stale-mapping reviews and rule saving.
"""

import uuid
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from app.db.models import (
    ControlItem,
    Event,
    Framework,
    ManagedConfiguration,
    Mapping,
    RuleBackfillJob,
)
from app.ingest import cloudwatch_logs, common, loki
from app.mapping.rules import evaluate_by_framework, parse_rules
from app.services import managed_configurations
from app.worker import rule_backfill

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)

HOSTILE = '<img src=x onerror="alert(1)">'


def _rule(*targets, rule_id="kinds", source="test"):
    return {
        "id": rule_id,
        "when": {"source": source},
        "map_to": [{"framework": "A", **target} for target in targets],
        "confidence": 0.8,
    }


def _rules(*targets):
    return parse_rules({"rules": [_rule(*targets)]})


# --- Parsing and evaluation ------------------------------------------------------


def test_targets_keep_the_kind_they_name():
    rules = _rules(
        {"ref": "5.1", "kind": "clause"},
        {"ref": "5.1", "kind": "control"},
        {"ref": "5.1"},
        {"ref": "7.1", "kind": " Clause "},
    )
    (rule,) = rules
    assert [(t.ref, t.kind) for t in rule.targets] == [
        ("5.1", "clause"),
        ("5.1", "control"),
        ("5.1", None),
        ("7.1", "clause"),
    ]
    details = evaluate_by_framework({"source": "test"}, rules, details=True)
    assert [(hit["ref"], hit["kind"]) for hit in details["A"]] == [
        ("5.1", "clause"),
        ("5.1", "control"),
        ("5.1", None),
        ("7.1", "clause"),
    ]
    # Without details, matches stay a list of distinct references.
    assert evaluate_by_framework({"source": "test"}, rules) == {"A": ["5.1", "7.1"]}
    # An unsupported kind skips the whole rule, so evidence never attaches to a
    # guess about what the rule meant.
    assert _rules({"ref": "5.1"}, {"ref": "8.1", "kind": "chapter"}) == []


def test_migrated_rules_keep_their_target_kinds():
    from app.core.evidence_migration import unify

    rule = _rule({"ref": "5.1", "kind": "clause"}, {"ref": "6.1"})
    converted, _ = unify({"rules": {"rules": [rule]}}, "A")
    (migrated,) = converted["rules"]["rules"]
    assert migrated["map_to"] == [
        {"framework": "A", "ref": "5.1", "kind": "clause"},
        {"framework": "A", "ref": "6.1"},
    ]
    assert unify(converted, "A")[0] == converted


# --- Collector ingestion -----------------------------------------------------------


@pytest.fixture
def catalogue(contract_db):
    db = contract_db
    db.add(Framework(slug="A"))
    db.flush()

    def add(ref, kind, title=None):
        row = ControlItem(framework_slug="A", type=kind, ref=ref, title=title or ref)
        db.add(row)
        db.flush()
        return row

    rows = SimpleNamespace(
        clause_only=add("4.1", "clause"),
        perimeter=add("7.1", "annex_control"),
        perimeter_custom=add("7.1", "custom", HOSTILE),
    )
    return SimpleNamespace(db=db, add=add, rows=rows)


def _event(db, source="test"):
    row = Event(
        source=source,
        system="ci",
        action="deploy",
        outcome="success",
        severity=2,
        summary="Policy published",
        timestamp=datetime(2026, 3, 1, 12, 0),
        external_id=uuid.uuid4().hex,
        raw_pointer={},
        normalized_payload={},
    )
    db.add(row)
    db.flush()
    return row


def _mapped(db, event):
    db.expire_all()
    return {m.control_item_id for m in db.query(Mapping).filter_by(event_id=event.id)}


def _apply(catalogue, monkeypatch, *targets):
    monkeypatch.setattr(common, "load_rules", lambda *a, **k: _rules(*targets))
    event = _event(catalogue.db)
    common.apply_rules(catalogue.db, event)
    return _mapped(catalogue.db, event)


@pytest.mark.parametrize("kind", ["clause", "control"])
def test_rule_evidence_attaches_only_to_the_row_the_target_names(
    catalogue, monkeypatch, kind
):
    # The other row is added last, which is where a reference-only lookup lands.
    first, last = ("clause", "annex_control")
    if kind == "control":
        first, last = last, first
    named = catalogue.add("5.1", first)
    catalogue.add("5.1", last)
    assert _apply(catalogue, monkeypatch, {"ref": "5.1", "kind": kind}) == {named.id}


def test_targets_without_a_kind_prefer_the_control_then_the_clause(
    catalogue, monkeypatch
):
    # The clause is added last, which is where a reference-only lookup lands.
    control = catalogue.add("5.1", "annex_control")
    catalogue.add("5.1", "clause")
    clause = catalogue.rows.clause_only
    mapped = _apply(catalogue, monkeypatch, {"ref": "5.1"}, {"ref": "4.1"})
    assert mapped == {control.id, clause.id}


def test_missing_clauses_attach_nothing_and_missing_controls_get_placeholders(
    catalogue, monkeypatch
):
    before = catalogue.db.query(ControlItem).count()
    mapped = _apply(
        catalogue,
        monkeypatch,
        {"ref": "9.9", "kind": "clause"},
        {"ref": "9.8", "kind": "control"},
        # A control target with only a clause of that reference gets a control.
        {"ref": "4.1", "kind": "control"},
    )
    added = catalogue.db.query(ControlItem).filter(ControlItem.id.in_(mapped)).all()
    assert sorted((row.ref, row.type) for row in added) == [
        ("4.1", "annex_control"),
        ("9.8", "annex_control"),
    ]
    assert catalogue.db.query(ControlItem).count() == before + 2
    assert not catalogue.db.query(ControlItem).filter_by(ref="9.9").count()


def test_a_reference_two_controls_share_attaches_nothing(catalogue, monkeypatch):
    before = catalogue.db.query(ControlItem).count()
    mapped = _apply(
        catalogue, monkeypatch, {"ref": "7.1", "kind": "control"}, {"ref": "7.1"}
    )
    assert mapped == set()
    assert catalogue.db.query(ControlItem).count() == before


def test_two_targets_naming_one_row_map_it_once(catalogue, monkeypatch):
    control = catalogue.add("5.1", "annex_control")
    catalogue.add("5.1", "clause")
    targets = ({"ref": "5.1", "kind": "control"}, {"ref": "5.1"})
    assert _apply(catalogue, monkeypatch, *targets) == {control.id}


def test_rules_for_a_removed_framework_add_no_placeholders(catalogue, monkeypatch):
    rules = parse_rules(
        {"rules": [{**_rule(), "map_to": [{"framework": "GONE", "ref": "1.1"}]}]}
    )
    monkeypatch.setattr(common, "load_rules", lambda *a, **k: rules)
    event = _event(catalogue.db)
    assert common.apply_rules(catalogue.db, event) == 0
    assert not catalogue.db.query(ControlItem).filter_by(framework_slug="GONE").count()


@pytest.mark.parametrize("collector", [loki, cloudwatch_logs])
def test_log_collectors_attach_by_target_kind(catalogue, monkeypatch, collector):
    if catalogue.db.get_bind().dialect.name != "postgresql":
        pytest.skip("The log collectors insert mappings with PostgreSQL upserts")
    clause = catalogue.add("5.1", "clause")
    control = catalogue.add("5.1", "annex_control")
    rules = _rules({"ref": "5.1", "kind": "clause"}, {"ref": "9.9", "kind": "clause"})
    monkeypatch.setattr(collector, "load_rules", lambda *a, **k: rules)
    event = _event(catalogue.db)
    assert collector._apply_rules_and_store_mappings(catalogue.db, event) == 1
    assert _mapped(catalogue.db, event) == {clause.id}
    assert control.id not in _mapped(catalogue.db, event)


# --- Rule backfill -------------------------------------------------------------------


@pytest.fixture
def backfill(catalogue, monkeypatch):
    db = catalogue.db

    @contextmanager
    def session():
        yield db

    monkeypatch.setattr(rule_backfill, "SessionLocal", session)
    monkeypatch.setattr(rule_backfill, "clear_stats_caches", Mock())
    clause = catalogue.add("5.1", "clause")
    control = catalogue.add("5.1", "annex_control")
    event = _event(db)
    event.created_at = datetime(2026, 1, 1)

    def job(document):
        db.add(
            ManagedConfiguration(
                name="rules", version=1, document={"rules": [document]}
            )
        )
        row = RuleBackfillJob(
            rule_id=document["id"],
            source="test",
            rule_document=deepcopy(document),
            rules_version=1,
            cutoff=datetime(2026, 2, 1),
        )
        db.add(row)
        db.commit()
        return row

    return SimpleNamespace(db=db, clause=clause, control=control, event=event, job=job)


def test_backfill_attaches_to_the_clause_and_removes_its_old_control_mapping(
    backfill,
):
    db = backfill.db
    # Saved when the rule targeted the control with the same reference.
    db.add(
        Mapping(
            event_id=backfill.event.id,
            control_item_id=backfill.control.id,
            method="rule",
            mapped_by="system",
            confidence=0.5,
            rationale="auto by rule kinds (A)",
        )
    )
    task = backfill.job(_rule({"ref": "5.1", "kind": "clause"}))
    assert rule_backfill.process_batch(task.id) is False
    assert _mapped(db, backfill.event) == {backfill.clause.id}
    assert task.created_mappings == 1


def test_backfill_fails_when_a_target_names_several_controls(backfill, catalogue):
    task = backfill.job(_rule({"ref": "7.1", "kind": "control"}))
    with pytest.raises(ValueError, match="removed or are ambiguous"):
        rule_backfill.process_batch(task.id)
    assert _mapped(backfill.db, backfill.event) == set()


# --- Stale mapping review ------------------------------------------------------------


def test_stale_review_compares_the_row_each_target_names(backfill, monkeypatch):
    from app.services import control_evidence_stats

    monkeypatch.setattr(control_evidence_stats, "clear_stats_caches", Mock())
    db = backfill.db
    rows = {}
    for name, row in (("clause", backfill.clause), ("control", backfill.control)):
        rows[name] = Mapping(
            event_id=backfill.event.id,
            control_item_id=row.id,
            method="rule",
            mapped_by="system",
            confidence=0.8,
            rationale="auto by rule kinds (A)",
        )
        db.add(rows[name])
    db.add(
        ManagedConfiguration(
            name="rules",
            version=1,
            document={"rules": [_rule({"ref": "5.1", "kind": "clause"})]},
        )
    )
    db.commit()
    review = managed_configurations.preview_stale_rule_mappings(source="test", db=db)
    assert [item["mapping_id"] for item in review["stale"]] == [str(rows["control"].id)]
    pruned = managed_configurations.prune_stale_rule_mappings(
        payload=managed_configurations.PruneInput(
            version=1, source="test", mapping_ids=[str(rows["control"].id)]
        ),
        db=db,
    )
    assert pruned == {"deleted": 1}
    assert _mapped(db, backfill.event) == {backfill.clause.id}
    with pytest.raises(managed_configurations.DomainError) as refused:
        managed_configurations.prune_stale_rule_mappings(
            payload=managed_configurations.PruneInput(
                version=1, source="test", mapping_ids=[str(rows["clause"].id)]
            ),
            db=db,
        )
    assert refused.value.status_code == 409


# --- Saving rules --------------------------------------------------------------------


@pytest.fixture
def admin(keen):  # noqa: F811 - fixture imported above
    db = keen.db
    db.add(Framework(slug="A"))
    for ref, kind in (
        ("5.1", "clause"),
        ("5.1", "annex_control"),
        ("4.1", "clause"),
        ("6.1", "annex_control"),
        ("7.1", "annex_control"),
        ("7.1", "custom"),
    ):
        db.add(ControlItem(framework_slug="A", type=kind, ref=ref, title=ref))
    db.commit()
    client = keen.client(keen.user(role="admin"))

    def put(*targets, status=200, rule_id="kinds"):
        version = client.get("/v1/admin/mapping-rules").json()["version"]
        response = client.put(
            f"/v1/admin/mapping-rules/{rule_id}",
            json={
                "version": version,
                "rule": _rule(*targets, rule_id=rule_id),
                "apply_existing": False,
            },
            headers=unsafe(client),
        )
        assert response.status_code == status, response.text
        return response.json()

    return SimpleNamespace(client=client, put=put)


def _saved_targets(admin, rule_id="kinds"):
    rules = admin.client.get("/v1/admin/mapping-rules").json()["items"]
    (rule,) = [item for item in rules if item["id"] == rule_id]
    return [(t["ref"], t.get("kind")) for t in rule["map_to"]]


def test_saved_rules_record_the_kind_of_every_target(admin):
    admin.put(
        {"ref": "5.1", "kind": "clause"},
        {"ref": "5.1", "kind": "control"},
        # Unambiguous references take the kind of the row they name.
        {"ref": "4.1"},
        {"ref": "6.1"},
        # A repeated target is kept once.
        {"ref": "6.1", "kind": "control"},
    )
    assert _saved_targets(admin) == [
        ("5.1", "clause"),
        ("5.1", "control"),
        ("4.1", "clause"),
        ("6.1", "control"),
    ]
    exported = admin.client.get(
        "/v1/admin/mapping-rules/export.yaml", params={"source": "test"}
    )
    assert exported.status_code == 200
    (rule,) = yaml.safe_load(exported.text)["rules"]
    assert rule["map_to"][0] == {"framework": "A", "ref": "5.1", "kind": "clause"}


@pytest.mark.parametrize(
    "target,detail",
    [
        (
            {"ref": "5.1"},
            "A / 5.1 is both a control and a clause; choose which one the rule targets",
        ),
        (
            {"ref": "7.1", "kind": "control"},
            "More than one control in A has the reference 7.1; rename one before "
            "mapping evidence to it",
        ),
        (
            {"ref": "5.1", "kind": "chapter"},
            "A target's kind must be control or clause",
        ),
        ({"ref": "6.1", "kind": "clause"}, "Unknown clause: A / 6.1"),
        ({"ref": HOSTILE}, f"Unknown target: A / {HOSTILE}"),
    ],
)
def test_rules_naming_no_single_control_or_clause_are_refused(admin, target, detail):
    assert admin.put(target, status=400)["detail"] == detail
    assert admin.client.get("/v1/admin/mapping-rules").json()["items"] == []
