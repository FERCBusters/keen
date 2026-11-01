"""Offline ORM/SQL semantics. PostgreSQL migrations still need a deployment rehearsal."""

import importlib.util
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

from app.core.datetime_utils import utc_now_naive

from tests.crosswalk_reference import reference_pair_set
from tests.db_helpers import create_sqlite_schema

os.environ.setdefault("KEEN_DATABASE_URL", "postgresql://test:test@localhost/unused")
os.environ.setdefault("KEEN_BOOTSTRAP_ADMIN_USERNAME", "test")
os.environ.setdefault("KEEN_BOOTSTRAP_ADMIN_PASSWORD", "test-password")
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
import pytest
from app.db.models import (
    Base,
    ControlItem,
    CrossFrameworkControlLink,
    Event,
    FrameworkClause,
    Mapping,
    User,
)
from app.services.control_inheritance import evidence_pairs
from seed_demo_data import DemoConfig, DemoSeeder
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

SHOWCASE = (
    "iso_27001_2022",
    "KEEN-AF:1.0",
    "CYBER-ESSENTIALS:2026",
    "soc2_tsc",
    "pci_dss_v4",
)


@compiles(JSONB, "sqlite")
def sqlite_json(*a, **k):
    return "JSON"


@pytest.fixture(autouse=True)
def artifact_storage(tmp_path, monkeypatch):
    """Automatic link diary entries store artefacts; keep them in the test."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "artifact_storage_backend", "local")
    monkeypatch.setattr(settings, "artifact_local_dir", str(tmp_path / "artifacts"))
    return tmp_path / "artifacts"


def catalogue_session(foreign_keys=False):
    """An in-memory database holding the frameworks and one administrator."""
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def functions(conn, record):
        conn.create_function("NOW", 0, lambda: utc_now_naive().isoformat(" "))

    create_sqlite_schema(engine)
    session = Session(engine)
    session.add(
        User(username="demo", password_hash="disabled", role="admin", is_active=True)
    )
    data = json.loads((ROOT / "alembic/seed_frameworks.json").read_text())
    for slug, f in data.items():
        for c in f["controls"]:
            session.add(
                ControlItem(
                    framework_slug=slug,
                    type=c["type"],
                    ref=c["ref"],
                    title=c["title"],
                    meta=c.get("metadata", {}),
                )
            )
        for c in f.get("clauses", []):
            session.add(
                FrameworkClause(framework_slug=slug, ref=c["ref"], title=c["title"])
            )
    session.commit()
    from tests.test_osa_catalogue import migration

    migration().seed_catalogue(session.connection())
    session.commit()
    if foreign_keys:
        session.execute(text("PRAGMA foreign_keys=ON"))
    return session, engine


@pytest.fixture
def db():
    session, engine = catalogue_session()
    yield session
    session.close()
    engine.dispose()


def migrate(db, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "crosswalk",
        ROOT / "tests/fixtures/historical_migrations/0079_seed_control_crosswalk.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.op, "get_bind", lambda: db.connection())
    module.upgrade()
    db.flush()


def test_all_framework_seed_is_idempotent_and_has_no_extra_login_users(db):
    config = DemoConfig("iso_27001_2022", "Demo", None, False, True)
    DemoSeeder(db, config).run()
    db.flush()
    counts = {t.name: db.query(t).count() for t in Base.metadata.sorted_tables}
    seeder = DemoSeeder(db, config)
    assert (
        seeder.seed_date
        == db.query(Event).filter_by(source="demo-seed").one().timestamp.date()
    )
    seeder.run()
    db.flush()
    assert counts == {t.name: db.query(t).count() for t in Base.metadata.sorted_tables}
    assert counts["users"] == 1 and counts["isms_people"] == 6 and counts["risks"] >= 10
    assert {
        f for (f,) in db.query(ControlItem.framework_slug).join(Mapping).distinct()
    } == set(SHOWCASE)
    assert not seeder.warnings


def test_crosswalk_migration_references_existing_controls_and_preserves_edits(
    db, monkeypatch
):
    migrate(db, monkeypatch)
    assert db.query(CrossFrameworkControlLink).count() > 0
    row = db.query(CrossFrameworkControlLink).first()
    row.rationale = "Administrator reviewed"
    db.flush()
    migrate(db, monkeypatch)
    assert db.query(CrossFrameworkControlLink).count() > 0
    assert (
        db.get(CrossFrameworkControlLink, row.id).rationale == "Administrator reviewed"
    )


def test_evidence_inherits_one_hop_without_false_reverse_ce_mapping(db, monkeypatch):
    migrate(db, monkeypatch)
    ce = (
        db.query(ControlItem)
        .filter_by(framework_slug="CYBER-ESSENTIALS:2026", ref="A8.1")
        .one()
    )
    iso = (
        db.query(ControlItem)
        .filter_by(framework_slug="iso_27001_2022", ref="A.8.7")
        .one()
    )
    e = Event(
        source="test",
        external_id="one",
        timestamp=utc_now_naive(),
        summary="Synthetic malware check",
    )
    db.add(e)
    db.flush()
    db.add(
        Mapping(
            event_id=e.id,
            control_item_id=ce.id,
            confidence=1,
            method="test",
            rationale="test",
        )
    )
    db.flush()
    pairs = evidence_pairs("iso_27001_2022", db)
    assert (
        db.scalar(select(pairs.c.event_id).where(pairs.c.control_id == iso.id)) == e.id
    )
    e2 = Event(
        source="test",
        external_id="two",
        timestamp=utc_now_naive(),
        summary="General ISO evidence",
    )
    db.add(e2)
    db.flush()
    db.add(
        Mapping(
            event_id=e2.id,
            control_item_id=iso.id,
            confidence=1,
            method="test",
            rationale="test",
        )
    )
    db.flush()
    pairs = evidence_pairs("CYBER-ESSENTIALS:2026", db)
    assert (
        db.scalar(
            select(pairs.c.event_id).where(
                pairs.c.control_id == ce.id, pairs.c.event_id == e2.id
            )
        )
        is None
    )
    assert db.query(Mapping).count() == 2


@pytest.mark.parametrize("framework", SHOWCASE[1:])
def test_single_non_iso_framework_seeds_without_iso_refs(db, framework):
    seeder = DemoSeeder(db, DemoConfig(framework, "Demo", None, False, False))
    seeder.run()
    db.flush()
    assert not seeder.warnings
    assert {
        f for (f,) in db.query(ControlItem.framework_slug).join(Mapping).distinct()
    } == {framework}


def test_measured_assurance_has_thresholds_sources_and_chronological_results(db):
    from app.db.models import (
        AuditEvidence,
        IsmsEffectivenessMeasure,
        IsmsEffectivenessMetricEntry,
    )
    from demo_metrics import LINKS

    seeder = DemoSeeder(db, DemoConfig("iso_27001_2022", "Demo", None, False, True))
    seeder.run()
    db.flush()
    measures = (
        db.query(IsmsEffectivenessMeasure)
        .filter(
            IsmsEffectivenessMeasure.metric_key.startswith(
                seeder.account_code("SLA") + "-"
            )
        )
        .all()
    )
    assert len(measures) == sum(map(len, LINKS.values())) == 24
    for measure in measures:
        rows = (
            db.query(IsmsEffectivenessMetricEntry)
            .filter_by(measure_id=measure.id)
            .order_by(IsmsEffectivenessMetricEntry.period_start)
            .all()
        )
        assert len(rows) == 6
        assert rows[-1].period_end < seeder.seed_date
        assert {r.raw_payload["assessment"] for r in rows} == {"met", "breach"}
        for row in rows:
            source = db.get(Event, row.source_event_id)
            assert source.normalized_payload["value"] == row.metric_value
            assert source.normalized_payload["synthetic"] is True
            expected = (
                row.metric_value >= measure.target_value
                if measure.threshold_operator == "gte"
                else row.metric_value <= measure.target_value
            )
            assert (row.raw_payload["assessment"] == "met") == expected
        assert (
            db.query(AuditEvidence)
            .filter_by(entity_type="isms_effectiveness_measure", entity_id=measure.id)
            .count()
            == 1
        )


def test_live_rss_preset_is_idempotent_and_preserves_rules(db, monkeypatch):
    from app.core.config import settings
    from app.db.models import ManagedConfiguration, ManagedConfigurationRevision
    from app.ingest.demo_rss import FEED, RULE, seed_preset

    monkeypatch.setattr(settings, "demo_mode", True)
    db.add(
        ManagedConfiguration(
            name="rules",
            version=1,
            document={
                "rules": [
                    {"id": "existing", "when": {"source": "test"}, "map_to": ["A.5.1"]}
                ]
            },
        )
    )
    db.flush()
    seed_preset(db)
    assert db.get(ManagedConfiguration, "rss").document == {"feeds": [FEED]}
    assert [
        r["id"] for r in db.get(ManagedConfiguration, "rules").document["rules"]
    ] == ["existing", RULE["id"]]
    count = db.query(ManagedConfigurationRevision).count()
    seed_preset(db)
    assert db.query(ManagedConfigurationRevision).count() == count


def test_swot_demo_relationships_frameworks_and_soa_visibility(db, monkeypatch):
    from app.db.models import IsmsEntityControlLink, IsmsSwot, IsmsSwotLink
    from app.services import soa
    from app.services.features import update

    config = DemoConfig("iso_27001_2022", "Demo", None, False, True)
    DemoSeeder(db, config).run()
    rows = db.query(IsmsSwot).all()
    assert len(rows) == 9
    assert {r.kind for r in rows} == {"strength", "weakness", "opportunity", "threat"}
    assert sum(r.parent_id is not None for r in rows) == 3
    assert {r.target_type for r in db.query(IsmsSwotLink)} == {
        "risk",
        "asset",
        "person",
        "vendor",
        "objective",
        "document",
    }
    ids = {r.id for r in rows}
    rows[0].title = "Locally edited SWOT"
    rows[0].status = "deferred"
    DemoSeeder(db, config).run()
    assert {r.id for r in db.query(IsmsSwot)} == ids
    assert rows[0].title == "Locally edited SWOT"
    assert rows[0].status == "deferred"
    user = db.query(User).filter_by(username="demo").one()
    for framework in SHOWCASE:
        result = soa.statement_of_applicability(framework=framework, user=user, db=db)
        expected = {
            r.entity_id
            for r in db.query(IsmsEntityControlLink).filter_by(
                entity_type="swot", framework_slug=framework
            )
        }
        assert {r["id"] for r in result["swot_items"]} == {str(i) for i in expected}
        assert all(
            c["framework"] == framework
            for r in result["swot_items"]
            for c in r["controls"]
        )
    monkeypatch.setattr(soa, "can_read_isms", lambda *args: False)
    assert soa.statement_of_applicability(user=user, db=db)["swot_items"] == []
    monkeypatch.undo()
    from app.services.features import state

    update(db, user, state(db).version, {"swot": False})
    result = soa.statement_of_applicability(user=user, db=db)
    assert result["swot_items"] == [] and not result["can_view_swot"]


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    """One full demo dataset, shared by the read-only checks below."""
    from app.core.config import settings

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(settings, "artifact_storage_backend", "local")
        patch.setattr(
            settings, "artifact_local_dir", str(tmp_path_factory.mktemp("artifacts"))
        )
        session, engine = catalogue_session()
        seeder = DemoSeeder(
            session, DemoConfig("iso_27001_2022", "Demo", None, False, True)
        )
        seeder.run()
        session.flush()
        yield session, seeder
        session.close()
        engine.dispose()


def test_annex_references_never_resolve_to_their_clause_twins(seeded):
    db, seeder = seeded
    lookup = DemoSeeder(db, seeder.config)
    clauses = {
        ref
        for (ref,) in db.query(ControlItem.ref).filter_by(
            framework_slug="iso_27001_2022", type="clause"
        )
    }
    twins = [
        ref
        for (ref,) in db.query(ControlItem.ref).filter_by(
            framework_slug="iso_27001_2022", type="annex_control"
        )
        if ref[2:] in clauses
    ]
    # A.5.1 to A.5.3, A.6.1 to A.6.3, A.7.1 to A.7.5 and A.8.1 to A.8.3.
    assert len(twins) == 14
    for ref in twins:
        control = lookup._control(ref)
        assert (control.ref, control.type) == (ref, "annex_control")
    assert lookup._control("9.3").type == "clause"
    assert lookup._control("5.15").ref == "A.5.15"
    assert lookup._control("A.99.1") is None
    assert lookup.warnings["missing controls"] == {"A.99.1"}


def test_risk_register_holds_cia_risks_scored_like_the_register(seeded):
    from app.db.models import Risk, RiskControlLink, RiskInterestedParty
    from app.services.risks import (
        TREATMENT_STATUSES,
        TREATMENT_STRATEGIES,
        _clean_risk_types,
    )

    db, _ = seeded
    risks = db.query(Risk).all()
    cia = [r for r in risks if r.risk_types]
    assert len(risks) >= 25 and len(cia) >= 20 and len(risks) > len(cia)
    # The CIA Triad tab filters on the canonical names Keen stores.
    assert {t for r in cia for t in r.risk_types} == {
        "Confidentiality",
        "Integrity",
        "Availability",
    }
    for risk in risks:
        assert _clean_risk_types(risk.risk_types) == risk.risk_types
        assert risk.title and risk.treatment_plan
        assert risk.risk_score == risk.register_likelihood * risk.register_impact
        assert risk.residual_risk_score == (
            risk.register_residual_likelihood * risk.register_residual_impact
        )
        assert (risk.vulnerability_score, risk.impact_score) == (
            risk.register_likelihood,
            risk.register_impact,
        )
        assert (risk.risk_owner_user_id is None) != (risk.risk_owner_role_id is None)
        assert risk.treatment_strategy in TREATMENT_STRATEGIES
        assert risk.treatment_status in TREATMENT_STATUSES
    assert {f for (f,) in db.query(RiskControlLink.framework_slug).distinct()} == set(
        SHOWCASE
    )
    affected = db.query(RiskInterestedParty).all()
    assert any(p.interested_party_id for p in affected)
    assert any(p.vendor_id for p in affected)


def test_pestle_analysis_covers_every_factor_in_both_lenses(seeded):
    from collections import Counter

    from app.db.models import PestleItem
    from app.services.pestle import PESTLE_TYPES, pestle_visualisations

    db, _ = seeded
    user = db.query(User).filter_by(username="demo").one()
    items = db.query(PestleItem).all()
    assert Counter(i.type for i in items) == {t: 3 for t in PESTLE_TYPES}
    assert {i.overall_relevance.code for i in items} == {"high", "medium", "low"}
    assert all(i.business_process_links and i.clause_links for i in items)
    counts = pestle_visualisations(framework="iso_27001_2022", user=user, db=db)[
        "lens_type_counts"
    ]
    assert all(counts[lens][typ] for lens in counts for typ in PESTLE_TYPES)


def test_documents_link_controls_and_record_their_automatic_diary_entries(seeded):
    from datetime import timedelta
    from hashlib import sha256

    from app.db.models import (
        Artifact,
        IsmsDocument,
        IsmsDocumentRevision,
        IsmsEntityClauseLink,
        IsmsEntityControlLink,
    )
    from app.security.diary_visibility import link_entry_external_id
    from app.services.isms.link_diary import MANUAL_RATIONALE

    db, seeder = seeded
    documents = db.query(IsmsDocument).filter(IsmsDocument.title.like("Demo %")).all()
    assert len(documents) >= 40
    frameworks = set()
    for document in documents:
        revision = (
            db.query(IsmsDocumentRevision)
            .filter_by(document_id=document.id, version=1)
            .one()
        )
        assert document.content_version == 1 and document.content_html
        assert revision.sha256 == sha256(document.content_html.encode()).hexdigest()
        controls = {
            (link.framework_slug, link.control_item_id)
            for link in db.query(IsmsEntityControlLink).filter_by(
                entity_type="document", entity_id=document.id
            )
        }
        clauses = [
            link.clause.ref
            for link in db.query(IsmsEntityClauseLink).filter_by(
                entity_type="document", entity_id=document.id
            )
        ]
        assert controls or clauses
        frameworks |= {framework for framework, _ in controls}
        entry = (
            db.query(Event)
            .filter_by(
                source="diary",
                external_id=link_entry_external_id("document", document.id),
            )
            .one()
        )
        assert (entry.system, entry.action) == ("isms", "isms_link")
        assert entry.normalized_payload["links"] == [
            {"url": f"/isms-document.html?id={document.id}", "text": "Document"}
        ]
        assert entry.timestamp == document.created_at + timedelta(minutes=12)
        mapped = {
            m.control_item_id
            for m in db.query(Mapping).filter_by(
                event_id=entry.id, rationale=MANUAL_RATIONALE
            )
        }
        clause_items = {
            c.id
            for c in db.query(ControlItem).filter(
                ControlItem.framework_slug == "iso_27001_2022",
                ControlItem.type == "clause",
                ControlItem.ref.in_(clauses or [""]),
            )
        }
        assert mapped == {item for _, item in controls} | clause_items
        assert db.query(Artifact).filter_by(event_id=entry.id).count() == 1
    assert frameworks == set(SHOWCASE)
    assert seeder.stats["document link diary entries.created"] == len(documents)


def test_every_linked_record_counts_towards_control_coverage(seeded):
    """Measures, SWOT items, risks and the rest count, as saving them does."""
    from app.db.models import (
        InterestedPartyControlLink,
        IsmsEntityClauseLink,
        IsmsEntityControlLink,
        RiskControlLink,
    )

    db, seeder = seeded
    linked = defaultdict(set)
    for record_type, record_id, control_id in db.query(
        IsmsEntityControlLink.entity_type,
        IsmsEntityControlLink.entity_id,
        IsmsEntityControlLink.control_item_id,
    ):
        linked[record_type, record_id].add(control_id)
    # A record linked only to clauses has an entry too.
    for record_type, record_id in db.query(
        IsmsEntityClauseLink.entity_type, IsmsEntityClauseLink.entity_id
    ):
        linked[record_type, record_id] |= set()
    for model, record_type, column in (
        (RiskControlLink, "risk", RiskControlLink.risk_id),
        (
            InterestedPartyControlLink,
            "interested_party",
            InterestedPartyControlLink.interested_party_id,
        ),
    ):
        for record_id, control_id in db.query(column, model.control_item_id):
            linked[record_type, record_id].add(control_id)
    counted = Counter()
    for (record_type, record_id), controls in linked.items():
        if record_type == "access_control_matrix":
            continue
        entry = (
            db.query(Event)
            .filter_by(
                source="diary", external_id=f"isms-link:{record_type}:{record_id}"
            )
            .one()
        )
        mapped = {
            m.control_item_id for m in db.query(Mapping).filter_by(event_id=entry.id)
        }
        assert controls <= mapped, record_type
        counted[record_type] += 1
    assert set(counted) == {
        "document",
        "effectiveness_measure",
        "interested_party",
        "meeting",
        "objective",
        "risk",
        "swot",
    }
    for record_type, count in counted.items():
        name = record_type.replace("_", " ")
        assert seeder.stats[f"{name} link diary entries.created"] == count


def test_a_dry_run_leaves_document_link_entries_out(db, artifact_storage):
    seeder = DemoSeeder(db, DemoConfig("iso_27001_2022", "Demo", None, True, True))
    seeder.run()
    db.flush()
    assert db.query(Event).filter_by(source="diary", system="isms").count() == 0
    assert seeder.stats["link diary entries.skipped in dry run"] == 1
    assert not artifact_storage.exists()


def test_evidence_spans_sources_and_covers_annex_a(seeded):
    db, seeder = seeded
    seeded_events = (
        db.query(Event)
        .filter(Event.external_id.like(seeder.account_code("EVENT") + "-%"))
        .all()
    )
    assert {
        "bookstack",
        "cloudwatch_logs",
        "forgejo",
        "github",
        "google_workspace",
        "jenkins",
        "loki",
        "redmine",
        "riskledger",
    } <= {e.source for e in seeded_events}
    # Evidence accumulates over months, so trends and freshness have history.
    assert len({e.timestamp.date() for e in seeded_events}) >= 60
    annex = {
        c.id
        for c in db.query(ControlItem).filter_by(
            framework_slug="iso_27001_2022", type="annex_control"
        )
    }
    direct = {
        control
        for (control,) in db.query(Mapping.control_item_id)
        .filter(Mapping.control_item_id.in_(annex))
        .distinct()
    }
    pairs = evidence_pairs("iso_27001_2022", db)
    assert set(db.execute(select(pairs))) == reference_pair_set("iso_27001_2022", db)
    supported = {row[0] for row in db.execute(select(pairs.c.control_id).distinct())}
    assert len(direct) >= 85
    # Every Annex A control has evidence, a few only through crosswalks.
    assert annex <= supported and annex - direct
    # One record can evidence controls in several frameworks directly.
    scans = [e for e in seeded_events if e.action == "vulnerability-scan"]
    assert len(scans) == 12
    for scan in scans:
        assert {
            framework
            for (framework,) in db.query(ControlItem.framework_slug)
            .join(Mapping, Mapping.control_item_id == ControlItem.id)
            .filter(Mapping.event_id == scan.id)
        } == {"iso_27001_2022", "soc2_tsc", "pci_dss_v4"}
    cyber_essentials = (
        db.query(Mapping.control_item_id)
        .join(ControlItem, ControlItem.id == Mapping.control_item_id)
        .filter(ControlItem.framework_slug == "CYBER-ESSENTIALS:2026")
        .distinct()
        .count()
    )
    assert cyber_essentials >= 20


def test_stories_show_linked_records_on_a_timeline_and_flow(seeded):
    from app.services import stories
    from app.services.entity_changelog import list_entity_changelogs

    db, _ = seeded
    user = db.query(User).filter_by(username="demo").one()
    listing = stories.list_stories(user=user, db=db)
    assert listing["total"] == 4
    assert {s["status"] for s in listing["items"]} == {"open", "closed"}
    comments = 0
    every_kind = set()
    for row in listing["items"]:
        story = stories.get_story(
            story_id=row["id"], framework="iso_27001_2022", user=user, db=db
        )
        items = story["items"]
        kinds = {item["kind"] for item in items}
        every_kind |= kinds
        assert {"event", "note", "document", "risk", "asset"} <= kinds
        assert not any(item["withheld"] for item in items)
        assert all(item["occurred_at"] for item in items if item["kind"] == "event")
        assert len({(item["x"], item["y"]) for item in items}) == len(items)
        ids = {item["id"] for item in items}
        assert story["links"] and all(
            link["label"] and {link["from_item_id"], link["to_item_id"]} <= ids
            for link in story["links"]
        )
        assert story["controls"]
        comments += story["comment_count"]
        history = list_entity_changelogs(
            db, entity_type="story", entity_id=row["id"], limit=10, offset=0
        )
        assert [entry["action"] for entry in history["items"]] == ["created"]
    assert comments >= 1
    # People, suppliers, measures and minutes take part too, and their pages
    # list the stories back.
    assert {"person", "vendor", "effectiveness_measure", "meeting"} <= every_kind
    contractor = next(
        item
        for row in listing["items"]
        for item in stories.get_story(
            story_id=row["id"], framework="iso_27001_2022", user=user, db=db
        )["items"]
        if item["kind"] == "person"
    )
    related = stories.related_stories(
        kind="person", entity_id=contractor["entity_id"], user=user, db=db
    )
    assert [s["title"] for s in related["items"]] == [
        "Demo Contractor leaver: every route in closed on the last day"
    ]


def test_the_awareness_quiz_records_each_round_on_its_measure(seeded):
    from app.db.models import (
        IsmsEffectivenessMetricEntry,
        IsmsEntityControlLink,
        Quiz,
        QuizAttempt,
    )
    from app.services.quizzes import authoring, results

    db, _ = seeded
    user = db.query(User).filter_by(username="demo").one()
    quiz = db.query(Quiz).one()
    assert (quiz.title, quiz.status, quiz.pass_mark) == (
        "Demo Security awareness refresher",
        "active",
        70,
    )
    attempts = db.query(QuizAttempt).all()
    assert len(attempts) == 16 and {a.status for a in attempts} == {"complete"}
    entries = (
        db.query(IsmsEffectivenessMetricEntry)
        .filter_by(measure_id=quiz.measure_id)
        .order_by(IsmsEffectivenessMetricEntry.period_start)
        .all()
    )
    assert len(entries) == 3
    assert all(0 < entry.metric_value <= 100 for entry in entries)
    linked = {
        ref
        for (ref,) in db.query(ControlItem.ref)
        .join(
            IsmsEntityControlLink,
            IsmsEntityControlLink.control_item_id == ControlItem.id,
        )
        .filter(IsmsEntityControlLink.entity_id == quiz.measure_id)
    }
    assert linked == {"A.6.3", "A.5.10", "A.5.17", "A.6.8", "A.8.7"}
    detail = authoring.get_quiz(quiz_id=quiz.id, db=db, user=user)
    assert [r["recorded"] for r in detail["rounds"]] == [True, True, True]
    # The fictional people have no accounts to sit the next round with.
    assert detail["audience"]["size"] == 0
    insights = results.quiz_results(quiz_id=quiz.id, round_id=None, db=db, user=user)
    assert insights["summary"]["responses"] == 16
    assert insights["insights"]["common_wrong_answers"]


def test_seeded_frameworks_join_the_selection_with_iso_as_default(db, monkeypatch):
    from app.core.config import settings
    from app.db.models import ManagedConfiguration
    from app.services.frameworks import framework_selection

    db.add(
        ManagedConfiguration(
            name="organisation-frameworks",
            document={
                "enabled": ["KEEN-AF:1.0", "nist_csf_2"],
                "default": "KEEN-AF:1.0",
            },
            version=1,
        )
    )
    db.flush()
    config = DemoConfig("iso_27001_2022", "Demo", None, False, True)
    DemoSeeder(db, config).run()
    selection = framework_selection(db=db)
    enabled = {item["slug"] for item in selection["items"] if item["enabled"]}
    assert enabled == set(SHOWCASE) | {"nist_csf_2"}
    assert selection["default"] == "iso_27001_2022"
    DemoSeeder(db, config).run()
    assert framework_selection(db=db)["version"] == selection["version"]

    # An environment restriction still applies, and a single framework run
    # adds its framework without changing the default.
    monkeypatch.setattr(settings, "enabled_frameworks", "iso_27001_2022,soc2_tsc")
    row = db.get(ManagedConfiguration, "organisation-frameworks")
    row.document = {"enabled": ["iso_27001_2022"], "default": "iso_27001_2022"}
    db.flush()
    DemoSeeder(db, DemoConfig("soc2_tsc", "Demo", None, False, False)).run()
    selection = framework_selection(db=db)
    assert {item["slug"] for item in selection["items"] if item["enabled"]} == {
        "iso_27001_2022",
        "soc2_tsc",
    }
    assert selection["default"] == "iso_27001_2022"


def test_teardown_removes_the_seeded_dataset(artifact_storage):
    from app.db.models import IsmsDocument
    from teardown_demo_data import DemoTeardown, TeardownConfig

    db, engine = catalogue_session(foreign_keys=True)
    try:
        assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1
        # Records that merely resemble the prefix stay.
        db.add(IsmsDocument(title="Demonstration notes"))
        db.add(
            Event(
                source="github",
                external_id="real-event",
                timestamp=utc_now_naive(),
                summary="Demo day for the new release",
            )
        )
        db.flush()

        def counts():
            return {t.name: db.query(t).count() for t in Base.metadata.sorted_tables}

        before = counts()
        DemoSeeder(db, DemoConfig("iso_27001_2022", "Demo", None, False, True)).run()
        db.flush()
        # Every kind of linked record has automatic entries, and teardown
        # removes them all.
        entries = db.query(Event).filter_by(source="diary", system="isms").all()
        assert {entry.external_id.split(":")[1] for entry in entries} == {
            "document",
            "effectiveness_measure",
            "interested_party",
            "meeting",
            "objective",
            "risk",
            "swot",
        }
        artefacts = len(entries)
        teardown = DemoTeardown(
            db, TeardownConfig("iso_27001_2022", "Demo", False, True)
        )
        teardown.run()
        db.flush()
        after = counts()
        # Configuration, reference levels and change history stay; stored
        # files of the automatic diary entries wait for deletion.
        kept = {
            "entity_changelogs",
            "evidence_object_cleanup",
            "managed_configuration_revisions",
            "managed_configurations",
            "pestle_relevance_levels",
        }
        assert {t: n for t, n in after.items() if t not in kept} == {
            t: n for t, n in before.items() if t not in kept
        }
        assert after["evidence_object_cleanup"] == artefacts > 0
        assert db.query(IsmsDocument).one().title == "Demonstration notes"
        assert db.query(Event).one().external_id == "real-event"
    finally:
        db.close()
        engine.dispose()


@pytest.mark.parametrize("script", ["seed_demo_data", "teardown_demo_data"])
def test_scripts_need_explicit_permission_outside_demo_mode(
    script, monkeypatch, capsys
):
    from app.core.config import settings

    module = importlib.import_module(script)
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(sys, "argv", [script, "--dry-run"])
    with pytest.raises(SystemExit) as refused:
        module.parse_args()
    assert refused.value.code == 2
    assert "--allow-non-demo" in capsys.readouterr().err
    monkeypatch.setattr(sys, "argv", [script, "--dry-run", "--allow-non-demo"])
    assert module.parse_args().dry_run
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(sys, "argv", [script, "--dry-run"])
    assert module.parse_args().dry_run
