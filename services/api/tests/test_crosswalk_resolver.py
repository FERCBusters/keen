"""The crosswalk resolver and its readers against the reference relation.

``crosswalk_links`` resolves inherited evidence from short reads of links and
NIST memberships. The catalogue here has overlapping memberships, links
inside a framework, links that duplicate a shared reference, controls without
memberships and frameworks left out of the selection; every restriction the
readers use is compared with ``tests.crosswalk_reference`` within one
transaction, so remembered resolutions are compared too.
"""

import random
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from app.core.config import settings
from app.db.models import (
    ControlItem,
    CrossFrameworkControlLink,
    Event,
    Framework,
    ManagedConfiguration,
    Mapping,
    OsaControlMapping,
)
from app.services import control_inheritance, control_links
from app.services.control_inheritance import (
    SHARED_NIST_RATIONALE,
    control_event_counts,
    crosswalk_links,
    crosswalk_sources,
    effective_framework_ids,
    event_evidence,
    evidence_pairs,
    evidenced_control_ids,
    framework_evidence_sources,
    has_inherited_evidence,
    inherited_control_ids,
    inherited_event_controls,
    page_controls,
)
from app.services.control_links import list_control_links
from sqlalchemy import delete, func, insert, select
from sqlalchemy import event as sql_event

from tests.crosswalk_reference import (
    reference_evidence_pairs,
    reference_pair_set,
    reference_rows,
    resolved_rows,
)

FRAMEWORKS = ["X0", "X1", "X2", "X3", "X4"]
# References of their own, so a PostgreSQL catalogue adds no pairs.
REFS = ["XR-1", "XR-2", "XR-3", "XR-4", "XR-5"]
SELECTIONS = [
    FRAMEWORKS,
    ["X0", "X1"],
    ["X0"],
    ["X1", "X2", "X3"],
    ["X0", "X2", "X4"],
]


@pytest.fixture
def catalogue(contract_db, monkeypatch):
    monkeypatch.setattr(settings, "enabled_frameworks", "")
    monkeypatch.setattr(settings, "aggregate_cache_ttl_seconds", 0)
    db = contract_db
    rng = random.Random(20261101)
    db.add_all([Framework(slug=slug) for slug in FRAMEWORKS])
    selection = ManagedConfiguration(
        name="organisation-frameworks",
        version=1,
        document={"enabled": FRAMEWORKS, "default": "X0"},
    )
    db.add(selection)
    controls = [
        ControlItem(framework_slug=slug, ref=f"{slug}-{n}", type="control")
        for slug in FRAMEWORKS
        for n in range(6)
    ]
    db.add_all(controls)
    db.flush()
    for control in controls:
        # Some controls hold no reference, others several shared ones.
        for ref in rng.sample(REFS, rng.randint(0, 3)):
            db.add(OsaControlMapping(control_id=control.id, nist_ref=ref))
    pairs = set()
    while len(pairs) < 40:
        source, target = rng.sample(controls, 2)
        pairs.add((source.id, target.id))
    start = datetime(2026, 1, 1, 9, 0)
    for n, (source, target) in enumerate(sorted(pairs, key=str)):
        db.add(
            CrossFrameworkControlLink(
                source_control_id=source,
                target_control_id=target,
                rationale=f"Reviewed {n}",
                created_at=start + timedelta(minutes=n),
            )
        )
    events = [
        Event(
            source="test",
            external_id=f"resolver-{n}",
            summary="Resolver",
            timestamp=start + timedelta(days=n),
        )
        for n in range(24)
    ]
    db.add_all(events)
    db.flush()
    for event in events[:-2]:
        for control in rng.sample(controls, rng.randint(1, 4)):
            db.add(
                Mapping(event_id=event.id, control_item_id=control.id, method="test")
            )
    db.commit()
    return SimpleNamespace(
        db=db, rng=rng, controls=controls, events=events, selection=selection
    )


def select_frameworks(catalogue, enabled):
    catalogue.selection.document = {"enabled": enabled, "default": enabled[0]}
    catalogue.db.commit()


def restrictions(catalogue):
    """Every shape of restriction the readers pass, with chosen controls."""
    rng = random.Random(7)
    ids = [control.id for control in catalogue.controls]
    found = [{"target_framework": slug} for slug in FRAMEWORKS + ["absent"]]
    for size in (0, 1, 3, 8):
        chosen = rng.sample(ids, size)
        others = rng.sample(ids, size)
        found += [
            {"target_ids": chosen},
            {"source_ids": chosen},
            {"target_ids": chosen, "source_ids": others},
            {"target_framework": "X0", "source_ids": chosen},
            {"target_framework": "X1", "target_ids": chosen},
        ]
    return found


@pytest.mark.parametrize("enabled", [None, [], *SELECTIONS])
def test_links_match_the_relation_for_every_restriction(catalogue, enabled):
    db = catalogue.db
    for restriction in restrictions(catalogue):
        expected = reference_rows(db, enabled=enabled, **restriction)
        assert resolved_rows(crosswalk_links(db, enabled=enabled, **restriction)) == (
            expected
        ), restriction
        # A remembered resolution is the same resolution.
        assert resolved_rows(crosswalk_links(db, enabled=enabled, **restriction)) == (
            expected
        )
        grouped = {}
        for _, source, target, *_ in expected:
            grouped.setdefault(target, set()).add(source)
        sources = crosswalk_sources(db, enabled=enabled, **restriction)
        assert dict(sources) == {t: frozenset(found) for t, found in grouped.items()}


def test_links_carry_ids_rationale_and_provenance(catalogue):
    db = catalogue.db
    links = crosswalk_links(db, enabled=None, target_framework="X0")
    explicit = {row.id: row for row in db.query(CrossFrameworkControlLink)}
    derived = [link for link in links if link.derived]
    assert derived and any(not link.derived for link in links)
    for link in links:
        if link.derived:
            assert link.id == f"{link.source_control_id}:{link.target_control_id}"
            assert link.rationale == SHARED_NIST_RATIONALE
            assert link.created_at is None
            assert (link.source_control_id, link.target_control_id) not in {
                (row.source_control_id, row.target_control_id)
                for row in explicit.values()
            }
        else:
            row = explicit[uuid.UUID(link.id)]
            assert link.id == str(row.id)
            assert (link.source_control_id, link.target_control_id) == (
                row.source_control_id,
                row.target_control_id,
            )
            assert (link.rationale, link.created_at) == (row.rationale, row.created_at)
    assert len({link.id for link in links}) == len(links)
    assert list(links) == sorted(
        links, key=lambda row: (str(row.target_control_id), str(row.source_control_id))
    )
    with pytest.raises(ValueError):
        crosswalk_links(db, enabled=None)
    with pytest.raises(ValueError):
        crosswalk_sources(db, enabled=None)
    # Remembered results cannot be changed by a reader.
    with pytest.raises(TypeError):
        crosswalk_sources(db, enabled=None, target_framework="X0")[links[0][2]] = set()


@pytest.mark.parametrize("enabled", SELECTIONS)
def test_readers_match_the_relation_under_each_selection(catalogue, enabled):
    db, events = catalogue.db, catalogue.events
    select_frameworks(catalogue, enabled)
    some_events = [event.id for event in events[::3]]
    for framework in FRAMEWORKS:
        reference = reference_pair_set(framework, db)
        assert set(db.execute(select(evidence_pairs(framework, db)))) == reference
        listed = event_evidence(db, framework, some_events)
        assert {
            (control, event) for event, found in listed.items() for control in found
        } == reference_pair_set(framework, db, some_events)
        assert all(listed.values())
        links = reference_rows(db, target_framework=framework, enabled=enabled)
        direct = {
            control.id
            for control in catalogue.controls
            if control.framework_slug == framework
        }
        assert set(effective_framework_ids(framework, db)) == direct | {
            row[1] for row in links
        }
        assert evidenced_control_ids(db, framework) == {
            control for control, _ in reference
        }
        sources = sorted({row[1] for row in links}, key=str)
        assert has_inherited_evidence(db, framework) == bool(
            sources
            and db.scalar(
                select(func.count(Mapping.id)).where(
                    Mapping.control_item_id.in_(sources)
                )
            )
        )
        counts = {}
        for control, _ in reference:
            counts[control] = counts.get(control, 0) + 1
        assert control_event_counts(db, framework) == counts

        page = page_controls(db, framework, some_events)
        expected = {}
        for control, event in reference_pair_set(framework, db, some_events):
            expected.setdefault(event, set()).add(str(control))
        assert {
            event: {item["id"] for item in items}
            for event, items in page.items()
            if items
        } == expected
        for event in some_events:
            rows = inherited_event_controls(db, framework, event)
            mapped = set(
                db.scalars(
                    select(Mapping.control_item_id).where(Mapping.event_id == event)
                )
            )
            assert {
                (link.source_control_id, link.target_control_id)
                for _, _, link, _ in rows
            } == {
                (row[1], row[2])
                for row in reference_rows(
                    db,
                    target_framework=framework,
                    enabled=enabled,
                    source_ids=sorted(mapped, key=str),
                )
            }
            for mapping, target, link, source in rows:
                assert mapping.event_id == event
                assert mapping.control_item_id == source.id == link.source_control_id
                assert target.id == link.target_control_id
            assert [(target.ref, str(source.id)) for _, target, _, source in rows] == (
                sorted((target.ref, str(source.id)) for _, target, _, source in rows)
            )
    chosen = [control.id for control in catalogue.controls[::4]]
    assert set(inherited_control_ids(db, chosen)) == set(chosen) | {
        row[1] for row in reference_rows(db, target_ids=chosen, enabled=enabled)
    }


def statements_of(db, call):
    found = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        found.append(statement)

    sql_event.listen(db.get_bind(), "before_cursor_execute", capture)
    try:
        result = call()
    finally:
        sql_event.remove(db.get_bind(), "before_cursor_execute", capture)
    return result, found


def test_a_transaction_resolves_each_restriction_once(catalogue):
    db = catalogue.db
    first, statements = statements_of(db, lambda: effective_framework_ids("X0", db))
    assert statements
    again, statements = statements_of(db, lambda: effective_framework_ids("X0", db))
    assert again == first and statements == []
    # The readers of one page share the resolution.
    sources, statements = statements_of(
        db, lambda: framework_evidence_sources(db, "X0")
    )
    assert statements == [] and set().union(*sources.values()) == set(first)
    _, statements = statements_of(db, lambda: has_inherited_evidence(db, "X0"))
    assert len(statements) == 1
    selected = crosswalk_sources(db, enabled=FRAMEWORKS, target_framework="X0")
    assert crosswalk_sources(db, enabled=FRAMEWORKS, target_framework="X0") is selected
    db.commit()
    _, statements = statements_of(db, lambda: effective_framework_ids("X0", db))
    assert statements


def explicit_into(db, target):
    return {
        link.source_control_id
        for link in crosswalk_links(db, enabled=None, target_ids=[target.id])
        if not link.derived
    }


def test_changes_in_the_session_are_resolved_afresh(catalogue):
    db, controls = catalogue.db, catalogue.controls
    target = controls[0]
    sources = [c for c in controls if c.framework_slug != target.framework_slug]
    unlinked = [c for c in sources if c.id not in explicit_into(db, target)]
    # A pending link is seen, as a query would flush it first.
    db.add(
        CrossFrameworkControlLink(
            source_control_id=unlinked[0].id,
            target_control_id=target.id,
            rationale="Pending",
        )
    )
    assert unlinked[0].id in explicit_into(db, target)
    # A write statement is seen.
    db.execute(
        insert(CrossFrameworkControlLink).values(
            id=uuid.uuid4(),
            source_control_id=unlinked[1].id,
            target_control_id=target.id,
            rationale="Inserted",
            created_at=datetime(2026, 2, 1),
        )
    )
    assert unlinked[1].id in explicit_into(db, target)
    db.query(CrossFrameworkControlLink).filter_by(
        source_control_id=unlinked[1].id, target_control_id=target.id
    ).delete()
    assert unlinked[1].id not in explicit_into(db, target)
    # A shared reference added by statement is seen as a derived pair.
    lonely = ControlItem(framework_slug="X4", ref="lonely", type="control")
    db.add(lonely)
    db.flush()
    assert crosswalk_links(db, enabled=None, source_ids=[lonely.id]) == ()
    db.execute(
        insert(OsaControlMapping).values(control_id=lonely.id, nist_ref="LONE-1")
    )
    db.execute(
        insert(OsaControlMapping).values(control_id=target.id, nist_ref="LONE-1")
    )
    (pair,) = crosswalk_links(db, enabled=None, source_ids=[lonely.id])
    assert pair.derived and pair.target_control_id == target.id
    db.execute(delete(OsaControlMapping).where(OsaControlMapping.nist_ref == "LONE-1"))
    assert crosswalk_links(db, enabled=None, source_ids=[lonely.id]) == ()
    assert resolved_rows(
        crosswalk_links(db, enabled=None, target_ids=[target.id])
    ) == reference_rows(db, target_ids=[target.id])


def test_the_selection_and_transactions_discard_remembered_links(catalogue):
    db = catalogue.db
    assert crosswalk_links(db, enabled=None, target_framework="X0")
    assert "keen_crosswalk" in db.info
    db.rollback()
    assert "keen_crosswalk" not in db.info
    x1 = {c.id for c in catalogue.controls if c.framework_slug == "X1"}
    assert set(effective_framework_ids("X1", db)) > x1
    catalogue.selection.document = {"enabled": ["X0"], "default": "X0"}
    db.flush()
    assert "keen_crosswalk" not in db.info
    assert set(effective_framework_ids("X1", db)) == x1
    own = {c.id for c in catalogue.controls if c.framework_slug == "X0"}
    inside = reference_rows(db, target_framework="X0", enabled=["X0"])
    assert {row[1] for row in inside} <= own
    assert effective_framework_ids("X0", db) == sorted(own, key=str)
    db.commit()
    assert "keen_crosswalk" not in db.info


def test_the_catalogue_list_orders_reviewed_links_first(contract_db):
    db = contract_db
    meta = {"osa_nist_controls": ["LT-1", "LT-2"]}

    def control(framework, ref, refs=()):
        row = ControlItem(
            framework_slug=framework,
            ref=ref,
            type="control",
            meta={"osa_nist_controls": list(refs)},
        )
        db.add(row)
        return row

    target = ControlItem(framework_slug="L-T", ref="T1", type="control", meta=meta)
    db.add(target)
    later = control("L-Z", "Z1", ["LT-1"])
    earlier = control("L-Y", "Y9", ["LT-1"])
    shared_y2 = control("L-Y", "Y2", ["LT-2"])
    shared_y1 = control("L-Y", "Y1", ["LT-1", "LT-2"])
    shared_w = control("L-W", "W1", ["LT-1"])
    db.flush()
    for row in (target, later, earlier, shared_y2, shared_y1, shared_w):
        for ref in row.meta["osa_nist_controls"]:
            db.add(OsaControlMapping(control_id=row.id, nist_ref=ref))
    db.add_all(
        [
            CrossFrameworkControlLink(
                source_control_id=later.id,
                target_control_id=target.id,
                rationale="Second review",
                created_at=datetime(2026, 2, 2),
            ),
            CrossFrameworkControlLink(
                source_control_id=earlier.id,
                target_control_id=target.id,
                rationale="First review",
                created_at=datetime(2026, 2, 1),
            ),
        ]
    )
    db.commit()
    items = list_control_links(target_id=target.id, db=db)["items"]
    assert [item["source_ref"] for item in items] == ["Y9", "Z1", "W1", "Y1", "Y2"]
    assert [item["derived"] for item in items] == [False, False, True, True, True]
    assert items[0]["rationale"] == "First review"
    assert items[3]["id"] == f"{shared_y1.id}:{target.id}"
    assert items[3]["rationale"] == (
        SHARED_NIST_RATIONALE + " Shared NIST controls: LT-1, LT-2."
    )
    assert (
        items[4]["rationale"] == SHARED_NIST_RATIONALE + " Shared NIST controls: LT-2."
    )
    # The list shows every link, whatever the organisation's selection.
    assert {item["source_framework"] for item in items} == {"L-W", "L-Y", "L-Z"}


def test_evidence_pairs_keep_their_columns(catalogue):
    db = catalogue.db
    select_frameworks(catalogue, ["X1"])
    for framework in ("X0", "X1", "absent"):
        pairs = evidence_pairs(framework, db)
        reference = reference_evidence_pairs(framework, db)
        assert list(pairs.c.keys()) == list(reference.c.keys())
        assert list(pairs.c.keys()) == ["control_id", "event_id"]
        assert set(db.execute(select(pairs))) == reference_pair_set(framework, db)


def test_a_control_removed_between_reads_is_left_out(catalogue, monkeypatch):
    """Another transaction can remove a control after its links were read."""
    db, events = catalogue.db, catalogue.events
    gone = uuid.uuid4()
    resolve = control_inheritance.crosswalk_links

    def with_removed_control(db, **restriction):
        links = resolve(db, **restriction)
        if not links:
            return links
        first = links[0]
        stale = first._replace(
            id=str(uuid.uuid4()),
            source_control_id=gone
            if restriction.get("target_ids")
            else first.source_control_id,
            target_control_id=first.target_control_id
            if restriction.get("target_ids")
            else gone,
        )
        return (*links, stale)

    page = page_controls(db, "X0", [event.id for event in events])
    monkeypatch.setattr(control_inheritance, "crosswalk_links", with_removed_control)
    assert page_controls(db, "X0", [event.id for event in events]) == page
    target = next(
        control
        for control in catalogue.controls
        if crosswalk_links(db, enabled=None, target_ids=[control.id])
    )
    expected = list_control_links(target_id=target.id, db=db)
    monkeypatch.setattr(control_links, "crosswalk_links", with_removed_control)
    assert list_control_links(target_id=target.id, db=db) == expected
