"""Focused invariants for managed source configuration and rule authoring."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.core import managed_configuration
from app.core.evidence_migration import unify
from app.core.managed_configuration import load_document
from app.mapping.collector import (
    collector_id,
    collector_pointer_filter,
    event_collector,
)
from app.mapping.rules import evaluate_by_framework, parse_rules
from app.services.errors import DomainError
from app.services.managed_configurations import (
    _safe_document,
    _save,
    _validate_connector,
    _validate_regex,
)
from fastapi import HTTPException


class ManagedConfigurationTests(unittest.TestCase):
    def test_upgrade_keeps_broad_rule_and_converts_bookstack_page(self):
        documents = {
            "jenkins": {
                "jobs": [{"name": "release", "label": "prod", "kind": "deployment"}]
            },
            "rules": {
                "framework": "ISO27001:2022",
                "rules": [
                    {
                        "id": "deployment",
                        "when": {
                            "source": "jenkins",
                            "action": "deployment",
                            "system": "prod",
                        },
                        "map_to": ["A.8.32"],
                    },
                    {
                        "id": "all_builds",
                        "when": {"source": "jenkins", "outcome": "failed"},
                        "map_to": ["A.8.15"],
                        "enabled": False,
                    },
                ],
            },
            "bookstack": {
                "pages": [
                    {
                        "match": {"book": {"slug": "policies"}, "page_slug": "backup"},
                        "map_to": ["A.5.1"],
                        "confidence": 0.7,
                    }
                ]
            },
        }
        converted, counts = unify(documents, "ISO27001:2022")
        entries = {rule["id"]: rule for rule in converted["rules"]["rules"]}
        self.assertEqual(
            entries["deployment"]["when"]["collector"],
            collector_id("jenkins", "jobs", "release"),
        )
        self.assertNotIn("collector", entries["all_builds"]["when"])
        self.assertFalse(entries["all_builds"]["enabled"])
        page = entries["bookstack_page_1"]
        self.assertEqual(page["when"]["bookstack_book_slug"], "policies")
        self.assertEqual(page["when"]["bookstack_page_slug"], "backup")
        self.assertEqual(page["confidence"], 0.7)
        self.assertEqual(converted["bookstack"]["page_mappings"], [])
        self.assertEqual(counts["bookstack_pages"], 1)
        self.assertEqual(unify(converted, "ISO27001:2022")[0], converted)

    def test_collection_identity_distinguishes_queries_with_same_event_fields(self):
        failed = collector_id("loki", "queries", "ossec_login_failed")
        rules = parse_rules(
            {
                "rules": [
                    {
                        "id": "login",
                        "when": {
                            "source": "loki",
                            "system": "ossec",
                            "action": "ossec_login",
                            "outcome": "failed",
                            "collector": failed,
                        },
                        "map_to": [
                            {"framework": "ISO27001:2022", "ref": "A.8.15"},
                            {"framework": "CE", "ref": "logging"},
                        ],
                    }
                ]
            }
        )
        event = {
            "source": "loki",
            "system": "ossec",
            "action": "ossec_login",
            "outcome": "failed",
            "raw_pointer": {"loki": {"query_name": "ossec_login_failed"}},
        }
        self.assertEqual(
            set(evaluate_by_framework(event, rules)), {"ISO27001:2022", "CE"}
        )
        event["raw_pointer"]["loki"]["query_name"] = "other_failed_login"
        self.assertEqual(evaluate_by_framework(event, rules), {})

    def test_collector_identity_for_legacy_event_provenance(self):
        examples = [
            ("jenkins", "jobs", "release", {"jenkins": {"job": "release"}}),
            (
                "rss",
                "feeds",
                "https://example.org/feed",
                {"rss": {"feed_url": "https://example.org/feed"}},
            ),
            (
                "cloudwatch_logs",
                "queries",
                "alerts",
                {"cloudwatch_logs": {"name": "alerts"}},
            ),
            (
                "google_workspace",
                "streams",
                "login",
                {"google_workspace": {"stream": "login"}},
            ),
            ("taiga", "projects", "42", {"taiga": {"project_id": 42}}),
            ("webhooks", "providers", "ci", {"webhook": {"provider": "ci"}}),
            (
                "github",
                "repos",
                "org/repo",
                {"github": {"endpoint": "repo_events", "owner": "org", "repo": "repo"}},
            ),
            ("bookstack", "selected_pages", "17", {"bookstack": {"page_id": 17}}),
        ]
        for adapter, section, key, pointer in examples:
            with self.subTest(adapter=adapter):
                source = "webhook:ci" if adapter == "webhooks" else adapter
                identity = collector_id(adapter, section, key)
                self.assertEqual(
                    event_collector({"source": source, "raw_pointer": pointer}),
                    identity,
                )
                self.assertTrue(
                    all(
                        pointer.get(k, {}).items() >= v.items()
                        for k, v in collector_pointer_filter(identity).items()
                    )
                )

    def test_database_override_and_yaml_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jenkins.yml"
            path.write_text("jobs:\n  - name: yaml-job\n", encoding="utf-8")

            class Session:
                def __init__(self, doc):
                    self.doc = doc

                def get(self, model, name):
                    return (
                        SimpleNamespace(document=self.doc)
                        if self.doc is not None
                        else None
                    )

            self.assertEqual(
                load_document("jenkins", str(path), db=Session(None))["jobs"][0][
                    "name"
                ],
                "yaml-job",
            )
            self.assertEqual(
                load_document(
                    "jenkins", str(path), db=Session({"jobs": [{"name": "db-job"}]})
                )["jobs"][0]["name"],
                "db-job",
            )
            self.assertIn("yaml-job", path.read_text(encoding="utf-8"))

    def test_a_worker_read_opens_its_own_session_for_the_override(self):
        # Without a caller session, the override is read through a short-lived
        # session of its own and the shipped file is never opened.
        class Session:
            def __init__(self, doc):
                self.doc = doc

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get(self, model, name):
                return SimpleNamespace(document=self.doc) if self.doc else None

        with patch.object(
            managed_configuration,
            "SessionLocal",
            lambda: Session({"jobs": [{"name": "override"}]}),
        ):
            document = load_document("jenkins", "/nonexistent/jenkins.yml")
        self.assertEqual(document, {"jobs": [{"name": "override"}]})
        # With no override the shipped file is read through the same path.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jenkins.yml"
            path.write_text("jobs:\n  - name: shipped\n", encoding="utf-8")
            with patch.object(
                managed_configuration, "SessionLocal", lambda: Session(None)
            ):
                document = load_document("jenkins", str(path))
        self.assertEqual(document, {"jobs": [{"name": "shipped"}]})

    def test_a_shipped_file_that_is_no_mapping_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jenkins.yml"
            path.write_text("- name: listed\n", encoding="utf-8")

            class Session:
                def get(self, model, name):
                    return None

            with self.assertRaises(ValueError) as error:
                load_document("jenkins", str(path), db=Session())
            self.assertEqual(
                str(error.exception), "jenkins configuration must be an object"
            )
            path.write_text("", encoding="utf-8")
            self.assertEqual(load_document("jenkins", str(path), db=Session()), {})

    def test_cross_framework_match_is_independent(self):
        rules = parse_rules(
            {
                "rules": [
                    {
                        "id": "deploy",
                        "when": {"source": "jenkins", "outcome": "success"},
                        "confidence": 0.75,
                        "map_to": [
                            {"framework": "ISO27001:2022", "ref": "A.8.32"},
                            {"framework": "CE", "ref": "security-update-management"},
                        ],
                    }
                ]
            }
        )
        hits = evaluate_by_framework(
            {"source": "jenkins", "outcome": "success"}, rules, details=True
        )
        self.assertEqual(set(hits), {"ISO27001:2022", "CE"})
        self.assertEqual(hits["CE"][0]["confidence"], 0.75)
        self.assertIn("deploy", hits["CE"][0]["rationale"])
        self.assertEqual(
            evaluate_by_framework({"source": "jenkins", "outcome": "failed"}, rules), {}
        )

    def test_disabled_rule_does_not_match(self):
        self.assertEqual(
            parse_rules(
                {
                    "rules": [
                        {
                            "id": "off",
                            "enabled": False,
                            "when": {"source": "rss"},
                            "map_to": ["A.8.1"],
                        }
                    ]
                }
            ),
            [],
        )

    def test_credentials_cannot_be_saved(self):
        with self.assertRaises((HTTPException, DomainError)):
            _safe_document({"feeds": [{"auth": {"password": "secret"}}]})

    def test_unsafe_regex_rejected(self):
        _validate_regex("failure.*sshd", "summary_regex")
        with self.assertRaises((HTTPException, DomainError)):
            _validate_regex("(a+)+$", "summary_regex")

    def test_version_conflict_prevents_overwrite(self):
        class Query:
            def filter_by(self, **kwargs):
                return self

            def with_for_update(self):
                return self

            def one_or_none(self):
                return SimpleNamespace(version=2)

        class DB:
            def query(self, *args):
                return Query()

            def add(self, *args):
                raise AssertionError("A conflict must not write")

            def commit(self):
                raise AssertionError("A conflict must not commit")

        with self.assertRaises((HTTPException, DomainError)) as error:
            _save(DB(), "jenkins", {"jobs": []}, 1, SimpleNamespace(id="admin"))
        self.assertEqual(error.exception.status_code, 409)

    def test_connector_checks_rss_url_and_tls(self):
        for feed in (
            {"url": "http://example.org/feed"},
            {"url": "https://example.org/feed", "verify_tls": False},
        ):
            with self.assertRaises((HTTPException, DomainError)):
                _validate_connector("rss", {"feeds": [feed]})


class EvidenceMigrationTests(unittest.TestCase):
    """Legacy mapping documents convert into the unified model, or fail loudly."""

    def _rule(self, rid, when, **extra):
        return {"id": rid, "when": when, "map_to": ["A.5.1"], **extra}

    def _unify(self, documents):
        return unify(documents, "ISO27001:2022")

    def _rules_by_id(self, converted):
        return {rule["id"]: rule for rule in converted["rules"]["rules"]}

    def test_webhook_rule_links_only_to_a_configured_provider(self):
        # A webhook rule links to its provider collector when the provider is
        # configured; an unknown provider keeps the rule source-wide.
        documents = {
            "webhooks": {"providers": {"ci": {"secret_header": "X-Token"}}},
            "rules": {
                "rules": [
                    self._rule("known", {"source": "webhook:ci"}),
                    self._rule("unknown", {"source": "webhook:other"}),
                ]
            },
        }
        converted, counts = self._unify(documents)
        entries = self._rules_by_id(converted)
        self.assertEqual(
            entries["known"]["when"]["collector"],
            collector_id("webhooks", "providers", "ci"),
        )
        self.assertNotIn("collector", entries["unknown"]["when"])
        self.assertEqual(counts["linked"], 1)

    def test_webhook_rule_stays_broad_without_a_providers_section(self):
        documents = {
            "webhooks": {},
            "rules": {"rules": [self._rule("hook", {"source": "webhook:ci"})]},
        }
        converted, counts = self._unify(documents)
        self.assertNotIn("collector", self._rules_by_id(converted)["hook"]["when"])
        self.assertEqual(counts["linked"], 0)

    def test_unknown_source_and_malformed_collection_entries_stay_broad(self):
        # Sources without a collection section, and Loki queries whose event
        # defaults are malformed, never produce a collector association.
        documents = {
            "loki": {
                "queries": [
                    {"name": "broken", "event": "system=ossec"},
                    {"name": "nameless-system", "event": {"action": "login"}},
                ]
            },
            "rules": {
                "rules": [
                    self._rule("agent", {"source": "agent", "system": "ossec"}),
                    self._rule("loki", {"source": "loki", "system": "ossec"}),
                ]
            },
        }
        converted, counts = self._unify(documents)
        entries = self._rules_by_id(converted)
        self.assertNotIn("collector", entries["agent"]["when"])
        self.assertNotIn("collector", entries["loki"]["when"])
        self.assertEqual(counts["linked"], 0)

    def test_loki_rule_links_when_event_defaults_identify_one_query(self):
        documents = {
            "loki": {
                "queries": [
                    {
                        "name": "ssh_failed",
                        "event": {
                            "system": "sshd",
                            "action": "login",
                            "outcome": "failed",
                        },
                    },
                    {
                        "name": "ssh_ok",
                        "event": {
                            "system": "sshd",
                            "action": "login",
                            "outcome": "success",
                        },
                    },
                ]
            },
            "rules": {
                "rules": [
                    self._rule(
                        "failed",
                        {"source": "loki", "system": "sshd", "outcome": "failed"},
                    ),
                    self._rule("either", {"source": "loki", "system": "sshd"}),
                ]
            },
        }
        converted, counts = self._unify(documents)
        entries = self._rules_by_id(converted)
        self.assertEqual(
            entries["failed"]["when"]["collector"],
            collector_id("loki", "queries", "ssh_failed"),
        )
        self.assertNotIn("collector", entries["either"]["when"])
        self.assertEqual(counts["linked"], 1)

    def test_google_workspace_rule_links_by_system_or_application(self):
        # Streams emit their configured system, falling back to the Workspace
        # application name, so either form identifies the stream.
        documents = {
            "google_workspace": {
                "streams": [
                    {"name": "logins", "application": "login"},
                    {"name": "drive-share", "system": "drive"},
                ]
            },
            "rules": {
                "rules": [
                    self._rule(
                        "login", {"source": "google_workspace", "system": "login"}
                    ),
                    self._rule(
                        "drive", {"source": "google_workspace", "system": "drive"}
                    ),
                ]
            },
        }
        converted, counts = self._unify(documents)
        entries = self._rules_by_id(converted)
        self.assertEqual(
            entries["login"]["when"]["collector"],
            collector_id("google_workspace", "streams", "logins"),
        )
        self.assertEqual(
            entries["drive"]["when"]["collector"],
            collector_id("google_workspace", "streams", "drive-share"),
        )
        self.assertEqual(counts["linked"], 2)

    def test_rss_rule_links_by_feed_system_and_ignores_feeds_without_url(self):
        documents = {
            "rss": {
                "feeds": [
                    {"url": "https://example.org/a.xml", "system": "status"},
                    {"system": "status"},
                    {"url": "https://example.org/b.xml", "system": "blog"},
                ]
            },
            "rules": {
                "rules": [
                    self._rule("status", {"source": "rss", "system": "status"}),
                    self._rule("blog", {"source": "rss", "system": "blog"}),
                ]
            },
        }
        converted, counts = self._unify(documents)
        entries = self._rules_by_id(converted)
        self.assertEqual(
            entries["status"]["when"]["collector"],
            collector_id("rss", "feeds", "https://example.org/a.xml"),
        )
        self.assertEqual(
            entries["blog"]["when"]["collector"],
            collector_id("rss", "feeds", "https://example.org/b.xml"),
        )
        self.assertEqual(counts["linked"], 2)

    def test_rule_without_comparable_condition_stays_broad(self):
        # A rule that only names the source matches every job, so it keeps its
        # source-wide scope even when one job is configured.
        documents = {
            "jenkins": {"jobs": [{"name": "only"}]},
            "rules": {"rules": [self._rule("all", {"source": "jenkins"})]},
        }
        converted, counts = self._unify(documents)
        self.assertNotIn("collector", self._rules_by_id(converted)["all"]["when"])
        self.assertEqual(counts["linked"], 0)

    def test_explicit_collector_is_preserved_without_relinking(self):
        explicit = collector_id("jenkins", "jobs", "other")
        documents = {
            "jenkins": {"jobs": [{"name": "release", "label": "prod"}]},
            "rules": {
                "rules": [
                    self._rule(
                        "pinned",
                        {"source": "jenkins", "system": "prod", "collector": explicit},
                    )
                ]
            },
        }
        converted, counts = self._unify(documents)
        self.assertEqual(
            self._rules_by_id(converted)["pinned"]["when"]["collector"], explicit
        )
        self.assertEqual(counts["linked"], 0)

    def test_duplicate_rule_id_is_rejected(self):
        documents = {
            "rules": {
                "rules": [
                    self._rule("dup", {"source": "jenkins"}),
                    self._rule("dup", {"source": "rss"}),
                ]
            }
        }
        with self.assertRaises(ValueError) as error:
            self._unify(documents)
        self.assertIn("Duplicate rule ID dup", str(error.exception))

    def test_rule_that_cannot_be_normalised_is_rejected(self):
        # A rule the ingestion parser would skip must stop the migration, so an
        # administrator corrects it and the mapping survives.
        documents = {
            "rules": {"rules": [{"id": "broken", "when": {"source": "jenkins"}}]}
        }
        with self.assertRaises(ValueError) as error:
            self._unify(documents)
        self.assertIn("Cannot normalize rule broken", str(error.exception))

    def test_rule_without_id_is_numbered_by_position(self):
        converted, counts = self._unify(
            {"rules": {"rules": [{"when": {"source": "rss"}, "map_to": ["A.5.1"]}]}}
        )
        self.assertEqual(converted["rules"]["rules"][0]["id"], "r0")
        self.assertEqual(converted["rules"]["rules"][0]["enabled"], True)
        self.assertEqual(counts["rules"], 1)

    def test_bookstack_mapping_must_be_an_object(self):
        with self.assertRaises(ValueError) as error:
            self._unify({"bookstack": {"pages": ["policies/backup"]}})
        self.assertEqual(str(error.exception), "BookStack page mapping #1 is invalid")

    def test_bookstack_mapping_match_must_be_an_object(self):
        with self.assertRaises(ValueError) as error:
            self._unify(
                {"bookstack": {"pages": [{"match": "backup", "map_to": ["A.5.1"]}]}}
            )
        self.assertEqual(str(error.exception), "BookStack page mapping #1 has no match")

    def test_bookstack_mapping_needs_targets(self):
        for refs in ([], "", " , ", None, 7):
            with self.subTest(refs=refs):
                item = {"match": {"slug": "backup"}}
                if refs is not None:
                    item["map_to"] = refs
                with self.assertRaises(ValueError) as error:
                    self._unify({"bookstack": {"pages": [item]}})
                self.assertEqual(
                    str(error.exception), "BookStack page mapping #1 has no targets"
                )

    def test_bookstack_mapping_accepts_comma_separated_controls(self):
        converted, counts = self._unify(
            {
                "bookstack": {
                    "page_mappings": [
                        {"match": {"slug": "backup"}, "controls": "A.5.1, A.8.13,"}
                    ]
                }
            }
        )
        rule = self._rules_by_id(converted)["bookstack_page_1"]
        self.assertEqual(
            rule["map_to"],
            [
                {"framework": "ISO27001:2022", "ref": "A.5.1"},
                {"framework": "ISO27001:2022", "ref": "A.8.13"},
            ],
        )
        self.assertEqual(rule["confidence"], 0.95)
        self.assertEqual(rule["description"], "BookStack page 1")
        self.assertEqual(counts["bookstack_pages"], 1)

    def test_bookstack_mapping_accepts_book_named_as_text(self):
        converted, _ = self._unify(
            {
                "bookstack": {
                    "pages": [
                        {
                            "match": {"book": "policies", "name": "Backups"},
                            "map_to": ["A.5.1"],
                            "framework": "CE",
                            "rationale": "Backup policy",
                        }
                    ]
                }
            }
        )
        rule = self._rules_by_id(converted)["bookstack_page_1"]
        self.assertEqual(rule["when"]["bookstack_book_slug"], "policies")
        self.assertEqual(rule["when"]["bookstack_page_title"], "Backups")
        self.assertEqual(rule["description"], "Backup policy")
        self.assertEqual(rule["map_to"], [{"framework": "CE", "ref": "A.5.1"}])
        self.assertEqual(
            converted["bookstack"]["capture_pages"],
            [
                {
                    "book": "policies",
                    "name": "Backups",
                    "book_slug": "policies",
                    "title": "Backups",
                }
            ],
        )

    def test_bookstack_mapping_needs_a_selector(self):
        with self.assertRaises(ValueError) as error:
            self._unify(
                {"bookstack": {"pages": [{"match": {"slug": ""}, "map_to": ["A.5.1"]}]}}
            )
        self.assertEqual(
            str(error.exception), "BookStack page mapping #1 has no usable selector"
        )

    def test_bookstack_numeric_text_ids_select_the_page(self):
        # Legacy YAML often quotes IDs; they convert to whole numbers, pin the
        # rule to the page collector and add the page to the selected pages.
        converted, counts = self._unify(
            {
                "bookstack": {
                    "pages": [
                        {
                            "match": {"id": "17", "book_id": "3"},
                            "map_to": ["A.5.1"],
                        }
                    ]
                }
            }
        )
        rule = self._rules_by_id(converted)["bookstack_page_1"]
        self.assertEqual(rule["when"]["bookstack_page_id"], 17)
        self.assertEqual(rule["when"]["bookstack_book_id"], 3)
        self.assertEqual(
            rule["when"]["collector"],
            collector_id("bookstack", "selected_pages", 17),
        )
        self.assertEqual(
            converted["bookstack"]["selected_pages"], [{"id": 17, "book_id": 3}]
        )
        self.assertEqual(counts["bookstack_pages"], 1)

    def test_bookstack_selected_page_is_recorded_once(self):
        # A page already selected keeps its existing entry, and a book slug is
        # carried onto a newly selected page.
        converted, _ = self._unify(
            {
                "bookstack": {
                    "selected_pages": [{"id": 17, "book_id": 3}],
                    "pages": [
                        {"match": {"id": 17}, "map_to": ["A.5.1"]},
                        {
                            "match": {"id": 18, "book": {"slug": "policies"}},
                            "map_to": ["A.5.1"],
                        },
                    ],
                }
            }
        )
        self.assertEqual(
            converted["bookstack"]["selected_pages"],
            [{"id": 17, "book_id": 3}, {"id": 18, "book_slug": "policies"}],
        )
        self.assertEqual(
            converted["bookstack"]["selected_pages"][1],
            {"id": 18, "book_slug": "policies"},
        )

    def test_bookstack_rule_id_avoids_existing_rule_ids(self):
        converted, _ = self._unify(
            {
                "rules": {
                    "rules": [
                        self._rule("bookstack_page_1", {"source": "bookstack"}),
                        self._rule("bookstack_page_1_mapped", {"source": "bookstack"}),
                    ]
                },
                "bookstack": {
                    "pages": [{"match": {"slug": "backup"}, "map_to": ["A.5.1"]}]
                },
            }
        )
        ids = [rule["id"] for rule in converted["rules"]["rules"]]
        self.assertEqual(
            ids,
            [
                "bookstack_page_1",
                "bookstack_page_1_mapped",
                "bookstack_page_1_mapped_mapped",
            ],
        )
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
