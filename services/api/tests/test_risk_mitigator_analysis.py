"""Keen Mitigator analysis against persisted registers and explicit rules.

The rules are supplied by the test so each signal can be traced to its cause.
PostgreSQL is used when KEEN_CONTRACT_TEST_DATABASE_URL is set; the full-text
contribution is checked only there.
"""

import uuid

import sqlalchemy
from app.core.config import settings
from app.db.models import (
    ControlItem,
    FrameworkClause,
    InterestedParty,
    InterestedPartyName,
    InterestedPartyNature,
    PestleBusinessProcess,
    PestleBusinessProcessRelevance,
    PestleClauseRelevance,
    PestleItem,
    PestleRelevanceLevel,
    Risk,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
    RiskControlLink,
    RiskLibraryEntry,
)
from app.services import risk_mitigator as mitigator

HOSTILE = '<img src=x onerror="alert(1)">'

RULES = {
    "risk_types": {
        "Confidentiality": {
            "reason": "Information could leak.",
            "scenario_terms": ["leak", "stolen"],
            "mitigation_words": ["encryption"],
        },
        "Integrity": {"scenario_terms": ["tamper"], "mitigation_words": ["change"]},
        "Availability": {
            "reason": "Service could stop.",
            "scenario_terms": ["outage", "unavailable"],
            "mitigation_words": ["backup", "continuity"],
        },
        # Only the three CIA types are recognised.
        "Safety": {"scenario_terms": ["injury"]},
    },
    "asset_categories": {
        "Data": {
            "mitigation_words": ["classification"],
            "reason": "Data assets need classification.",
        },
    },
    "impact_flags": {
        "service_outage": {
            "label": "Service outage",
            "risk_types": ["Availability", "Bogus"],
            "mitigation_words": ["continuity"],
            "reason": "Outages need continuity.",
        },
        "brand": {"mitigation_words": ["communication"]},
    },
    "scenario_terms": {
        "supplier": {
            "terms": ["supplier", "third party"],
            "mitigation_words": ["agreement"],
            "reason": "A supplier is involved.",
        },
        "volcanic": {"terms": ["volcano"]},
    },
    "pestle_types": {
        "Legal": {
            "lens": "External",
            "scenario_terms": ["regulator", "fine"],
            "search_terms": ["legislation"],
            "impact_flags": ["compliance"],
            "reason": "Legal exposure.",
        },
        "Economical": {"scenario_terms": ["inflation"]},
        # Not a PESTLE(E) type, so never reported.
        "Spiritual": {"scenario_terms": ["regulator"]},
    },
    "pestle_business_processes": {
        "Board Strategy": {
            "pestle_types": ["Legal"],
            "terms": ["board"],
            "reason": "Board oversight.",
        },
        "software development": {"terms": ["code"]},
    },
    "interested_parties": {
        "regulators": {
            "name": "Regulators",
            "nature": "Enforcement",
            "terms": ["regulator"],
            "note": "Regulators may act.",
            "communication": {"event": "Breach", "when": "Within 72 hours"},
        },
        "staff": {"name": "Staff", "terms": ["employee"]},
    },
}


def _analyse(db, monkeypatch, rules=RULES, **kwargs):
    monkeypatch.setattr(mitigator, "load_rules", lambda path=None: rules)
    kwargs.setdefault("framework", "A")
    return mitigator.analyse_risk_mitigation(db, **kwargs)


def _control(db, ref, title, *, framework="A", kind="custom", in_scope=True):
    row = ControlItem(
        framework_slug=framework, ref=ref, type=kind, title=title, in_scope=in_scope
    )
    db.add(row)
    db.flush()
    return row


def _signals(result, kind):
    return [signal for signal in result["signals"] if signal["kind"] == kind]


# --- Rule loading ---------------------------------------------------------------


def test_rules_load_from_the_first_existing_candidate(tmp_path, monkeypatch):
    mitigator.load_rules.cache_clear()
    try:
        rules = tmp_path / "rules.yml"
        rules.write_text("risk_types:\n  Integrity:\n    scenario_terms: [tamper]\n")
        assert mitigator.load_rules(str(rules)) == {
            "risk_types": {"Integrity": {"scenario_terms": ["tamper"]}}
        }
        listed = tmp_path / "list.yml"
        listed.write_text("- not\n- a mapping\n")
        assert mitigator.load_rules(str(listed)) == {}
        empty = tmp_path / "empty.yml"
        empty.write_text("")
        assert mitigator.load_rules(str(empty)) == {}

        # The configured path is listed once even when also passed explicitly.
        monkeypatch.setattr(settings, "risk_mitigator_rules_path", str(rules))
        candidates = mitigator._rules_path_candidates(str(rules))
        assert candidates.count(rules) == 1 and candidates[0] == rules

        missing = tmp_path / "missing.yml"
        monkeypatch.setattr(settings, "risk_mitigator_rules_path", str(missing))
        monkeypatch.setattr(mitigator, "_default_rules_path", lambda: str(missing))
        monkeypatch.setattr(mitigator, "_repo_rules_path", lambda: None)
        assert mitigator.load_rules() == {}
    finally:
        mitigator.load_rules.cache_clear()


def test_the_source_checkout_rules_file_is_found_by_walking_up(tmp_path, monkeypatch):
    found = mitigator._repo_rules_path()
    assert found is not None and found.name == "risk_mitigator.yml"
    assert found.parent.name == "config"
    monkeypatch.setattr(mitigator, "__file__", str(tmp_path / "app" / "module.py"))
    assert mitigator._repo_rules_path() is None


# --- Questionnaire signals --------------------------------------------------------


def test_selected_types_and_weights_are_normalised(contract_db, monkeypatch):
    result = _analyse(
        contract_db,
        monkeypatch,
        issue="Portal outage",
        risk_types=["availability", "Availability", "Safety"],
        risk_weights={"Availability": 250, "Integrity": "abc", "Confidentiality": -5},
        impact_flags={"service_outage": True, "brand": False},
    )
    assert result["risk_types"] == ["Availability"]
    assert result["risk_weights"] == {
        "Confidentiality": 0,
        "Integrity": 0,
        "Availability": 100,
    }
    assert _signals(result, "risk_type") == [
        {
            "kind": "risk_type",
            "label": "Availability",
            "weight": 100,
            "matched_terms": ["outage"],
            "reason": "Service could stop.",
        }
    ]
    assert _signals(result, "impact") == [
        {
            "kind": "impact",
            "label": "Service outage",
            "matched_terms": ["continuity"],
            "reason": "Outages need continuity.",
        }
    ]
    note = result["draft_risk"]["note"]
    assert (
        "Dominant risk weighting: Confidentiality=0, Integrity=0, Availability=100"
        in (note)
    )
    assert "Selected impacts: service_outage" in note
    context = result["draft_risk"]["mitigator_context"]
    assert context["impact_flags"] == {"service_outage": True}
    assert context["source"] == "keen_mitigator"


def test_risk_types_are_inferred_when_none_are_selected(contract_db, monkeypatch):
    inferred = _analyse(
        contract_db, monkeypatch, issue="A supplier outage left the portal unavailable"
    )
    assert inferred["risk_types"] == ["Availability"]
    assert inferred["risk_weights"]["Availability"] == 50

    fallback = _analyse(contract_db, monkeypatch, issue="Quarterly planning notes")
    assert fallback["risk_types"] == ["Confidentiality"]
    assert fallback["risk_weights"] == {
        "Confidentiality": 50,
        "Integrity": 0,
        "Availability": 0,
    }

    # An impact flag adds its risk type with a lower default weight.
    flagged = _analyse(
        contract_db,
        monkeypatch,
        issue="Quarterly planning notes",
        impact_flags={"service_outage": "yes", "unknown_flag": True},
    )
    assert flagged["risk_types"] == ["Confidentiality", "Availability"]
    assert flagged["risk_weights"]["Availability"] == 40
    unknown = next(
        s for s in _signals(flagged, "impact") if s["label"] == "Unknown Flag"
    )
    assert unknown["reason"] == "The questionnaire marked this impact as relevant."

    scenario = _signals(inferred, "scenario")
    assert scenario == [
        {
            "kind": "scenario",
            "label": "Supplier",
            "matched_terms": ["supplier"],
            "reason": "A supplier is involved.",
        }
    ]


def test_asset_categories_add_signals_and_control_relevance(contract_db, monkeypatch):
    category = RiskCategory(name="Data")
    other = RiskCategory(name="Hardware")
    contract_db.add_all([category, other])
    contract_db.flush()
    classified = _control(contract_db, "KM-01", "Information classification")
    _control(contract_db, "KM-02", "Visitor badges")

    by_id = _analyse(
        contract_db,
        monkeypatch,
        issue="Customer files were stolen",
        category_id=category.id,
    )
    assert _signals(by_id, "asset_category") == [
        {
            "kind": "asset_category",
            "label": "Data",
            "matched_terms": ["Data"],
            "reason": "Data assets need classification.",
        }
    ]
    assert by_id["asset_category"] == "Data"
    assert by_id["draft_risk"]["category_id"] == str(category.id)
    suggestion = by_id["control_suggestions"][0]
    assert suggestion["control"]["id"] == str(classified.id)
    assert "Data assets need classification." in suggestion["reasons"]
    assert [s["control"]["ref"] for s in by_id["control_suggestions"]] == ["KM-01"]

    by_name = _analyse(
        contract_db, monkeypatch, issue="Files stolen", category_name=" data "
    )
    assert _signals(by_name, "asset_category")[0]["label"] == "data"

    # Categories without rules, and unknown categories, add no signal.
    unmatched = _analyse(
        contract_db, monkeypatch, issue="Files stolen", category_id=other.id
    )
    assert unmatched["asset_category"] == "Hardware"
    assert _signals(unmatched, "asset_category") == []
    unknown = _analyse(
        contract_db, monkeypatch, issue="Files stolen", category_id=uuid.uuid4()
    )
    assert unknown["asset_category"] == ""
    assert _signals(unknown, "asset_category") == []


# --- Control suggestions ----------------------------------------------------------


def test_controls_are_ranked_by_matching_terms_and_scope(contract_db, monkeypatch):
    backup = _control(contract_db, "KM-10", "Information backup and continuity")
    supplier = _control(contract_db, "KM-02", "Supplier relationships", in_scope=False)
    _control(contract_db, "KM-03", "Visitor badges")
    _control(contract_db, "4.1", "Backup context", kind="clause")
    _control(contract_db, "KM-01", "Backup", framework="B")

    result = _analyse(
        contract_db,
        monkeypatch,
        issue="Supplier outage",
        risk_types=["Availability"],
        asset_name=HOSTILE,
    )
    suggestions = result["control_suggestions"]
    assert result["suggestions"] == suggestions
    assert [s["control"]["ref"] for s in suggestions] == ["KM-10", "KM-02"]
    top, out_of_scope = suggestions
    assert top["matched_terms"][:2] == ["backup", "continuity"]
    assert "Service could stop." in top["reasons"]
    assert out_of_scope["control"]["in_scope"] is False
    assert out_of_scope["score"] < top["score"]
    assert (
        "Matched words in your scenario against the current control catalogue."
        in out_of_scope["reasons"]
    )
    assert all(s["confidence"] in {"strong", "medium", "weak"} for s in suggestions)

    draft = result["draft_risk"]
    assert draft["asset_name"] == HOSTILE
    assert draft["controls"] == [str(backup.id), str(supplier.id)]
    assert draft["framework"] == "A" and draft["threat_summary"] == "Supplier outage"
    assert (
        "Suggested controls: KM-10 Information backup and continuity; "
        "KM-02 Supplier relationships"
    ) in draft["note"]
    context = draft["mitigator_context"]
    assert [c["ref"] for c in context["suggested_controls"]] == ["KM-10", "KM-02"]

    limited = _analyse(
        contract_db,
        monkeypatch,
        issue="Supplier outage",
        risk_types=["Availability"],
        limit=1,
    )
    assert [s["control"]["ref"] for s in limited["control_suggestions"]] == ["KM-10"]
    other = _analyse(
        contract_db, monkeypatch, issue="Backup failure", framework="B", limit=99
    )
    assert [s["control"]["ref"] for s in other["control_suggestions"]] == ["KM-01"]
    assert other["library_suggestions"] == []


def test_library_scenarios_match_in_every_framework(contract_db, monkeypatch):
    # The template's control links are KEEN-AF references, so in another
    # framework the scenario still matches on its wording and its links narrow
    # to the references that exist there (none here).
    contract_db.add(
        RiskLibraryEntry(
            name="Frobnicator theft",
            threat_summary="A frobnicator is stolen from the warehouse",
            suggested_assessment={"keen_af_control_refs": ["AST-1"]},
        )
    )
    _control(contract_db, "KM-01", "Warehouse access", framework="B")
    contract_db.flush()
    result = _analyse(
        contract_db,
        monkeypatch,
        issue="Frobnicator theft from the warehouse",
        framework="B",
    )
    hit = next(
        h for h in result["library_suggestions"] if h["name"] == "Frobnicator theft"
    )
    assert hit["control_refs"] == []
    assert any(sig["kind"] == "risk_library" for sig in result["signals"])


def test_controls_without_any_matching_terms_are_not_suggested(
    contract_db, monkeypatch
):
    _control(contract_db, "KM-03", "Visitor badges")
    result = _analyse(contract_db, monkeypatch, rules={}, issue="Sunny weather")
    assert result["control_suggestions"] == []
    assert result["draft_risk"]["controls"] == []
    assert "Suggested controls" not in result["draft_risk"]["note"]
    assert result["risk_types"] == ["Confidentiality"]
    assert result["draft_pestle_item"] is None
    assert result["pestle_hits"] == []
    assert result["interested_party_suggestions"] == []


def test_long_issues_are_bounded_in_the_draft(contract_db, monkeypatch):
    issue = "Backup " + "x" * 12100
    result = _analyse(contract_db, monkeypatch, rules={}, issue=issue)
    assert len(result["draft_risk"]["threat_summary"]) == 12000
    assert result["draft_risk"]["mitigator_context"]["issue"] == issue[:12000]


def test_full_text_search_contributes_on_postgresql(contract_db, monkeypatch):
    if contract_db.get_bind().dialect.name != "postgresql":
        assert mitigator._fts_scores(contract_db, framework="A", terms=["backup"]) == {}
        return
    control = _control(contract_db, "KM-20", "Backup policy")
    _control(contract_db, "KM-21", "Visitor badges")
    scores = mitigator._fts_scores(contract_db, framework="A", terms=["backup", "ab"])
    assert set(scores) == {str(control.id)} and scores[str(control.id)] > 0
    assert mitigator._fts_scores(contract_db, framework="A", terms=["ab", "x"]) == {}

    result = _analyse(contract_db, monkeypatch, rules={}, issue="Backup")
    suggestion = result["control_suggestions"][0]
    assert suggestion["control"]["id"] == str(control.id)
    assert any(
        "PostgreSQL full-text search" in reason for reason in suggestion["reasons"]
    )


def test_full_text_search_matches_controls_that_share_any_of_the_words(
    contract_db,
):
    if contract_db.get_bind().dialect.name != "postgresql":
        return
    one = _control(contract_db, "KM-30", "Backup policy")
    two = _control(contract_db, "KM-31", "Backup encryption standard")
    _control(contract_db, "KM-32", "Visitor badges")
    _control(contract_db, "KM-33", "Backup encryption", kind="clause")
    words = ["backup", "encryption", "phishing", HOSTILE, "it's | !(x)"]
    scores = mitigator._fts_scores(contract_db, framework="A", terms=words)
    assert set(scores) == {str(one.id), str(two.id)}
    assert scores[str(two.id)] > scores[str(one.id)] > 0
    # Operator characters are not words, so they never reach the query.
    assert mitigator._fts_scores(contract_db, framework="A", terms=["|&!()"]) == {}


def test_the_search_boost_grows_with_each_matching_word(contract_db, monkeypatch):
    fewer = _control(contract_db, "KM-40", "Visitor badges")
    more = _control(contract_db, "KM-41", "Visitor badges")
    ranks = {str(fewer.id): 0.1, str(more.id): 0.3}
    monkeypatch.setattr(mitigator, "_fts_scores", lambda *a, **k: ranks)
    result = _analyse(contract_db, monkeypatch, rules={}, issue="phishing")
    scores = {s["control"]["id"]: s["score"] for s in result["control_suggestions"]}
    assert scores[str(more.id)] > scores[str(fewer.id)] > 0


def test_a_failed_full_text_query_leaves_the_transaction_usable(
    contract_db, monkeypatch
):
    _control(contract_db, "KM-20", "Backup policy")
    monkeypatch.setattr(
        mitigator, "text", lambda _sql: sqlalchemy.text("SELECT missing FROM nowhere")
    )
    assert mitigator._fts_scores(contract_db, framework="A", terms=["backup"]) == {}
    assert contract_db.query(ControlItem).count() == 1


# --- Related registers -------------------------------------------------------------


def _registers(db):
    high = PestleRelevanceLevel(code="high", label="High", sort_order=1)
    na = PestleRelevanceLevel(code="na", label="Not applicable", sort_order=9)
    board = PestleBusinessProcess(name="Board Strategy")
    software = PestleBusinessProcess(name="Software Development")
    db.add_all([high, na, board, software])
    db.flush()
    context_clause = FrameworkClause(framework_slug="A", ref="4.1", sort_order=1)
    parties_clause = FrameworkClause(
        framework_slug="A",
        ref="4.2",
        title="Interested parties",
        sort_order=2,
    )
    db.add_all([context_clause, parties_clause])
    db.flush()
    fines = PestleItem(
        framework_slug="A",
        type="Legal",
        lens="External",
        item="Regulator fines",
        rationale="Late breach notification",
        overall_relevance_id=high.id,
    )
    exact = PestleItem(
        framework_slug="A",
        type="Economical",
        lens="External",
        item="Regulator",
        overall_relevance_id=high.id,
    )
    unrelated = PestleItem(
        framework_slug="A",
        type="Social",
        lens="Internal",
        item="Hybrid working",
        overall_relevance_id=high.id,
    )
    elsewhere = PestleItem(
        framework_slug="B",
        type="Legal",
        lens="External",
        item="Regulator fines",
        overall_relevance_id=high.id,
    )
    db.add_all([fines, exact, unrelated, elsewhere])
    db.flush()
    db.add_all(
        [
            PestleBusinessProcessRelevance(
                pestle_item_id=fines.id,
                business_process_id=board.id,
                relevance_id=high.id,
            ),
            PestleBusinessProcessRelevance(
                pestle_item_id=fines.id,
                business_process_id=software.id,
                relevance_id=na.id,
            ),
            PestleClauseRelevance(
                pestle_item_id=fines.id,
                clause_id=parties_clause.id,
                relevance_id=high.id,
            ),
            PestleClauseRelevance(
                pestle_item_id=fines.id, clause_id=context_clause.id, relevance_id=na.id
            ),
        ]
    )
    regulators = InterestedPartyName(name="Regulators")
    enforcement = InterestedPartyNature(name="Enforcement")
    investors = InterestedPartyName(name="Investors")
    db.add_all([regulators, enforcement, investors])
    db.flush()
    party = InterestedParty(
        name_id=regulators.id, nature_id=enforcement.id, note="National regulator"
    )
    db.add_all([party, InterestedParty(name_id=investors.id, nature_id=enforcement.id)])
    category = RiskCategory(name="Data")
    db.add(category)
    db.flush()
    subcategory = RiskAssetSubcategory(category_id=category.id, name="Databases")
    db.add(subcategory)
    db.flush()
    asset = RiskAsset(
        name="Customer database", category_id=category.id, subcategory_id=subcategory.id
    )
    kettle = RiskAsset(
        name="Office kettle", category_id=category.id, subcategory_id=subcategory.id
    )
    db.add_all([asset, kettle])
    db.flush()
    related = Risk(
        asset_id=asset.id,
        risk_types=["Confidentiality"],
        threat_summary="Regulator fine after a customer database leak",
    )
    unrelated_risk = Risk(
        asset_id=kettle.id, risk_types=["Availability"], threat_summary="Power cut"
    )
    db.add_all([related, unrelated_risk])
    db.flush()
    access = _control(db, "KM-30", "Access control")
    db.add(
        RiskControlLink(
            risk_id=related.id, framework_slug="A", control_item_id=access.id
        )
    )
    db.flush()
    return dict(board=board, fines=fines, exact=exact, party=party, related=related)


def test_related_registers_are_matched_and_drafts_avoid_duplicates(
    contract_db, monkeypatch
):
    rows = _registers(contract_db)
    issue = "The regulator may fine us after a customer database leak. Details follow."
    result = _analyse(
        contract_db, monkeypatch, issue=issue, impact_flags={"compliance": True}
    )

    assert result["pestle_hits"] == [
        {
            "type": "Legal",
            "lens": "External",
            "score": 3 * 8 + 1,
            "matched_terms": ["regulator", "fine", "compliance"],
            "reason": "Legal exposure.",
        }
    ]
    assert [s["label"] for s in _signals(result, "pestle_type")] == ["Legal"]
    assert result["business_process_suggestions"] == [
        {
            "id": str(rows["board"].id),
            "name": "Board Strategy",
            "score": 4,
            "matched_terms": ["Legal"],
            "reason": "Board oversight.",
            "relevance_code": "medium",
            "exists": True,
        }
    ]

    suggestions = {item["item"]: item for item in result["pestle_suggestions"]}
    assert set(suggestions) == {"Regulator fines", "Regulator"}
    fines = suggestions["Regulator fines"]
    assert fines["id"] == str(rows["fines"].id) and fines["framework"] == "A"
    assert "The scenario matched the Legal PESTLE(E) lens." in fines["reasons"]
    # Links marked not applicable are left out of the summary.
    assert [bp["name"] for bp in fines["business_processes"]] == ["Board Strategy"]
    assert fines["business_processes"][0]["relevance_label"] == "High"
    assert [c["clause"]["ref"] for c in fines["clauses"]] == ["4.2"]
    exact = suggestions["Regulator"]
    assert (
        "The item name closely matches a phrase in the issue text." in exact["reasons"]
    )
    assert result["existing_matches"]["pestle_items"] == result["pestle_suggestions"]

    draft = result["draft_pestle_item"]
    assert draft["type"] == "Legal" and draft["lens"] == "External"
    assert draft["item"] == "The regulator may fine us after a customer database leak"
    assert (
        "Detected PESTLE(E) signals: Legal (regulator, fine, compliance)"
        in (draft["rationale"])
    )
    assert "Possible existing related items:" in draft["rationale"]
    assert draft["business_processes"] == result["business_process_suggestions"]

    party_suggestion = result["interested_party_suggestions"]
    assert len(party_suggestion) == 1
    regulators = party_suggestion[0]
    assert (regulators["name"], regulators["nature"]) == ("Regulators", "Enforcement")
    assert regulators["duplicate_exact"] is True
    assert regulators["reason"] == "The issue mentions terms that affect Regulators."
    assert regulators["draft"]["communications"] == [
        {"event": "Breach", "when": "Within 72 hours"}
    ]
    assert regulators["draft"]["controls"] == result["draft_risk"]["controls"]
    existing_parties = result["existing_matches"]["interested_parties"]
    assert [p["id"] for p in existing_parties] == [str(rows["party"].id)]
    assert existing_parties[0]["label"] == "Regulators - Enforcement"
    assert existing_parties[0]["confidence"] == "strong"

    cia = result["existing_matches"]["cia_risks"]
    assert [risk["id"] for risk in cia] == [str(rows["related"].id)]
    assert cia[0]["asset_name"] == "Customer database"
    assert "regulator" in cia[0]["matched_terms"]

    note = result["draft_risk"]["note"]
    assert "Detected PESTLE(E) types: Legal" in note
    assert "Detected interested parties: Regulators" in note


def test_related_registers_are_read_with_a_bounded_number_of_statements(
    contract_db, monkeypatch
):
    """Register rows and their links load eagerly: the statement count stays flat
    as the registers grow, instead of one round trip per risk, item or party."""
    rows = _registers(contract_db)
    for index in range(40):
        risk = Risk(
            asset_id=rows["related"].asset_id,
            risk_types=["Confidentiality"],
            threat_summary=f"Customer database leak scenario {index}",
        )
        contract_db.add(risk)
        contract_db.flush()
        contract_db.add(
            RiskControlLink(
                risk_id=risk.id,
                control_item_id=rows["related"].control_links[0].control_item_id,
                framework_slug="A",
            )
        )
    contract_db.flush()
    contract_db.expire_all()
    statements = []
    engine = contract_db.get_bind()

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    sqlalchemy.event.listen(engine, "before_cursor_execute", record)
    try:
        result = _analyse(
            contract_db,
            monkeypatch,
            issue="The regulator may fine us after a customer database leak.",
        )
    finally:
        sqlalchemy.event.remove(engine, "before_cursor_execute", record)
    assert result["existing_matches"]["cia_risks"]
    assert len(statements) <= 16, len(statements)


def test_pestle_hits_default_to_board_strategy_without_process_rules(
    contract_db, monkeypatch
):
    rules = {**RULES, "pestle_business_processes": {}}
    result = _analyse(contract_db, monkeypatch, rules=rules, issue="Regulator fine")
    assert result["business_process_suggestions"] == [
        {
            "id": None,
            "name": "Board Strategy",
            "score": 1,
            "matched_terms": [],
            "reason": "Default governance process for a newly identified PESTLE(E) scenario.",
            "relevance_code": "medium",
            "exists": False,
        }
    ]
    draft = result["draft_pestle_item"]
    assert draft["business_processes"] == []  # nothing to link until it exists
    assert draft["item"] == "Regulator fine"


def test_existing_pestle_items_alone_shape_the_draft(contract_db, monkeypatch):
    _registers(contract_db)
    rules = {"pestle_types": {}, "interested_parties": {}}
    result = _analyse(contract_db, monkeypatch, rules=rules, issue="Regulator")
    assert result["pestle_hits"] == []
    draft = result["draft_pestle_item"]
    first = result["pestle_suggestions"][0]
    assert (draft["type"], draft["lens"]) == (first["type"], first["lens"])
    assert "Detected PESTLE(E) signals" not in draft["rationale"]


def test_short_issue_titles_use_the_first_sentence():
    assert mitigator._short_issue_title("") == "Keen Mitigator scenario"
    assert mitigator._short_issue_title("  First part.  Second part") == "First part"
    assert mitigator._short_issue_title("?!") == "?!"
    title = mitigator._short_issue_title("word " * 100)
    assert len(title) == 180 and title.endswith("…")


# --- Formatting helpers ----------------------------------------------------------


def test_blank_matching_terms_are_dropped_before_matching():
    """A rule term that is only whitespace never matches anything."""
    prepared = mitigator._prepare_terms(("   ", "Volcano", "third party"))

    assert [term for term, _, _ in prepared] == ["Volcano", "third party"]
    assert prepared[0][1] == "volcano"
    # Long tokens bridge fuzzy matches; their order is not significant.
    assert sorted(prepared[1][2]) == ["party", "third"]


def test_control_documents_include_metadata_and_tags_when_present():
    """Search text covers a control's metadata and tags so either can match."""
    from types import SimpleNamespace

    control = SimpleNamespace(
        ref="A.1",
        framework_slug="",
        title="Backups",
        type="custom",
        meta={"owner": "ops"},
        tags={"domain": "resilience"},
    )
    document = mitigator._control_doc(control)

    assert "A.1" in document and "Backups" in document
    assert "{'owner': 'ops'}" in document
    assert "{'domain': 'resilience'}" in document


def test_missing_catalogue_rows_serialise_as_empty_objects():
    assert mitigator._control_out(None) == {}
    assert mitigator._clause_summary(None) == {}
