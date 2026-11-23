import ast
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from mosp.core.datetime_utils import utc_from_timestamp_naive, utc_now_naive

from tests.source_tree import SOURCES, name_in


def test_utc_now_preserves_naive_utc_storage_contract():
    before = datetime.now(timezone.utc).replace(tzinfo=None)
    actual = utc_now_naive()
    after = datetime.now(timezone.utc).replace(tzinfo=None)
    assert actual.tzinfo is None
    assert before <= actual <= after


@pytest.mark.parametrize("zone", ["UTC0", "AEST-10", "EST5"])
def test_timestamp_conversion_is_independent_of_host_timezone(monkeypatch, zone):
    if not hasattr(time, "tzset"):
        pytest.skip("Requires POSIX timezone switching")
    try:
        with monkeypatch.context() as m:
            m.setenv("TZ", zone)
            time.tzset()
            assert utc_from_timestamp_naive(0) == datetime(1970, 1, 1)
            assert utc_from_timestamp_naive(0.125) == datetime(
                1970, 1, 1, 0, 0, 0, 125000
            )
            assert utc_from_timestamp_naive(-0.25) == datetime(
                1969, 12, 31, 23, 59, 59, 750000
            )
            expected = datetime.now(timezone.utc).replace(tzinfo=None)
            assert abs((utc_now_naive() - expected).total_seconds()) < 2
    finally:
        time.tzset()


def test_no_deprecated_datetime_utc_methods():
    root = Path(__file__).resolve().parents[1]
    offenders = []
    trees = [SOURCES, *(root / name for name in ("alembic", "scripts", "tests"))]
    for tree_root in trees:
        for path in tree_root.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in {
                    "utcnow",
                    "utcfromtimestamp",
                }:
                    offenders.append(f"{name_in(path, tree_root)}:{node.lineno}")
    assert offenders == [], "\n".join(offenders)
