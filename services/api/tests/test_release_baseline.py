"""Release migration runs in an isolated schema on the PostgreSQL CI service."""

import gzip
import json
import re
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.core.config import settings
from sqlalchemy import create_engine, text

from tests.db_helpers import required_database_url
from tests.test_builtin_rules import ALL_RULES as BUILTIN_RULES


def test_release_schema_and_final_seed_data(monkeypatch):
    url = required_database_url("KEEN_RETENTION_TEST_DATABASE_URL")
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

                def canonical(items):
                    return sorted(json.dumps(row, sort_keys=True) for row in items)

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
            # The seeded framework has every built-in rule's controls, so each
            # rule is added whole after the deployment's own.
            rules = conn.scalar(
                text("SELECT document FROM managed_configurations WHERE name='rules'")
            )["rules"]
            assert rules[-len(BUILTIN_RULES) :] == BUILTIN_RULES
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
        "0049_default_groups",
        "0048_events_retention_time",
        "0047_affected_links",
        "0046_asset_owner_only",
        "0045_mcp_oauth",
        "0044_posture_risk_treatment",
        "0043_access_matrix_fields",
        "0042_access_matrix_approver",
        "0041_mfa_code_failures",
        "0040_remove_audittrail_read",
        "0039_measure_submission_secrets",
        "0038_builtin_host_rules",
        "0037_builtin_rules",
        "0036_control_owners",
        "0035_notification_delivery",
        "0034_inbox_measure_outside",
        "0033_inbox_items",
        "0032_rule_suggestions",
        "0031_theme_ink_default",
        "0030_agent_server_keys",
        "0029_stats_delete_increment",
        "0028_automatic_purge_job_index",
        "0027_cloud_posture",
        "0026_posture_risk_links",
        "0025_ueu_evidence",
        "0024_dvstf_description",
        "0023_workstation_people",
        "0022_insert_vacuum_upkeep",
        "0021_dashboard_query_support",
        "0020_theme_ink",
        "0019_quizzes",
        "0018_keen_seed_wording",
        "0017_story_records",
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
    url = required_database_url("KEEN_RETENTION_TEST_DATABASE_URL")
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
    url = required_database_url("KEEN_RETENTION_TEST_DATABASE_URL")
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


def test_access_matrix_status_and_hosting_accounts_are_removed():
    url = required_database_url("KEEN_RETENTION_TEST_DATABASE_URL")
    root = Path(__file__).resolve().parents[1]
    engine = create_engine(url)
    schema = "matrix_" + uuid.uuid4().hex
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))

    def catalogue(conn, query):
        return set(conn.scalars(text(query), {"schema": schema}))

    def tables(conn):
        return catalogue(
            conn,
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = :schema AND table_name LIKE 'isms_%'",
        )

    def columns(conn):
        return catalogue(
            conn,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :schema "
            "AND table_name = 'isms_access_control_matrix_entries'",
        )

    accounts = {"isms_aws_accounts", "isms_access_control_matrix_aws_accounts"}
    try:
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path TO {schema},public"))
            conn.commit()
            config.attributes["connection"] = conn
            command.upgrade(config, "0001_release_schema")
            command.stamp(config, "0002_release_seed")
            command.upgrade(config, "0042_access_matrix_approver")
            conn.execute(
                text(
                    "INSERT INTO isms_access_control_matrix_entries"
                    " (id, task_action, status) VALUES (:id, 'Deploy', 'Approved')"
                ),
                {"id": uuid.uuid4()},
            )
            conn.commit()

            command.upgrade(config, "0043_access_matrix_fields")
            assert not accounts & tables(conn)
            assert "status" not in columns(conn)

            command.downgrade(config, "0042_access_matrix_approver")
            assert accounts <= tables(conn)
            assert "status" in columns(conn)
            # Rows saved without a status stand approved; a new row waits.
            assert (
                conn.scalar(
                    text("SELECT status FROM isms_access_control_matrix_entries")
                )
                == "Approved"
            )
            added = conn.scalar(
                text(
                    "INSERT INTO isms_access_control_matrix_entries (id)"
                    " VALUES (:id) RETURNING status"
                ),
                {"id": uuid.uuid4()},
            )
            assert added == "Pending Approval"
            conn.rollback()
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        engine.dispose()


def test_closing_times_and_failure_starts_are_added_empty():
    url = required_database_url("KEEN_RETENTION_TEST_DATABASE_URL")
    root = Path(__file__).resolve().parents[1]
    engine = create_engine(url)
    schema = "treated_" + uuid.uuid4().hex
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    added = {
        ("risks", "closed_at"),
        ("agent_posture_observations", "attention_since"),
    }

    def present(conn):
        rows = conn.execute(
            text(
                "SELECT table_name, column_name, is_nullable, data_type"
                " FROM information_schema.columns WHERE table_schema = :schema"
                " AND (table_name, column_name) IN"
                " (('risks', 'closed_at'),"
                " ('agent_posture_observations', 'attention_since'))"
            ),
            {"schema": schema},
        )
        return {(r[0], r[1]): (r[2], r[3]) for r in rows}

    try:
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path TO {schema},public"))
            conn.commit()
            config.attributes["connection"] = conn
            command.upgrade(config, "0001_release_schema")
            command.stamp(config, "0002_release_seed")
            command.upgrade(config, "0043_access_matrix_fields")
            assert present(conn) == {}
            command.upgrade(config, "0044_posture_risk_treatment")
            # Nothing is dated yet: earlier closures and failures read as
            # needing attention until they happen again.
            assert present(conn) == dict.fromkeys(
                added, ("YES", "timestamp without time zone")
            )
            command.downgrade(config, "0043_access_matrix_fields")
            assert present(conn) == {}
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        engine.dispose()


def test_interested_parties_recorded_per_framework_are_merged():
    url = required_database_url("KEEN_RETENTION_TEST_DATABASE_URL")
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


def test_stories_take_the_new_record_kinds_and_downgrade_drops_them():
    url = required_database_url("KEEN_RETENTION_TEST_DATABASE_URL")
    root = Path(__file__).resolve().parents[1]
    engine = create_engine(url)
    schema = "story_kinds_" + uuid.uuid4().hex
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    story, event_item = uuid.uuid4(), uuid.uuid4()
    try:
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path TO {schema},public"))
            conn.commit()
            config.attributes["connection"] = conn
            command.upgrade(config, "0001_release_schema")
            command.stamp(config, "0002_release_seed")
            command.upgrade(config, "0016_parties_org_wide")
            run = lambda sql, **params: conn.execute(text(sql), params)  # noqa: E731

            def add_item(item_id, kind):
                run(
                    "INSERT INTO story_items (id, story_id, kind, entity_id) VALUES"
                    " (:id, :story, :kind, :entity)",
                    id=item_id,
                    story=story,
                    kind=kind,
                    entity=uuid.uuid4(),
                )

            run(
                "INSERT INTO stories (id, title, created_at, updated_at) VALUES"
                " (:id, 'Backup failure', now(), now())",
                id=story,
            )
            add_item(event_item, "event")
            conn.commit()
            with pytest.raises(Exception, match="ck_story_item_kind"):
                add_item(uuid.uuid4(), "meeting")
            conn.rollback()

            command.upgrade(config, "0017_story_records")
            new_items = {
                kind: uuid.uuid4()
                for kind in (
                    "swot",
                    "person",
                    "vendor",
                    "effectiveness_measure",
                    "meeting",
                )
            }
            for kind, item_id in new_items.items():
                add_item(item_id, kind)
            run(
                "INSERT INTO story_links (id, story_id, from_item_id, to_item_id)"
                " VALUES (:id, :story, :a, :b)",
                id=uuid.uuid4(),
                story=story,
                a=event_item,
                b=new_items["meeting"],
            )
            conn.commit()
            with pytest.raises(Exception, match="ck_story_item_kind"):
                add_item(uuid.uuid4(), "books")
            conn.rollback()

            command.downgrade(config, "0016_parties_org_wide")
            assert conn.execute(text("SELECT id, kind FROM story_items")).all() == [
                (event_item, "event")
            ]
            assert conn.scalar(text("SELECT count(*) FROM story_links")) == 0
            with pytest.raises(Exception, match="ck_story_item_kind"):
                add_item(uuid.uuid4(), "swot")
            conn.rollback()
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        engine.dispose()


def test_seeded_text_names_keen_and_downgrade_restores_it(monkeypatch):
    url = required_database_url("KEEN_RETENTION_TEST_DATABASE_URL")
    root = Path(__file__).resolve().parents[1]
    engine = create_engine(url)
    schema = "seed_wording_" + uuid.uuid4().hex
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
    columns = (
        ("frameworks", "slug", "name"),
        ("frameworks", "slug", "description"),
        ("control_items", "id", "metadata"),
        ("framework_clauses", "id", "metadata"),
        ("cross_framework_control_links", "id", "rationale"),
    )

    def seeded_text(conn):
        return {
            f"{table}.{column}": dict(
                conn.execute(text(f"SELECT {key}::text, {column}::text FROM {table}"))
                .tuples()
                .all()
            )
            for table, key, column in columns
        }

    # The product name in capitals, as a word; KEEN-AF:1.0 is an identifier.
    capitals = re.compile(r"(?<![\w-])KEEN(?![\w-])")
    own_wording = "Our KEEN controls, as the organisation’s auditors described them"
    try:
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path TO {schema},public"))
            conn.commit()
            config.attributes["connection"] = conn
            command.upgrade(config, "0017_story_records")
            conn.execute(
                text("UPDATE frameworks SET description = :d WHERE slug = 'soc2_tsc'"),
                {"d": own_wording},
            )
            conn.commit()
            before = seeded_text(conn)
            assert (
                before["frameworks.name"]["KEEN-AF:1.0"] == "KEEN Assurance Framework"
            )

            command.upgrade(config, "0018_keen_seed_wording")
            after = seeded_text(conn)
            descriptions = after["frameworks.description"]
            assert descriptions.pop("soc2_tsc") == own_wording
            for name, values in after.items():
                for key, value in values.items():
                    assert not capitals.search(value or ""), (name, key, value)
                    assert not re.search("[‘’]", value or ""), (name, key, value)
            assert after["frameworks.name"]["KEEN-AF:1.0"] == "Keen Assurance Framework"
            assert "based on Vanta's controls" in descriptions["KEEN-AF:1.0"]
            assert "Coverage is OSA's assessment" in descriptions["iso_27001_2022"]
            changed = {
                name: {k for k, v in values.items() if before[name][k] != v}
                for name, values in after.items()
            }
            assert len(changed["cross_framework_control_links.rationale"]) == 312
            assert all(
                after["cross_framework_control_links.rationale"][k].startswith(
                    "Keen starter crosswalk ["
                )
                for k in changed["cross_framework_control_links.rationale"]
            )
            control_metadata = after["control_items.metadata"]
            keen_af = conn.scalars(
                text(
                    "SELECT id::text FROM control_items WHERE framework_slug = 'KEEN-AF:1.0'"
                )
            ).all()
            assert len(keen_af) == 247
            for key in keen_af:
                document = json.loads(control_metadata[key])
                assert document["attribution"].endswith("Adapted by Keen.")
                assert document["donation"].startswith("Auditor-donated Keen ")
            editorial = [
                json.loads(value)
                for value in after["framework_clauses.metadata"].values()
                if '"Keen editorial summaries"' in value
            ]
            assert len(editorial) == 76
            assert sum("organisation's" in m["description"] for m in editorial) == 6

            command.downgrade(config, "0017_story_records")
            assert seeded_text(conn) == before
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        engine.dispose()


def test_dashboard_query_migrations_install_and_remove_cleanly():
    url = required_database_url("KEEN_RETENTION_TEST_DATABASE_URL")
    root = Path(__file__).resolve().parents[1]
    engine = create_engine(url)
    schema = "dashboard_" + uuid.uuid4().hex
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))

    def installed(conn):
        index = conn.scalar(
            text(
                "SELECT indexdef FROM pg_indexes WHERE schemaname=:schema "
                "AND indexname='ix_evidence_purge_jobs_manual_status'"
            ),
            {"schema": schema},
        )
        statistics = set(
            conn.scalars(
                text(
                    "SELECT s.stxname FROM pg_statistic_ext s "
                    "JOIN pg_namespace n ON n.oid=s.stxnamespace WHERE n.nspname=:schema"
                ),
                {"schema": schema},
            )
        )
        options = set(
            conn.scalars(
                text(
                    "SELECT c.relname || ':' || o FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "CROSS JOIN unnest(c.reloptions) o "
                    "WHERE n.nspname=:schema AND c.relname IN ('events','mappings')"
                ),
                {"schema": schema},
            )
        )
        return index, statistics, options

    def retention_upkeep(conn):
        """The automatic purge-job index and the mapping-removal trigger's level."""
        automatic = conn.scalar(
            text(
                "SELECT indexdef FROM pg_indexes WHERE schemaname=:schema "
                "AND indexname='ix_evidence_purge_jobs_automatic_status'"
            ),
            {"schema": schema},
        )
        per_statement = conn.scalar(
            text(
                "SELECT t.tgtype & 1 = 0 FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname=:schema AND c.relname='mappings' "
                "AND t.tgname='trg_control_evidence_stats_mappings_delete'"
            ),
            {"schema": schema},
        )
        return automatic, per_statement

    def theme_default(conn):
        return conn.scalar(
            text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_schema=:schema AND table_name='users' "
                "AND column_name='pref_theme'"
            ),
            {"schema": schema},
        )

    def retention_time_index(conn):
        return conn.scalar(
            text(
                "SELECT indexdef FROM pg_indexes WHERE schemaname=:schema "
                "AND indexname='ix_events_retention_time'"
            ),
            {"schema": schema},
        )

    try:
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path TO {schema},public"))
            conn.commit()
            config.attributes["connection"] = conn
            command.upgrade(config, "0001_release_schema")
            # The catalogue seed is exercised by the release-baseline test.
            command.stamp(config, "0002_release_seed")
            command.upgrade(config, "head")
            index, statistics, options = installed(conn)
            assert index is not None and "WHERE (NOT automatic)" in index
            assert statistics == {"st_events_day_bucket", "st_events_hour_bucket"}
            assert options == {
                "events:autovacuum_vacuum_insert_scale_factor=0.02",
                "mappings:autovacuum_vacuum_insert_scale_factor=0.02",
            }
            automatic, per_statement = retention_upkeep(conn)
            assert "WHERE automatic" in automatic and per_statement is True
            assert theme_default(conn) == "'ink'::character varying"
            # Retention reads the newest events in the order it counts them.
            assert 'LEAST("timestamp", created_at), id' in retention_time_index(conn)
            command.downgrade(config, "0047_affected_links")
            assert retention_time_index(conn) is None
            command.downgrade(config, "0020_theme_ink")
            assert installed(conn) == (None, set(), set())
            assert retention_upkeep(conn) == (None, False)
            assert theme_default(conn) == "'sand'::character varying"
            command.upgrade(config, "head")
            assert retention_time_index(conn) is not None
            assert installed(conn)[1:] == (statistics, options)
            assert retention_upkeep(conn)[1] is True
            assert theme_default(conn) == "'ink'::character varying"
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        engine.dispose()


def test_audit_trail_permission_is_removed_and_its_holders_refresh():
    url = required_database_url("KEEN_RETENTION_TEST_DATABASE_URL")
    root = Path(__file__).resolve().parents[1]
    engine = create_engine(url)
    schema = "audittrail_" + uuid.uuid4().hex
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))

    def versions(conn):
        rows = conn.execute(text("SELECT username, authz_version FROM users")).all()
        return {username: version for username, version in rows}

    def description(conn, code):
        return conn.scalar(
            text("SELECT description FROM permissions WHERE code = :code"),
            {"code": code},
        )

    try:
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path TO {schema},public"))
            conn.commit()
            config.attributes["connection"] = conn
            command.upgrade(config, "0001_release_schema")
            command.stamp(config, "0002_release_seed")
            command.upgrade(config, "0039_measure_submission_secrets")
            ids = {
                name: uuid.uuid4()
                for name in ("trail", "manage", "direct", "member", "bystander")
            }
            for code, key, text_ in (
                ("audittrail.read", "trail", "Audit trail"),
                ("audits.manage", "manage", "Sampling requires source read access."),
            ):
                conn.execute(
                    text(
                        "INSERT INTO permissions (id, code, description, created_at)"
                        " VALUES (:id, :code, :d, now())"
                    ),
                    {"id": ids[key], "code": code, "d": text_},
                )
            for name in ("direct", "member", "bystander"):
                conn.execute(
                    text(
                        "INSERT INTO users (id, username, password_hash, created_at,"
                        " updated_at, pref_auto_apply_filters, pref_source_colors,"
                        " pref_landing_page, pref_date_format, authz_version)"
                        " VALUES (:id, :name, 'unused', now(), now(), false, '{}',"
                        " '/', 'dmy', 3)"
                    ),
                    {"id": ids[name], "name": name},
                )
            group = uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO groups (id, name, created_at)"
                    " VALUES (:id, 'auditors', now())"
                ),
                {"id": group},
            )
            conn.execute(
                text(
                    "INSERT INTO group_permissions (group_id, permission_id)"
                    " VALUES (:g, :p)"
                ),
                {"g": group, "p": ids["trail"]},
            )
            conn.execute(
                text("INSERT INTO user_groups (user_id, group_id) VALUES (:u, :g)"),
                {"u": ids["member"], "g": group},
            )
            for user, permission in (
                (ids["direct"], ids["trail"]),
                (ids["bystander"], ids["manage"]),
            ):
                conn.execute(
                    text(
                        "INSERT INTO user_permissions (user_id, permission_id)"
                        " VALUES (:u, :p)"
                    ),
                    {"u": user, "p": permission},
                )
            conn.commit()

            command.upgrade(config, "0040_remove_audittrail_read")
            assert description(conn, "audittrail.read") is None
            for table in ("user_permissions", "group_permissions"):
                assert (
                    conn.scalar(
                        text(f"SELECT count(*) FROM {table} WHERE permission_id = :p"),
                        {"p": ids["trail"]},
                    )
                    == 0
                )
            # Holders, directly or through a group, refresh their snapshots;
            # other accounts and assignments are untouched.
            assert versions(conn) == {"direct": 4, "member": 4, "bystander": 3}
            assert conn.scalar(text("SELECT count(*) FROM user_groups")) == 1
            assert conn.scalar(text("SELECT count(*) FROM user_permissions")) == 1
            assert "Includes events.read and isms.read" in description(
                conn, "audits.manage"
            )
            assert "Includes events.read and isms.read" in description(
                conn, "audits.admin"
            )

            command.downgrade(config, "0039_measure_submission_secrets")
            assert description(conn, "audittrail.read").startswith("Can view")
            assert description(conn, "audits.manage").endswith(
                "Sampling requires source read access."
            )
            assert description(conn, "audits.admin") == (
                "Edit and delete any audit or scheduled audit template."
            )
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        engine.dispose()


def test_interested_parties_are_removed_and_affected_links_added():
    url = required_database_url("KEEN_RETENTION_TEST_DATABASE_URL")
    root = Path(__file__).resolve().parents[1]
    engine = create_engine(url)
    schema = "affected_" + uuid.uuid4().hex
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))

    def tables(conn):
        return set(
            conn.scalars(
                text(
                    "SELECT table_name FROM information_schema.tables"
                    " WHERE table_schema = :schema"
                ),
                {"schema": schema},
            )
        )

    def run(conn, sql, **params):
        return conn.execute(text(sql), params)

    def party_schema(conn):
        """The register's columns, constraints and indexes, without the schema name."""
        named = {"schema": schema, "tables": sorted(party_tables)}
        columns = run(
            conn,
            "SELECT table_name, column_name, data_type, character_maximum_length,"
            " is_nullable, column_default FROM information_schema.columns"
            " WHERE table_schema = :schema AND table_name = ANY(:tables)",
            **named,
        ).all()
        constraints = run(
            conn,
            "SELECT c.conrelid::regclass::text, c.conname,"
            " pg_get_constraintdef(c.oid) FROM pg_constraint c"
            " JOIN pg_namespace n ON n.oid = c.connamespace"
            " WHERE n.nspname = :schema"
            " AND c.conrelid::regclass::text = ANY(:tables)",
            **named,
        ).all()
        indexes = run(
            conn,
            "SELECT tablename, indexname, replace(indexdef, :prefix, '')"
            " FROM pg_indexes WHERE schemaname = :schema AND tablename = ANY(:tables)",
            prefix=schema + ".",
            **named,
        ).all()
        return set(columns), set(constraints), set(indexes)

    party_tables = {
        "risk_interested_parties",
        "interested_party_communications",
        "interested_party_control_links",
        "interested_parties",
        "interested_party_names",
        "interested_party_natures",
    }
    ids = {
        key: uuid.uuid4()
        for key in (
            "holder",
            "member",
            "bystander",
            "group",
            "read",
            "manage",
            "other",
            "name",
            "nature",
            "party",
            "category",
            "subcategory",
            "asset",
            "risk",
            "audit",
            "client",
            "grant",
        )
    }
    try:
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path TO {schema},public"))
            conn.commit()
            config.attributes["connection"] = conn
            command.upgrade(config, "0001_release_schema")
            command.stamp(config, "0002_release_seed")
            command.upgrade(config, "0046_asset_owner_only")
            assert party_tables <= tables(conn)
            before = party_schema(conn)
            assert all(before)
            for key, code in (
                ("read", "interested_parties.read"),
                ("manage", "interested_parties.manage"),
                ("other", "risk.read"),
            ):
                run(
                    conn,
                    "INSERT INTO permissions (id, code, created_at)"
                    " VALUES (:id, :code, now())",
                    id=ids[key],
                    code=code,
                )
            for name, landing in (
                ("holder", "/interested_parties.html?framework=x"),
                ("member", "/interested_party.html"),
                ("bystander", "/risks.html"),
            ):
                run(
                    conn,
                    "INSERT INTO users (id, username, password_hash, created_at,"
                    " updated_at, pref_auto_apply_filters, pref_source_colors,"
                    " pref_landing_page, pref_date_format, authz_version)"
                    " VALUES (:id, :name, 'unused', now(), now(), false, '{}',"
                    " :landing, 'dmy', 3)",
                    id=ids[name],
                    name=name,
                    landing=landing,
                )
            run(
                conn,
                "INSERT INTO user_permissions (user_id, permission_id)"
                " VALUES (:u, :p), (:b, :o)",
                u=ids["holder"],
                p=ids["read"],
                b=ids["bystander"],
                o=ids["other"],
            )
            run(
                conn,
                "INSERT INTO groups (id, name, created_at) VALUES (:id, 'parties', now())",
                id=ids["group"],
            )
            run(
                conn,
                "INSERT INTO group_permissions (group_id, permission_id)"
                " VALUES (:g, :p)",
                g=ids["group"],
                p=ids["manage"],
            )
            run(
                conn,
                "INSERT INTO user_groups (user_id, group_id) VALUES (:u, :g)",
                u=ids["member"],
                g=ids["group"],
            )
            run(
                conn,
                "INSERT INTO interested_party_names (id, name) VALUES (:id, 'Customers')",
                id=ids["name"],
            )
            run(
                conn,
                "INSERT INTO interested_party_natures (id, name)"
                " VALUES (:id, 'External')",
                id=ids["nature"],
            )
            run(
                conn,
                "INSERT INTO interested_parties (id, name_id, nature_id, note,"
                " created_at, updated_at) VALUES (:id, :n, :t, '', now(), now())",
                id=ids["party"],
                n=ids["name"],
                t=ids["nature"],
            )
            run(
                conn,
                "INSERT INTO risk_categories (id, name) VALUES (:id, 'IT')",
                id=ids["category"],
            )
            run(
                conn,
                "INSERT INTO risk_asset_subcategories (id, category_id, name)"
                " VALUES (:id, :c, 'Servers')",
                id=ids["subcategory"],
                c=ids["category"],
            )
            run(
                conn,
                "INSERT INTO risk_assets (id, name, category_id, subcategory_id)"
                " VALUES (:id, 'Platform', :c, :s)",
                id=ids["asset"],
                c=ids["category"],
                s=ids["subcategory"],
            )
            run(
                conn,
                "INSERT INTO risks (id, asset_id) VALUES (:id, :a)",
                id=ids["risk"],
                a=ids["asset"],
            )
            run(
                conn,
                "INSERT INTO risk_interested_parties (id, risk_id, interested_party_id,"
                " created_at) VALUES (:id, :r, :p, now())",
                id=uuid.uuid4(),
                r=ids["risk"],
                p=ids["party"],
            )
            run(
                conn,
                "INSERT INTO audits (id, title, framework_slug, created_at,"
                " updated_at, schedule_date_rule) VALUES"
                " (:id, 'Audit', 'iso_27001_2022', now(), now(), 'exact')",
                id=ids["audit"],
            )
            for entity_type, entity_id in (
                ("interested_party", ids["party"]),
                ("risk", ids["risk"]),
            ):
                run(
                    conn,
                    "INSERT INTO audit_evidence (id, audit_id, added_at,"
                    " entity_type, entity_id) VALUES (:id, :a, now(), :t, :e)",
                    id=uuid.uuid4(),
                    a=ids["audit"],
                    t=entity_type,
                    e=entity_id,
                )
                run(
                    conn,
                    "INSERT INTO entity_changelogs (id, entity_type, entity_id,"
                    " action, summary, changes, changed_at) VALUES"
                    " (:id, :t, :e, 'created', 'Created', '[]', now())",
                    id=uuid.uuid4(),
                    t=entity_type,
                    e=entity_id,
                )
            run(
                conn,
                "INSERT INTO managed_configurations (name, document, version,"
                " updated_at) VALUES ('features', CAST(:doc AS jsonb), 4, now())",
                doc=json.dumps({"interested_parties": False, "pestle": False}),
            )
            run(
                conn,
                "INSERT INTO mcp_clients (id, client_id, client_name, info, created_at)"
                " VALUES (:id, 'kmc_x', 'Client', '{}', now())",
                id=ids["client"],
            )
            run(
                conn,
                "INSERT INTO mcp_grants (id, user_id, client_pk, scopes,"
                " session_version, mfa_version, created_at, expires_at) VALUES"
                " (:id, :u, :c, CAST(:scopes AS jsonb), 1, 1, now(), now())",
                id=ids["grant"],
                u=ids["holder"],
                c=ids["client"],
                scopes=json.dumps(["risk.read", "interested_parties.read"]),
            )
            run(
                conn,
                "INSERT INTO mcp_tokens (id, token_hash, grant_id, kind, scopes,"
                " created_at, expires_at) VALUES (:id, 'hash', :g, 'access',"
                " CAST(:scopes AS jsonb), now(), now())",
                id=uuid.uuid4(),
                g=ids["grant"],
                scopes=json.dumps(["interested_parties.read"]),
            )
            conn.commit()

            command.upgrade(config, "0047_affected_links")
            assert not party_tables & tables(conn)
            assert "affected_links" in tables(conn)
            codes = set(run(conn, "SELECT code FROM permissions").scalars())
            assert "risk.read" in codes
            assert not {c for c in codes if c.startswith("interested_parties.")}
            # Holders, directly or through a group, refresh their snapshots.
            assert dict(
                run(conn, "SELECT username, authz_version FROM users").all()
            ) == {"holder": 4, "member": 4, "bystander": 3}
            assert dict(
                run(conn, "SELECT username, pref_landing_page FROM users").all()
            ) == {"holder": "/", "member": "/", "bystander": "/risks.html"}
            assert run(conn, "SELECT count(*) FROM group_permissions").scalar() == 0
            assert run(conn, "SELECT count(*) FROM user_permissions").scalar() == 1
            assert run(
                conn, "SELECT document FROM managed_configurations"
            ).scalar() == {"pestle": False}
            assert run(conn, "SELECT scopes FROM mcp_grants").scalar() == ["risk.read"]
            assert run(conn, "SELECT scopes FROM mcp_tokens").scalar() == []
            for table in ("audit_evidence", "entity_changelogs"):
                assert run(
                    conn, f"SELECT entity_type FROM {table}"
                ).scalars().all() == ["risk"]

            # A link names one owner and one target, and goes with either.
            def link(**columns):
                names = ", ".join(columns)
                values = ", ".join(f":{name}" for name in columns)
                run(
                    conn,
                    f"INSERT INTO affected_links (id, created_at, {names})"
                    f" VALUES (:id, now(), {values})",
                    id=uuid.uuid4(),
                    **columns,
                )

            link(risk_id=ids["risk"], asset_id=ids["asset"])
            conn.commit()
            for columns, constraint in (
                ({"asset_id": ids["asset"]}, "ck_affected_links_one_owner"),
                ({"risk_id": ids["risk"]}, "ck_affected_links_one_target"),
            ):
                with pytest.raises(Exception, match=constraint):
                    link(**columns)
                conn.rollback()
            run(conn, "DELETE FROM risks")
            assert run(conn, "SELECT count(*) FROM affected_links").scalar() == 0
            conn.commit()

            # A downgrade brings back the register's schema and permissions,
            # empty and held by nobody, and the upgrade runs again from there.
            command.downgrade(config, "0046_asset_owner_only")
            assert "affected_links" not in tables(conn)
            assert party_schema(conn) == before
            assert run(conn, "SELECT count(*) FROM interested_parties").scalar() == 0
            assert set(
                run(
                    conn,
                    "SELECT code FROM permissions WHERE code LIKE 'interested_parties.%'",
                ).scalars()
            ) == {"interested_parties.read", "interested_parties.manage"}
            assert run(conn, "SELECT count(*) FROM group_permissions").scalar() == 0
            assert run(conn, "SELECT count(*) FROM user_permissions").scalar() == 1
            command.upgrade(config, "0047_affected_links")
            assert not party_tables & tables(conn)
            assert "affected_links" in tables(conn)
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        engine.dispose()


def test_starting_groups_arrive_and_the_reserved_permission_code_goes():
    url = required_database_url("KEEN_RETENTION_TEST_DATABASE_URL")
    root = Path(__file__).resolve().parents[1]
    engine = create_engine(url)
    schema = "groups_" + uuid.uuid4().hex
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))

    def run(conn, sql, **params):
        return conn.execute(text(sql), params)

    def groups(conn):
        rows = run(
            conn,
            "SELECT g.name, g.role, array_agg(p.code ORDER BY p.code)"
            " FILTER (WHERE p.code IS NOT NULL)"
            " FROM groups g LEFT JOIN group_permissions gp ON gp.group_id = g.id"
            " LEFT JOIN permissions p ON p.id = gp.permission_id"
            " GROUP BY g.name, g.role",
        ).all()
        return {name: (role, codes or []) for name, role, codes in rows}

    codes = (
        "events.read risk.read risk.manage isms.read isms.manage pestle.read"
        " pestle.manage audits.read audits.manage audits.admin incident.create"
        " incident.delete question.create question.delete stories.read"
        " stories.manage stories.admin quiz.read quiz.manage"
    ).split()
    ids = {key: uuid.uuid4() for key in ("star", "direct", "member", "other", "own")}
    try:
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path TO {schema},public"))
            conn.commit()
            config.attributes["connection"] = conn
            command.upgrade(config, "0001_release_schema")
            command.stamp(config, "0002_release_seed")
            command.upgrade(config, "0048_events_retention_time")
            run(conn, "DELETE FROM permissions")
            # quiz.read is left out: a group gets the permissions that exist.
            for code in [c for c in codes if c != "quiz.read"]:
                run(
                    conn,
                    "INSERT INTO permissions (id, code, created_at)"
                    " VALUES (:id, :code, now())",
                    id=uuid.uuid4(),
                    code=code,
                )
            # A permission stored under the reserved code before it was
            # refused, held directly and through a group.
            run(
                conn,
                "INSERT INTO permissions (id, code, created_at) VALUES (:id, '*', now())",
                id=ids["star"],
            )
            for name in ("direct", "member", "other"):
                run(
                    conn,
                    "INSERT INTO users (id, username, password_hash, created_at,"
                    " updated_at, pref_auto_apply_filters, pref_source_colors,"
                    " pref_landing_page, pref_date_format, authz_version)"
                    " VALUES (:id, :name, 'unused', now(), now(), false, '{}',"
                    " '/', 'dmy', 3)",
                    id=ids[name],
                    name=name,
                )
            # The installation's own group already has one of the names.
            run(
                conn,
                "INSERT INTO groups (id, name, created_at) VALUES (:id, 'staff', now())",
                id=ids["own"],
            )
            run(
                conn,
                "INSERT INTO group_permissions (group_id, permission_id) VALUES (:g, :p)",
                g=ids["own"],
                p=ids["star"],
            )
            run(
                conn,
                "INSERT INTO user_groups (user_id, group_id) VALUES (:u, :g)",
                u=ids["member"],
                g=ids["own"],
            )
            run(
                conn,
                "INSERT INTO user_permissions (user_id, permission_id) VALUES (:u, :p)",
                u=ids["direct"],
                p=ids["star"],
            )
            conn.commit()

            command.upgrade(config, "0049_default_groups")

            assert (
                run(conn, "SELECT count(*) FROM permissions WHERE code = '*'").scalar()
                == 0
            )
            assert run(conn, "SELECT count(*) FROM user_permissions").scalar() == 0
            assert dict(
                run(conn, "SELECT username, authz_version FROM users").all()
            ) == {
                "direct": 4,
                "member": 4,
                "other": 3,
            }
            assert groups(conn) == {
                # The existing group keeps its name, members and (now no) permissions.
                "staff": (None, []),
                "Internal auditors": (
                    None,
                    [
                        "audits.admin",
                        "events.read",
                        "incident.create",
                        "incident.delete",
                        "isms.manage",
                        "pestle.manage",
                        "question.create",
                        "question.delete",
                        "quiz.manage",
                        "risk.manage",
                        "stories.admin",
                    ],
                ),
                "External auditors": (
                    None,
                    [
                        "audits.manage",
                        "events.read",
                        "isms.read",
                        "pestle.read",
                        "risk.read",
                    ],
                ),
            }
            assert run(conn, "SELECT count(*) FROM user_groups").scalar() == 1
            descriptions = dict(run(conn, "SELECT name, description FROM groups").all())
            assert descriptions["staff"] is None
            assert descriptions["External auditors"].endswith("Change nothing else.")
            # No starting group holds a permission that changes anything beyond
            # what its description says: the external auditors' only write is audits.
            held = set(groups(conn)["External auditors"][1])
            assert {c for c in held if c.split(".")[1] not in ("read",)} == {
                "audits.manage"
            }

            # A group in use stays on downgrade; one nobody joined goes.
            run(
                conn,
                "INSERT INTO user_groups (user_id, group_id)"
                " SELECT :u, id FROM groups WHERE name = 'Internal auditors'",
                u=ids["other"],
            )
            conn.commit()
            command.downgrade(config, "0048_events_retention_time")
            assert set(groups(conn)) == {"staff", "Internal auditors"}
            # Upgrading again adds what is missing and repeats nothing.
            command.upgrade(config, "0049_default_groups")
            assert set(groups(conn)) == {
                "staff",
                "Internal auditors",
                "External auditors",
            }
            assert len(groups(conn)["Internal auditors"][1]) == 11
            # Without a group of that name, Staff arrives too.
            command.downgrade(config, "0048_events_retention_time")
            run(conn, "DELETE FROM groups WHERE name = 'staff'")
            conn.commit()
            command.upgrade(config, "0049_default_groups")
            assert groups(conn)["Staff"] == (None, ["events.read", "isms.read"])
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        engine.dispose()
