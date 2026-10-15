"""Diary entry creation contracts via the shared event/artifact store."""

from datetime import datetime
from unittest.mock import MagicMock, patch

from app.ingest import diary


def test_add_diary_entry_builds_the_stored_contract():
    db = MagicMock()
    with patch.object(
        diary,
        "store_event_with_artifact",
        return_value={"deduped": False},
    ) as store:
        result = diary.add_diary_entry(
            db,
            "ops@example.com",
            "Reviewed vendor patching",
            details={"checked": "register"},
            links=[{"url": "https://host.example/doc"}],
            timestamp=datetime(2026, 10, 9, 12, 0, 0),
        )
    assert result == {"deduped": False}
    call = store.call_args.kwargs
    assert call["source"] == "diary"
    assert call["system"] == "manual"
    assert call["actor"] == "ops@example.com"
    assert call["action"] == "diary_entry"
    assert call["outcome"] == "info"
    assert call["severity"] == 2
    assert call["summary"] == "diary: Reviewed vendor patching"
    assert call["raw_pointer"] == {"diary": {"author": "ops@example.com"}}
    assert call["normalized_payload"]["details"] == {"checked": "register"}
    assert call["normalized_payload"]["links"] == [{"url": "https://host.example/doc"}]
    assert call["timestamp"] == datetime(2026, 10, 9, 12, 0, 0)
    assert len(call["external_id"]) == 32
    assert call["artifact_kind"] == "diary_entry"
    assert call["captured_by"] == "keen:diary"
    assert call["artifact_key"].startswith("diary/2026-10-09/")


def test_add_diary_entry_defaults_and_id_stability():
    with patch.object(diary, "store_event_with_artifact", return_value={}) as store:
        diary.add_diary_entry(MagicMock(), None, "Blank note")
        call = store.call_args.kwargs
        assert call["actor"] is None
        assert call["normalized_payload"] == {
            "summary": "Blank note",
            "details": {},
            "author": None,
        }
        assert "links" not in call["normalized_payload"]
        assert call["timestamp"].year >= 2025
        first = call["external_id"]
        diary.add_diary_entry(MagicMock(), None, "Blank note")
        assert store.call_args.kwargs["external_id"] == first
