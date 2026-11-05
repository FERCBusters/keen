"""Collection runs hold their PostgreSQL session locks through one helper.

connections.advisory_lock takes a session advisory lock on a connection of its
own, releases it as the run ends and discards the connection when the release
fails, so a held lock never returns to the pool. A copy of it could drift from
any of that, so the application takes session advisory locks only there.
"""

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
SESSION_LOCK = re.compile(r"pg_(?:try_)?advisory_(?:lock|unlock)(?:_shared)?\s*\(")


def session_lock_calls(source: str) -> int:
    return len(SESSION_LOCK.findall(source))


def test_session_advisory_locks_are_taken_only_by_the_shared_helper():
    found = {
        path.relative_to(APP).as_posix(): count
        for path in sorted(APP.rglob("*.py"))
        if (count := session_lock_calls(path.read_text()))
    }
    # One acquisition and one release, both in connections.advisory_lock.
    assert found == {"ingest/connections.py": 2}


def test_the_check_finds_a_hand_rolled_session_lock():
    source = (
        'connection.execute(text("SELECT pg_try_advisory_lock(42)"))\n'
        'connection.execute(text("SELECT pg_advisory_unlock(42)"))\n'
        # Transaction-scoped locks end with their transaction and are fine.
        'db.execute(text("SELECT pg_advisory_xact_lock(42)"))\n'
    )
    assert session_lock_calls(source) == 2
