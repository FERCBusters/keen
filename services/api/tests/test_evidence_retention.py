"""Run with a disposable KEEN_RETENTION_TEST_DATABASE_URL (PostgreSQL).

Uses an isolated schema and actual retention SQL/transactions. Storage calls are
mocked except for temporary local files. No production database is required.
"""

import importlib.util
import json
import os
import tempfile
import unittest
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.db import models
from app.db.session import Base
from app.services import evidence_retention as r
from app.storage import s3
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from tests.db_helpers import schema_engine


class StorageTests(unittest.TestCase):
    def test_local_cleanup_and_traversal(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch.object(s3.settings, "artifact_local_dir", td),
        ):
            p = Path(td) / "events" / "test"
            p.parent.mkdir()
            p.write_bytes(b"evidence")
            s3.delete_stored_object("local://local/events/test")
            self.assertFalse(p.exists())
            s3.delete_stored_object("local://local/events/test")  # idempotent
            with self.assertRaises(ValueError):
                s3.delete_stored_object("local://local/../secret")

    def test_s3_exact_version_and_no_bypass(self):
        from unittest.mock import Mock

        client = Mock()
        with (
            patch.object(s3, "_client", return_value=client),
            patch.object(s3.settings, "s3_bucket", "evidence"),
        ):
            s3.delete_stored_object("s3://evidence/events/a%20b?versionId=v%2F1")
        client.delete_object.assert_called_once_with(
            Bucket="evidence", Key="events/a b", VersionId="v/1"
        )
        client.get_bucket_versioning.assert_not_called()

    def test_unpinned_versioned_object_is_not_hidden(self):
        from unittest.mock import Mock

        client = Mock()
        client.get_bucket_versioning.return_value = {"Status": "Enabled"}
        with (
            patch.object(s3, "_client", return_value=client),
            patch.object(s3.settings, "s3_bucket", "evidence"),
        ):
            with self.assertRaises(s3.UnpinnedVersionedObject):
                s3.delete_stored_object("s3://evidence/old")
        client.delete_object.assert_not_called()

    def test_unversioned_and_wrong_bucket(self):
        from unittest.mock import Mock

        client = Mock()
        client.get_bucket_versioning.return_value = {}
        with (
            patch.object(s3, "_client", return_value=client),
            patch.object(s3.settings, "s3_bucket", "evidence"),
        ):
            s3.delete_stored_object("s3://evidence/old")
            with self.assertRaises(ValueError):
                s3.delete_stored_object("s3://another-bucket/old")
        client.delete_object.assert_called_once_with(Bucket="evidence", Key="old")


class WorkerLoopTests(unittest.TestCase):
    """The retention entry point and its time budget, without a database."""

    def test_database_failure_is_recorded_and_storage_cleanup_continues(self):
        from unittest.mock import MagicMock

        session = MagicMock()
        session.__enter__.return_value = session
        session.__exit__.return_value = False
        with (
            patch.object(r, "SessionLocal", return_value=session),
            patch.object(r, "database_batch", side_effect=RuntimeError("lock lost")),
            patch.object(r, "storage_batch", return_value=True) as storage,
        ):
            self.assertTrue(r.run_retention())
        storage.assert_called_once()
        session.commit.assert_called_once()
        (statement, params), _ = session.execute.call_args
        self.assertIn("evidence_purge_jobs", str(statement))
        self.assertEqual(
            params["error"], "Database cleanup failed (RuntimeError); worker will retry"
        )
        self.assertNotIn("lock lost", params["error"])

    def test_results_of_both_batches_decide_whether_more_work_remains(self):
        with (
            patch.object(r, "database_batch", return_value=False),
            patch.object(r, "storage_batch", return_value=False),
        ):
            self.assertFalse(r.run_retention())
        with (
            patch.object(r, "database_batch", return_value=True),
            patch.object(r, "storage_batch", return_value=False),
        ):
            self.assertTrue(r.run_retention())

    def test_storage_batch_yields_after_its_time_budget(self):
        from unittest.mock import MagicMock

        with (
            patch.object(r.time, "monotonic", side_effect=[0.0, 10.5]),
            patch.object(r, "SessionLocal", MagicMock()) as sessions,
        ):
            self.assertTrue(r.storage_batch())
        sessions.assert_not_called()


class RetentionDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The optional bridge is used by the bundle's WASM PostgreSQL validation.
        bridge = os.environ.get("KEEN_RETENTION_TEST_BRIDGE")
        url = os.environ.get("KEEN_RETENTION_TEST_DATABASE_URL")
        cls.engine = None
        if bridge:
            spec = importlib.util.spec_from_file_location("bridge", bridge)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            cls.session = module.Session
        elif url:
            cls.schema = "retention_test_" + uuid.uuid4().hex
            cls.engine = create_engine(url)
            with cls.engine.begin() as c:
                c.execute(text(f"CREATE SCHEMA {cls.schema}"))
            cls.engine.dispose()
            cls.engine = schema_engine(url, cls.schema)
            Base.metadata.create_all(
                cls.engine,
                tables=[
                    t
                    for t in Base.metadata.sorted_tables
                    if t.name
                    not in {
                        "evidence_retention_policy",
                        "evidence_purge_jobs",
                        "evidence_object_cleanup",
                        "audit_event_retention_holds",
                        "evidence_retention_overrides",
                    }
                ],
            )
            path = (
                Path(__file__).parents[1]
                / "tests/fixtures/historical_migrations/0083_evidence_retention.py"
            )
            spec = importlib.util.spec_from_file_location("retention_migration", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with (
                cls.engine.begin() as c,
                patch.object(
                    module, "op", SimpleNamespace(execute=lambda q: c.execute(text(q)))
                ),
            ):
                module.upgrade()
            spec = importlib.util.spec_from_file_location(
                "override_migration",
                Path(__file__).parents[1]
                / "alembic/versions/0009_retention_overrides.py",
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with (
                cls.engine.begin() as c,
                patch.object(
                    module, "op", SimpleNamespace(execute=lambda q: c.execute(text(q)))
                ),
            ):
                module.upgrade()
            for filename in ["0042_control_evidence_stats.py"]:
                spec = importlib.util.spec_from_file_location(
                    "stats_migration", path.parent / filename
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                with (
                    cls.engine.begin() as c,
                    patch.object(
                        module,
                        "op",
                        SimpleNamespace(
                            execute=lambda q: c.execute(text(q)),
                            create_table=lambda *a, **k: None,
                            create_index=lambda *a, **k: None,
                        ),
                    ),
                ):
                    module.upgrade()
            cls.session = sessionmaker(bind=cls.engine)
        else:
            raise unittest.SkipTest(
                "Set KEEN_RETENTION_TEST_DATABASE_URL to run PostgreSQL integration tests"
            )

    @classmethod
    def tearDownClass(cls):
        if cls.engine:
            with cls.engine.begin() as c:
                c.execute(text(f"DROP SCHEMA {cls.schema} CASCADE"))
            cls.engine.dispose()

    def setUp(self):
        self.mocksession = patch.object(r, "SessionLocal", self.session)
        self.mocksession.start()
        self.addCleanup(self.mocksession.stop)
        self.mockcache = patch.object(r, "clear_stats_caches")
        self.mockcache.start()
        self.addCleanup(self.mockcache.stop)
        with self.session() as db:
            db.execute(
                text(
                    "TRUNCATE events, audits, evidence_purge_jobs,evidence_object_cleanup CASCADE"
                )
            )
            db.execute(
                text("UPDATE evidence_retention_policy SET mode='disabled',value=NULL")
            )
            db.execute(text("DELETE FROM evidence_retention_overrides"))
            db.commit()

    def insert(self, model, **values):
        # Apply the same Python defaults as the ORM, while keeping the tests
        # usable against both libpq PostgreSQL and the validation bridge.
        for c in model.__table__.columns:
            if c.name in values:
                continue
            if c.default is not None:
                arg = c.default.arg
                values[c.name] = arg(None) if callable(arg) else arg
        params = {}
        cols = []
        binds = []
        for k, v in values.items():
            cols.append('"' + k + '"')
            params[k] = json.dumps(v) if isinstance(v, (dict, list)) else v
            binds.append(":" + k)
        with self.session() as db:
            db.execute(
                text(
                    f"INSERT INTO {model.__tablename__} ({','.join(cols)}) VALUES ({','.join(binds)})"
                ),
                params,
            )
            db.commit()
        return values["id"]

    def event(self, days=0, **extra):
        return self.insert(
            models.Event,
            timestamp=r.now() - timedelta(days=days),
            source="test",
            summary="event",
            external_id=uuid.uuid4().hex,
            **extra,
        )

    def audit(self):
        return self.insert(models.Audit, framework_slug="TEST", title="audit")

    def sample(self, a, e):
        return self.insert(models.AuditEvidence, audit_id=a, event_id=e)

    def artifact(self, e, uri="local://local/test", **extra):
        return self.insert(
            models.Artifact,
            event_id=e,
            kind="test",
            storage_uri=uri,
            sha256="0" * 64,
            **extra,
        )

    def sql(self, q, **params):
        with self.session() as db:
            cursor = db.execute(text(q), params)
            result = list(cursor.mappings()) if cursor.returns_rows else []
            db.commit()
            return result

    def job(self, mode="all", value=None):
        with self.session() as db:
            r.enqueue(db, mode, value, False, "test")
            db.commit()

    def left(self):
        return {str(x["id"]) for x in self.sql("SELECT id FROM events")}

    def override(self, scope, key, mode, value=None):
        self.sql(
            "INSERT INTO evidence_retention_overrides (scope,key,mode,value) VALUES (:scope,:key,:mode,:value)",
            scope=scope,
            key=key,
            mode=mode,
            value=value,
        )

    def rule_mapping(self, event, rule, *, method="rule", mapped_by="system"):
        control = self.insert(
            models.ControlItem,
            framework_slug="TEST",
            type="custom",
            ref=uuid.uuid4().hex,
        )
        self.insert(
            models.Mapping,
            event_id=event,
            control_item_id=control,
            method=method,
            mapped_by=mapped_by,
            rationale=f"auto by rule {rule} (TEST)",
        )

    def test_source_override_with_disabled_global_and_audit_hold(self):
        _old, held, recent = self.event(90), self.event(90), self.event(1)
        self.sample(self.audit(), held)
        self.override("source", "test", "age", 30)
        with self.session() as db:
            self.assertEqual(r.preview(db, "disabled", None)["eligible_events"], 1)
        r.database_batch()
        self.assertEqual(self.left(), {str(held), str(recent)})

    def test_rule_beats_source_and_overlapping_rules_all_must_expire(self):
        keep, delete, ordinary = self.event(90), self.event(90), self.event(90)
        for event in (keep, delete):
            self.rule_mapping(event, "short")
        self.rule_mapping(keep, "forever")
        self.override("source", "test", "disabled")
        self.override("rule", "short", "age", 30)
        self.override("rule", "forever", "disabled")
        self.job("age", 1)
        r.database_batch()
        self.assertEqual(self.left(), {str(keep), str(ordinary)})

    def test_rule_count_deduplicates_controls_and_excludes_audit_holds(self):
        old, new, held, other = (
            self.event(90),
            self.event(1),
            self.event(),
            self.event(90),
        )
        for event in (old, new, held):
            self.rule_mapping(event, "counted")
            self.rule_mapping(event, "counted")
        self.sample(self.audit(), held)
        self.override("rule", "counted", "count", 1)
        self.override("source", "test", "disabled")
        self.job("age", 1)
        r.database_batch()
        self.assertEqual(self.left(), {str(new), str(held), str(other)})

    def test_global_count_excludes_overridden_groups(self):
        _old, new, special = self.event(90), self.event(1), self.event()
        self.rule_mapping(special, "saved")
        self.override("rule", "saved", "disabled")
        self.job("count", 1)
        r.database_batch()
        self.assertEqual(self.left(), {str(new), str(special)})

    def test_manual_rationale_does_not_claim_rule_retention(self):
        event = self.event(90)
        self.rule_mapping(event, "saved", method="manual")
        self.override("rule", "saved", "disabled")
        self.job("age", 30)
        r.database_batch()
        self.assertEqual(self.left(), set())

    def test_proposed_preview_and_purge_all(self):
        self.event(90)
        self.override("source", "test", "disabled")
        with self.session() as db:
            self.assertEqual(r.preview(db, "age", 30)["eligible_events"], 0)
            self.assertEqual(
                r.preview(db, "age", 30, overrides=[])["eligible_events"], 1
            )
        self.job()
        r.database_batch()
        self.assertEqual(self.left(), set())

    def test_diary_entries_survive_retention(self):
        self.event(400)
        diary_event = self.insert(
            models.Event,
            timestamp=r.now() - timedelta(days=400),
            source="diary",
            summary="diary",
            external_id=uuid.uuid4().hex,
        )
        with self.session() as db:
            # The ordinary event is eligible; the diary entry is protected in
            # every retention mode, including a blanket purge.
            self.assertEqual(r.preview(db, "age", 30)["eligible_events"], 1)
        self.job(mode="all")
        r.database_batch()
        self.assertEqual(self.left(), {str(diary_event)})

    def test_job_snapshots_overrides(self):
        event = self.event(90)
        self.override("source", "test", "disabled")
        self.job("age", 30)
        self.sql("DELETE FROM evidence_retention_overrides")
        r.database_batch()
        self.assertEqual(self.left(), {str(event)})

    def test_save_cancels_previous_policy_job_and_rejects_stale_editor(self):
        from app.services.errors import DomainError
        from app.services.retention_management import SettingsPayload, set_policy

        self.override("source", "test", "disabled")
        self.job("age", 30)
        with self.session() as db:
            previous = r.policy(db)["updated_at"]
            set_policy(
                payload=SettingsPayload(
                    mode="count", value=2, overrides=[], expected_updated_at=previous
                ),
                db=db,
                user=SimpleNamespace(username="admin"),
            )
        self.assertEqual(
            self.sql("SELECT status FROM evidence_purge_jobs")[0]["status"], "cancelled"
        )
        self.assertEqual(self.sql("SELECT * FROM evidence_retention_overrides"), [])
        with self.session() as db:
            with self.assertRaises(DomainError):
                set_policy(
                    payload=SettingsPayload(
                        mode="disabled", expected_updated_at=previous
                    ),
                    db=db,
                    user=SimpleNamespace(username="admin"),
                )
        self.assertEqual(
            self.sql("SELECT mode FROM evidence_retention_policy")[0]["mode"], "count"
        )

    def test_source_count_is_separate_from_global_and_rule_counts(self):
        _old, new, special = self.event(90), self.event(1), self.event()
        other_old, other_new = self.event(90), self.event()
        self.sql(
            "UPDATE events SET source='other' WHERE id IN (:a,:b)",
            a=other_old,
            b=other_new,
        )
        self.rule_mapping(special, "saved")
        self.override("source", "test", "count", 1)
        self.override("rule", "saved", "disabled")
        self.job("count", 1)
        r.database_batch()
        self.assertEqual(self.left(), {str(new), str(special), str(other_new)})

    def test_disabled_default(self):
        e = self.event(90)
        self.assertFalse(r.database_batch())
        self.assertIn(str(e), self.left())

    def test_age_preserves_audit_and_new_events(self):
        old = self.event(90)
        held = self.event(90)
        new = self.event(1)
        self.sample(self.audit(), held)
        self.artifact(old)
        self.job("age", 30)
        self.assertTrue(r.database_batch())
        self.assertEqual(self.left(), {str(held), str(new)})
        self.assertEqual(len(self.sql("SELECT * FROM evidence_object_cleanup")), 1)
        self.assertFalse(r.database_batch())

    def test_count_keeps_newest_plus_audit_holds(self):
        self.event(90)
        held = self.event(80)
        new = self.event(1)
        newest = self.event()
        self.sample(self.audit(), held)
        self.job("count", 2)
        r.database_batch()
        self.assertEqual(self.left(), {str(held), str(new), str(newest)})

    def test_hold_survives_unsampling_and_other_audit_deletion(self):
        e = self.event()
        a = self.audit()
        b = self.audit()
        self.sample(a, e)
        self.sample(b, e)
        self.sql("DELETE FROM audit_evidence")
        self.sql("DELETE FROM audits WHERE id=:id", id=a)
        self.job()
        r.database_batch()
        self.assertIn(str(e), self.left())
        self.sql("DELETE FROM audits WHERE id=:id", id=b)
        self.job()
        r.database_batch()
        self.assertEqual(self.left(), set())

    def test_hold_enforced_by_foreign_key(self):
        e = self.event()
        self.sample(self.audit(), e)
        with self.assertRaises(Exception):
            self.sql("DELETE FROM events WHERE id=:id", id=e)
        self.assertIn(str(e), self.left())

    def test_all_has_fixed_cutoff(self):
        self.event()
        self.job()
        new = self.event()
        r.database_batch()
        self.assertEqual(self.left(), {str(new)})

    def test_lineage_protects_parent_until_child_removed(self):
        parent = self.event(90)
        child = self.event(1)
        pa = self.artifact(parent, "local://local/parent")
        self.artifact(child, "local://local/child", parent_artifact_id=pa)
        self.sample(self.audit(), child)
        self.job()
        r.database_batch()
        self.assertEqual(self.left(), {str(parent), str(child)})
        self.assertFalse(self.sql("SELECT * FROM evidence_object_cleanup"))

    def test_storage_failure_retries_without_losing_intent(self):
        e = self.event()
        self.artifact(e)
        self.job()
        r.database_batch()
        with patch.object(
            r, "delete_stored_object", side_effect=PermissionError("secret")
        ):
            r.storage_batch()
        row = self.sql("SELECT * FROM evidence_object_cleanup")[0]
        self.assertEqual(row["attempts"], 1)
        self.assertNotIn("secret", row["last_error"])
        self.sql(
            "UPDATE evidence_object_cleanup SET next_attempt_at=:t",
            t=r.now() - timedelta(seconds=1),
        )
        with patch.object(r, "delete_stored_object") as delete:
            r.storage_batch()
            delete.assert_called_once()
        self.assertFalse(self.sql("SELECT * FROM evidence_object_cleanup"))

    def test_shared_storage_is_preserved(self):
        old = self.event(90)
        new = self.event()
        self.artifact(old)
        self.artifact(new)
        self.job("age", 30)
        r.database_batch()
        with patch.object(r, "delete_stored_object") as delete:
            r.storage_batch()
            delete.assert_not_called()
        self.assertEqual(len(self.sql("SELECT * FROM evidence_object_cleanup")), 1)

    def test_transaction_rolls_back_outbox_on_delete_error(self):
        e = self.event()
        self.artifact(e)
        self.job()
        original = r.queue_object

        def interrupted(db, uri):
            original(db, uri)
            raise RuntimeError("interrupted after outbox write")

        with patch.object(r, "queue_object", side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                r.database_batch()
        self.assertIn(str(e), self.left())
        self.assertFalse(self.sql("SELECT * FROM evidence_object_cleanup"))

    def test_measurement_sample_holds_origin_event(self):
        e = self.event()
        a = self.audit()
        m = self.insert(
            models.IsmsEffectivenessMeasure,
            framework_slug="TEST",
            summary="measure",
        )
        metric = self.insert(
            models.IsmsEffectivenessMetricEntry,
            measure_id=m,
            source_event_id=e,
            metric_value=99,
        )
        self.insert(
            models.AuditEvidence,
            audit_id=a,
            entity_type="isms_effectiveness_metric",
            entity_id=metric,
        )
        self.job()
        r.database_batch()
        self.assertIn(str(e), self.left())

    def test_new_measurement_on_sampled_measure_is_protected(self):
        a = self.audit()
        m = self.insert(
            models.IsmsEffectivenessMeasure,
            framework_slug="TEST",
            summary="measure",
        )
        self.insert(
            models.AuditEvidence,
            audit_id=a,
            entity_type="isms_effectiveness_measure",
            entity_id=m,
        )
        e = self.event()
        self.insert(
            models.IsmsEffectivenessMetricEntry,
            measure_id=m,
            source_event_id=e,
            metric_value=99,
        )
        self.job()
        r.database_batch()
        self.assertIn(str(e), self.left())

    def test_control_evidence_counts_follow_deletion(self):
        old = self.event(90)
        held = self.event(90)
        self.sample(self.audit(), held)
        c = self.insert(
            models.ControlItem,
            framework_slug="COUNTERS",
            type="custom",
            ref=uuid.uuid4().hex,
        )
        self.insert(models.Mapping, event_id=old, control_item_id=c, method="manual")
        self.insert(models.Mapping, event_id=held, control_item_id=c, method="manual")
        self.job()
        r.database_batch()
        self.assertEqual(
            self.sql(
                "SELECT evidence_count FROM control_evidence_stats WHERE control_item_id=:id",
                id=c,
            )[0]["evidence_count"],
            1,
        )

    def test_only_one_active_job_and_bounded_batches(self):
        with self.session() as db:
            one, created = r.enqueue(db, "all", None, False, "test")
            two, second_created = r.enqueue(db, "age", 5, True, "scheduler")
            self.assertTrue(created)
            self.assertFalse(second_created)
            self.assertEqual(one, two)
            db.commit()
        # New events are outside the first job's cutoff; create a fresh job.
        self.sql("UPDATE evidence_purge_jobs SET status='cancelled'")
        self.event()
        self.event()
        self.event()
        self.job()
        with patch.object(r, "BATCH_SIZE", 2):
            self.assertTrue(r.database_batch())
            self.assertEqual(len(self.left()), 1)
            self.assertTrue(r.database_batch())
            self.assertFalse(r.database_batch())


class PolicyTests(unittest.TestCase):
    def test_policy_validation(self):
        from app.services.retention_management import PolicyPayload, PurgePayload
        from pydantic import ValidationError

        for payload in [
            dict(mode="age", value=0),
            dict(mode="count", value=-1),
            dict(mode="count", value=True),
            dict(mode="age", value=36501),
            dict(mode="age"),
            dict(mode="disabled", value=10),
        ]:
            with self.assertRaises(ValidationError):
                PolicyPayload(**payload)
        self.assertEqual(PolicyPayload(mode="count", value=100).value, 100)
        with self.assertRaises(ValidationError):
            PurgePayload(mode="all", confirmation="yes")

    def test_override_validation(self):
        from app.services.retention_management import SettingsPayload
        from pydantic import ValidationError

        for override in [
            {"scope": "connection", "key": "test", "mode": "disabled"},
            {"scope": "source", "key": "", "mode": "disabled"},
            {"scope": "rule", "key": " test ", "mode": "disabled"},
            {"scope": "rule", "key": "test", "mode": "age", "value": 36501},
        ]:
            with self.assertRaises(ValidationError):
                SettingsPayload(overrides=[override])
        entry = {"scope": "rule", "key": "test", "mode": "disabled"}
        with self.assertRaises(ValidationError):
            SettingsPayload(overrides=[entry, entry])
        self.assertIsNone(SettingsPayload().overrides)
        self.assertEqual(SettingsPayload(overrides=[]).overrides, [])

    def test_routes_require_administrator(self):
        from app.api.routes.evidence_retention import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        cases = [
            ("GET", "", None),
            ("PUT", "", {"mode": "disabled"}),
            ("POST", "/preview", {"mode": "count", "value": 5}),
            ("POST", "/preview-all", {}),
            ("POST", "/purge", {"mode": "all", "confirmation": "PURGE EVIDENCE"}),
            ("POST", "/cancel", {}),
            ("POST", "/retry-storage", {}),
        ]
        for method, path, body in cases:
            response = client.request(
                method, "/v1/admin/evidence-retention" + path, json=body
            )
            self.assertIn(response.status_code, (401, 403), response.text)


if __name__ == "__main__":
    unittest.main()
