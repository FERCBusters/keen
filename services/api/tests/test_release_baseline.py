"""Release migration runs in an isolated schema on the PostgreSQL CI service."""

import gzip
import json
import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.core.config import settings
from sqlalchemy import create_engine, text


def test_release_schema_and_final_seed_data(monkeypatch):
    url = os.environ.get("KEEN_RETENTION_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires PostgreSQL integration database")
    root = Path(__file__).resolve().parents[1]
    engine = create_engine(url)
    schema = "release_" + uuid.uuid4().hex
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    for field in type(settings).model_fields:
        value = getattr(settings, field)
        if isinstance(value, str) and value.startswith("/app/config/"):
            monkeypatch.setattr(
                settings, field, str(root.parents[1] / "config" / Path(value).name)
            )
    monkeypatch.setattr(settings, "default_framework_slug", "KEEN-AF:1.0")
    monkeypatch.setattr(settings, "enabled_frameworks", "")
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    try:
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path TO {schema},public"))
            conn.commit()
            config.attributes["connection"] = conn
            command.upgrade(config, "0002_release_seed")
            expected = json.loads(
                gzip.decompress((root / "alembic/release_seeds.json.gz").read_bytes())
            )
            for table, rows in expected.items():
                actual = list(
                    conn.scalars(text(f"SELECT row_to_json(t) FROM {table} t"))
                )
                canonical = lambda items: sorted(
                    json.dumps(row, sort_keys=True) for row in items
                )
                assert canonical(actual) == canonical(rows), table
            assert conn.scalar(text("SELECT count(*) FROM frameworks")) == 94
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM control_items WHERE framework_slug='KEEN-AF:1.0'"
                    )
                )
                == 247
            )
            conn.execute(
                text(
                    "INSERT INTO keen_agents (id,name,token_hash,enabled,created_at,expires_at,health) VALUES ('baseline-pet','static-pet',:hash,true,now(),now()+interval '90 days','{}'::jsonb)"
                ),
                {"hash": "a" * 64},
            )
            conn.commit()
            command.upgrade(config, "head")
            assert (
                conn.scalar(text("SELECT count(*) FROM agent_enrollment_profiles")) == 0
            )
            pet = conn.execute(
                text(
                    "SELECT enrollment_profile_id,enrollment_labels,token_hash FROM keen_agents WHERE id='baseline-pet'"
                )
            ).one()
            assert tuple(pet) == (None, {}, "a" * 64)
            assert conn.scalar(text("SELECT count(*) FROM source_connections")) == 0
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM control_items WHERE framework_slug='KEEN-AF:1.0'"
                    )
                )
                == 247
            )
            assert "Apache" in conn.scalar(
                text("SELECT description FROM frameworks WHERE slug='KEEN-AF:1.0'")
            )
            conn.execute(
                text("SELECT connection_id, connection_name FROM events LIMIT 1")
            )
            assert conn.scalar(text("SELECT count(*) FROM frameworks")) == 94
            for slug, count in (("iso_9001_2015", 37), ("iso_9001_2026", 39)):
                assert (
                    conn.scalar(
                        text(
                            "SELECT count(*) FROM framework_clauses WHERE framework_slug=:slug"
                        ),
                        {"slug": slug},
                    )
                    == count
                )
            selection = conn.scalar(
                text(
                    "SELECT document FROM managed_configurations WHERE name='organisation-frameworks'"
                )
            )
            assert selection == {"enabled": ["KEEN-AF:1.0"], "default": "KEEN-AF:1.0"}
            conn.execute(
                text(
                    "UPDATE control_items SET title='Local edit' WHERE framework_slug='soc2_tsc' AND ref='CC6.1'"
                )
            )
            conn.commit()
            command.upgrade(config, "head")
            assert (
                conn.scalar(
                    text(
                        "SELECT title FROM control_items WHERE framework_slug='soc2_tsc' AND ref='CC6.1'"
                    )
                )
                == "Local edit"
            )
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        engine.dispose()


def test_release_revision_chain_contains_no_catalogue_transformations():
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    script = ScriptDirectory.from_config(config)
    assert [r.revision for r in script.walk_revisions()] == [
        "0016_parties_org_wide",
        "0015_risk_wizard",
        "0014_measure_source_type",
        "0013_events_source_timestamp",
        "0012_stories",
        "0011_retire_unused_counters",
        "0010_swot_planning",
        "0009_retention_overrides",
        "0008_integration_auth_kind",
        "0007_audit_permissions",
        "0006_session_revocation",
        "0005_agent_enrollment",
        "0004_source_connections",
        "0002_release_seed",
        "0001_release_schema",
    ]


def test_retiring_the_unused_counters_keeps_each_visualisation_preference():
    url = os.environ.get("KEEN_RETENTION_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires PostgreSQL integration database")
    root = Path(__file__).resolve().parents[1]
    engine = create_engine(url)
    schema = "counters_" + uuid.uuid4().hex
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))

    def tables(conn):
        return set(
            conn.scalars(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :schema"
                ),
                {"schema": schema},
            )
        )

    def columns(conn):
        return set(
            conn.scalars(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = 'users'"
                ),
                {"schema": schema},
            )
        )

    try:
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path TO {schema},public"))
            conn.commit()
            config.attributes["connection"] = conn
            command.upgrade(config, "0001_release_schema")
            command.stamp(config, "0002_release_seed")
            command.upgrade(config, "0010_swot_planning")
            for name, chosen in (
                ("heatmap-user", {"mode": "heatmap", "range": "30d"}),
                ("unknown-mode-user", {"mode": "pie"}),
                ("no-choice-user", None),
            ):
                conn.execute(
                    text(
                        "INSERT INTO users (id, username, password_hash, created_at,"
                        " updated_at, pref_auto_apply_filters, pref_source_colors,"
                        " pref_viz_mode, pref_landing_page, pref_date_format,"
                        " authz_version, pref_default_visualisation) VALUES"
                        " (:id, :name, 'unused', now(), now(), false, '{}', 'graph',"
                        " '/', 'dmy', 1, CAST(:chosen AS jsonb))"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "name": name,
                        "chosen": json.dumps(chosen) if chosen else None,
                    },
                )
            for n in range(2):
                conn.execute(
                    text(
                        "INSERT INTO events (id, timestamp, source, summary,"
                        " external_id, created_at) VALUES"
                        " (:id, now(), 'ci', 'build', :external, now())"
                    ),
                    {"id": uuid.uuid4(), "external": f"build-{n}"},
                )
            conn.commit()

            command.upgrade(config, "0011_retire_unused_counters")
            assert not {"global_event_stats", "framework_event_stats"} & tables(conn)
            assert "pref_viz_mode" not in columns(conn)
            assert "pref_default_visualisation" in columns(conn)

            command.downgrade(config, "0010_swot_planning")
            assert {"global_event_stats", "framework_event_stats"} <= tables(conn)
            modes = dict(
                conn.execute(text("SELECT username, pref_viz_mode FROM users")).all()
            )
            assert modes == {
                "heatmap-user": "heatmap",
                "unknown-mode-user": "graph",
                "no-choice-user": "graph",
            }
            assert conn.execute(
                text("SELECT stats_key, total_events FROM global_event_stats")
            ).all() == [("events", 2)]
            assert conn.scalar(text("SELECT count(*) FROM framework_event_stats")) == 0
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        engine.dispose()


def test_measure_method_text_is_folded_into_the_description():
    url = os.environ.get("KEEN_RETENTION_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires PostgreSQL integration database")
    root = Path(__file__).resolve().parents[1]
    engine = create_engine(url)
    schema = "measures_" + uuid.uuid4().hex
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))

    def columns(conn):
        return set(
            conn.scalars(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :schema "
                    "AND table_name = 'isms_effectiveness_measures'"
                ),
                {"schema": schema},
            )
        )

    try:
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path TO {schema},public"))
            conn.commit()
            config.attributes["connection"] = conn
            command.upgrade(config, "0001_release_schema")
            command.stamp(config, "0002_release_seed")
            command.upgrade(config, "0013_events_source_timestamp")
            rows = {
                "described": ("Patch SLA", "Already explained.", "Count on time.", "%"),
                "method-only": ("Restore test", "", "Run a restore.", "Pass/fail"),
                "unnamed": ("", "", "", "Incidents per month"),
                "bare": ("Bare", "", "", ""),
            }
            for key, (summary, description, method, metric) in rows.items():
                conn.execute(
                    text(
                        "INSERT INTO isms_effectiveness_measures (id, framework_slug,"
                        " summary, description, effectiveness_measure, metric,"
                        " metric_key, target_unit, threshold_operator, frequency,"
                        " notes, created_at, updated_at) VALUES (:id, 'A', :summary,"
                        " :description, :method, :metric, :key, '', '', '', '',"
                        " now(), now())"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "summary": summary,
                        "description": description,
                        "method": method,
                        "metric": metric,
                        "key": key,
                    },
                )
            conn.commit()

            command.upgrade(config, "0014_measure_source_type")
            assert "source_type" in columns(conn)
            assert not {"effectiveness_measure", "metric"} & columns(conn)
            migrated = {
                key: (summary, description, source)
                for key, summary, description, source in conn.execute(
                    text(
                        "SELECT metric_key, summary, description, source_type"
                        " FROM isms_effectiveness_measures"
                    )
                ).all()
            }
            assert migrated == {
                "described": (
                    "Patch SLA",
                    "Already explained.\n\nCount on time.\n\n%",
                    "other",
                ),
                "method-only": ("Restore test", "Run a restore.\n\nPass/fail", "other"),
                # A measure that only had a metric definition keeps it as its name.
                "unnamed": ("Incidents per month", "Incidents per month", "other"),
                "bare": ("Bare", "", "other"),
            }

            command.downgrade(config, "0013_events_source_timestamp")
            assert {"effectiveness_measure", "metric"} <= columns(conn)
            assert "source_type" not in columns(conn)
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        engine.dispose()


def test_interested_parties_recorded_per_framework_are_merged():
    url = os.environ.get("KEEN_RETENTION_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires PostgreSQL integration database")
    root = Path(__file__).resolve().parents[1]
    engine = create_engine(url)
    schema = "parties_" + uuid.uuid4().hex
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))

    def columns(conn):
        return set(
            conn.scalars(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :schema "
                    "AND table_name = 'interested_parties'"
                ),
                {"schema": schema},
            )
        )

    ids = {key: uuid.uuid4() for key in ("name", "nature", "a", "b", "c", "staff")}
    control = {fw: uuid.uuid4() for fw in ("A", "B")}
    try:
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path TO {schema},public"))
            conn.commit()
            config.attributes["connection"] = conn
            command.upgrade(config, "0001_release_schema")
            command.stamp(config, "0002_release_seed")
            command.upgrade(config, "0015_risk_wizard")
            run = lambda sql, **params: conn.execute(text(sql), params)  # noqa: E731
            for fw in ("A", "B"):
                run(
                    "INSERT INTO frameworks (id, slug, created_at) VALUES (:id, :fw, now())",
                    id=uuid.uuid4(),
                    fw=fw,
                )
                run(
                    "INSERT INTO control_items (id, framework_slug, type, ref, title,"
                    " created_at) VALUES (:id, :fw, 'control', '1', 'Control', now())",
                    id=control[fw],
                    fw=fw,
                )
            run(
                "INSERT INTO interested_party_names (id, name) VALUES (:n, 'Customers'),"
                " (:s, 'Staff')",
                n=ids["name"],
                s=ids["staff"],
            )
            run(
                "INSERT INTO interested_party_natures (id, name) VALUES (:id, 'External')",
                id=ids["nature"],
            )
            # The same party recorded in three frameworks, and one that is unique.
            for key, fw, note, created in (
                ("a", "A", "Retail customers", "2026-01-01"),
                ("b", "B", "Retail customers", "2026-02-01"),
                ("c", "C", "Enterprise accounts too", "2026-03-01"),
            ):
                run(
                    "INSERT INTO interested_parties (id, framework_slug, name_id,"
                    " nature_id, note, created_at, updated_at) VALUES (:id, :fw, :name,"
                    " :nature, :note, :created, now())",
                    id=ids[key],
                    fw=fw,
                    name=ids["name"],
                    nature=ids["nature"],
                    note=note,
                    created=created,
                )
            run(
                "INSERT INTO interested_parties (id, framework_slug, name_id, nature_id,"
                " note, created_at, updated_at) VALUES (:id, 'A', :name, :nature, '',"
                " now(), now())",
                id=ids["staff"],
                name=ids["staff"],
                nature=ids["nature"],
            )
            for key, fw in (("a", "A"), ("b", "B"), ("c", "B")):
                run(
                    "INSERT INTO interested_party_control_links (interested_party_id,"
                    " framework_slug, control_item_id, created_at) VALUES (:p, :fw, :c,"
                    " now())",
                    p=ids[key],
                    fw=fw,
                    c=control[fw],
                )
            run(
                "INSERT INTO interested_party_communications (id, interested_party_id,"
                " event, \"when\", with_whom, methods) VALUES (:id, :p, 'Incident',"
                " 'Within 24 hours', 'Individual member', '[]')",
                id=uuid.uuid4(),
                p=ids["b"],
            )
            category, subcategory, asset, risk = (uuid.uuid4() for _ in range(4))
            run(
                "INSERT INTO risk_categories (id, name) VALUES (:id, 'Data')",
                id=category,
            )
            run(
                "INSERT INTO risk_asset_subcategories (id, category_id, name) VALUES"
                " (:id, :c, 'PII')",
                id=subcategory,
                c=category,
            )
            run(
                "INSERT INTO risk_assets (id, name, category_id, subcategory_id) VALUES"
                " (:id, 'Payroll', :c, :s)",
                id=asset,
                c=category,
                s=subcategory,
            )
            run("INSERT INTO risks (id, asset_id) VALUES (:id, :a)", id=risk, a=asset)
            # The risk names the party twice through its duplicates.
            for key in ("a", "b"):
                run(
                    "INSERT INTO risk_interested_parties (id, risk_id,"
                    " interested_party_id, created_at) VALUES (:id, :r, :p, now())",
                    id=uuid.uuid4(),
                    r=risk,
                    p=ids[key],
                )
            run(
                "INSERT INTO entity_changelogs (id, entity_type, entity_id, action,"
                " summary, changes, changed_at) VALUES (:id, 'interested_party', :p,"
                " 'created', 'Created', '[]', now())",
                id=uuid.uuid4(),
                p=ids["c"],
            )
            conn.commit()

            command.upgrade(config, "0016_parties_org_wide")
            assert "framework_slug" not in columns(conn)
            parties = conn.execute(
                text("SELECT id, note FROM interested_parties ORDER BY created_at")
            ).all()
            assert [row[0] for row in parties] == [ids["a"], ids["staff"]]
            assert parties[0][1] == "Retail customers\n\nEnterprise accounts too"
            links = conn.execute(
                text(
                    "SELECT framework_slug, control_item_id FROM"
                    " interested_party_control_links WHERE interested_party_id = :p"
                    " ORDER BY framework_slug"
                ),
                {"p": ids["a"]},
            ).all()
            assert links == [("A", control["A"]), ("B", control["B"])]
            assert (
                conn.scalar(
                    text(
                        "SELECT interested_party_id FROM interested_party_communications"
                    )
                )
                == ids["a"]
            )
            assert conn.execute(
                text("SELECT interested_party_id FROM risk_interested_parties")
            ).all() == [(ids["a"],)]
            assert (
                conn.scalar(text("SELECT entity_id FROM entity_changelogs")) == ids["a"]
            )
            with pytest.raises(Exception, match="uq_interested_parties_name_nature"):
                run(
                    "INSERT INTO interested_parties (id, name_id, nature_id, note,"
                    " created_at, updated_at) VALUES (:id, :name, :nature, '', now(),"
                    " now())",
                    id=uuid.uuid4(),
                    name=ids["name"],
                    nature=ids["nature"],
                )
            conn.rollback()

            command.downgrade(config, "0015_risk_wizard")
            assert "framework_slug" in columns(conn)
            assert conn.execute(
                text(
                    "SELECT id, framework_slug FROM interested_parties ORDER BY created_at"
                )
            ).all() == [(ids["a"], "A"), (ids["staff"], "")]
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        engine.dispose()
