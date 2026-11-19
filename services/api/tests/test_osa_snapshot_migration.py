"""Migration 0056: the OSA catalogues move to OSA's data of 12 November 2026.

Run against the release's real seed and the real snapshot, so the counts here
are the ones an installation sees.
"""

import importlib.util
import json
import re
import uuid
from pathlib import Path

import pytest
from alembic import command
from app.core.config import settings
from sqlalchemy import text

from tests.test_link_migrations import release

BEFORE = "0055_osa_crosswalk_notes"
AFTER = "0056_osa_snapshot_2026_11"
OSA = "https://www.opensecurityarchitecture.org/"
SNAPSHOT = json.loads(
    (
        Path(__file__).resolve().parents[1] / "alembic/catalogues/osa-2026-11-12.json"
    ).read_text()
)
PUBLISHED = {
    framework["id"]: {clause["id"]: clause for clause in framework["clauses"]}
    for framework in SNAPSHOT["frameworks"]
}


@pytest.fixture(autouse=True)
def shipped_config(monkeypatch):
    """Seed from the repository's config files, as the release baseline test does."""
    root = Path(__file__).resolve().parents[3]
    for field in type(settings).model_fields:
        value = getattr(settings, field)
        if isinstance(value, str) and value.startswith("/app/config/"):
            monkeypatch.setattr(
                settings, field, str(root / "config" / Path(value).name)
            )
    monkeypatch.setattr(settings, "default_framework_slug", "KEEN-AF:1.0")
    monkeypatch.setattr(settings, "enabled_frameworks", "")


def control(conn, slug, ref):
    return (
        conn.execute(
            text(
                "SELECT id, title, in_scope, tags, metadata FROM control_items"
                " WHERE framework_slug = :slug AND ref = :ref"
            ),
            {"slug": slug, "ref": ref},
        )
        .mappings()
        .first()
    )


def refs(conn, slug):
    return set(
        conn.scalars(
            text("SELECT ref FROM control_items WHERE framework_slug = :slug"),
            {"slug": slug},
        )
    )


def change(conn, slug, ref, assignment, **values):
    conn.execute(
        text(
            f"UPDATE control_items SET {assignment}"  # noqa: S608 - test's own SQL
            " WHERE framework_slug = :slug AND ref = :ref"
        ),
        {"slug": slug, "ref": ref, **values},
    )


def test_every_osa_framework_moves_to_the_new_snapshot_and_nothing_in_use_is_lost():
    with release(BEFORE, seeds=True) as (conn, config):
        untouched = {
            row.framework_slug: row.n
            for row in conn.execute(
                text(
                    "SELECT framework_slug, count(*) AS n FROM control_items"
                    " WHERE metadata->>'source' IS DISTINCT FROM :osa"
                    " GROUP BY framework_slug"
                ),
                {"osa": OSA},
            )
        }
        assert {"KEEN-AF:1.0", "UK-DVSTF:1.0", "CYBER-ESSENTIALS:2026"} <= set(
            untouched
        )
        # PCI DSS is reissued: five requirements OSA no longer publishes, each
        # with something of the installation's on it, and the rest left alone.
        gone = sorted(refs(conn, "pci_dss_v4") - set(PUBLISHED["pci_dss_v4"]))
        assert len(gone) > 10
        tagged, descoped, described, linked, ignored = gone[:5]
        change(conn, "pci_dss_v4", tagged, 'tags = \'{"team": "payments"}\'')
        change(conn, "pci_dss_v4", descoped, "in_scope = false")
        change(
            conn,
            "pci_dss_v4",
            described,
            'metadata = metadata || \'{"description": "Ours."}\'',
        )
        iso = control(conn, "iso_27001_2022", "A.5.19")
        conn.execute(
            text(
                "INSERT INTO cross_framework_control_links"
                " (id, source_control_id, target_control_id, rationale, created_at)"
                " VALUES (:id, :source, :target, 'Ours', now())"
            ),
            {
                "id": uuid.uuid4(),
                "source": control(conn, "pci_dss_v4", linked)["id"],
                "target": iso["id"],
            },
        )
        # An administrator's own title and description on controls OSA still has.
        retitled, plain = sorted(PUBLISHED["nist_csf_2"])[:2]
        before_plain = control(conn, "nist_csf_2", plain)
        change(conn, "nist_csf_2", retitled, "title = 'Our own name for it'")
        change(
            conn,
            "nist_csf_2",
            plain,
            'metadata = metadata || \'{"description": "How we meet it."}\','
            " in_scope = false",
        )
        assert iso["title"] == "Information security in supplier relationships"
        conn.commit()

        command.upgrade(config, AFTER)

        # Every framework OSA publishes is present with exactly OSA's controls,
        # plus any superseded control that was kept.
        for slug, published in PUBLISHED.items():
            held = refs(conn, slug)
            assert set(published) <= held, slug
            if slug != "pci_dss_v4":
                assert held == set(published), slug
        assert len(PUBLISHED) == 96
        assert len(PUBLISHED["pci_dss_v4"]) == 257
        assert refs(conn, "pci_dss_v4") - set(PUBLISHED["pci_dss_v4"]) == {
            tagged,
            descoped,
            described,
            linked,
        }
        for ref in (tagged, descoped, described, linked):
            assert control(conn, "pci_dss_v4", ref)["metadata"]["superseded"] == (
                "2026-11-12"
            )
        assert control(conn, "pci_dss_v4", ignored) is None
        assert control(conn, "pci_dss_v4", tagged)["tags"] == {"team": "payments"}
        assert control(conn, "pci_dss_v4", described)["metadata"]["description"] == (
            "Ours."
        )
        assert "superseded" not in control(conn, "pci_dss_v4", "1.2.1")["metadata"]

        # A framework new to Keen arrives whole and in scope, with OSA's credit.
        assert len(refs(conn, "nist_ssdf")) == len(PUBLISHED["nist_ssdf"]) == 42
        fresh = control(conn, "nist_ssdf", sorted(PUBLISHED["nist_ssdf"])[0])
        assert fresh["in_scope"] is True
        assert "description" not in fresh["metadata"]
        assert fresh["metadata"]["source_commit"] == SNAPSHOT["source_commit"]
        name, description = conn.execute(
            text("SELECT name, description FROM frameworks WHERE slug = 'nist_ssdf'")
        ).one()
        assert name == "NIST Secure Software Development Framework (SSDF 1.1)"
        assert description.endswith("Offline snapshot 2026-11-12.")
        assert (
            conn.execute(
                text("SELECT description FROM frameworks WHERE slug = 'iso_27001_2022'")
            )
            .scalar()
            .endswith("Offline snapshot 2026-11-12.")
        )
        assert (
            conn.execute(
                text("SELECT name FROM frameworks WHERE slug = 'pci_dss_v4'")
            ).scalar()
            == "PCI DSS v4.0.1"
        )

        # The framework OSA retired goes, since nothing here used it.
        assert refs(conn, "eba_ict") == set()
        assert (
            conn.execute(
                text("SELECT count(*) FROM frameworks WHERE slug = 'eba_ict'")
            ).scalar()
            == 0
        )

        # ISO 27001 keeps the standard's titles and its ids, and takes OSA's
        # current mapping; OSA's own wording is recorded beside it.
        after = control(conn, "iso_27001_2022", "A.5.19")
        published = PUBLISHED["iso_27001_2022"]["A.5.19"]
        assert after["id"] == iso["id"]
        assert after["title"] == "Information security in supplier relationships"
        assert after["metadata"]["title"] == after["title"]
        assert after["metadata"]["osa_title"] == published["title"] != after["title"]
        assert after["metadata"]["osa_nist_controls"] == published["controls"]
        assert after["metadata"]["rationale"] == published["rationale"]
        assert after["metadata"]["coverage_pct"] == published["coverage_pct"]
        assert "description" not in after["metadata"]
        assert set(
            conn.scalars(
                text(
                    "SELECT nist_ref FROM osa_control_mappings WHERE control_id = :id"
                ),
                {"id": iso["id"]},
            )
        ) == set(published["controls"])
        # Its numbered clauses are still clauses, under the same titles.
        clause_rows = dict(
            conn.execute(
                text(
                    "SELECT ref, title FROM framework_clauses"
                    " WHERE framework_slug = 'iso_27001_2022'"
                )
            ).all()
        )
        assert set(clause_rows) == {
            ref for ref in PUBLISHED["iso_27001_2022"] if not ref.startswith("A.")
        }
        assert clause_rows["4.1"] == control(conn, "iso_27001_2022", "4.1")["title"]

        # Elsewhere a title the installation left alone becomes OSA's, and one
        # an administrator wrote stays, as does their description and scope.
        kept = control(conn, "nist_csf_2", retitled)
        assert kept["title"] == "Our own name for it"
        assert (
            kept["metadata"]["osa_title"] == PUBLISHED["nist_csf_2"][retitled]["title"]
        )
        moved = control(conn, "nist_csf_2", plain)
        assert moved["id"] == before_plain["id"]
        assert moved["title"] == PUBLISHED["nist_csf_2"][plain]["title"][:256]
        assert moved["metadata"]["description"] == "How we meet it."
        assert moved["in_scope"] is False
        assert "osa_title" not in moved["metadata"]

        # Frameworks that are not OSA's are exactly as they were.
        assert untouched == {
            row.framework_slug: row.n
            for row in conn.execute(
                text(
                    "SELECT framework_slug, count(*) AS n FROM control_items"
                    " WHERE metadata->>'source' IS DISTINCT FROM :osa"
                    " GROUP BY framework_slug"
                ),
                {"osa": OSA},
            )
        }


def test_a_retired_framework_stays_while_the_organisation_uses_it():
    with release(BEFORE, seeds=True) as (conn, config):
        first, second = sorted(refs(conn, "eba_ict"))[:2]
        change(conn, "eba_ict", first, "in_scope = false")
        conn.execute(
            text("DELETE FROM managed_configurations WHERE name = :name"),
            {"name": "organisation-frameworks"},
        )
        conn.execute(
            text(
                "INSERT INTO managed_configurations"
                " (name, document, version, updated_at)"
                " VALUES ('organisation-frameworks', CAST(:doc AS jsonb), 1, now())"
            ),
            {"doc": json.dumps({"enabled": ["eba_ict"], "default": "KEEN-AF:1.0"})},
        )
        conn.commit()

        command.upgrade(config, AFTER)

        # Only the control with a scope decision on it is left, superseded.
        assert refs(conn, "eba_ict") == {first}
        assert control(conn, "eba_ict", first)["metadata"]["superseded"] == "2026-11-12"
        assert control(conn, "eba_ict", second) is None
        assert (
            conn.execute(
                text("SELECT count(*) FROM frameworks WHERE slug = 'eba_ict'")
            ).scalar()
            == 1
        )
        # Downgrading is harmless and restores nothing.
        command.downgrade(config, BEFORE)
        assert refs(conn, "eba_ict") == {first}
        assert len(refs(conn, "pci_dss_v4")) == 257


def test_every_published_framework_has_its_regions_in_the_framework_editor():
    """The region filter is derived from the snapshot, so the two list the same."""
    source = (
        Path(__file__).resolve().parents[2] / "ui/public/pages/framework-regions.js"
    ).read_text()
    listed = set(re.findall(r"^  (\w+): \[", source, re.MULTILINE))
    assert listed == set(PUBLISHED)
    assert "OSA " + SNAPSHOT["retrieved"] in source


def snapshot_migration(data=None, tmp_path=None):
    """Migration 0056's module, reading ``data`` in place of the shipped snapshot."""
    spec = importlib.util.spec_from_file_location(
        "osa_snapshot_0056",
        Path(__file__).resolve().parents[1]
        / "alembic/versions/0056_osa_snapshot_2026_11.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if data is not None:
        module.DATA = tmp_path / "snapshot.json"
        module.DATA.write_text(json.dumps(data))
    return module


def without_clause(ref):
    """The shipped snapshot with one ISO 27001 clause no longer published."""
    data = json.loads(json.dumps(SNAPSHOT))
    for framework in data["frameworks"]:
        if framework["id"] == "iso_27001_2022":
            framework["clauses"] = [c for c in framework["clauses"] if c["id"] != ref]
    return data


def clause(conn, ref):
    return (
        conn.execute(
            text(
                "SELECT c.id, c.metadata, p.ref AS parent FROM framework_clauses c"
                " LEFT JOIN framework_clauses p ON p.id = c.parent_clause_id"
                " WHERE c.framework_slug = 'iso_27001_2022' AND c.ref = :ref"
            ),
            {"ref": ref},
        )
        .mappings()
        .first()
    )


def file_under(conn, ref, parent):
    conn.execute(
        text(
            "UPDATE framework_clauses SET parent_clause_id = ("
            " SELECT id FROM framework_clauses"
            " WHERE framework_slug = 'iso_27001_2022' AND ref = :parent)"
            " WHERE framework_slug = 'iso_27001_2022' AND ref = :ref"
        ),
        {"ref": ref, "parent": parent},
    )


def link_entity(conn, ref):
    conn.execute(
        text(
            "INSERT INTO isms_entity_clause_links"
            " (entity_type, entity_id, framework_slug, clause_id)"
            " VALUES ('policy', :entity, 'iso_27001_2022', :clause)"
        ),
        {"entity": uuid.uuid4(), "clause": clause(conn, ref)["id"]},
    )


def entity_links(conn, ref):
    return conn.execute(
        text(
            "SELECT count(*) FROM isms_entity_clause_links l"
            " JOIN framework_clauses c ON c.id = l.clause_id"
            " WHERE c.framework_slug = 'iso_27001_2022' AND c.ref = :ref"
        ),
        {"ref": ref},
    ).scalar()


def test_a_dropped_clause_stays_while_a_clause_in_use_sits_under_it(tmp_path):
    """Deleting a parent clause would delete the clauses filed under it, and
    every link to them, so a dropped parent with a surviving child is kept."""
    with release(BEFORE, seeds=True) as (conn, config):
        file_under(conn, "6.1.3", "6.1")
        link_entity(conn, "6.1.3")
        snapshot_migration(without_clause("6.1"), tmp_path).apply_snapshot(conn)

        child = clause(conn, "6.1.3")
        assert child is not None and child["parent"] == "6.1"
        assert entity_links(conn, "6.1.3") == 1
        assert clause(conn, "6.1")["metadata"]["superseded"] == "2026-11-12"
        # The parent's own record goes, as an unused dropped control does.
        assert control(conn, "iso_27001_2022", "6.1") is None


def test_a_dropped_clause_chain_above_a_surviving_clause_stays(tmp_path):
    """Every level above a clause that stays is kept, not only its parent."""
    data = without_clause("6.1")
    for framework in data["frameworks"]:
        if framework["id"] == "iso_27001_2022":
            framework["clauses"] = [c for c in framework["clauses"] if c["id"] != "6.2"]
    with release(BEFORE, seeds=True) as (conn, config):
        file_under(conn, "6.1", "6.2")
        file_under(conn, "6.1.3", "6.1")
        snapshot_migration(data, tmp_path).apply_snapshot(conn)

        assert clause(conn, "6.1.3")["parent"] == "6.1"
        assert clause(conn, "6.1")["parent"] == "6.2"
        for ref in ("6.1", "6.2"):
            assert clause(conn, ref)["metadata"]["superseded"] == "2026-11-12"


def test_an_unused_dropped_clause_with_no_surviving_child_is_deleted(tmp_path):
    with release(BEFORE, seeds=True) as (conn, config):
        snapshot_migration(without_clause("6.1"), tmp_path).apply_snapshot(conn)
        assert clause(conn, "6.1") is None
        # Its OSA child, no longer under a published parent, stays at the top.
        assert clause(conn, "6.1.3")["parent"] is None


def test_clause_parents_stay_as_the_installation_has_them():
    """OSA files 6.1.3 under 6.1 and publishes nothing else under it. Audit
    scope reads a clause with children as a heading only, so 6.1 stays a clause
    of its own; a parent an administrator chose is kept."""
    with release(BEFORE, seeds=True) as (conn, config):
        file_under(conn, "5.3", "4.4")
        conn.commit()

        command.upgrade(config, AFTER)

        assert clause(conn, "6.1.3")["parent"] is None
        assert clause(conn, "5.3")["parent"] == "4.4"
