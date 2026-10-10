"""Redmine REST fixtures exercise network contracts without a live server."""

import copy
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import httpx
from app.ingest import redmine as r
from app.mapping.collector import (
    collector_id,
    collector_pointer_filter,
    matches_collector,
)
from app.security.redaction import redact_obj

PROJECT = {"id": 2, "identifier": "infra", "name": "Infrastructure"}
ISSUE = {
    "id": 7,
    "project": {"id": 2, "name": "Infrastructure"},
    "subject": "Patch rollout",
    "updated_on": "2026-10-01T10:00:00Z",
    "created_on": "2026-09-01T00:00:00Z",
    "status": {"id": 3, "name": "Resolved", "is_closed": True},
    "tracker": {"id": 1, "name": "Task"},
    "author": {"id": 5, "name": "Original author"},
    "journals": [
        {
            "id": 10,
            "created_on": "2026-09-02T00:00:00Z",
            "user": {"id": 6, "name": "Operator"},
            "notes": "Patching completed",
            "details": [
                {
                    "property": "attr",
                    "name": "status_id",
                    "old_value": "1",
                    "new_value": "3",
                }
            ],
        }
    ],
}


def client(handler):
    return httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        headers={"X-Redmine-API-Key": "fixture-secret"},
    )


class RedmineTests(unittest.TestCase):
    def test_config(self):
        for project in ["*", "infra", 12]:
            r.validate_config({"projects": [{"project": project}]})
        for cfg in [
            {"projects": [{"project": "*"}, {"project": "infra"}]},
            {"projects": [{"project": "../secret"}]},
            {"projects": [{"project": True}]},
            {"projects": [{"project": "infra"}, {"project": "infra"}]},
            {"include_journals": "false"},
            {"max_pages": 0},
        ]:
            with self.subTest(cfg=cfg), self.assertRaises(r.RedmineError):
                r.validate_config(cfg)

    def test_api_header_prefix_pagination(self):
        seen = []

        def handler(req):
            seen.append(req)
            self.assertEqual(req.headers["X-Redmine-API-Key"], "fixture-secret")
            self.assertNotIn("fixture-secret", str(req.url))
            self.assertEqual(req.url.path, "/redmine/projects.json")
            offset = int(req.url.params["offset"])
            return httpx.Response(
                200,
                json={
                    "projects": [{"id": offset + 1}],
                    "offset": offset,
                    "limit": 1,
                    "total_count": 2,
                },
            )

        with client(handler) as c:
            self.assertEqual(
                len(list(r._pages(c, "https://host.example/redmine", "projects"))), 2
            )
        self.assertEqual([str(req.url.params["offset"]) for req in seen], ["0", "1"])

    def test_repeated_page_and_limit_fail(self):
        def handler(req):
            return httpx.Response(
                200,
                json={
                    "issues": [{"id": 1}],
                    "offset": int(req.url.params["offset"]),
                    "total_count": 4,
                },
            )

        with client(handler) as c:
            with self.assertRaisesRegex(r.RedmineError, "repeated"):
                list(r._pages(c, "https://host", "issues"))
        with client(handler) as c:
            with self.assertRaisesRegex(r.RedmineError, "page limit"):
                list(r._pages(c, "https://host", "issues", max_pages=1))

    def test_http_errors_and_redirect_no_body_or_key(self):
        for code in [302, 401, 403, 429, 500]:
            with client(
                lambda req: httpx.Response(
                    code,
                    text="fixture-secret",
                    headers={"location": "https://evil.example"},
                )
            ) as c:
                with self.assertRaises(r.RedmineError) as error:
                    r._json(c, "https://host", "issues.json")
                self.assertIn(str(code), str(error.exception))
                self.assertNotIn("fixture-secret", str(error.exception))

    def test_bad_json_and_oversize(self):
        for response in [
            httpx.Response(200, text="bad json"),
            httpx.Response(200, content=b"x" * (8 * 1024 * 1024 + 1)),
        ]:
            with client(lambda req: response) as c:
                with self.assertRaises(r.RedmineError):
                    r._json(c, "https://host", "issues.json")

    def test_journal_dedup_fields_and_edited_versions(self):
        stored = []
        with patch.object(
            r,
            "store_event_with_artifact",
            side_effect=lambda db, **kw: stored.append(kw) or {"deduped": False},
        ):
            r._store(
                None,
                "https://host",
                {"project": "*"},
                PROJECT,
                ISSUE,
                ISSUE["journals"][0],
            )
            modified = copy.deepcopy(ISSUE)
            modified["status"]["name"] = "Closed"
            modified["updated_on"] = "2026-10-02T10:00:00Z"
            r._store(
                None,
                "https://host",
                {"project": "*"},
                PROJECT,
                modified,
                modified["journals"][0],
            )
            modified["journals"][0]["notes"] = "Edited comment"
            r._store(
                None,
                "https://host",
                {"project": "*"},
                PROJECT,
                modified,
                modified["journals"][0],
            )
            r._store(None, "https://host", {"project": "*"}, PROJECT, ISSUE)
        self.assertEqual(stored[0]["external_id"], stored[1]["external_id"])
        self.assertNotEqual(stored[1]["external_id"], stored[2]["external_id"])
        self.assertEqual(stored[0]["action"], "issue.comment")
        self.assertEqual(stored[0]["actor"], "Operator")
        self.assertIsNone(stored[3]["actor"])
        self.assertEqual(stored[3]["action"], "issue.snapshot")
        fields = stored[0]["normalized_payload"]["fields"]
        self.assertEqual(fields["change.attr.status_id.new_value"], "3")
        self.assertTrue(fields["change.attr.status_id.changed"])
        self.assertEqual(fields["project.identifier"], "infra")
        identity = collector_id("redmine", "projects", "*")
        self.assertTrue(matches_collector(identity, redact_obj(stored[0])))
        self.assertEqual(
            collector_pointer_filter(identity),
            {"redmine": {"section": "projects", "project_selector": "*"}},
        )

    def test_closed_issues_history_and_successful_cursor(self):
        db = MagicMock()
        db.query.return_value.filter_by.return_value.one_or_none.return_value = None
        requests = []

        def handler(req):
            requests.append(req)
            if req.url.path.endswith("/issues.json"):
                return httpx.Response(
                    200, json={"issues": [{"id": 7}], "offset": 0, "total_count": 1}
                )
            return httpx.Response(200, json={"issue": ISSUE})

        with (
            client(handler) as c,
            patch.object(
                r, "store_event_with_artifact", return_value={"deduped": False}
            ) as store,
        ):
            result = r._project(db, c, "https://host", {"project": "*"}, PROJECT, {})
        self.assertEqual(requests[0].url.params["status_id"], "*")
        self.assertEqual(requests[0].url.params["subproject_id"], "!*")
        self.assertNotIn("updated_on", requests[0].url.params)
        self.assertEqual(requests[1].url.params["include"], "journals")
        self.assertEqual(result["created_events"], 2)
        db.commit.assert_called_once()
        self.assertEqual(store.call_count, 2)

    def test_failure_does_not_advance_cursor(self):
        cursor = MagicMock(
            last_ts=datetime(2026, 10, 1),
            meta={
                "last_full_scan": datetime.now(timezone.utc)
                .replace(tzinfo=None)
                .isoformat()
            },
        )
        before = cursor.last_ts
        db = MagicMock()
        db.query.return_value.filter_by.return_value.one_or_none.return_value = cursor
        with client(lambda req: httpx.Response(403)) as c:
            with self.assertRaises(r.RedmineError):
                r._project(db, c, "https://host", {"project": "infra"}, PROJECT, {})
        self.assertEqual(cursor.last_ts, before)
        db.commit.assert_not_called()

    def test_periodic_reconciliation_keeps_history_floor(self):
        floor = "2026-01-01T00:00:00"
        cursor = MagicMock(
            last_ts=datetime.now(timezone.utc).replace(tzinfo=None),
            meta={
                "history_since": floor,
                "last_full_scan": (
                    datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)
                ).isoformat(),
            },
        )
        db = MagicMock()
        db.query.return_value.filter_by.return_value.one_or_none.return_value = cursor
        with patch.object(r, "_pages", return_value=[]) as pages:
            r._project(db, None, "https://host", {"project": "infra"}, PROJECT, {})
        self.assertEqual(
            pages.call_args.args[3]["updated_on"], ">=2025-12-31T23:55:00Z"
        )
        self.assertEqual(cursor.meta["history_since"], floor)

    def test_disabled_and_no_projects(self):
        with patch.object(r.settings, "redmine_enabled", False):
            self.assertTrue(r.ingest_redmine_all(None)[0]["skipped"])
        with (
            patch.object(r.settings, "demo_mode", True),
            patch.object(r.settings, "redmine_enabled", True),
        ):
            self.assertTrue(r.ingest_redmine_all(None)[0]["skipped"])

    def test_run_lock_release_on_error_and_busy(self):
        db = MagicMock()
        connection = (
            db.get_bind.return_value.connect.return_value.__enter__.return_value
        )
        connection.execute.return_value.scalar.return_value = True
        with self.assertRaises(RuntimeError):
            with r._run_lock(db, "https://host") as acquired:
                self.assertTrue(acquired)
                raise RuntimeError("failure")
        self.assertIn("pg_advisory_unlock", str(connection.execute.call_args.args[0]))
        connection.reset_mock()
        connection.execute.return_value.scalar.return_value = False
        with r._run_lock(db, "https://host") as acquired:
            self.assertFalse(acquired)
        self.assertEqual(connection.execute.call_count, 1)

    def test_managed_collection_registration(self):
        from app.api.routes.managed_configurations import (
            _collector_key,
            _editable_collector,
            _validate_connector,
        )
        from fastapi import HTTPException

        _validate_connector("redmine", {"projects": [{"project": "*"}]})
        self.assertEqual(
            _collector_key("redmine", "projects", {"project": "infra"}), "infra"
        )
        self.assertTrue(_editable_collector("redmine", "projects"))
        with self.assertRaises(HTTPException):
            _validate_connector("redmine", {"projects": [{"project": "../escape"}]})

    def test_all_projects_discovery_isolation(self):
        @contextmanager
        def locked(*args):
            yield True

        db = MagicMock()
        with (
            patch.object(r.settings, "redmine_enabled", True),
            patch.object(r.settings, "demo_mode", False),
            patch.object(r.settings, "redmine_base_url", "https://host/redmine"),
            patch.object(r.settings, "redmine_api_key", "fixture-secret"),
            patch.object(r, "is_safe_url", return_value=True),
            patch.object(
                r, "load_document", return_value={"projects": [{"project": "*"}]}
            ),
            patch.object(r, "_run_lock", locked),
            patch.object(r, "_pages", return_value=[PROJECT, {"id": 3}]),
            patch.object(r.httpx, "Client"),
            patch.object(
                r,
                "_project",
                side_effect=[
                    r.RedmineError("Redmine API HTTP 403"),
                    {"project_id": 3, "created_events": 2},
                ],
            ),
        ):
            results = r.ingest_redmine_all(db)
        self.assertIn("error", results[0])
        self.assertEqual(results[1]["created_events"], 2)
        db.rollback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
