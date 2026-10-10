import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.ingest.forgejo import _store_api_activity_items
from app.mapping.collector import collector_id, event_collector


class ForgejoProvenanceTests(unittest.TestCase):
    def test_api_event_keeps_configured_feed_identity(self):
        feed = "https://git.example.org/team/repo.rss"
        db = Mock()
        cur = SimpleNamespace(last_ts=None)
        with patch(
            "app.ingest.forgejo.store_event_with_artifact",
            return_value={"deduped": False},
        ) as store:
            result = _store_api_activity_items(
                db,
                cur,
                all_items=[
                    {"id": 7, "created": "2026-10-06T00:00:00Z", "op_type": "push"}
                ],
                cursor_name="test",
                feed_url=feed,
                host="git.example.org",
                owner="team",
                repo="repo",
                label=None,
                since=datetime(2026, 10, 1),
                cursor_last=None,
            )
        event = store.call_args.kwargs
        self.assertEqual(event_collector(event), collector_id("forgejo", "feeds", feed))
        self.assertEqual(event["normalized_payload"]["feed_url"], feed)
        self.assertEqual(result["created_events"], 1)
        db.commit.assert_called_once()
