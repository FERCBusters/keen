import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from app.ingest import forgejo, gitea, gitea_compatible, gitlab
from app.mapping.collector import (
    collector_id,
    collector_pointer_filter,
    matches_collector,
)
from app.services.errors import DomainError
from app.services.managed_configurations import (
    _collector_key,
    _editable_collector,
    _validate_connector,
)
from fastapi import HTTPException


class GitDiscoveryTests(unittest.TestCase):
    def test_discovery_paginates_short_server_pages(self):
        for module in (forgejo, gitea):
            with (
                self.subTest(module=module.__name__),
                patch.object(gitea_compatible, "is_safe_url", return_value=True),
                patch.object(gitea_compatible, "ingestion_client") as factory,
            ):
                client = factory.return_value.__enter__.return_value
                client.get.side_effect = [
                    Mock(
                        status_code=200,
                        json=lambda: [{"owner": {"login": "mig5"}, "name": "one"}],
                    ),
                    Mock(
                        status_code=200,
                        json=lambda: [{"owner": {"login": "mig5"}, "name": "two"}],
                    ),
                    Mock(status_code=200, json=lambda: []),
                ]
                self.assertEqual(
                    len(
                        module.collector._discover_repositories(
                            "https://git.example.org", "users", "mig5"
                        )
                    ),
                    2,
                )
                self.assertEqual(
                    client.get.call_args_list[1].kwargs["params"]["page"], 2
                )
                self.assertFalse(factory.call_args.kwargs["follow_redirects"])

    def test_repeated_page_fails(self):
        with (
            patch.object(gitea_compatible, "is_safe_url", return_value=True),
            patch.object(gitea_compatible, "ingestion_client") as factory,
        ):
            factory.return_value.__enter__.return_value.get.return_value = Mock(
                status_code=200,
                json=lambda: [{"owner": {"login": "mig5"}, "name": "one"}],
            )
            with self.assertRaisesRegex(ValueError, "repeated"):
                forgejo.collector._discover_repositories(
                    "https://git.example.org", "users", "mig5"
                )

    def test_discovery_does_not_follow_repository_url(self):
        with (
            patch.object(gitea_compatible, "is_safe_url", return_value=True),
            patch.object(gitea_compatible, "ingestion_client") as factory,
        ):
            client = factory.return_value.__enter__.return_value
            client.get.side_effect = [
                Mock(
                    status_code=200,
                    json=lambda: [
                        {
                            "owner": {"login": "someone-else"},
                            "name": "one",
                            "html_url": "https://attacker.test",
                        }
                    ],
                )
            ]
            with self.assertRaisesRegex(ValueError, "outside"):
                forgejo.collector._discover_repositories(
                    "https://git.example.org", "users", "mig5"
                )

    def test_scope_identity_matches_and_does_not_cross_sources(self):
        for source in ("forgejo", "gitea"):
            identity = collector_id(source, "users", "mig5")
            event = {
                "source": source,
                "raw_pointer": {
                    source: {
                        "owner": "mig5",
                        "feed": "https://example.org/mig5/one.rss",
                    }
                },
            }
            self.assertTrue(matches_collector(identity, event))
            self.assertEqual(
                collector_pointer_filter(identity), {source: {"owner": "mig5"}}
            )
            self.assertFalse(
                matches_collector(collector_id(source, "users", "other"), event)
            )
        identity = collector_id("gitlab", "groups", "team/sub")
        self.assertTrue(
            matches_collector(
                identity,
                {
                    "source": "gitlab",
                    "raw_pointer": {"gitlab": {"section": "groups", "key": "team/sub"}},
                },
            )
        )

    def test_config_validation(self):
        for source in ("forgejo", "gitea"):
            _validate_connector(
                source,
                {"users": [{"user": "mig5"}], "organizations": [{"org": "team"}]},
            )
            self.assertEqual(_collector_key(source, "users", {"user": "mig5"}), "mig5")
            self.assertTrue(_editable_collector(source, "users"))
            with self.assertRaises((HTTPException, DomainError)):
                _validate_connector(source, {"users": [{"user": "../bad"}]})
        _validate_connector(
            "gitlab", {"groups": [{"group": "team/sub"}], "users": [{"user": "mig5"}]}
        )

    def test_gitlab_pagination_and_error(self):
        client = Mock()
        client.get.side_effect = [
            Mock(status_code=200, json=lambda: [{"id": 1}]),
            Mock(status_code=200, json=lambda: []),
        ]
        self.assertEqual(
            gitlab._pages(client, "https://gitlab.example.org", "groups/team/projects"),
            [{"id": 1}],
        )
        client.get.side_effect = None
        client.get.return_value = Mock(status_code=403)
        with self.assertRaisesRegex(RuntimeError, "403"):
            gitlab._pages(client, "https://gitlab.example.org", "groups/team/projects")

    def test_empty_configuration_is_explicit(self):
        with (
            patch.object(gitea_compatible.settings, "forgejo_enabled", True),
            patch("app.ingest.connections.deployment_document", return_value={}),
        ):
            self.assertEqual(forgejo.ingest_forgejo_all(None), [])

    def test_gitlab_group_discovery_and_storage(self):
        db = Mock()
        db.scalars.return_value.all.return_value = []
        db.query.return_value.filter_by.return_value.one_or_none.return_value = None
        events = [
            {
                "id": 2,
                "created_at": "2026-10-06T00:00:00Z",
                "action_name": "pushed",
                "author_username": "mig5",
            }
        ]
        with (
            patch.object(gitlab.settings, "gitlab_enabled", True),
            # The event is dated absolutely and the initial lookback is 14 days.
            patch.object(
                gitlab, "utc_now_naive", return_value=datetime(2026, 10, 7, 12, 0, 0)
            ),
            patch.object(gitlab, "is_safe_url", return_value=True),
            patch.object(
                gitlab,
                "load_document",
                return_value={"groups": [{"group": "team/sub"}]},
            ),
            patch.object(gitlab, "ingestion_client"),
            patch.object(
                gitlab,
                "_pages",
                side_effect=[
                    [{"id": 3, "path_with_namespace": "team/sub/repo"}],
                    events,
                ],
            ) as pages,
            patch.object(
                gitlab, "store_event_with_artifact", return_value={"deduped": False}
            ) as store,
        ):
            result = gitlab.ingest_gitlab_all(db)
        self.assertIn("groups/team%2Fsub/projects", pages.call_args_list[0].args)
        self.assertEqual(pages.call_args_list[0].args[-1]["include_subgroups"], "true")
        self.assertEqual(result[-1]["created_events"], 1)
        event = store.call_args.kwargs
        self.assertEqual(event["source"], "gitlab")
        self.assertTrue(
            matches_collector(collector_id("gitlab", "groups", "team/sub"), event)
        )
        db.commit.assert_called_once()

    def test_discovery_inputs_reach_runner(self):
        db = Mock()
        db.scalars.return_value.all.return_value = []
        with (
            patch.object(gitea_compatible.settings, "forgejo_enabled", True),
            patch.object(
                gitea_compatible.settings, "forgejo_base_url", "https://git.example.org"
            ),
            patch.object(
                forgejo.collector,
                "load_config",
                return_value={"users": [{"user": "mig5"}]},
            ),
            patch.object(
                forgejo.collector,
                "_discover_repositories",
                return_value=[{"owner": {"login": "mig5"}, "name": "repo"}],
            ),
            patch.object(
                forgejo.collector,
                "ingest_repo_feed",
                return_value={"created_events": 1},
            ) as ingest,
        ):
            result = forgejo.ingest_forgejo_all(db)
        self.assertEqual(
            ingest.call_args.kwargs["feed_url"], "https://git.example.org/mig5/repo.rss"
        )
        self.assertEqual(result[0]["discovered_repositories"], 1)


class GitLabCollectionBoundaryTests(unittest.TestCase):
    """GitLab paging, activity validation and per-scope error isolation."""

    BASE = "https://gitlab.example.org"

    def _page(self, payload):
        return Mock(status_code=200, json=lambda: payload)

    def test_pages_refuse_a_body_that_is_no_list(self):
        client = Mock()
        client.get.return_value = self._page({"message": "401 Unauthorized"})
        with self.assertRaisesRegex(ValueError, "invalid list"):
            gitlab._pages(client, self.BASE, "groups/team/projects")

    def test_pages_stop_when_the_server_repeats_a_page(self):
        # A server that ignores the page parameter would otherwise be read
        # forever; the repeated page ends the collection with an error.
        client = Mock()
        client.get.return_value = self._page([{"id": 1}])
        with self.assertRaisesRegex(ValueError, "repeated a page"):
            gitlab._pages(client, self.BASE, "groups/team/projects")
        self.assertEqual(client.get.call_count, 2)

    def test_pages_stop_at_the_row_limit(self):
        client = Mock()
        client.get.side_effect = [
            self._page([{"id": n} for n in range(60000)]),
            self._page([{"id": n} for n in range(60000, 120001)]),
        ]
        with self.assertRaisesRegex(ValueError, "exceeds collection limit"):
            gitlab._pages(client, self.BASE, "users/mig5/projects")

    def test_pages_stop_after_a_thousand_pages(self):
        client = Mock()
        client.get.side_effect = [self._page([{"id": n}]) for n in range(1000)]
        with self.assertRaisesRegex(ValueError, "exceeded 1000 pages"):
            gitlab._pages(client, self.BASE, "users/mig5/projects")
        self.assertEqual(client.get.call_count, 1000)
        self.assertEqual(client.get.call_args.kwargs["params"]["page"], 1000)

    def _db(self):
        db = Mock()
        db.query.return_value.filter_by.return_value.one_or_none.return_value = None
        return db

    def test_activity_before_the_cursor_or_without_a_timestamp_is_skipped(self):
        # The date filter overlaps the cursor day, so rows older than the cursor
        # and rows without a parseable timestamp are left out of storage.
        db = self._db()
        events = [
            {"id": 1, "created_at": "2026-09-01T00:00:00Z", "action_name": "pushed"},
            {"id": 2, "created_at": "garbage", "action_name": "pushed"},
            {"id": 3, "created_at": "2026-10-06T00:00:00Z", "action_name": "pushed"},
        ]
        with (
            patch.object(
                gitlab, "utc_now_naive", return_value=datetime(2026, 10, 7, 12, 0, 0)
            ),
            patch.object(gitlab, "_pages", return_value=events),
            patch.object(
                gitlab, "store_event_with_artifact", return_value={"deduped": False}
            ) as store,
        ):
            result = gitlab._project_events(
                db, Mock(), self.BASE, "users", "mig5", {"id": 9}, None
            )
        self.assertEqual(result, {"project": 9, "created_events": 1})
        self.assertEqual(store.call_count, 1)
        self.assertEqual(store.call_args.kwargs["system"], "9")
        cursor = db.add.call_args.args[0]
        self.assertEqual(cursor.last_ts, datetime(2026, 10, 6, 0, 0, 0))

    def test_activity_without_a_stable_id_is_refused(self):
        db = self._db()
        events = [{"created_at": "2026-10-06T00:00:00Z", "action_name": "pushed"}]
        with (
            patch.object(
                gitlab, "utc_now_naive", return_value=datetime(2026, 10, 7, 12, 0, 0)
            ),
            patch.object(gitlab, "_pages", return_value=events),
            patch.object(gitlab, "store_event_with_artifact") as store,
        ):
            with self.assertRaisesRegex(ValueError, "stable event ID"):
                gitlab._project_events(
                    db, Mock(), self.BASE, "users", "mig5", {"id": 9}, "label"
                )
        store.assert_not_called()
        db.commit.assert_not_called()

    def _run(self, db, config, pages, safe=True):
        with (
            patch.object(gitlab.settings, "gitlab_enabled", True),
            patch.object(gitlab.settings, "gitlab_base_url", self.BASE + "/"),
            patch.object(gitlab, "is_safe_url", return_value=safe),
            patch.object(gitlab.settings, "gitlab_token", "synthetic-token"),
            patch.object(
                gitlab, "utc_now_naive", return_value=datetime(2026, 10, 7, 12, 0, 0)
            ),
            patch.object(gitlab, "load_document", return_value=config),
            patch.object(gitlab, "ingestion_client"),
            patch.object(gitlab, "_pages", side_effect=pages) as paged,
            patch.object(
                gitlab, "store_event_with_artifact", return_value={"deduped": False}
            ),
        ):
            return gitlab.ingest_gitlab_all(db), paged

    def test_an_unsafe_base_url_stops_the_run_before_any_request(self):
        db = self._db()
        db.scalars.return_value.all.return_value = []
        results, paged = self._run(db, {"groups": [{"group": "team"}]}, [], safe=False)
        self.assertEqual(
            results[0]["error"], "Configure a safe HTTPS KEEN_GITLAB_BASE_URL"
        )
        paged.assert_not_called()

    def test_duplicate_projects_are_collected_once(self):
        # A project shared into a group as well as owned by it appears twice in
        # discovery; its activity is read once.
        db = self._db()
        db.scalars.return_value.all.return_value = []
        results, paged = self._run(
            db,
            {"groups": [{"group": "team"}]},
            [[{"id": 3, "path_with_namespace": "team/a"}, {"id": 3}], []],
        )
        self.assertEqual(results[0]["discovered_projects"], 2)
        self.assertEqual(
            [r for r in results if "project" in r],
            [
                {
                    "project": "team/a",
                    "created_events": 0,
                    "connection_id": "env:gitlab",
                    "connection_name": "Environment default",
                }
            ],
        )
        self.assertEqual(paged.call_count, 2)

    def test_one_failing_project_is_reported_without_stopping_the_scope(self):
        db = self._db()
        db.scalars.return_value.all.return_value = []
        results, _ = self._run(
            db,
            {"users": [{"user": "mig5"}]},
            [
                [{"id": 1, "path_with_namespace": "mig5/one"}, {"id": 2}],
                RuntimeError("GitLab API HTTP 403; secret-DO-NOT-LEAK"),
                [],
            ],
        )
        failed = [r for r in results if r.get("project_id") == 1]
        self.assertEqual(len(failed), 1)
        self.assertEqual(
            failed[0]["error"],
            "Collection failed; check the input, credentials, upstream service and storage configuration",
        )
        self.assertNotIn("DO-NOT-LEAK", str(results))
        self.assertEqual(
            [r for r in results if r.get("project") == 2][0]["created_events"], 0
        )
        db.rollback.assert_called_once()

    def test_a_failing_scope_is_reported_and_the_next_scope_still_runs(self):
        db = self._db()
        db.scalars.return_value.all.return_value = []
        results, _ = self._run(
            db,
            {"groups": [{"group": "team"}], "users": [{"user": "mig5"}]},
            [ValueError("GitLab API returned an invalid list"), []],
        )
        self.assertEqual(results[0]["scope"], "groups")
        self.assertEqual(results[0]["account"], "team")
        self.assertTrue(results[0]["error"].startswith("Collection failed"))
        self.assertEqual(results[1]["scope"], "users")
        self.assertEqual(results[1]["discovered_projects"], 0)
        db.rollback.assert_called_once()

    def test_an_existing_cursor_sets_the_window_and_is_moved_forward(self):
        db = Mock()
        cursor = Mock(last_ts=datetime(2026, 10, 5, 0, 0, 0))
        db.query.return_value.filter_by.return_value.one_or_none.return_value = cursor
        events = [
            {"id": 1, "created_at": "2026-10-04T00:00:00Z", "action_name": "pushed"},
            {"id": 2, "created_at": "2026-10-06T00:00:00Z", "action_name": "pushed"},
        ]
        with (
            patch.object(
                gitlab, "utc_now_naive", return_value=datetime(2026, 10, 7, 12, 0, 0)
            ),
            patch.object(gitlab, "_pages", return_value=events) as paged,
            patch.object(
                gitlab, "store_event_with_artifact", return_value={"deduped": True}
            ) as store,
        ):
            result = gitlab._project_events(
                db, Mock(), self.BASE, "groups", "team", {"id": 9}, None
            )
        # The API filter overlaps the cursor by a day; the cursor still decides.
        self.assertEqual(paged.call_args.args[-1]["after"], "2026-10-04")
        self.assertEqual(store.call_count, 1)
        self.assertEqual(result["created_events"], 0)
        self.assertEqual(cursor.last_ts, datetime(2026, 10, 6, 0, 0, 0))
        db.add.assert_called_once_with(cursor)
