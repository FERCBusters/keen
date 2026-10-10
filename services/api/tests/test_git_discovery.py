import unittest
from unittest.mock import Mock, patch

from app.api.routes.managed_configurations import (
    _collector_key,
    _editable_collector,
    _validate_connector,
)
from app.ingest import forgejo, gitea, gitlab
from app.mapping.collector import (
    collector_id,
    collector_pointer_filter,
    matches_collector,
)
from fastapi import HTTPException


class GitDiscoveryTests(unittest.TestCase):
    def test_discovery_paginates_short_server_pages(self):
        for module in (forgejo, gitea):
            with (
                self.subTest(module=module.__name__),
                patch.object(module, "is_safe_url", return_value=True),
                patch.object(module, "ingestion_client") as factory,
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
                        module._discover_repositories(
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
            patch.object(forgejo, "is_safe_url", return_value=True),
            patch.object(forgejo, "ingestion_client") as factory,
        ):
            factory.return_value.__enter__.return_value.get.return_value = Mock(
                status_code=200,
                json=lambda: [{"owner": {"login": "mig5"}, "name": "one"}],
            )
            with self.assertRaisesRegex(ValueError, "repeated"):
                forgejo._discover_repositories(
                    "https://git.example.org", "users", "mig5"
                )

    def test_discovery_does_not_follow_repository_url(self):
        with (
            patch.object(forgejo, "is_safe_url", return_value=True),
            patch.object(forgejo, "ingestion_client") as factory,
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
                forgejo._discover_repositories(
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
            with self.assertRaises(HTTPException):
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
            patch.object(forgejo.settings, "forgejo_enabled", True),
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
            patch.object(forgejo.settings, "forgejo_enabled", True),
            patch.object(
                forgejo.settings, "forgejo_base_url", "https://git.example.org"
            ),
            patch.object(
                forgejo,
                "load_forgejo_config",
                return_value={"users": [{"user": "mig5"}]},
            ),
            patch.object(
                forgejo,
                "_discover_repositories",
                return_value=[{"owner": {"login": "mig5"}, "name": "repo"}],
            ),
            patch.object(
                forgejo, "ingest_forgejo_repo_feed", return_value={"created_events": 1}
            ) as ingest,
        ):
            result = forgejo.ingest_forgejo_all(db)
        self.assertEqual(
            ingest.call_args.kwargs["feed_url"], "https://git.example.org/mig5/repo.rss"
        )
        self.assertEqual(result[0]["discovered_repositories"], 1)
