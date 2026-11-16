"""Migrations 0052 to 0054: business processes go, risks and factors gain their
other link, and columns nothing read are dropped."""

import json
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from tests.db_helpers import required_database_url

BEFORE = "0051_isms_business_processes"
PROCESSES = "0052_remove_business_processes"
LINKS = "0053_risk_and_factor_links"
COLUMNS = "0054_drop_unused_columns"
LEGACY = {
    "threat_score",
    "vulnerability_score",
    "impact_score",
    "residual_vulnerability_score",
    "residual_impact_score",
}


@contextmanager
def release(revision):
    """A connection to a scratch schema migrated to ``revision``, without seeds."""
    engine = create_engine(required_database_url("KEEN_RETENTION_TEST_DATABASE_URL"))
    schema = "links_" + uuid.uuid4().hex
    root = Path(__file__).resolve().parents[1]
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    try:
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path TO {schema},public"))
            conn.commit()
            config.attributes["connection"] = conn
            command.upgrade(config, "0001_release_schema")
            command.stamp(config, "0002_release_seed")
            command.upgrade(config, revision)
            conn.info["schema"] = schema
            yield conn, config
            conn.rollback()
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        engine.dispose()


def tables(conn):
    return set(
        conn.scalars(
            text(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = :schema"
            ),
            {"schema": conn.info["schema"]},
        )
    )


def columns(conn, table):
    return set(
        conn.scalars(
            text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = :schema AND table_name = :table"
            ),
            {"schema": conn.info["schema"], "table": table},
        )
    )


def record_change(conn, kind, action, path, before, after, changes):
    entry = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO entity_changelogs (id, entity_type, entity_id, action,"
            " summary, before_state, after_state, changes, changed_at,"
            " request_path) VALUES (:id, :kind, :entity, :action, '',"
            " CAST(:before AS jsonb), CAST(:after AS jsonb),"
            " CAST(:changes AS jsonb), now(), :path)"
        ),
        {
            "id": entry,
            "kind": kind,
            "entity": uuid.uuid4(),
            "action": action,
            "before": json.dumps(before),
            "after": json.dumps(after),
            "changes": json.dumps(changes),
            "path": path,
        },
    )
    return entry


def history(conn):
    return {
        row.id: row
        for row in conn.execute(
            text("SELECT id, before_state, after_state, changes FROM entity_changelogs")
        )
    }


def test_business_processes_leave_the_schema_the_history_and_the_feature_settings():
    removed = {"pestle_business_processes", "pestle_business_process_relevance"}
    with release(BEFORE) as (conn, config):
        process = uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO pestle_business_processes (id, name)"
                " VALUES (:id, 'Payroll')"
            ),
            {"id": process},
        )
        linked = {"item": "Privacy law", "business_processes": [{"name": "Payroll"}]}
        of_process = record_change(
            conn, "pestle_business_process", "created", "/v1/x", None, {}, []
        )
        mixed = record_change(
            conn,
            "pestle_item",
            "updated",
            "/v1/pestle/items/1",
            linked,
            linked | {"item": "Privacy law tightens"},
            [{"field": "business_processes"}, {"field": "item"}],
        )
        only_processes = record_change(
            conn,
            "pestle_item",
            "updated",
            "/v1/pestle/items/1/business-processes",
            linked,
            linked,
            [{"field": "business_processes"}],
        )
        created = record_change(
            conn, "pestle_item", "created", "/v1/pestle/items", None, linked, []
        )
        other = record_change(
            conn,
            "risk",
            "updated",
            "/v1/risks/1",
            {"business_processes": 1},
            {"business_processes": 2},
            [{"field": "business_processes"}],
        )
        for name, document in (
            ("features", {"business_processes": False, "pestle": True}),
            ("other", {"business_processes": 1}),
        ):
            conn.execute(
                text(
                    "INSERT INTO managed_configurations (name, document, version,"
                    " updated_at) VALUES (:name, CAST(:document AS jsonb), 3, now())"
                ),
                {"name": name, "document": json.dumps(document)},
            )
        conn.commit()

        command.upgrade(config, PROCESSES)
        assert not removed & tables(conn)
        kept = history(conn)
        # The history of the processes themselves goes, with a factor's entry
        # that recorded nothing else.
        assert set(kept) == {mixed, created, other}
        assert kept[mixed].before_state == {"item": "Privacy law"}
        assert kept[mixed].after_state == {"item": "Privacy law tightens"}
        assert kept[mixed].changes == [{"field": "item"}]
        assert kept[created].before_state is None
        assert kept[created].after_state == {"item": "Privacy law"}
        # Another record's history is not a factor's to rewrite.
        assert kept[other].changes == [{"field": "business_processes"}]
        assert of_process not in kept and only_processes not in kept
        settings = dict(
            conn.execute(
                text("SELECT name, document FROM managed_configurations")
            ).all()
        )
        assert settings == {
            "features": {"pestle": True},
            "other": {"business_processes": 1},
        }
        versions = dict(
            conn.execute(text("SELECT name, version FROM managed_configurations")).all()
        )
        assert versions == {"features": 4, "other": 3}

        command.downgrade(config, BEFORE)
        assert removed <= tables(conn)
        # Both tables return empty and take rows as they did.
        assert conn.scalar(text("SELECT count(*) FROM pestle_business_processes")) == 0
        conn.execute(
            text(
                "INSERT INTO pestle_business_processes (id, name)"
                " VALUES (:id, 'Payroll')"
            ),
            {"id": process},
        )


def test_risks_gain_clause_links_and_factors_gain_control_relevance():
    added = {"risk_clause_links", "pestle_control_relevance"}
    with release(PROCESSES) as (conn, config):
        assert not added & tables(conn)
        command.upgrade(config, LINKS)
        assert added <= tables(conn)
        assert columns(conn, "risk_clause_links") == {
            "risk_id",
            "framework_slug",
            "clause_id",
            "created_at",
        }
        assert columns(conn, "pestle_control_relevance") == {
            "pestle_item_id",
            "control_item_id",
            "relevance_id",
            "created_at",
            "updated_at",
        }
        # The links are of a risk and a clause that exist.
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO risk_clause_links (risk_id, framework_slug,"
                    " clause_id) VALUES (:risk, 'A', :clause)"
                ),
                {"risk": uuid.uuid4(), "clause": uuid.uuid4()},
            )
        conn.rollback()
        command.downgrade(config, PROCESSES)
        assert not added & tables(conn)


def test_the_legacy_risk_scores_and_unused_artifact_columns_are_dropped():
    with release(LINKS) as (conn, config):
        category, subcategory, asset, risk = (uuid.uuid4() for _ in range(4))
        conn.execute(
            text("INSERT INTO risk_categories (id, name) VALUES (:id, 'Data')"),
            {"id": category},
        )
        conn.execute(
            text(
                "INSERT INTO risk_asset_subcategories (id, category_id, name)"
                " VALUES (:id, :category, 'Databases')"
            ),
            {"id": subcategory, "category": category},
        )
        conn.execute(
            text(
                "INSERT INTO risk_assets (id, name, category_id, subcategory_id)"
                " VALUES (:id, 'Payroll', :category, :subcategory)"
            ),
            {"id": asset, "category": category, "subcategory": subcategory},
        )
        conn.execute(
            text(
                "INSERT INTO risks (id, asset_id, register_likelihood,"
                " register_impact, register_residual_likelihood, threat_score,"
                " vulnerability_score) VALUES (:id, :asset, 4, 5, 2, 3, 3)"
            ),
            {"id": risk, "asset": asset},
        )
        state = {"note": "Reviewed", "threat_score": 1, "impact_score": 4}
        rated = record_change(
            conn,
            "risk",
            "updated",
            "/v1/risks/1",
            state,
            state | {"impact_score": 5, "note": "Rated"},
            [{"field": "impact_score"}, {"field": "note"}],
        )
        # A new risk has no earlier state: a JSON null, not an object.
        created = record_change(conn, "risk", "created", "/v1/risks", None, state, [])
        factor = record_change(
            conn,
            "pestle_item",
            "updated",
            "/v1/pestle/items/1",
            {"impact_score": 1},
            {"impact_score": 2},
            [{"field": "impact_score"}],
        )
        conn.commit()

        command.upgrade(config, COLUMNS)
        assert not LEGACY & columns(conn, "risks")
        assert not {"pii_flags", "retention_class"} & columns(conn, "artifacts")
        kept = history(conn)
        assert kept[rated].before_state == {"note": "Reviewed"}
        assert kept[rated].after_state == {"note": "Rated"}
        assert kept[rated].changes == [{"field": "note"}]
        assert kept[created].before_state is None
        assert kept[created].after_state == {"note": "Reviewed"}
        # Only a risk's history held the scores.
        assert kept[factor].changes == [{"field": "impact_score"}]

        command.downgrade(config, LINKS)
        assert LEGACY <= columns(conn, "risks")
        assert {"pii_flags", "retention_class"} <= columns(conn, "artifacts")
        # The scores mirror the register ratings again; a missing rating is 1.
        scores = conn.execute(
            text(
                "SELECT threat_score, vulnerability_score, impact_score,"
                " residual_vulnerability_score, residual_impact_score FROM risks"
            )
        ).one()
        assert tuple(scores) == (1, 4, 5, 2, 1)
        # A new row takes the defaults, and the range checks are back.
        conn.execute(text("UPDATE risks SET threat_score = DEFAULT"))
        with pytest.raises(IntegrityError):
            conn.execute(text("UPDATE risks SET impact_score = 9"))
        conn.rollback()
