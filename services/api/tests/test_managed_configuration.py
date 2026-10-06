"""Focused invariants for managed source configuration and rule authoring."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

from app.api.routes.managed_configurations import _safe_document, _save, _validate_connector, _validate_regex
from app.core.managed_configuration import load_document
from app.mapping.rules import evaluate_by_framework, parse_rules
from app.mapping.collector import collector_id, event_collector, collector_pointer_filter
from app.core.evidence_migration import unify


class ManagedConfigurationTests(unittest.TestCase):
    def test_upgrade_keeps_broad_rule_and_converts_bookstack_page(self):
        documents = {
            "jenkins": {"jobs": [{"name": "release", "label": "prod", "kind": "deployment"}]},
            "rules": {"frameworks": {"ISO27001:2022": [
                {"id": "deployment", "when": {"source": "jenkins", "action": "deployment", "system": "prod"},
                 "map_to": ["A.8.32"]},
                {"id": "all_builds", "when": {"source": "jenkins", "outcome": "failed"},
                 "map_to": ["A.8.15"], "enabled": False},
            ]}},
            "bookstack": {"pages": [{"match": {"book": {"slug": "policies"}, "page_slug": "backup"},
                                     "map_to": ["A.5.1"], "confidence": .7}]},
        }
        converted, counts = unify(documents, "ISO27001:2022")
        entries = {rule["id"]: rule for rule in converted["rules"]["rules"]}
        self.assertEqual(entries["deployment"]["when"]["collector"], collector_id("jenkins", "jobs", "release"))
        self.assertNotIn("collector", entries["all_builds"]["when"])
        self.assertFalse(entries["all_builds"]["enabled"])
        page = entries["bookstack_page_1"]
        self.assertEqual(page["when"]["bookstack_book_slug"], "policies")
        self.assertEqual(page["when"]["bookstack_page_slug"], "backup")
        self.assertEqual(page["confidence"], .7)
        self.assertEqual(converted["bookstack"]["page_mappings"], [])
        self.assertEqual(counts["bookstack_pages"], 1)
        self.assertEqual(unify(converted, "ISO27001:2022")[0], converted)

    def test_collection_identity_distinguishes_queries_with_same_event_fields(self):
        failed = collector_id("loki", "queries", "ossec_login_failed")
        rules = parse_rules({"rules": [{
            "id": "login", "when": {"source": "loki", "system": "ossec",
                                   "action": "ossec_login", "outcome": "failed",
                                   "collector": failed},
            "map_to": [{"framework": "ISO27001:2022", "ref": "A.8.15"},
                       {"framework": "CE", "ref": "logging"}],
        }]})
        event = {"source": "loki", "system": "ossec", "action": "ossec_login",
                 "outcome": "failed", "raw_pointer": {"loki": {"query_name": "ossec_login_failed"}}}
        self.assertEqual(set(evaluate_by_framework(event, rules)), {"ISO27001:2022", "CE"})
        event["raw_pointer"]["loki"]["query_name"] = "other_failed_login"
        self.assertEqual(evaluate_by_framework(event, rules), {})

    def test_collector_identity_for_legacy_event_provenance(self):
        examples = [
            ("jenkins", "jobs", "release", {"jenkins": {"job": "release"}}),
            ("rss", "feeds", "https://example.org/feed", {"rss": {"feed_url": "https://example.org/feed"}}),
            ("cloudwatch_logs", "queries", "alerts", {"cloudwatch_logs": {"name": "alerts"}}),
            ("google_workspace", "streams", "login", {"google_workspace": {"stream": "login"}}),
            ("taiga", "projects", "42", {"taiga": {"project_id": 42}}),
            ("webhooks", "providers", "ci", {"webhook": {"provider": "ci"}}),
            ("github", "repos", "org/repo", {"github": {"endpoint": "repo_events", "owner": "org", "repo": "repo"}}),
            ("bookstack", "selected_pages", "17", {"bookstack": {"page_id": 17}}),
        ]
        for adapter, section, key, pointer in examples:
            with self.subTest(adapter=adapter):
                source = "webhook:ci" if adapter == "webhooks" else adapter
                identity = collector_id(adapter, section, key)
                self.assertEqual(event_collector({"source": source, "raw_pointer": pointer}), identity)
                self.assertTrue(all(pointer.get(k, {}).items() >= v.items()
                                    for k, v in collector_pointer_filter(identity).items()))

    def test_database_override_and_yaml_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jenkins.yml"
            path.write_text("jobs:\n  - name: yaml-job\n", encoding="utf-8")
            class Session:
                def __init__(self, doc): self.doc = doc
                def get(self, model, name):
                    return SimpleNamespace(document=self.doc) if self.doc is not None else None
            self.assertEqual(load_document("jenkins", str(path), db=Session(None))["jobs"][0]["name"], "yaml-job")
            self.assertEqual(load_document("jenkins", str(path), db=Session({"jobs": [{"name": "db-job"}]}))["jobs"][0]["name"], "db-job")
            self.assertIn("yaml-job", path.read_text(encoding="utf-8"))

    def test_cross_framework_match_is_independent(self):
        rules = parse_rules({"rules": [{
            "id": "deploy", "when": {"source": "jenkins", "outcome": "success"},
            "confidence": 0.75,
            "map_to": [{"framework": "ISO27001:2022", "ref": "A.8.32"},
                       {"framework": "CE", "ref": "security-update-management"}],
        }]})
        hits = evaluate_by_framework({"source": "jenkins", "outcome": "success"}, rules, details=True)
        self.assertEqual(set(hits), {"ISO27001:2022", "CE"})
        self.assertEqual(hits["CE"][0]["confidence"], 0.75)
        self.assertIn("deploy", hits["CE"][0]["rationale"])
        self.assertEqual(evaluate_by_framework({"source": "jenkins", "outcome": "failed"}, rules), {})

    def test_disabled_rule_does_not_match(self):
        self.assertEqual(parse_rules({"rules": [{"id": "off", "enabled": False,
            "when": {"source": "rss"}, "map_to": ["A.8.1"]}]}), [])

    def test_credentials_cannot_be_saved(self):
        with self.assertRaises(HTTPException):
            _safe_document({"feeds": [{"auth": {"password": "secret"}}]})

    def test_unsafe_regex_rejected(self):
        _validate_regex("failure.*sshd", "summary_regex")
        with self.assertRaises(HTTPException):
            _validate_regex("(a+)+$", "summary_regex")

    def test_version_conflict_prevents_overwrite(self):
        class Query:
            def filter_by(self, **kwargs): return self
            def with_for_update(self): return self
            def one_or_none(self): return SimpleNamespace(version=2)

        class DB:
            def query(self, *args): return Query()
            def add(self, *args): raise AssertionError("A conflict must not write")
            def commit(self): raise AssertionError("A conflict must not commit")

        with self.assertRaises(HTTPException) as error:
            _save(DB(), "jenkins", {"jobs": []}, 1, SimpleNamespace(id="admin"))
        self.assertEqual(error.exception.status_code, 409)

    def test_connector_checks_rss_url_and_tls(self):
        for feed in ({"url": "http://example.org/feed"},
                     {"url": "https://example.org/feed", "verify_tls": False}):
            with self.assertRaises(HTTPException):
                _validate_connector("rss", {"feeds": [feed]})


if __name__ == "__main__":
    unittest.main()
