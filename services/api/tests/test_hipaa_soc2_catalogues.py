"""Catalogue integrity and non-destructive, offline framework seeding."""

import importlib.util
import json
from pathlib import Path

from app.db.models import ControlItem, Framework, FrameworkClause
from sqlalchemy import create_engine, select, update

from tests.db_helpers import create_sqlite_schema

PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic/legacy_versions/0089_hipaa_soc2_catalogues.py"
)
spec = importlib.util.spec_from_file_location("catalogue_migration", PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def data():
    result = {}
    for file in ("soc2-tsc-2017.json", "hipaa-2026.json"):
        result.update(json.loads((migration.DATA / file).read_text()))
    return result


def test_catalogue_structure_and_provenance():
    catalogues = data()
    soc = catalogues["SOC2-TSC:2017"]
    assert len(soc["controls"]) == 122 and not soc["clauses"]
    assert any(x["ref"] == "CC6.6" for x in soc["controls"])
    assert all(x["metadata"]["license"] == "CC-BY-SA-4.0" for x in soc["controls"])
    for slug, fw in catalogues.items():
        seen = set()
        for item in fw["clauses"] + fw["controls"]:
            assert item["ref"] not in seen
            assert 0 < len(item["title"]) <= 256 and len(item["ref"]) <= 64
            parent = item["metadata"].get("parent_ref")
            assert not parent or parent in seen
            assert item["metadata"]["upstream_url"].startswith("https://")
            assert item["metadata"]["description"]
            seen.add(item["ref"])
    sec = {x["ref"]: x for x in catalogues["HIPAA-SECURITY:2026"]["clauses"]}
    assert sec["164.308(a)(1)(ii)(A)"]["metadata"]["implementation"] == "required"
    assert sec["164.312(a)(2)(iii)"]["metadata"]["implementation"] == "addressable"
    assert not sec["164.304"]["in_scope"]
    assert sec["164.308(a)(1)(ii)(A)"]["in_scope"]
    assert "Document why" in sec["164.306(d)(3)"]["metadata"]["description"]


def test_seed_roundtrip_parents_idempotence_and_preserve_edits():
    engine = create_engine("sqlite://")
    create_sqlite_schema(engine)
    with engine.begin() as conn:
        migration.seed_catalogues(conn)
        before = conn.execute(select(ControlItem.__table__)).mappings().all()
        assert len(before) == sum(
            len(x["clauses"]) + len(x["controls"]) for x in data().values()
        )
        clauses = conn.execute(select(FrameworkClause.__table__)).mappings().all()
        by_id = {x["id"]: x for x in clauses}
        for row in clauses:
            parent = row["metadata"].get("parent_ref")
            if parent:
                assert by_id[row["parent_clause_id"]]["ref"] == parent
                assert (
                    by_id[row["parent_clause_id"]]["framework_slug"]
                    == row["framework_slug"]
                )
        chosen = before[0]["id"]
        conn.execute(
            update(ControlItem)
            .where(ControlItem.id == chosen)
            .values(
                title="Local edited title",
                in_scope=False,
                meta={"description": "Local description"},
            )
        )
        conn.execute(
            update(Framework)
            .where(Framework.slug == "SOC2-TSC:2017")
            .values(name="Local framework name")
        )
        migration.seed_catalogues(conn)
        after = conn.execute(select(ControlItem.__table__)).mappings().all()
        assert {x["id"] for x in before} == {x["id"] for x in after}
        edited = next(x for x in after if x["id"] == chosen)
        assert edited["title"] == "Local edited title" and not edited["in_scope"]
        assert edited["metadata"] == {"description": "Local description"}
        assert (
            conn.execute(
                select(Framework.name).where(Framework.slug == "SOC2-TSC:2017")
            ).scalar_one()
            == "Local framework name"
        )
    engine.dispose()
